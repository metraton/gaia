"""
AC-5 regression tests: content-scanners ignore fixture/sample directories.

Verifies that _scan_workloads, _scan_releases, _scan_tf_modules,
_scan_clusters_defined, and _scan_features all honour the expanded skip-set
that includes ``tests``, ``fixtures``, ``templates``, and ``examples``.

Symptom this prevents: YAML workload/release manifests living under fixture or
template directories inside the gaia repo (e.g. ``fixtures/bildwiz-api/``,
``examples/qxo-app/``) must NOT be stored as live workloads or services in
the workspace DB.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# _scan_workloads
# ---------------------------------------------------------------------------

class TestScanWorkloadsSkipsDirs:
    """_scan_workloads must not return entries from fixture/template dirs."""

    _DEPLOYMENT_YAML = (
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n"
        "  name: {name}\n"
        "spec: {{}}\n"
    )

    def _write_deployment(self, path: Path, name: str) -> None:
        path.mkdir(parents=True, exist_ok=True)
        (path / "deployment.yaml").write_text(self._DEPLOYMENT_YAML.format(name=name))

    def test_fixtures_dir_is_skipped(self, tmp_path):
        from tools.scan.store_populator import _scan_workloads

        self._write_deployment(tmp_path / "fixtures" / "bildwiz-api", "bildwiz-api")
        result = _scan_workloads(tmp_path)
        assert result == [], "fixture deployment must not surface as a live workload"

    def test_templates_dir_is_skipped(self, tmp_path):
        from tools.scan.store_populator import _scan_workloads

        self._write_deployment(tmp_path / "templates" / "qxo-app", "qxo-app")
        result = _scan_workloads(tmp_path)
        assert result == [], "template deployment must not surface as a live workload"

    def test_examples_dir_is_skipped(self, tmp_path):
        from tools.scan.store_populator import _scan_workloads

        self._write_deployment(tmp_path / "examples" / "sample-app", "sample-app")
        result = _scan_workloads(tmp_path)
        assert result == [], "example deployment must not surface as a live workload"

    def test_tests_dir_is_skipped(self, tmp_path):
        from tools.scan.store_populator import _scan_workloads

        self._write_deployment(tmp_path / "tests" / "e2e", "test-deploy")
        result = _scan_workloads(tmp_path)
        assert result == [], "test deployment must not surface as a live workload"

    def test_real_workload_outside_skip_dirs_is_found(self, tmp_path):
        """YAMLs in normal dirs (k8s/, deploy/) must still be detected."""
        from tools.scan.store_populator import _scan_workloads

        k8s = tmp_path / "k8s"
        k8s.mkdir()
        (k8s / "api-deployment.yaml").write_text(
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: api\nspec: {}\n"
        )
        result = _scan_workloads(tmp_path)
        assert any(r["name"] == "api" for r in result), (
            "deployment in k8s/ must be found"
        )

    def test_skip_dir_mixed_with_real_workloads(self, tmp_path):
        """Workloads in real dirs surface; those in fixture dirs do not."""
        from tools.scan.store_populator import _scan_workloads

        # Real workload
        real = tmp_path / "deploy"
        real.mkdir()
        (real / "web.yaml").write_text(
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\nspec: {}\n"
        )
        # Fixture workload -- must be suppressed
        self._write_deployment(tmp_path / "fixtures" / "<app-name>", "<app-name>")

        result = _scan_workloads(tmp_path)
        names = {r["name"] for r in result}
        assert "web" in names
        assert "<app-name>" not in names


# ---------------------------------------------------------------------------
# _scan_releases
# ---------------------------------------------------------------------------

class TestScanReleasesSkipsDirs:
    """_scan_releases must not return entries from fixture/template dirs."""

    _HELM_RELEASE_YAML = (
        "apiVersion: helm.toolkit.fluxcd.io/v2beta1\n"
        "kind: HelmRelease\n"
        "metadata:\n"
        "  name: {name}\n"
        "spec: {{}}\n"
    )

    def _write_release(self, path: Path, name: str) -> None:
        path.mkdir(parents=True, exist_ok=True)
        (path / "release.yaml").write_text(self._HELM_RELEASE_YAML.format(name=name))

    def test_fixtures_dir_is_skipped(self, tmp_path):
        from tools.scan.store_populator import _scan_releases

        self._write_release(tmp_path / "fixtures" / "infra", "infra-release")
        result = _scan_releases(tmp_path)
        assert result == []

    def test_templates_dir_is_skipped(self, tmp_path):
        from tools.scan.store_populator import _scan_releases

        self._write_release(tmp_path / "templates" / "base", "base-release")
        result = _scan_releases(tmp_path)
        assert result == []

    def test_examples_dir_is_skipped(self, tmp_path):
        from tools.scan.store_populator import _scan_releases

        self._write_release(tmp_path / "examples" / "demo", "demo-release")
        result = _scan_releases(tmp_path)
        assert result == []

    def test_tests_dir_is_skipped(self, tmp_path):
        from tools.scan.store_populator import _scan_releases

        self._write_release(tmp_path / "tests" / "fixtures", "test-release")
        result = _scan_releases(tmp_path)
        assert result == []

    def test_real_release_outside_skip_dirs_is_found(self, tmp_path):
        from tools.scan.store_populator import _scan_releases

        flux = tmp_path / "clusters" / "prod"
        flux.mkdir(parents=True)
        (flux / "infra.yaml").write_text(self._HELM_RELEASE_YAML.format(name="infra"))
        result = _scan_releases(tmp_path)
        assert any(r["name"] == "infra" for r in result)


# ---------------------------------------------------------------------------
# _scan_tf_modules
# ---------------------------------------------------------------------------

class TestScanTfModulesSkipsDirs:
    """_scan_tf_modules must not include modules from fixture/example dirs."""

    _MODULE_TF = 'module "example_mod" {{\n  source = "terraform-aws-modules/vpc/aws"\n  version = "5.0.0"\n}}\n'

    def _write_tf(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        (path / "main.tf").write_text(self._MODULE_TF)

    def test_fixtures_dir_is_skipped(self, tmp_path):
        from tools.scan.store_populator import _scan_tf_modules

        self._write_tf(tmp_path / "fixtures" / "vpc")
        result = _scan_tf_modules(tmp_path)
        assert result == []

    def test_examples_dir_is_skipped(self, tmp_path):
        from tools.scan.store_populator import _scan_tf_modules

        self._write_tf(tmp_path / "examples" / "basic")
        result = _scan_tf_modules(tmp_path)
        assert result == []

    def test_templates_dir_is_skipped(self, tmp_path):
        from tools.scan.store_populator import _scan_tf_modules

        self._write_tf(tmp_path / "templates" / "vpc")
        result = _scan_tf_modules(tmp_path)
        assert result == []

    def test_tests_dir_is_skipped(self, tmp_path):
        from tools.scan.store_populator import _scan_tf_modules

        self._write_tf(tmp_path / "tests" / "unit")
        result = _scan_tf_modules(tmp_path)
        assert result == []

    def test_real_module_outside_skip_dirs_is_found(self, tmp_path):
        from tools.scan.store_populator import _scan_tf_modules

        modules_dir = tmp_path / "modules" / "vpc"
        modules_dir.mkdir(parents=True)
        (modules_dir / "main.tf").write_text(self._MODULE_TF)
        result = _scan_tf_modules(tmp_path)
        assert any(r["name"] == "example_mod" for r in result)


# ---------------------------------------------------------------------------
# _scan_clusters_defined
# ---------------------------------------------------------------------------

class TestScanClustersDefinedSkipsDirs:
    """_scan_clusters_defined must not pick up cluster resources from fixture dirs."""

    _CLUSTER_TF = (
        'resource "google_container_cluster" "sample_cluster" {{\n'
        '  name = "sample"\n'
        '}}\n'
    )

    def _write_tf(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        (path / "cluster.tf").write_text(self._CLUSTER_TF)

    def test_fixtures_dir_is_skipped(self, tmp_path):
        from tools.scan.store_populator import _scan_clusters_defined

        self._write_tf(tmp_path / "fixtures" / "gke")
        result = _scan_clusters_defined(tmp_path)
        assert result == []

    def test_examples_dir_is_skipped(self, tmp_path):
        from tools.scan.store_populator import _scan_clusters_defined

        self._write_tf(tmp_path / "examples" / "cluster")
        result = _scan_clusters_defined(tmp_path)
        assert result == []

    def test_templates_dir_is_skipped(self, tmp_path):
        from tools.scan.store_populator import _scan_clusters_defined

        self._write_tf(tmp_path / "templates" / "gke")
        result = _scan_clusters_defined(tmp_path)
        assert result == []

    def test_tests_dir_is_skipped(self, tmp_path):
        from tools.scan.store_populator import _scan_clusters_defined

        self._write_tf(tmp_path / "tests" / "infra")
        result = _scan_clusters_defined(tmp_path)
        assert result == []

    def test_real_cluster_outside_skip_dirs_is_found(self, tmp_path):
        from tools.scan.store_populator import _scan_clusters_defined

        live_dir = tmp_path / "live" / "gke"
        live_dir.mkdir(parents=True)
        (live_dir / "cluster.tf").write_text(self._CLUSTER_TF)
        result = _scan_clusters_defined(tmp_path)
        assert any(r["name"] == "sample_cluster" for r in result)


# ---------------------------------------------------------------------------
# _scan_features
# ---------------------------------------------------------------------------

class TestScanFeaturesSkipsDirs:
    """_scan_features must not emit feature rows from fixture/template dirs."""

    def test_fixtures_dir_feature_descriptor_is_skipped(self, tmp_path):
        from tools.scan.store_populator import _scan_features

        path = tmp_path / "fixtures" / "my-feature"
        path.mkdir(parents=True)
        (path / "feature.json").write_text('{"name": "my-feature"}')
        result = _scan_features(tmp_path)
        assert result == []

    def test_templates_dir_feature_descriptor_is_skipped(self, tmp_path):
        from tools.scan.store_populator import _scan_features

        path = tmp_path / "templates" / "tmpl-feature"
        path.mkdir(parents=True)
        (path / "feature.yaml").write_text("name: tmpl-feature\n")
        result = _scan_features(tmp_path)
        assert result == []

    def test_examples_dir_feature_descriptor_is_skipped(self, tmp_path):
        from tools.scan.store_populator import _scan_features

        path = tmp_path / "examples" / "demo-feature"
        path.mkdir(parents=True)
        (path / "feature.json").write_text('{"name": "demo-feature"}')
        result = _scan_features(tmp_path)
        assert result == []

    def test_tests_dir_feature_descriptor_is_skipped(self, tmp_path):
        from tools.scan.store_populator import _scan_features

        path = tmp_path / "tests" / "unit" / "flag-feature"
        path.mkdir(parents=True)
        (path / "feature.json").write_text('{"name": "flag-feature"}')
        result = _scan_features(tmp_path)
        assert result == []

    def test_real_feature_outside_skip_dirs_is_found(self, tmp_path):
        from tools.scan.store_populator import _scan_features

        path = tmp_path / "src" / "auth-feature"
        path.mkdir(parents=True)
        (path / "feature.json").write_text('{"name": "auth-feature"}')
        result = _scan_features(tmp_path)
        assert any(r["name"] == "auth-feature" for r in result)
