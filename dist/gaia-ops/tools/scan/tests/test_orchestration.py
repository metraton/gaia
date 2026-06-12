"""
Unit tests for the Orchestration Scanner (T023).

Tests K8s manifest detection, Helm chart detection, Kustomize detection,
Flux/ArgoCD detection, service mesh detection, and empty project behavior.
"""

import os
import textwrap
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest

from tools.scan.scanners.orchestration import OrchestrationScanner


@pytest.fixture
def scanner() -> OrchestrationScanner:
    """Create an OrchestrationScanner instance."""
    return OrchestrationScanner()


# ---------------------------------------------------------------------------
# Scanner basics
# ---------------------------------------------------------------------------


class TestOrchScannerBasics:
    """Test scanner metadata and basic contract."""

    def test_scanner_name(self, scanner: OrchestrationScanner) -> None:
        assert scanner.SCANNER_NAME == "orchestration"

    def test_scanner_version(self, scanner: OrchestrationScanner) -> None:
        assert scanner.SCANNER_VERSION == "1.0.0"

    def test_owned_sections(self, scanner: OrchestrationScanner) -> None:
        assert scanner.OWNED_SECTIONS == ["orchestration"]

    def test_source_tag(self, scanner: OrchestrationScanner) -> None:
        assert scanner.source_tag == "scanner:orchestration"


# ---------------------------------------------------------------------------
# Empty project
# ---------------------------------------------------------------------------


class TestEmptyProject:
    """Test empty project returns empty dict.

    Must mock kubeconfig detection to prevent the host system's
    ~/.kube/config from being picked up.
    """

    def test_empty_project_returns_empty_sections(
        self, scanner: OrchestrationScanner, empty_project: Path
    ) -> None:
        with patch.dict(os.environ, {"KUBECONFIG": ""}, clear=False):
            with patch(
                "tools.scan.scanners.orchestration.Path.home",
                return_value=empty_project / "_fake_home",
            ):
                result = scanner.scan(empty_project)
        assert result.sections == {}

    def test_no_orchestration_in_empty(
        self, scanner: OrchestrationScanner, empty_project: Path
    ) -> None:
        with patch.dict(os.environ, {"KUBECONFIG": ""}, clear=False):
            with patch(
                "tools.scan.scanners.orchestration.Path.home",
                return_value=empty_project / "_fake_home",
            ):
                result = scanner.scan(empty_project)
        assert "orchestration" not in result.sections


# ---------------------------------------------------------------------------
# Kubernetes detection
# ---------------------------------------------------------------------------


class TestKubernetesDetection:
    """Test Kubernetes manifest detection."""

    def test_detect_deployment_kind(
        self, scanner: OrchestrationScanner, k8s_project: Path
    ) -> None:
        result = scanner.scan(k8s_project)
        orch = result.sections["orchestration"]
        assert orch["kubernetes"]["detected"] is True
        assert "Deployment" in orch["kubernetes"]["manifest_patterns"]

    def test_detect_service_kind(
        self, scanner: OrchestrationScanner, k8s_project: Path
    ) -> None:
        result = scanner.scan(k8s_project)
        orch = result.sections["orchestration"]
        assert "Service" in orch["kubernetes"]["manifest_patterns"]

    def test_detect_statefulset(
        self, scanner: OrchestrationScanner, tmp_path: Path
    ) -> None:
        manifests = tmp_path / "k8s"
        manifests.mkdir()
        (manifests / "statefulset.yaml").write_text(
            "apiVersion: apps/v1\nkind: StatefulSet\nmetadata:\n  name: db\n"
        )
        result = scanner.scan(tmp_path)
        orch = result.sections["orchestration"]
        assert orch["kubernetes"]["detected"] is True
        assert "StatefulSet" in orch["kubernetes"]["manifest_patterns"]


# ---------------------------------------------------------------------------
# Helm detection
# ---------------------------------------------------------------------------


class TestHelmDetection:
    """Test Helm chart detection."""

    def test_detect_helm_chart(
        self, scanner: OrchestrationScanner, helm_project: Path
    ) -> None:
        result = scanner.scan(helm_project)
        orch = result.sections["orchestration"]
        assert orch["helm"]["detected"] is True
        assert len(orch["helm"]["charts"]) >= 1

    def test_helm_chart_path_recorded(
        self, scanner: OrchestrationScanner, helm_project: Path
    ) -> None:
        result = scanner.scan(helm_project)
        orch = result.sections["orchestration"]
        charts = orch["helm"]["charts"]
        assert any("Chart.yaml" in c for c in charts)


# ---------------------------------------------------------------------------
# Kustomize detection
# ---------------------------------------------------------------------------


class TestKustomizeDetection:
    """Test Kustomize detection."""

    def test_detect_kustomize(
        self, scanner: OrchestrationScanner, tmp_path: Path
    ) -> None:
        k8s_dir = tmp_path / "base"
        k8s_dir.mkdir()
        (k8s_dir / "kustomization.yaml").write_text(
            "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\n"
        )
        result = scanner.scan(tmp_path)
        orch = result.sections["orchestration"]
        assert orch["kustomize"]["detected"] is True

    def test_kustomize_files_recorded(
        self, scanner: OrchestrationScanner, tmp_path: Path
    ) -> None:
        k8s_dir = tmp_path / "overlays" / "dev"
        k8s_dir.mkdir(parents=True)
        (k8s_dir / "kustomization.yaml").write_text("resources:\n  - ../../base\n")
        result = scanner.scan(tmp_path)
        orch = result.sections["orchestration"]
        assert len(orch["kustomize"]["files"]) >= 1


# ---------------------------------------------------------------------------
# Flux detection
# ---------------------------------------------------------------------------


class TestFluxDetection:
    """Test Flux GitOps detection."""

    def test_detect_flux_from_api_group(
        self, scanner: OrchestrationScanner, flux_project: Path
    ) -> None:
        result = scanner.scan(flux_project)
        orch = result.sections["orchestration"]
        assert orch["gitops"]["tool"] == "flux"

    def test_flux_api_groups_recorded(
        self, scanner: OrchestrationScanner, flux_project: Path
    ) -> None:
        result = scanner.scan(flux_project)
        orch = result.sections["orchestration"]
        assert len(orch["gitops"]["api_groups"]) >= 1
        assert any("toolkit.fluxcd.io" in g for g in orch["gitops"]["api_groups"])

    def test_detect_flux_from_directory_conventions(
        self, scanner: OrchestrationScanner, tmp_path: Path
    ) -> None:
        # Create 2 of 3 Flux convention dirs (clusters + infrastructure)
        (tmp_path / "clusters").mkdir()
        (tmp_path / "infrastructure").mkdir()
        result = scanner.scan(tmp_path)
        orch = result.sections["orchestration"]
        assert orch["gitops"]["tool"] == "flux"


# ---------------------------------------------------------------------------
# ArgoCD detection
# ---------------------------------------------------------------------------


class TestArgoCDDetection:
    """Test ArgoCD detection."""

    def test_detect_argocd_from_api_group(
        self, scanner: OrchestrationScanner, argocd_project: Path
    ) -> None:
        result = scanner.scan(argocd_project)
        orch = result.sections["orchestration"]
        assert orch["gitops"]["tool"] == "argocd"

    def test_argocd_api_groups_recorded(
        self, scanner: OrchestrationScanner, argocd_project: Path
    ) -> None:
        result = scanner.scan(argocd_project)
        orch = result.sections["orchestration"]
        assert any("argoproj.io" in g for g in orch["gitops"]["api_groups"])


# ---------------------------------------------------------------------------
# Service mesh detection
# ---------------------------------------------------------------------------


class TestServiceMeshDetection:
    """Test service mesh detection."""

    def test_detect_istio(
        self, scanner: OrchestrationScanner, istio_project: Path
    ) -> None:
        result = scanner.scan(istio_project)
        orch = result.sections["orchestration"]
        assert orch["service_mesh"]["tool"] == "istio"

    def test_istio_indicators_recorded(
        self, scanner: OrchestrationScanner, istio_project: Path
    ) -> None:
        result = scanner.scan(istio_project)
        orch = result.sections["orchestration"]
        assert len(orch["service_mesh"]["indicators"]) >= 1

    def test_detect_linkerd(
        self, scanner: OrchestrationScanner, linkerd_project: Path
    ) -> None:
        result = scanner.scan(linkerd_project)
        orch = result.sections["orchestration"]
        assert orch["service_mesh"]["tool"] == "linkerd"

    def test_detect_consul(
        self, scanner: OrchestrationScanner, tmp_path: Path
    ) -> None:
        manifests = tmp_path / "k8s"
        manifests.mkdir()
        (manifests / "deployment.yaml").write_text(
            textwrap.dedent("""\
                apiVersion: apps/v1
                kind: Deployment
                metadata:
                  name: test
                  annotations:
                    consul.hashicorp.com/connect-inject: "true"
            """)
        )
        result = scanner.scan(tmp_path)
        orch = result.sections["orchestration"]
        assert orch["service_mesh"]["tool"] == "consul"


# ---------------------------------------------------------------------------
# ScanResult contract
# ---------------------------------------------------------------------------


class TestOrchResultContract:
    """Test scan result follows expected contract."""

    def test_source_tag_present(
        self, scanner: OrchestrationScanner, k8s_project: Path
    ) -> None:
        result = scanner.scan(k8s_project)
        assert result.sections["orchestration"]["_source"] == "scanner:orchestration"

    def test_result_has_duration(
        self, scanner: OrchestrationScanner, k8s_project: Path
    ) -> None:
        result = scanner.scan(k8s_project)
        assert result.duration_ms >= 0

    def test_result_scanner_name(
        self, scanner: OrchestrationScanner, k8s_project: Path
    ) -> None:
        result = scanner.scan(k8s_project)
        assert result.scanner == "orchestration"
