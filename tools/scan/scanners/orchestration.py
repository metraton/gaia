"""
Orchestration Scanner

Detects Kubernetes, GitOps, Helm, Kustomize, and service mesh indicators
from project filesystem. Only produces output when orchestration tooling
is detected -- returns empty dict for projects with no orchestration files.
"""

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.scan.scanners.base import BaseScanner, ScanResult
from tools.scan.walk import walk_project, walk_project_named

logger = logging.getLogger(__name__)

_K8S_KINDS = frozenset({
    "Deployment",
    "Service",
    "StatefulSet",
    "DaemonSet",
    "Job",
    "CronJob",
    "Ingress",
    "ConfigMap",
    "Secret",
    "HelmRelease",
    "Kustomization",
    "Pod",
    "ReplicaSet",
    "Namespace",
    "ServiceAccount",
    "ClusterRole",
    "ClusterRoleBinding",
    "Role",
    "RoleBinding",
    "PersistentVolumeClaim",
    "PersistentVolume",
    "NetworkPolicy",
})

_FLUX_API_GROUPS = frozenset({
    "toolkit.fluxcd.io",
    "source.toolkit.fluxcd.io",
    "kustomize.toolkit.fluxcd.io",
    "helm.toolkit.fluxcd.io",
    "notification.toolkit.fluxcd.io",
    "image.toolkit.fluxcd.io",
})

_ARGOCD_API_GROUPS = frozenset({
    "argoproj.io",
})

_FLUX_DIR_CONVENTIONS = frozenset({
    "clusters",
    "infrastructure",
    "apps",
})

_ISTIO_INDICATORS = frozenset({
    "sidecar.istio.io",
    "istio.io",
    "networking.istio.io",
    "security.istio.io",
})

_LINKERD_INDICATORS = frozenset({
    "linkerd.io",
    "viz.linkerd.io",
    "config.linkerd.io",
})

_CONSUL_INDICATORS = frozenset({
    "consul.hashicorp.com",
    "connect-inject",
})

# Maximum number of YAML files to scan to stay within performance budget
_MAX_YAML_FILES = 500

_MAX_YAML_SIZE = 256 * 1024


class OrchestrationScanner(BaseScanner):
    """Scanner for Kubernetes, GitOps, Helm, Kustomize, and service mesh.

    Only creates the 'orchestration' section when indicators are found.
    Returns empty dict for projects with no orchestration tooling.

    Pure Function Contract:
    - No file writes
    - No state modification beyond the per-instance YAML file list cache
    - No network calls
    - No command execution
    - Only filesystem reads
    """

    @property
    def SCANNER_NAME(self) -> str:
        return "orchestration"

    @property
    def SCANNER_VERSION(self) -> str:
        return "1.0.0"

    @property
    def OWNED_SECTIONS(self) -> List[str]:
        return ["orchestration"]

    def scan(self, root: Path) -> ScanResult:
        """Scan the project for orchestration indicators.

        Args:
            root: Absolute path to the project root directory.

        Returns:
            ScanResult with 'orchestration' section if indicators found,
            or ScanResult with empty sections if nothing detected.
        """
        start_ms = time.monotonic() * 1000
        warnings: List[str] = []

        try:
            kubernetes = self._detect_kubernetes(root, warnings)
            gitops = self._detect_gitops(root, warnings)
            helm = self._detect_helm(root, warnings)
            kustomize = self._detect_kustomize(root, warnings)
            service_mesh = self._detect_service_mesh(root, warnings)

            has_indicators = (
                kubernetes["detected"]
                or gitops["tool"] is not None
                or helm["detected"]
                or kustomize["detected"]
                or service_mesh["tool"] is not None
            )

            if not has_indicators:
                elapsed = (time.monotonic() * 1000) - start_ms
                return self.make_result(
                    sections={},
                    warnings=warnings,
                    duration_ms=elapsed,
                )

            orchestration_data: Dict[str, Any] = {
                "kubernetes": kubernetes,
                "gitops": gitops,
                "helm": helm,
                "kustomize": kustomize,
                "service_mesh": service_mesh,
            }

            elapsed = (time.monotonic() * 1000) - start_ms
            return self.make_result(
                sections={"orchestration": orchestration_data},
                warnings=warnings,
                duration_ms=elapsed,
            )

        except Exception as exc:
            elapsed = (time.monotonic() * 1000) - start_ms
            logger.warning("Orchestration scanner failed: %s", exc)
            return self.make_result(
                sections={},
                warnings=[f"Orchestration scanner error: {exc}"],
                duration_ms=elapsed,
            )

    def _detect_kubernetes(
        self, root: Path, warnings: List[str]
    ) -> Dict[str, Any]:
        """Detect Kubernetes indicators from manifests, kubeconfig, etc."""
        indicators: List[str] = []
        manifest_patterns: List[str] = []

        yaml_kinds = self._scan_yaml_for_kinds(root, warnings)
        if yaml_kinds:
            indicators.append("kubernetes manifests found")
            manifest_patterns.extend(sorted(yaml_kinds))

        kubeconfig_path = self._find_kubeconfig()
        if kubeconfig_path:
            indicators.append(f"kubeconfig: {kubeconfig_path}")

        return {
            "detected": len(indicators) > 0,
            "indicators": indicators,
            "manifest_patterns": manifest_patterns,
        }

    def _scan_yaml_for_kinds(
        self, root: Path, warnings: List[str]
    ) -> List[str]:
        """Scan YAML files for Kubernetes resource kinds."""
        kinds_found: set = set()
        yaml_files = self._find_yaml_files(root)

        for yaml_path in yaml_files:
            try:
                content = self._safe_read(yaml_path)
                if content is None:
                    continue

                for line in content.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("kind:"):
                        kind_value = stripped[5:].strip().strip('"').strip("'")
                        if kind_value in _K8S_KINDS:
                            kinds_found.add(kind_value)
            except Exception:
                # Individual file read failures must not abort the scanner
                continue

        return sorted(kinds_found)

    def _find_kubeconfig(self) -> Optional[str]:
        """Check for kubeconfig presence via env var or default path."""
        kubeconfig_env = os.environ.get("KUBECONFIG")
        if kubeconfig_env:
            for kc_path in kubeconfig_env.split(os.pathsep):
                if Path(kc_path).is_file():
                    return kc_path

        default_kc = Path.home() / ".kube" / "config"
        if default_kc.is_file():
            return str(default_kc)

        return None

    def _detect_gitops(
        self, root: Path, warnings: List[str]
    ) -> Dict[str, Any]:
        """Detect Flux, ArgoCD, or other GitOps tooling."""
        api_groups: List[str] = []
        tool: Optional[str] = None
        config_path: Optional[str] = None

        flux_groups, argocd_groups = self._scan_yaml_for_api_groups(
            root, warnings
        )

        if flux_groups:
            tool = "flux"
            api_groups.extend(sorted(flux_groups))
            config_path = self._find_flux_config_path(root)
        elif argocd_groups:
            tool = "argocd"
            api_groups.extend(sorted(argocd_groups))
            config_path = self._find_argocd_config_path(root)

        if tool is None:
            flux_dir = self._check_flux_directory_conventions(root)
            if flux_dir:
                tool = "flux"
                config_path = flux_dir

        if tool is not None and config_path is None:
            config_path = self._derive_config_path_from_kustomize(root)

        return {
            "tool": tool,
            "api_groups": api_groups,
            "config_path": config_path,
        }

    def _scan_yaml_for_api_groups(
        self, root: Path, warnings: List[str]
    ) -> tuple:
        """Scan YAML files for Flux and ArgoCD API groups.

        A group counts when its name appears as a substring of any line.

        Returns:
            Tuple of (flux_groups, argocd_groups) sets.
        """
        flux_groups: set = set()
        argocd_groups: set = set()

        yaml_files = self._find_yaml_files(root)

        for yaml_path in yaml_files:
            try:
                content = self._safe_read(yaml_path)
                if content is None:
                    continue

                for line in content.splitlines():
                    stripped = line.strip()

                    for fg in _FLUX_API_GROUPS:
                        if fg in stripped:
                            flux_groups.add(fg)

                    for ag in _ARGOCD_API_GROUPS:
                        if ag in stripped:
                            argocd_groups.add(ag)
            except Exception:
                continue

        return flux_groups, argocd_groups

    def _find_flux_config_path(self, root: Path) -> Optional[str]:
        """Find the Flux configuration root directory.

        Checks root-level directories first, then searches subdirectories
        for gitops-related directory names.
        """
        candidates = ["clusters", "flux-system", "gitops"]

        for candidate in candidates:
            candidate_path = root / candidate
            if candidate_path.is_dir():
                return candidate

        try:
            for entry in sorted(root.iterdir()):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                if entry.name in ("node_modules", "vendor", "__pycache__"):
                    continue
                for candidate in candidates:
                    nested = entry / candidate
                    if nested.is_dir():
                        return str(nested.relative_to(root))
        except OSError:
            pass

        return None

    def _find_argocd_config_path(self, root: Path) -> Optional[str]:
        """Find the ArgoCD configuration root directory."""
        candidates = ["argocd", "argo-cd", "applications"]
        for candidate in candidates:
            candidate_path = root / candidate
            if candidate_path.is_dir():
                return candidate
        return None

    def _check_flux_directory_conventions(
        self, root: Path
    ) -> Optional[str]:
        """Check for Flux directory conventions (clusters/, infrastructure/, apps/)."""
        matched_dirs = []
        for dirname in _FLUX_DIR_CONVENTIONS:
            if (root / dirname).is_dir():
                matched_dirs.append(dirname)

        if len(matched_dirs) >= 2:
            return "clusters" if (root / "clusters").is_dir() else matched_dirs[0]

        return None

    def _derive_config_path_from_kustomize(self, root: Path) -> Optional[str]:
        """Derive gitops config_path from detected kustomize file locations.

        Returns the first directory named gitops, flux, argocd, deploy or k8s
        found on the path of a kustomization file, in walk order; None when
        there is none.
        """
        gitops_dirs: List[str] = []

        try:
            for kust_file in walk_project_named(
                root, ["kustomization.yaml", "kustomization.yml", "Kustomization"]
            ):
                rel = kust_file.relative_to(root)
                parts = rel.parent.parts
                for i, part in enumerate(parts):
                    if part.lower() in ("gitops", "flux", "argocd", "deploy", "k8s"):
                        gitops_path = str(Path(*parts[: i + 1]))
                        if gitops_path not in gitops_dirs:
                            gitops_dirs.append(gitops_path)
                        break
        except Exception:
            pass

        if gitops_dirs:
            return gitops_dirs[0]
        return None

    def _detect_helm(
        self, root: Path, warnings: List[str]
    ) -> Dict[str, Any]:
        """Detect Helm charts from Chart.yaml files."""
        charts: List[str] = []

        try:
            for chart_yaml in walk_project_named(root, ["Chart.yaml"]):
                rel_path = str(chart_yaml.relative_to(root))
                charts.append(rel_path)
        except Exception:
            warnings.append("Failed to scan for Helm Chart.yaml files")

        return {
            "detected": len(charts) > 0,
            "charts": sorted(charts),
        }

    def _detect_kustomize(
        self, root: Path, warnings: List[str]
    ) -> Dict[str, Any]:
        """Detect Kustomize from kustomization.yaml files."""
        files: List[str] = []

        try:
            for kust_file in walk_project_named(root, ["kustomization.yaml", "kustomization.yml", "Kustomization"]):
                rel_path = str(kust_file.relative_to(root))
                if rel_path not in files:
                    files.append(rel_path)
        except Exception:
            warnings.append("Failed to scan for kustomization.yaml files")

        return {
            "detected": len(files) > 0,
            "files": sorted(files),
        }

    def _detect_service_mesh(
        self, root: Path, warnings: List[str]
    ) -> Dict[str, Any]:
        """Detect Istio, Linkerd, or Consul Connect from marker strings in YAML lines.

        When several meshes are found the priority is istio, linkerd, consul.
        """
        tool: Optional[str] = None
        indicators: List[str] = []

        istio_found = False
        linkerd_found = False
        consul_found = False

        yaml_files = self._find_yaml_files(root)

        for yaml_path in yaml_files:
            try:
                content = self._safe_read(yaml_path)
                if content is None:
                    continue

                for line in content.splitlines():
                    stripped = line.strip()

                    for prefix in _ISTIO_INDICATORS:
                        if prefix in stripped:
                            istio_found = True
                            indicator = f"istio: {prefix}"
                            if indicator not in indicators:
                                indicators.append(indicator)

                    for prefix in _LINKERD_INDICATORS:
                        if prefix in stripped:
                            linkerd_found = True
                            indicator = f"linkerd: {prefix}"
                            if indicator not in indicators:
                                indicators.append(indicator)

                    for prefix in _CONSUL_INDICATORS:
                        if prefix in stripped:
                            consul_found = True
                            indicator = f"consul: {prefix}"
                            if indicator not in indicators:
                                indicators.append(indicator)
            except Exception:
                continue

        if istio_found:
            tool = "istio"
        elif linkerd_found:
            tool = "linkerd"
        elif consul_found:
            tool = "consul"

        return {
            "tool": tool,
            "indicators": sorted(indicators),
        }

    def _find_yaml_files(self, root: Path) -> List[Path]:
        """Find YAML files in the project, respecting scan limits.

        Uses walk_project for filtered os.walk (skips node_modules, .git, etc.)
        instead of rglob which traverses all directories.

        Caches the list on the instance, keyed by ``root``, so the detection
        methods share one walk.
        """
        cache_attr = "_yaml_files_cache"
        cache_root_attr = "_yaml_files_root"

        cached_root = getattr(self, cache_root_attr, None)
        if cached_root == root:
            cached = getattr(self, cache_attr, None)
            if cached is not None:
                return cached

        yaml_files: List[Path] = []
        count = 0

        try:
            for p in walk_project(root, [".yaml", ".yml"]):
                yaml_files.append(p)
                count += 1
                if count >= _MAX_YAML_FILES:
                    break
        except Exception:
            pass

        object.__setattr__(self, cache_attr, yaml_files)
        object.__setattr__(self, cache_root_attr, root)

        return yaml_files

    def _safe_read(self, path: Path) -> Optional[str]:
        """Read a file safely, returning None on failure or if too large."""
        try:
            if not path.is_file():
                return None
            if path.stat().st_size > _MAX_YAML_SIZE:
                return None
            return path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            return None

    @staticmethod
    def _should_skip_path(path: Path) -> bool:
        """Check if a path should be skipped (hidden dirs, vendor, node_modules)."""
        parts = path.parts
        for part in parts:
            if part.startswith(".") and part not in (".", ".."):
                return True
            if part in ("node_modules", "vendor", "__pycache__", ".git"):
                return True
        return False


def scan(root: Path) -> Dict[str, Any]:
    """Convenience function: instantiate OrchestrationScanner and run scan.

    Args:
        root: Absolute path to the project root directory.

    Returns:
        Dict mapping section names to section data (from ScanResult.sections).
    """
    scanner = OrchestrationScanner()
    result = scanner.scan(root)
    return result.sections
