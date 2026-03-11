"""
Infrastructure Scanner

Detects cloud providers, IaC tools, container tooling, CI/CD platforms,
and infrastructure-related directory paths. Only produces the 'infrastructure'
section when at least one indicator is found; returns empty dict for projects
with no infrastructure files.

Schema: data-model.md section 2.7
Contract: contracts/scanner-interface.md
"""

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.scan.scanners.base import BaseScanner, ScanResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Terraform provider patterns
# ---------------------------------------------------------------------------
_TF_PROVIDER_PATTERNS: Dict[str, str] = {
    "gcp": r'provider\s+"google"',
    "aws": r'provider\s+"aws"',
    "azure": r'provider\s+"azurerm"',
}

# ---------------------------------------------------------------------------
# Cloud-related environment variable prefixes (non-secret only)
# ---------------------------------------------------------------------------
_CLOUD_ENV_VARS: Dict[str, List[str]] = {
    "gcp": [
        "GOOGLE_CLOUD_PROJECT",
        "GCLOUD_PROJECT",
        "GCP_PROJECT",
        "CLOUDSDK_CORE_PROJECT",
    ],
    "aws": [
        "AWS_DEFAULT_REGION",
        "AWS_REGION",
    ],
    "azure": [
        "AZURE_SUBSCRIPTION_ID",
        "ARM_SUBSCRIPTION_ID",
    ],
}

# ---------------------------------------------------------------------------
# IaC tool markers
# ---------------------------------------------------------------------------
_IAC_MARKERS: List[Dict[str, str]] = [
    {"tool": "terraform", "rglob": "*.tf"},
    {"tool": "terragrunt", "rglob": "terragrunt.hcl"},
    {"tool": "pulumi", "rglob": "Pulumi.yaml"},
    {"tool": "cdk", "rglob": "cdk.json"},
]

# ---------------------------------------------------------------------------
# Container markers
# ---------------------------------------------------------------------------
_CONTAINER_GLOBS: List[Dict[str, Any]] = [
    {"tool": "docker", "rglob_patterns": ["Dockerfile", "Dockerfile.*"]},
    {"tool": "docker-compose", "rglob_patterns": ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]},
]

# ---------------------------------------------------------------------------
# CI/CD platform markers
# ---------------------------------------------------------------------------
_CICD_MARKERS: List[Dict[str, Any]] = [
    {"platform": "github-actions", "type": "dir", "path": ".github/workflows"},
    {"platform": "gitlab-ci", "type": "file", "path": ".gitlab-ci.yml"},
    {"platform": "jenkins", "type": "file", "path": "Jenkinsfile"},
    {"platform": "circleci", "type": "dir", "path": ".circleci"},
]

# ---------------------------------------------------------------------------
# Known infrastructure directory names
# ---------------------------------------------------------------------------
_INFRA_DIR_NAMES: Dict[str, List[str]] = {
    "gitops": ["gitops", "flux", "argocd", "deploy", "k8s"],
    "terraform": ["terraform", "terragrunt", "infra", "infrastructure"],
    "app_services": ["app_services", "app-services", "services", "apps"],
}


class InfrastructureScanner(BaseScanner):
    """Detects cloud providers, IaC, containers, CI/CD, and infra paths.

    Pure function contract:
    - No file writes
    - No state modification
    - No network calls
    - Only reads: filesystem paths, file contents, environment variables
    """

    @property
    def SCANNER_NAME(self) -> str:
        return "infrastructure"

    @property
    def SCANNER_VERSION(self) -> str:
        return "1.0.0"

    @property
    def OWNED_SECTIONS(self) -> List[str]:
        return ["infrastructure"]

    def scan(self, root: Path) -> ScanResult:
        """Scan for infrastructure indicators.

        Args:
            root: Absolute path to the project root directory.

        Returns:
            ScanResult with 'infrastructure' section if indicators found,
            or empty sections dict if none detected.
        """
        start = time.monotonic()
        warnings: List[str] = []

        try:
            cloud_providers = self._detect_cloud_providers(root, warnings)
            iac = self._detect_iac(root, warnings)
            containers = self._detect_containers(root, warnings)
            ci_cd = self._detect_cicd(root, warnings)
            paths = self._detect_paths(root, warnings)

            # Only produce section when at least one indicator is found
            has_indicators = (
                cloud_providers
                or iac
                or containers
                or ci_cd
                or paths.get("gitops") is not None
                or paths.get("terraform") is not None
                or paths.get("app_services") is not None
            )

            if not has_indicators:
                duration_ms = (time.monotonic() - start) * 1000
                return self.make_result(sections={}, warnings=warnings, duration_ms=duration_ms)

            sections: Dict[str, Any] = {
                "infrastructure": {
                    "cloud_providers": cloud_providers,
                    "iac": iac,
                    "containers": containers,
                    "ci_cd": ci_cd,
                    "paths": paths,
                }
            }

            duration_ms = (time.monotonic() - start) * 1000
            return self.make_result(sections=sections, warnings=warnings, duration_ms=duration_ms)

        except Exception as exc:
            logger.warning("Infrastructure scanner failed: %s", exc)
            duration_ms = (time.monotonic() - start) * 1000
            return self.make_result(sections={}, warnings=[str(exc)], duration_ms=duration_ms)

    # ------------------------------------------------------------------
    # Cloud provider detection
    # ------------------------------------------------------------------

    def _detect_cloud_providers(
        self, root: Path, warnings: List[str]
    ) -> List[Dict[str, Any]]:
        """Detect cloud providers from Terraform, CLI configs, and env vars."""
        providers: Dict[str, Dict[str, Any]] = {}

        # 1. Terraform provider blocks
        self._detect_providers_from_terraform(root, providers, warnings)

        # 2. CLI configs
        self._detect_providers_from_cli_configs(providers)

        # 3. Environment variables (non-secret only)
        self._detect_providers_from_env_vars(providers)

        return list(providers.values())

    def _detect_providers_from_terraform(
        self,
        root: Path,
        providers: Dict[str, Dict[str, Any]],
        warnings: List[str],
    ) -> None:
        """Scan .tf files for provider blocks."""
        tf_files = list(root.rglob("*.tf"))
        for tf_file in tf_files:
            try:
                content = tf_file.read_text(encoding="utf-8", errors="replace")
                for cloud_name, pattern in _TF_PROVIDER_PATTERNS.items():
                    if re.search(pattern, content):
                        if cloud_name not in providers:
                            providers[cloud_name] = {
                                "name": cloud_name,
                                "detected_by": "terraform_provider",
                            }
            except OSError as exc:
                warnings.append(f"Could not read {tf_file}: {exc}")

    def _detect_providers_from_cli_configs(
        self, providers: Dict[str, Dict[str, Any]]
    ) -> None:
        """Check for cloud CLI config files."""
        home = Path.home()

        # GCP: gcloud CLI config
        gcloud_config = home / ".config" / "gcloud" / "properties"
        if gcloud_config.is_file() and "gcp" not in providers:
            providers["gcp"] = {
                "name": "gcp",
                "detected_by": "cli_config",
            }

        # AWS: aws CLI config
        aws_config = home / ".aws" / "config"
        if aws_config.is_file() and "aws" not in providers:
            providers["aws"] = {
                "name": "aws",
                "detected_by": "cli_config",
            }

        # Azure: az CLI config
        azure_config = home / ".azure" / "azureProfile.json"
        if azure_config.is_file() and "azure" not in providers:
            providers["azure"] = {
                "name": "azure",
                "detected_by": "cli_config",
            }

    def _detect_providers_from_env_vars(
        self, providers: Dict[str, Dict[str, Any]]
    ) -> None:
        """Detect cloud providers from non-secret environment variables."""
        for cloud_name, env_vars in _CLOUD_ENV_VARS.items():
            if cloud_name in providers:
                continue
            for var in env_vars:
                if os.environ.get(var):
                    providers[cloud_name] = {
                        "name": cloud_name,
                        "detected_by": "env_var",
                    }
                    break

    # ------------------------------------------------------------------
    # IaC tool detection
    # ------------------------------------------------------------------

    def _detect_iac(
        self, root: Path, warnings: List[str]
    ) -> List[Dict[str, Any]]:
        """Detect IaC tools from file presence."""
        results: List[Dict[str, Any]] = []

        for marker in _IAC_MARKERS:
            try:
                found_files = sorted(root.rglob(marker["rglob"]))
                if found_files:
                    # Determine base path from the first detected file
                    relative_files = [
                        str(f.relative_to(root)) for f in found_files[:10]
                    ]
                    base_path = self._common_base_path(relative_files)
                    results.append(
                        {
                            "tool": marker["tool"],
                            "base_path": base_path,
                            "detected_files": relative_files,
                        }
                    )
            except OSError as exc:
                warnings.append(f"IaC detection error for {marker['tool']}: {exc}")

        return results

    # ------------------------------------------------------------------
    # Container detection
    # ------------------------------------------------------------------

    def _detect_containers(
        self, root: Path, warnings: List[str]
    ) -> List[Dict[str, Any]]:
        """Detect container tooling from file presence."""
        results: List[Dict[str, Any]] = []

        for container_def in _CONTAINER_GLOBS:
            found: List[str] = []
            for rglob_pattern in container_def["rglob_patterns"]:
                try:
                    matches = sorted(root.rglob(rglob_pattern))
                    found.extend(str(m.relative_to(root)) for m in matches)
                except OSError as exc:
                    warnings.append(
                        f"Container detection error for {container_def['tool']}: {exc}"
                    )

            if found:
                results.append(
                    {
                        "tool": container_def["tool"],
                        "files": sorted(set(found)),
                    }
                )

        return results

    # ------------------------------------------------------------------
    # CI/CD detection
    # ------------------------------------------------------------------

    def _detect_cicd(
        self, root: Path, warnings: List[str]
    ) -> List[Dict[str, Any]]:
        """Detect CI/CD platforms from config files/directories."""
        results: List[Dict[str, Any]] = []

        for marker in _CICD_MARKERS:
            target = root / marker["path"]
            detected = False

            if marker["type"] == "dir":
                detected = target.is_dir()
            elif marker["type"] == "file":
                detected = target.is_file()

            if detected:
                results.append(
                    {
                        "platform": marker["platform"],
                        "config_path": marker["path"],
                    }
                )

        return results

    # ------------------------------------------------------------------
    # Infrastructure path detection
    # ------------------------------------------------------------------

    def _detect_paths(
        self, root: Path, warnings: List[str]
    ) -> Dict[str, Optional[str]]:
        """Detect infrastructure-related directories."""
        detected: Dict[str, Optional[str]] = {
            "gitops": None,
            "terraform": None,
            "app_services": None,
        }

        try:
            # Only check immediate subdirectories of root (depth=1)
            subdirs = [
                d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")
            ]
        except OSError as exc:
            warnings.append(f"Path detection error: {exc}")
            return detected

        for subdir in subdirs:
            dir_name = subdir.name.lower()
            for path_key, candidates in _INFRA_DIR_NAMES.items():
                if detected[path_key] is None and dir_name in candidates:
                    detected[path_key] = str(subdir.relative_to(root))

        return detected

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _common_base_path(paths: List[str]) -> str:
        """Find the common base directory from a list of relative paths.

        Returns '.' if paths are in the root directory.
        """
        if not paths:
            return "."

        parts_list = [Path(p).parent.parts for p in paths]
        if not parts_list:
            return "."

        common: List[str] = []
        for segments in zip(*parts_list):
            if len(set(segments)) == 1:
                common.append(segments[0])
            else:
                break

        return str(Path(*common)) if common else "."


# Module-level convenience for verify commands
def scan(root: Path) -> Dict[str, Any]:
    """Module-level convenience function for infrastructure scanning.

    Args:
        root: Absolute path to the project root directory.

    Returns:
        Dict mapping section names to section data.
    """
    scanner = InfrastructureScanner()
    result = scanner.scan(root)
    return result.sections
