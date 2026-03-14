"""
Infrastructure Scanner

Detects cloud providers, IaC tools, container tooling, CI/CD platforms,
application services, and infrastructure-related directory paths. Only
produces the 'infrastructure' section when at least one indicator is found;
returns empty dict for projects with no infrastructure files.

Schema: data-model.md section 2.7
Contract: contracts/scanner-interface.md
"""

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from tools.scan.scanners.base import BaseScanner, ScanResult
from tools.scan.walk import walk_project, walk_project_named

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
        return ["infrastructure", "application_services"]

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
            app_services = self._detect_application_services(root, warnings)

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

            if not has_indicators and not app_services:
                duration_ms = (time.monotonic() - start) * 1000
                return self.make_result(sections={}, warnings=warnings, duration_ms=duration_ms)

            sections: Dict[str, Any] = {}

            if has_indicators:
                sections["infrastructure"] = {
                    "cloud_providers": cloud_providers,
                    "iac": iac,
                    "containers": containers,
                    "ci_cd": ci_cd,
                    "paths": paths,
                }

            if app_services:
                sections["application_services"] = {
                    "services": app_services,
                    "base_path": self._common_base_path(
                        [s["path"] for s in app_services]
                    ),
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
        for tf_file in walk_project(root, [".tf"]):
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
        """Detect IaC tools from file presence.

        Groups detected files into distinct IaC roots. For example, if both
        ``terraform/`` (shared modules) and ``features/infra/`` contain .tf
        files, they become separate entries. Validates that all reported file
        paths actually exist on disk (Fix 3: ghost references).
        """
        results: List[Dict[str, Any]] = []

        for marker in _IAC_MARKERS:
            try:
                rglob_pattern = marker["rglob"]
                if rglob_pattern.startswith("*."):
                    ext = rglob_pattern[1:]
                    found_files = sorted(walk_project(root, [ext]))
                else:
                    found_files = sorted(walk_project_named(root, [rglob_pattern]))

                if not found_files:
                    continue

                # Fix 3: filter out files whose paths no longer exist
                found_files = [f for f in found_files if f.exists()]

                if not found_files:
                    continue

                relative_files = [
                    str(f.relative_to(root)) for f in found_files
                ]

                # Group files into distinct IaC roots. A "root" is the
                # top-level directory under `root` that contains the file
                # (depth-1 or depth-2 for monorepo subdirs).
                iac_roots = self._group_iac_roots(root, found_files)

                for iac_root_path, root_files in sorted(iac_roots.items()):
                    root_relative_files = [
                        str(f.relative_to(root)) for f in root_files[:10]
                    ]
                    results.append(
                        {
                            "tool": marker["tool"],
                            "base_path": iac_root_path,
                            "detected_files": root_relative_files,
                        }
                    )
            except OSError as exc:
                warnings.append(f"IaC detection error for {marker['tool']}: {exc}")

        return results

    @staticmethod
    def _group_iac_roots(
        root: Path, files: List[Path]
    ) -> Dict[str, List[Path]]:
        """Group IaC files by their top-level infrastructure root directory.

        Identifies distinct IaC roots by looking for well-known directory
        names (``terraform/``, ``infra/``) in the file path. Files that
        share a common IaC root are grouped together.
        """
        # Known IaC root directory names
        iac_dir_names = {"terraform", "terragrunt", "infra", "infrastructure", "iac"}

        groups: Dict[str, List[Path]] = {}

        for f in files:
            rel = f.relative_to(root)
            parts = rel.parts

            # Find the deepest IaC-root-named directory in the path
            iac_root = None
            for i, part in enumerate(parts[:-1]):  # exclude filename
                if part.lower() in iac_dir_names:
                    # Use path up to and including this directory
                    iac_root = str(Path(*parts[: i + 1]))
                    break

            if iac_root is None:
                # No known IaC directory found; use common base path logic
                iac_root = str(rel.parent) if len(parts) > 1 else "."

            if iac_root not in groups:
                groups[iac_root] = []
            groups[iac_root].append(f)

        return groups

    # ------------------------------------------------------------------
    # Container detection
    # ------------------------------------------------------------------

    def _detect_containers(
        self, root: Path, warnings: List[str]
    ) -> List[Dict[str, Any]]:
        """Detect container tooling from file presence.

        Validates that all reported paths actually exist (Fix 3: ghost refs).
        """
        results: List[Dict[str, Any]] = []

        for container_def in _CONTAINER_GLOBS:
            found: List[str] = []
            try:
                # Separate exact names from prefix patterns (e.g., "Dockerfile.*")
                exact_names = []
                prefixes = []
                for pattern in container_def["rglob_patterns"]:
                    if "*" in pattern:
                        # "Dockerfile.*" -> prefix "Dockerfile."
                        prefixes.append(pattern.replace("*", ""))
                    else:
                        exact_names.append(pattern)

                for match in walk_project_named(root, exact_names):
                    if match.exists():
                        found.append(str(match.relative_to(root)))

                # Handle prefix patterns (e.g., "Dockerfile.*") via walk
                if prefixes:
                    from tools.scan.walk import walk_project_prefix
                    for match in walk_project_prefix(root, prefixes):
                        if match.exists():
                            found.append(str(match.relative_to(root)))
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
        """Detect CI/CD platforms from config files/directories.

        Checks root-level marker files first, then scans subdirectories
        for CI/CD configuration files and manifests (e.g., gitlab-runner
        kustomize files).
        """
        results: List[Dict[str, Any]] = []
        detected_platforms: set = set()

        # Check root-level markers
        for marker in _CICD_MARKERS:
            target = root / marker["path"]
            detected = False

            if marker["type"] == "dir":
                detected = target.is_dir()
            elif marker["type"] == "file":
                detected = target.is_file()

            if detected:
                entry: Dict[str, Any] = {
                    "platform": marker["platform"],
                    "config_path": marker["path"],
                }
                # Enrich GitLab CI entries with related files and stages
                if marker["platform"] == "gitlab-ci":
                    self._enrich_gitlab_ci(root, entry, warnings)
                results.append(entry)
                detected_platforms.add(marker["platform"])

        # Check subdirectories for CI/CD config files (handles monorepo
        # and nested project structures)
        if not results:
            self._detect_cicd_in_subdirs(root, results, detected_platforms, warnings)

        # Detect CI/CD from manifest content (e.g., gitlab-runner in
        # kustomize manifests)
        if "gitlab-ci" not in detected_platforms:
            self._detect_cicd_from_manifests(root, results, detected_platforms, warnings)

        return results

    def _detect_cicd_in_subdirs(
        self,
        root: Path,
        results: List[Dict[str, Any]],
        detected_platforms: set,
        warnings: List[str],
    ) -> None:
        """Check immediate subdirectories for CI/CD config files."""
        try:
            for entry in sorted(root.iterdir()):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                if entry.name in ("node_modules", "vendor", "__pycache__"):
                    continue
                for marker in _CICD_MARKERS:
                    if marker["platform"] in detected_platforms:
                        continue
                    target = entry / marker["path"]
                    detected = False
                    if marker["type"] == "dir":
                        detected = target.is_dir()
                    elif marker["type"] == "file":
                        detected = target.is_file()
                    if detected:
                        rel_path = str(target.relative_to(root))
                        cicd_entry: Dict[str, Any] = {
                            "platform": marker["platform"],
                            "config_path": rel_path,
                        }
                        # Enrich GitLab CI entries found in subdirectories
                        if marker["platform"] == "gitlab-ci":
                            self._enrich_gitlab_ci(entry, cicd_entry, warnings)
                        results.append(cicd_entry)
                        detected_platforms.add(marker["platform"])
        except OSError as exc:
            warnings.append(f"CI/CD subdirectory scan error: {exc}")

    def _detect_cicd_from_manifests(
        self,
        root: Path,
        results: List[Dict[str, Any]],
        detected_platforms: set,
        warnings: List[str],
    ) -> None:
        """Detect CI/CD platforms from Kubernetes manifest content.

        Looks for CI/CD-related resources like gitlab-runner deployments
        in kustomize/Kubernetes manifests.
        """
        # CI/CD-related directory or file name patterns
        cicd_manifest_indicators = {
            "gitlab-runner": "gitlab-ci",
            "github-actions-runner": "github-actions",
            "jenkins": "jenkins",
        }

        try:
            for dirpath, dirnames, filenames in os.walk(str(root)):
                dirnames[:] = [
                    d for d in dirnames
                    if d not in ("node_modules", ".git", "__pycache__",
                                 ".terraform", "vendor", "dist", "build",
                                 ".venv", "venv")
                    and not d.startswith(".")
                ]
                dir_name = os.path.basename(dirpath).lower()
                for indicator, platform in cicd_manifest_indicators.items():
                    if platform in detected_platforms:
                        continue
                    if indicator in dir_name:
                        rel_path = str(Path(dirpath).relative_to(root))
                        results.append(
                            {
                                "platform": platform,
                                "config_path": rel_path,
                            }
                        )
                        detected_platforms.add(platform)
        except OSError as exc:
            warnings.append(f"CI/CD manifest scan error: {exc}")

    # ------------------------------------------------------------------
    # GitLab CI enrichment
    # ------------------------------------------------------------------

    def _enrich_gitlab_ci(
        self,
        root: Path,
        entry: Dict[str, Any],
        warnings: List[str],
    ) -> None:
        """Enrich a GitLab CI entry with related files and stage names.

        Looks for:
        - .gitlab/ci/ directory (reusable CI components/templates)
        - Additional CI yml files (e.g., .gitlab-ci-builder.yml)
        - .ci-local/ directory (local CI testing)
        - Stage names extracted from the main .gitlab-ci.yml
        """
        related_files: List[str] = []

        try:
            # Check for .gitlab/ci/ directory
            gitlab_ci_dir = root / ".gitlab" / "ci"
            if gitlab_ci_dir.is_dir():
                related_files.append(".gitlab/ci/")

            # Check for additional CI yml files at root
            for candidate in sorted(root.iterdir()):
                if not candidate.is_file():
                    continue
                name = candidate.name
                if (
                    name != ".gitlab-ci.yml"
                    and name.startswith(".gitlab-ci")
                    and name.endswith((".yml", ".yaml"))
                ):
                    related_files.append(name)

            # Check for .ci-local/ directory
            ci_local_dir = root / ".ci-local"
            if ci_local_dir.is_dir():
                related_files.append(".ci-local/")

        except OSError as exc:
            warnings.append(f"GitLab CI enrichment error (related files): {exc}")

        if related_files:
            entry["related_files"] = related_files

        # Extract stage names from the main .gitlab-ci.yml
        try:
            ci_file = root / ".gitlab-ci.yml"
            if ci_file.is_file():
                content = ci_file.read_text(encoding="utf-8", errors="replace")
                stages = self._extract_yaml_stages(content)
                if stages:
                    entry["stages"] = stages
        except OSError as exc:
            warnings.append(f"GitLab CI enrichment error (stages): {exc}")

    @staticmethod
    def _extract_yaml_stages(content: str) -> List[str]:
        """Extract stage names from a YAML string using simple line parsing.

        Looks for a top-level ``stages:`` key followed by ``- name`` lines.
        Handles the common format without requiring a YAML library.
        """
        stages: List[str] = []
        in_stages = False

        for line in content.splitlines():
            stripped = line.strip()

            # Detect the start of the stages block (must be top-level, no leading spaces)
            if line.startswith("stages:") and not line[0].isspace():
                in_stages = True
                continue

            if in_stages:
                # A list item under stages
                if stripped.startswith("- "):
                    stage_name = stripped[2:].strip().strip("'\"")
                    if stage_name:
                        stages.append(stage_name)
                elif stripped == "" or stripped.startswith("#"):
                    # Blank lines and comments are OK inside the block
                    continue
                else:
                    # Any other non-list content ends the stages block
                    break

        return stages

    # ------------------------------------------------------------------
    # Infrastructure path detection
    # ------------------------------------------------------------------

    def _detect_paths(
        self, root: Path, warnings: List[str]
    ) -> Dict[str, Optional[str]]:
        """Detect infrastructure-related directories.

        Searches depth=1 and depth=2 to handle monorepo structures where
        infra directories live inside a workspace subdirectory (e.g.,
        ``qxo-monorepo/terraform/``).
        """
        detected: Dict[str, Optional[str]] = {
            "gitops": None,
            "terraform": None,
            "app_services": None,
        }

        skip = {"node_modules", ".git", "__pycache__", ".terraform", "vendor",
                "dist", "build", ".venv", "venv"}

        try:
            # Depth=1: immediate subdirectories of root
            subdirs = [
                d for d in root.iterdir()
                if d.is_dir() and not d.name.startswith(".") and d.name not in skip
            ]
        except OSError as exc:
            warnings.append(f"Path detection error: {exc}")
            return detected

        for subdir in subdirs:
            dir_name = subdir.name.lower()
            for path_key, candidates in _INFRA_DIR_NAMES.items():
                if detected[path_key] is None and dir_name in candidates:
                    detected[path_key] = str(subdir.relative_to(root))

        # Depth=2: check inside each depth-1 subdirectory for infra dirs
        # This handles monorepo layouts like qxo-monorepo/terraform/
        for subdir in subdirs:
            try:
                for child in subdir.iterdir():
                    if not child.is_dir() or child.name.startswith("."):
                        continue
                    if child.name in skip:
                        continue
                    child_name = child.name.lower()
                    for path_key, candidates in _INFRA_DIR_NAMES.items():
                        if detected[path_key] is None and child_name in candidates:
                            detected[path_key] = str(child.relative_to(root))
            except OSError:
                continue

        return detected

    # ------------------------------------------------------------------
    # Application service detection
    # ------------------------------------------------------------------

    def _detect_application_services(
        self, root: Path, warnings: List[str]
    ) -> List[Dict[str, Any]]:
        """Detect microservices from directory conventions.

        Scans for directories containing ``service-runtime/`` subdirs or
        ``Dockerfile`` files, following common monorepo patterns like
        ``features/*-feature/*-service/service-runtime/``.

        Returns a list of service descriptors (scanner-owned fields only).
        """
        services: List[Dict[str, Any]] = []
        seen_names: Set[str] = set()

        skip = {"node_modules", ".git", "__pycache__", ".terraform",
                ".terragrunt-cache", "vendor", "dist", "build",
                ".venv", "venv", "charts", "infra"}

        # Search up to depth=4 for service-runtime dirs and Dockerfiles
        # that indicate a service boundary
        self._find_services_recursive(
            root, root, services, seen_names, skip, warnings, depth=0, max_depth=4
        )

        return services

    def _find_services_recursive(
        self,
        root: Path,
        current: Path,
        services: List[Dict[str, Any]],
        seen_names: Set[str],
        skip: Set[str],
        warnings: List[str],
        depth: int,
        max_depth: int,
    ) -> None:
        """Recursively find service directories."""
        if depth >= max_depth:
            return

        try:
            entries = sorted(current.iterdir())
        except OSError:
            return

        for entry in entries:
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if entry.name in skip:
                continue

            # Check if this directory looks like a service
            has_service_runtime = (entry / "service-runtime").is_dir()
            has_dockerfile = (entry / "Dockerfile").is_file()
            has_docker_compose = (
                (entry / "docker-compose.yml").is_file()
                or (entry / "docker-compose.yaml").is_file()
            )

            if has_service_runtime or has_dockerfile:
                service_name = entry.name
                if service_name not in seen_names:
                    seen_names.add(service_name)
                    services.append({
                        "name": service_name,
                        "path": str(entry.relative_to(root)),
                        "has_dockerfile": has_dockerfile,
                        "has_docker_compose": has_docker_compose,
                        "has_service_runtime": has_service_runtime,
                    })

            # Continue recursing into subdirectories
            self._find_services_recursive(
                root, entry, services, seen_names, skip, warnings,
                depth + 1, max_depth,
            )

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
