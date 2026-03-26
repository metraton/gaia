"""
Scan Orchestrator

Runs all registered scanners in parallel, collects results, combines them
with existing project-context.json using the merge rules, updates metadata,
and performs an atomic write.

Pipeline:
  1. Load existing project-context.json (if present)
  2. Run all scanners in parallel (ThreadPoolExecutor)
  3. Collect and combine scanner sections (handling environment sub-keys)
  4. Merge with existing context (section ownership model)
  5. Update metadata (last_updated, last_scan, scanner_version)
  6. Atomic write to project-context.json
  7. Return ScanOutput

Contract: specs/002-gaia-scan/data-model.md section 4
         specs/002-gaia-scan/contracts/merge-behavior.md
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.scan import __version__ as scanner_package_version
from tools.scan.config import CONTRACT_CONFIG_PATH, ScanConfig
from tools.scan.merge import (
    AGENT_ENRICHED_SECTIONS,
    collect_scanner_sections,
    merge_context,
)
from tools.scan.registry import ScannerRegistry
from tools.scan.scanners.base import BaseScanner, ScanResult
from tools.scan.workspace import WorkspaceInfo, detect_workspace_type

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanOutput:
    """Aggregated output from all scanners.

    Attributes:
        context: Full merged project-context data (top-level with metadata,
                 paths, and sections).
        sections_updated: Section names that were updated by scanners.
        sections_preserved: Agent-enriched sections left untouched.
        warnings: Aggregated warnings from all scanners.
        errors: Aggregated errors from all scanners.
        duration_ms: Total scan time in milliseconds.
        scanner_results: Per-scanner ScanResult mapping.
    """

    context: Dict[str, Any] = field(default_factory=dict)
    sections_updated: List[str] = field(default_factory=list)
    sections_preserved: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    duration_ms: float = 0.0
    scanner_results: Dict[str, ScanResult] = field(default_factory=dict)


class ScanOrchestrator:
    """Orchestrates parallel scanner execution with fault isolation.

    Runs all scanners from a ScannerRegistry, collects their results,
    merges sections with existing context, applies backward compatibility,
    and returns a ScanOutput. Individual scanner failures are caught and
    reported without aborting the scan.

    Args:
        registry: ScannerRegistry with discovered scanners.
        config: ScanConfig with orchestration settings.
    """

    def __init__(
        self,
        registry: Optional[ScannerRegistry] = None,
        config: Optional[ScanConfig] = None,
    ) -> None:
        self.registry = registry or ScannerRegistry()
        self.config = config or ScanConfig()

    def _run_scanner(
        self,
        scanner: BaseScanner,
        project_root: Path,
    ) -> ScanResult:
        """Run a single scanner with fault isolation.

        Args:
            scanner: Scanner instance to execute.
            project_root: Project root path.

        Returns:
            ScanResult from the scanner, or an error result on failure.
        """
        start_ms = time.monotonic() * 1000
        try:
            result = scanner.scan(project_root)
            return result
        except Exception as exc:
            elapsed_ms = (time.monotonic() * 1000) - start_ms
            error_msg = (
                f"Scanner '{scanner.SCANNER_NAME}' failed: "
                f"{type(exc).__name__}: {exc}"
            )
            logger.warning(error_msg)
            return ScanResult(
                scanner=scanner.SCANNER_NAME,
                sections={},
                warnings=[error_msg],
                duration_ms=elapsed_ms,
            )

    def _load_existing_context(self, output_path: Path) -> Dict[str, Any]:
        """Load existing project-context.json if present.

        Args:
            output_path: Path to project-context.json.

        Returns:
            Parsed JSON dict, or empty structure if file does not exist.
        """
        if not output_path.is_file():
            return {}

        try:
            with open(output_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load existing context: %s", exc)
            return {}

    def _resolve_output_path(self, project_root: Path) -> Path:
        """Resolve the output path for project-context.json.

        Args:
            project_root: Project root path.

        Returns:
            Absolute path to project-context.json.
        """
        if self.config.output_path:
            return self.config.output_path
        return project_root / ".claude" / "project-context" / "project-context.json"

    def _build_metadata(
        self,
        existing_metadata: Dict[str, Any],
        project_root: Path,
    ) -> Dict[str, Any]:
        """Build updated metadata section (Rule 6: always update).

        Preserves user-set fields (environment, cloud_provider, etc.) while
        updating timestamps and scanner version.

        Args:
            existing_metadata: Existing metadata from project-context.json.
            project_root: Project root path.

        Returns:
            Updated metadata dict.
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        metadata = dict(existing_metadata) if existing_metadata else {}
        metadata["version"] = metadata.get("version", "2.0")
        metadata["last_updated"] = now_iso

        # Read contract_version from context-contracts.json
        contract_version = self._read_contract_version()
        if contract_version:
            metadata["contract_version"] = contract_version

        # Ensure scan_config sub-section exists
        scan_config = metadata.get("scan_config", {})
        if not isinstance(scan_config, dict):
            scan_config = {}
        scan_config["last_scan"] = now_iso
        scan_config["scanner_version"] = scanner_package_version
        scan_config["staleness_hours"] = self.config.staleness_hours
        metadata["scan_config"] = scan_config

        return metadata

    @staticmethod
    def _read_contract_version() -> Optional[str]:
        """Read the version field from config/context-contracts.json.

        Returns:
            Version string (e.g. "3.0"), or None if file is missing or unreadable.
        """
        try:
            if CONTRACT_CONFIG_PATH.is_file():
                with open(CONTRACT_CONFIG_PATH, "r") as f:
                    data = json.load(f)
                version = data.get("version")
                if isinstance(version, str) and version:
                    return version
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("Failed to read contract version: %s", exc)
        return None

    def _atomic_write(self, output_path: Path, data: Dict[str, Any]) -> None:
        """Atomically write data to JSON file.

        Writes to a temp file in the same directory, then renames.
        This prevents corruption from concurrent reads or crashes.

        Args:
            output_path: Target file path.
            data: Dict to serialize as JSON.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = output_path.with_suffix(".tmp")

        try:
            with open(tmp_path, "w") as f:
                json.dump(data, f, indent=2, sort_keys=False)
                f.write("\n")
            os.rename(str(tmp_path), str(output_path))
        except OSError as exc:
            logger.error("Atomic write failed: %s", exc)
            # Clean up temp file if rename failed
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise

    def run(
        self,
        project_root: Optional[Path] = None,
        write_output: bool = True,
    ) -> ScanOutput:
        """Run all registered scanners and return aggregated output.

        Full pipeline:
          1. Load existing project-context.json
          2. Run scanners in parallel (or sequentially)
          3. Collect and combine scanner sections
          4. Merge with existing context using ownership rules
          5. Update metadata
          6. Atomic write to project-context.json (if write_output=True)
          7. Return ScanOutput

        Args:
            project_root: Project root path. Falls back to config.project_root.
            write_output: Whether to write the result to disk (default True).

        Returns:
            ScanOutput with merged sections, warnings, errors, and timing.
        """
        root = project_root or self.config.project_root
        start_ms = time.monotonic() * 1000

        # Step 1: Load existing context
        output_path = self._resolve_output_path(root)
        existing_full = self._load_existing_context(output_path)
        existing_sections = existing_full.get("sections", {})
        existing_metadata = existing_full.get("metadata", {})

        # Step 1.5: Detect workspace type BEFORE running scanners
        workspace_info = detect_workspace_type(root)
        if workspace_info.is_multi_repo:
            logger.info(
                "Multi-repo workspace: %d repos detected",
                len(workspace_info.repo_dirs),
            )

        # Step 2: Run all scanners
        scanners = self.registry.get_all()
        if self.config.scanners:
            requested = set(self.config.scanners)
            scanners = [s for s in scanners if s.SCANNER_NAME in requested]

        # Pass workspace info to each scanner instance
        for scanner in scanners:
            scanner.workspace_info = workspace_info

        scanner_results: Dict[str, ScanResult] = {}
        all_warnings: List[str] = []
        all_errors: List[str] = []

        if scanners and self.config.parallel:
            scanner_results, all_warnings, all_errors = self._run_parallel(
                scanners, root
            )
        else:
            scanner_results, all_warnings, all_errors = self._run_sequential(
                scanners, root
            )

        # Step 3: Collect and combine scanner sections
        scan_sections = collect_scanner_sections(scanner_results)

        # Step 4: Merge with existing context
        section_owners = self.registry.get_section_owners()

        # Merge (no backward-compat sections -- consumers read v2 directly)
        merged_sections = merge_context(
            existing=existing_sections,
            scan_sections=scan_sections,
            section_owners=section_owners,
        )

        # Step 5: Build metadata
        metadata = self._build_metadata(existing_metadata, root)

        # Determine which sections were updated vs preserved
        sections_updated = sorted(set(scan_sections.keys()))
        sections_preserved = sorted(
            name for name in existing_sections
            if name in AGENT_ENRICHED_SECTIONS
        )

        # Ensure architecture_overview exists as empty dict so contract
        # references are satisfied (it appears in ALL agent contracts).
        # Other agent-enriched sections are only created when an agent
        # populates them -- no empty {} placeholders.
        if "architecture_overview" not in merged_sections:
            merged_sections["architecture_overview"] = {}

        # --- Derive infrastructure.paths from scanner data ---
        self._derive_infrastructure_paths(merged_sections)

        # --- Cross-populate git.monorepo.workspace_config ---
        self._cross_populate_monorepo(merged_sections)

        # --- Remove empty {} placeholders for agent-enriched and mixed sections ---
        # These sections should only exist when they have actual data.
        # architecture_overview is the exception -- always present (even empty).
        from tools.scan.merge import MIXED_SECTION_SCANNER_FIELDS
        remove_if_empty = (
            AGENT_ENRICHED_SECTIONS
            | frozenset(MIXED_SECTION_SCANNER_FIELDS.keys())
        ) - {"architecture_overview"}
        for section_name in list(merged_sections.keys()):
            if section_name in remove_if_empty:
                if merged_sections[section_name] == {}:
                    del merged_sections[section_name]

        # Build full output document (no top-level paths -- use
        # infrastructure.paths as the single source of truth)
        full_context: Dict[str, Any] = {
            "metadata": metadata,
            "sections": merged_sections,
        }

        # Step 7: Atomic write
        if write_output:
            self._atomic_write(output_path, full_context)

        elapsed_ms = (time.monotonic() * 1000) - start_ms

        return ScanOutput(
            context=full_context,
            sections_updated=sections_updated,
            sections_preserved=sections_preserved,
            warnings=all_warnings,
            errors=all_errors,
            duration_ms=elapsed_ms,
            scanner_results=scanner_results,
        )

    @staticmethod
    def _derive_infrastructure_paths(
        merged_sections: Dict[str, Any],
    ) -> None:
        """Derive infrastructure.paths shortcuts from detected scanner data.

        Populates infrastructure.paths.gitops, .terraform, and .app_services
        from orchestration and infrastructure scanner results when the paths
        are not already set.

        Args:
            merged_sections: Merged sections dict (mutated in place).
        """
        infra = merged_sections.get("infrastructure")
        if not isinstance(infra, dict):
            return

        paths = infra.setdefault("paths", {})

        # --- gitops: derive from orchestration.gitops.config_path ---
        if not paths.get("gitops"):
            orch = merged_sections.get("orchestration")
            if isinstance(orch, dict):
                gitops = orch.get("gitops", {})
                if isinstance(gitops, dict) and gitops.get("config_path"):
                    paths["gitops"] = gitops["config_path"]

        # --- terraform: derive from infrastructure.iac entries ---
        if not paths.get("terraform"):
            for iac_entry in infra.get("iac", []):
                if isinstance(iac_entry, dict) and iac_entry.get("tool") in (
                    "terraform",
                    "terragrunt",
                ):
                    base_path = iac_entry.get("base_path")
                    if base_path and base_path != ".":
                        paths["terraform"] = base_path
                        break

        # --- app_services: derive from Dockerfile paths common parent ---
        if not paths.get("app_services"):
            containers = infra.get("containers", [])
            dockerfile_dirs: list = []
            for container in containers:
                if not isinstance(container, dict):
                    continue
                if container.get("tool") != "docker":
                    continue
                for fpath in container.get("files", []):
                    parent = str(Path(fpath).parent)
                    if parent != ".":
                        dockerfile_dirs.append(parent)

            if dockerfile_dirs:
                # Find common parent directory
                from pathlib import PurePosixPath

                parts_list = [PurePosixPath(d).parts for d in dockerfile_dirs]
                common: list = []
                for segments in zip(*parts_list):
                    if len(set(segments)) == 1:
                        common.append(segments[0])
                    else:
                        break
                if common:
                    paths["app_services"] = str(PurePosixPath(*common))

        # Clean up: remove None-valued path entries
        for key in list(paths.keys()):
            if paths[key] is None:
                del paths[key]

    @staticmethod
    def _cross_populate_monorepo(
        merged_sections: Dict[str, Any],
    ) -> None:
        """Cross-populate git.monorepo.workspace_config from project_identity.

        When the stack scanner detects a monorepo (project_identity.type ==
        'monorepo' and project_identity.monorepo has data), propagate the
        workspace_config to git.monorepo so both sections are consistent.

        Args:
            merged_sections: Merged sections dict (mutated in place).
        """
        identity = merged_sections.get("project_identity")
        git = merged_sections.get("git")
        if not isinstance(identity, dict) or not isinstance(git, dict):
            return

        monorepo_data = identity.get("monorepo", {})
        if not isinstance(monorepo_data, dict):
            return

        # If project_identity detected a monorepo, populate git.monorepo
        if monorepo_data.get("detected"):
            git_monorepo = git.setdefault("monorepo", {})
            if isinstance(git_monorepo, dict):
                tool = monorepo_data.get("tool")
                if tool and not git_monorepo.get("workspace_config"):
                    git_monorepo["workspace_config"] = tool

    def _run_parallel(
        self,
        scanners: List[BaseScanner],
        root: Path,
    ) -> tuple:
        """Run scanners in parallel using ThreadPoolExecutor.

        Args:
            scanners: List of scanner instances to run.
            root: Project root path.

        Returns:
            Tuple of (scanner_results, all_warnings, all_errors).
        """
        scanner_results: Dict[str, ScanResult] = {}
        all_warnings: List[str] = []
        all_errors: List[str] = []

        with ThreadPoolExecutor(
            max_workers=min(len(scanners), 8)
        ) as executor:
            future_to_scanner = {
                executor.submit(self._run_scanner, scanner, root): scanner
                for scanner in scanners
            }
            for future in as_completed(future_to_scanner):
                scanner = future_to_scanner[future]
                try:
                    result = future.result(
                        timeout=self.config.timeout_per_scanner
                    )
                except Exception as exc:
                    error_msg = (
                        f"Scanner '{scanner.SCANNER_NAME}' timed out or "
                        f"failed in executor: {type(exc).__name__}: {exc}"
                    )
                    logger.warning(error_msg)
                    result = ScanResult(
                        scanner=scanner.SCANNER_NAME,
                        sections={},
                        warnings=[error_msg],
                        duration_ms=0.0,
                    )
                    all_errors.append(error_msg)

                scanner_results[scanner.SCANNER_NAME] = result
                all_warnings.extend(result.warnings)

        return scanner_results, all_warnings, all_errors

    def _run_sequential(
        self,
        scanners: List[BaseScanner],
        root: Path,
    ) -> tuple:
        """Run scanners sequentially.

        Args:
            scanners: List of scanner instances to run.
            root: Project root path.

        Returns:
            Tuple of (scanner_results, all_warnings, all_errors).
        """
        scanner_results: Dict[str, ScanResult] = {}
        all_warnings: List[str] = []
        all_errors: List[str] = []

        for scanner in scanners:
            result = self._run_scanner(scanner, root)
            scanner_results[scanner.SCANNER_NAME] = result
            all_warnings.extend(result.warnings)

        return scanner_results, all_warnings, all_errors