#!/usr/bin/env python3
"""
Phase B: Agnostic Local Discovery

Explores repository structure within given paths, finds SSOT files,
extracts configuration, and validates internal coherence.

Reference: agent-discovery-rules.md + agent-validation-lifecycle.md (Phase B: B1-B5)
"""

import json
import logging
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Any, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class DiscoveryResult:
    """Result of local discovery"""
    discovered_files: Dict[str, List[str]]  # Categorized by type: {terraform, kustomization, helm, etc}
    ssot_files: Dict[str, str]  # SSOT files found and their paths
    configurations: Dict[str, Any]  # Extracted configuration from files
    internal_coherence: Dict[str, bool]  # Validation of internal consistency
    discrepancies: List[str]  # Things that don't match between files


class LocalDiscoverer:
    """
    Discovers information locally within given paths.

    Agnostic approach:
    - Doesn't assume directory structure
    - Searches by patterns, not by assumed names
    - Reports what was found, not what was expected
    - Limited depth (max 3 levels) to avoid getting lost
    """

    def __init__(self, root_path: Path, max_depth: int = 3):
        self.root_path = Path(root_path)
        self.max_depth = max_depth

        # SSOT file patterns (non-restrictive)
        self.ssot_patterns = {
            "terraform": [r"\.tf$", r"terraform\.tfvars$"],
            "kustomization": [r"kustomization\.ya?ml$"],
            "helmrelease": [r"HelmRelease\.ya?ml$"],
            "docker": [r"Dockerfile", r"docker-compose\.ya?ml$"],
            "github_workflows": [r"\.github/workflows/.*\.ya?ml$"],
            "helm_values": [r"values\.ya?ml$"],
            "git": [r"\.gitignore$", r"\.git/"]
        }

    def discover(self) -> DiscoveryResult:
        """Main discovery entry point (Phase B)"""

        logger.debug(f"B1: Exploring {self.root_path} (max depth: {self.max_depth})...")

        discovered = self._b1_explore_structure()
        logger.debug(f"B2: Found {sum(len(v) for v in discovered.values())} files")

        ssot = self._b2_find_ssot(discovered)
        logger.debug(f"B3: Identified {len(ssot)} SSOT files")

        config = self._b3_extract_configuration(ssot)
        logger.debug(f"B4: Extracted configuration from {len(config)} sources")

        coherence, discrepancies = self._b4_validate_coherence(config)
        logger.debug(f"B5: Validated coherence - {len(discrepancies)} discrepancies")

        return DiscoveryResult(
            discovered_files=discovered,
            ssot_files=ssot,
            configurations=config,
            internal_coherence=coherence,
            discrepancies=discrepancies
        )

    def _b1_explore_structure(self) -> Dict[str, List[str]]:
        """B1: Explore directory structure within depth limit"""
        discovered = {}

        for pattern_type, patterns in self.ssot_patterns.items():
            discovered[pattern_type] = []

            for root, dirs, files in self.root_path.walk():
                # Check depth
                depth = len(root.relative_to(self.root_path).parts)
                if depth >= self.max_depth:
                    continue

                for file in files:
                    filepath = root / file
                    rel_path = filepath.relative_to(self.root_path)

                    # Check against patterns
                    for pattern in patterns:
                        if re.search(pattern, str(rel_path)):
                            discovered[pattern_type].append(str(rel_path))
                            break

        return discovered

    def _b2_find_ssot(self, discovered: Dict[str, List[str]]) -> Dict[str, str]:
        """B2: Identify SSOT (Single Source of Truth) files"""
        ssot = {}

        # Find primary SSOT file per type
        for file_type, files in discovered.items():
            if files:
                # Usually the first one or the one in root
                primary = files[0]
                ssot[file_type] = primary
                logger.debug(f"SSOT for {file_type}: {primary}")

        return ssot

    def _b3_extract_configuration(self, ssot_files: Dict[str, str]) -> Dict[str, Any]:
        """B3: Extract configuration from SSOT files"""
        config = {}

        for file_type, filepath in ssot_files.items():
            full_path = self.root_path / filepath
            if not full_path.exists():
                logger.warning(f"SSOT file not found: {filepath}")
                continue

            try:
                content = full_path.read_text()
                config[file_type] = self._parse_file_content(content, file_type)
            except Exception as e:
                logger.warning(f"Failed to parse {filepath}: {e}")
                config[file_type] = {"_error": str(e)}

        return config

    def _parse_file_content(self, content: str, file_type: str) -> Dict[str, Any]:
        """Parse file content based on type"""
        try:
            # Try JSON first
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # Try YAML (simple parsing, not full YAML)
        if file_type in ["kustomization", "helmrelease", "helm_values", "docker", "github_workflows"]:
            return self._parse_yaml_simple(content)

        # Try terraform
        if file_type == "terraform":
            return self._parse_terraform_simple(content)

        # Fallback: just return first 100 lines
        return {"_raw": content[:500]}

    def _parse_yaml_simple(self, content: str) -> Dict[str, Any]:
        """Simple YAML parsing (extract key=value lines)"""
        result = {}
        for line in content.split("\n"):
            line = line.strip()
            if ":" in line and not line.startswith("#"):
                key, value = line.split(":", 1)
                result[key.strip()] = value.strip()
        return result

    def _parse_terraform_simple(self, content: str) -> Dict[str, Any]:
        """Simple Terraform parsing (extract locals, variables)"""
        result = {}

        # Find locals
        locals_match = re.search(r'locals\s*\{(.*?)\}', content, re.DOTALL)
        if locals_match:
            result["locals"] = locals_match.group(1).strip()

        # Find variables (just names)
        vars_matches = re.findall(r'variable\s+"([^"]+)"', content)
        if vars_matches:
            result["variables"] = vars_matches

        # Find resources (just names)
        resources = re.findall(r'resource\s+"([^"]+)"\s+"([^"]+)"', content)
        if resources:
            result["resources"] = [f"{t}.{n}" for t, n in resources]

        return result

    def _b4_validate_coherence(self, config: Dict[str, Any]) -> Tuple[Dict[str, bool], List[str]]:
        """B4: Validate internal coherence (do files reference each other consistently?)"""
        coherence = {}
        discrepancies = []

        # Check if names/references are consistent across file types
        # Example: HelmRelease.yaml references a chart, does helm values.yaml have it?
        # This is simplified - in real implementation would be more sophisticated

        for file_type in config:
            coherence[file_type] = True  # Assume coherent unless proven otherwise

        # Example discrepancy detection
        if "helmrelease" in config and "helm_values" in config:
            # Check if both have matching release names
            helm_name = config.get("helmrelease", {}).get("releaseName")
            values_name = config.get("helm_values", {}).get("name")
            if helm_name and values_name and helm_name != values_name:
                discrepancies.append(
                    f"HelmRelease name ({helm_name}) != values name ({values_name})"
                )
                coherence["helmrelease"] = False

        return coherence, discrepancies

    def generate_report(self, result: DiscoveryResult) -> str:
        """Generate human-readable discovery report"""
        lines = []
        lines.append(f"\n{'='*60}")
        lines.append("PHASE B: LOCAL DISCOVERY")
        lines.append(f"{'='*60}\n")

        # Files discovered
        total_files = sum(len(v) for v in result.discovered_files.values())
        lines.append(f"FILES DISCOVERED: {total_files}")
        for file_type, files in result.discovered_files.items():
            if files:
                lines.append(f"  {file_type}: {len(files)} file(s)")
                for f in files[:3]:  # Show first 3
                    lines.append(f"    - {f}")
                if len(files) > 3:
                    lines.append(f"    ... and {len(files) - 3} more")

        # SSOT files
        lines.append(f"\nSSOT FILES IDENTIFIED: {len(result.ssot_files)}")
        for ssot_type, path in result.ssot_files.items():
            lines.append(f"  {ssot_type}: {path}")

        # Configuration extracted
        lines.append(f"\nCONFIGURATION EXTRACTED: {len(result.configurations)} sources")
        for cfg_type, cfg in result.configurations.items():
            lines.append(f"  {cfg_type}: {len(cfg)} keys")

        # Coherence
        lines.append(f"\nINTERNAL COHERENCE:")
        all_coherent = all(result.internal_coherence.values())
        for coh_type, is_coherent in result.internal_coherence.items():
            status = "✓" if is_coherent else "✗"
            lines.append(f"  {status} {coh_type}")

        # Discrepancies
        if result.discrepancies:
            lines.append(f"\nDISCREPANCIES FOUND: {len(result.discrepancies)}")
            for disc in result.discrepancies:
                lines.append(f"  ⚠️  {disc}")
        else:
            lines.append("\n✓ NO DISCREPANCIES DETECTED")

        lines.append(f"\n{'='*60}\n")
        return "\n".join(lines)


# CLI Usage
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG)

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp")

    discoverer = LocalDiscoverer(path)
    result = discoverer.discover()
    print(discoverer.generate_report(result))
