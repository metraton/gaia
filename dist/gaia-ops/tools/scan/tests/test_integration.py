"""
Integration tests for the full scan pipeline (M6: T034-T038).

Tests the ScanOrchestrator end-to-end with realistic project fixtures,
verifying all 6 scanners produce correct v2 sections and agent-enriched
data is preserved. Backward-compat sections are no longer produced.

Tasks:
  T034: Full scan on mock DevOps project
  T035: Scan on minimal project
  T036: Idempotency test
  T037: Scanner failure isolation
"""

import copy
import json
import textwrap
import time
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest

from tools.scan.config import ScanConfig
from tools.scan.merge import AGENT_ENRICHED_SECTIONS
from tools.scan.orchestrator import ScanOrchestrator, ScanOutput
from tools.scan.registry import ScannerRegistry
from tools.scan.scanners.base import BaseScanner, ScanResult
from tools.scan.tests.conftest import create_git_dir


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def full_devops_project(tmp_path: Path) -> Path:
    """Create a realistic multi-language DevOps project fixture.

    Includes:
    - package.json (Node.js with NestJS deps)
    - pyproject.toml (Python)
    - .tf files with GCP provider
    - Dockerfile, docker-compose.yml
    - .github/workflows/ directory
    - .git/ directory with GitHub remote
    - K8s manifests
    - Helm Chart.yaml
    - Flux kustomization
    """
    # -- Node.js / NestJS application --
    pkg = {
        "name": "devops-integration-app",
        "version": "2.0.0",
        "description": "Integration test DevOps application",
        "dependencies": {
            "express": "^4.18.0",
            "@nestjs/core": "^10.0.0",
            "@nestjs/common": "^10.0.0",
        },
        "devDependencies": {
            "typescript": "^5.0.0",
        },
    }
    (tmp_path / "package.json").write_text(json.dumps(pkg, indent=2))
    (tmp_path / "package-lock.json").write_text("{}")
    (tmp_path / "tsconfig.json").write_text('{"compilerOptions": {"strict": true}}')

    # -- Python project --
    pyproject = textwrap.dedent("""\
        [project]
        name = "devops-backend"
        version = "1.0.0"
        description = "Backend service"
        dependencies = [
            "fastapi>=0.100.0",
        ]
    """)
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    (backend_dir / "pyproject.toml").write_text(pyproject)
    (backend_dir / "requirements.txt").write_text("fastapi>=0.100.0\nuvicorn>=0.23.0\n")

    # -- Terraform with GCP provider --
    tf_dir = tmp_path / "terraform"
    tf_dir.mkdir()
    (tf_dir / "main.tf").write_text(textwrap.dedent("""\
        provider "google" {
          project = "my-gcp-project"
          region  = "us-central1"
        }

        resource "google_compute_instance" "default" {
          name         = "test"
          machine_type = "e2-medium"
        }
    """))
    (tf_dir / "variables.tf").write_text(textwrap.dedent("""\
        variable "project_id" {
          type = string
        }
    """))

    # -- Container files --
    (tmp_path / "Dockerfile").write_text("FROM node:20-alpine\nWORKDIR /app\nCOPY . .\n")
    (tmp_path / "docker-compose.yml").write_text(textwrap.dedent("""\
        version: "3.8"
        services:
          app:
            build: .
            ports:
              - "3000:3000"
    """))

    # -- GitHub Actions CI --
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "ci.yml").write_text(textwrap.dedent("""\
        name: CI
        on: [push]
        jobs:
          test:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@v4
    """))

    # -- Git directory with GitHub remote --
    create_git_dir(
        root=tmp_path,
        remote_url="git@github.com:example/devops-integration-app.git",
        default_branch="main",
        extra_remotes={
            "upstream": "https://github.com/upstream/devops-integration-app.git",
        },
        branches=["develop", "feature/scan-integration"],
    )

    # -- Kubernetes manifests --
    k8s_dir = tmp_path / "k8s"
    k8s_dir.mkdir()
    (k8s_dir / "deployment.yaml").write_text(textwrap.dedent("""\
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: devops-app
        spec:
          replicas: 3
    """))
    (k8s_dir / "service.yaml").write_text(textwrap.dedent("""\
        apiVersion: v1
        kind: Service
        metadata:
          name: devops-app
        spec:
          type: ClusterIP
    """))

    # -- Helm chart --
    chart_dir = tmp_path / "charts" / "devops-app"
    chart_dir.mkdir(parents=True)
    (chart_dir / "Chart.yaml").write_text(textwrap.dedent("""\
        apiVersion: v2
        name: devops-app
        version: 1.0.0
        description: DevOps integration test chart
    """))

    # -- Flux GitOps --
    gitops_dir = tmp_path / "gitops" / "clusters" / "dev"
    gitops_dir.mkdir(parents=True)
    (gitops_dir / "kustomization.yaml").write_text(textwrap.dedent("""\
        apiVersion: kustomize.toolkit.fluxcd.io/v1
        kind: Kustomization
        metadata:
          name: dev-cluster
          namespace: flux-system
        spec:
          interval: 5m
          path: ./gitops/clusters/dev
          prune: true
    """))

    # -- Env files --
    (tmp_path / ".env.example").write_text("")

    return tmp_path


@pytest.fixture
def existing_agent_context(tmp_path: Path) -> Dict[str, Any]:
    """Create a pre-existing project-context.json with agent-enriched data.

    Simulates a scenario where agents previously populated cluster_details
    and operational_guidelines.
    """
    return {
        "metadata": {
            "version": "2.0",
            "last_updated": "2026-01-01T00:00:00Z",
            "scan_config": {
                "staleness_hours": 24,
                "last_scan": "2026-01-01T00:00:00Z",
                "scanner_version": "0.1.0",
            },
        },
        "paths": {},
        "sections": {
            "cluster_details": {
                "_source": "agent:cloud-troubleshooter",
                "cluster_name": "prod-us-central1",
                "node_count": 5,
            },
            "operational_guidelines": {
                "_source": "agent:devops-developer",
                "deployment_strategy": "blue-green",
                "rollback_procedure": "manual",
            },
        },
    }


def _make_orchestrator(
    project_root: Path,
    output_path: Path,
    parallel: bool = False,
) -> ScanOrchestrator:
    """Build a ScanOrchestrator with an explicit output path.

    Args:
        project_root: Project root directory.
        output_path: Path for project-context.json.
        parallel: Whether to run scanners in parallel.

    Returns:
        Configured ScanOrchestrator.
    """
    config = ScanConfig(
        project_root=project_root,
        output_path=output_path,
        parallel=parallel,
    )
    registry = ScannerRegistry()
    return ScanOrchestrator(registry=registry, config=config)


# ===========================================================================
# T034: Full scan on mock DevOps project
# ===========================================================================


class TestFullDevOpsScan:
    """Integration test: full scan on a realistic multi-language DevOps project."""

    def test_full_scan_produces_valid_context(
        self, full_devops_project: Path, tmp_path: Path
    ) -> None:
        """Full scan produces a valid project-context.json with all sections."""
        output_path = tmp_path / "output" / "project-context.json"
        orch = _make_orchestrator(full_devops_project, output_path)
        result = orch.run(project_root=full_devops_project)

        # ScanOutput should be returned
        assert isinstance(result, ScanOutput)
        # File should be written
        assert output_path.is_file()

        # Validate JSON structure
        written = json.loads(output_path.read_text())
        assert "metadata" in written
        assert "sections" in written
        assert written["metadata"]["version"] == "2.0"

    def test_project_identity_from_package_json(
        self, full_devops_project: Path, tmp_path: Path
    ) -> None:
        """project_identity section has name from package.json."""
        output_path = tmp_path / "output" / "project-context.json"
        orch = _make_orchestrator(full_devops_project, output_path)
        result = orch.run(project_root=full_devops_project)

        sections = result.context["sections"]
        assert "project_identity" in sections
        identity = sections["project_identity"]
        assert identity["name"] == "devops-integration-app"
        assert "_source" in identity

    def test_stack_detects_languages_and_frameworks(
        self, full_devops_project: Path, tmp_path: Path
    ) -> None:
        """stack section lists TypeScript, Python and detects NestJS framework."""
        output_path = tmp_path / "output" / "project-context.json"
        orch = _make_orchestrator(full_devops_project, output_path)
        result = orch.run(project_root=full_devops_project)

        sections = result.context["sections"]
        assert "stack" in sections
        stack = sections["stack"]

        # Multi-language: TypeScript (package.json + tsconfig) and Python
        lang_names = [l["name"] for l in stack["languages"]]
        assert "typescript" in lang_names
        assert "python" in lang_names

        # NestJS framework detected from @nestjs/core
        fw_names = [fw["name"] for fw in stack["frameworks"]]
        assert "nestjs" in fw_names

    def test_git_detects_github_platform(
        self, full_devops_project: Path, tmp_path: Path
    ) -> None:
        """git section lists GitHub platform from .git/config."""
        output_path = tmp_path / "output" / "project-context.json"
        orch = _make_orchestrator(full_devops_project, output_path)
        result = orch.run(project_root=full_devops_project)

        sections = result.context["sections"]
        assert "git" in sections
        git = sections["git"]
        assert git["platform"] == "github"
        assert git["default_branch"] == "main"
        assert len(git["remotes"]) >= 1

    def test_infrastructure_detects_gcp_terraform_docker_ci(
        self, full_devops_project: Path, tmp_path: Path
    ) -> None:
        """infrastructure section detects GCP, Terraform, Docker, GitHub Actions."""
        output_path = tmp_path / "output" / "project-context.json"
        orch = _make_orchestrator(full_devops_project, output_path)
        result = orch.run(project_root=full_devops_project)

        sections = result.context["sections"]
        assert "infrastructure" in sections
        infra = sections["infrastructure"]

        # Cloud: GCP from terraform provider
        cloud_names = [cp["name"] for cp in infra.get("cloud_providers", [])]
        assert "gcp" in cloud_names

        # IaC: Terraform detected
        iac_tools = [i["tool"] for i in infra.get("iac", [])]
        assert "terraform" in iac_tools

        # CI/CD: GitHub Actions
        ci_platforms = [c["platform"] for c in infra.get("ci_cd", [])]
        assert "github-actions" in ci_platforms

        # Containers: Docker
        container_tools = [c["tool"] for c in infra.get("containers", [])]
        assert "docker" in container_tools

    def test_orchestration_detects_flux_k8s_helm(
        self, full_devops_project: Path, tmp_path: Path
    ) -> None:
        """orchestration section detects Flux, Kubernetes manifests, and Helm."""
        output_path = tmp_path / "output" / "project-context.json"
        orch = _make_orchestrator(full_devops_project, output_path)
        result = orch.run(project_root=full_devops_project)

        sections = result.context["sections"]
        assert "orchestration" in sections
        orch_data = sections["orchestration"]

        # GitOps: Flux detected
        assert orch_data["gitops"]["tool"] == "flux"

        # Kubernetes: manifests found
        assert orch_data["kubernetes"]["detected"] is True

        # Helm: chart detected
        assert orch_data["helm"]["detected"] is True

    def test_environment_has_os_info(
        self, full_devops_project: Path, tmp_path: Path
    ) -> None:
        """environment section has OS information populated."""
        output_path = tmp_path / "output" / "project-context.json"
        orch = _make_orchestrator(full_devops_project, output_path)
        result = orch.run(project_root=full_devops_project)

        sections = result.context["sections"]
        assert "environment" in sections
        env = sections["environment"]
        assert "os" in env
        assert env["os"]["platform"] in ("linux", "darwin", "win32")
        assert env["os"]["architecture"] in ("x64", "arm64")

    def test_v2_sections_present_no_backward_compat(
        self, full_devops_project: Path, tmp_path: Path
    ) -> None:
        """v2 scanner sections present; backward-compat sections NOT produced."""
        output_path = tmp_path / "output" / "project-context.json"
        orch = _make_orchestrator(full_devops_project, output_path)
        result = orch.run(project_root=full_devops_project)

        sections = result.context["sections"]
        # v2 sections must be present
        assert "project_identity" in sections
        assert "stack" in sections
        assert "git" in sections
        assert "environment" in sections
        assert "infrastructure" in sections
        # backward-compat sections must NOT be produced
        assert "project_details" not in sections
        assert "application_architecture" not in sections
        assert "development_standards" not in sections

    def test_agent_enriched_data_preserved(
        self,
        full_devops_project: Path,
        existing_agent_context: Dict[str, Any],
        tmp_path: Path,
    ) -> None:
        """Pre-existing agent-enriched sections (cluster_details) are preserved."""
        output_path = tmp_path / "output" / "project-context.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Write existing context with agent-enriched data
        output_path.write_text(json.dumps(existing_agent_context, indent=2))

        orch = _make_orchestrator(full_devops_project, output_path)
        result = orch.run(project_root=full_devops_project)

        sections = result.context["sections"]
        # Agent-enriched cluster_details should be preserved
        assert "cluster_details" in sections
        assert sections["cluster_details"]["cluster_name"] == "prod-us-central1"
        assert sections["cluster_details"]["node_count"] == 5

        # operational_guidelines should also be preserved
        assert "operational_guidelines" in sections
        assert sections["operational_guidelines"]["deployment_strategy"] == "blue-green"

    def test_scan_completes_under_10_seconds(
        self, full_devops_project: Path, tmp_path: Path
    ) -> None:
        """Full scan completes in under 10 seconds (NFR-001)."""
        output_path = tmp_path / "output" / "project-context.json"
        orch = _make_orchestrator(full_devops_project, output_path)

        start = time.monotonic()
        result = orch.run(project_root=full_devops_project)
        elapsed = time.monotonic() - start

        assert elapsed < 10.0, f"Scan took {elapsed:.2f}s, exceeds 10s NFR-001 limit"

    def test_sections_updated_tracking(
        self, full_devops_project: Path, tmp_path: Path
    ) -> None:
        """ScanOutput tracks which sections were updated."""
        output_path = tmp_path / "output" / "project-context.json"
        orch = _make_orchestrator(full_devops_project, output_path)
        result = orch.run(project_root=full_devops_project)

        assert len(result.sections_updated) > 0
        # Core scanner-produced sections should be in the updated list
        assert "project_identity" in result.sections_updated
        assert "stack" in result.sections_updated
        assert "git" in result.sections_updated

    def test_json_schema_written_correctly(
        self, full_devops_project: Path, tmp_path: Path
    ) -> None:
        """project-context.json has correct top-level schema."""
        output_path = tmp_path / "output" / "project-context.json"
        orch = _make_orchestrator(full_devops_project, output_path)
        orch.run(project_root=full_devops_project)

        written = json.loads(output_path.read_text())
        # Top-level keys
        assert "metadata" in written
        assert "sections" in written
        # Metadata fields
        assert "last_updated" in written["metadata"]
        assert "scan_config" in written["metadata"]
        assert "scanner_version" in written["metadata"]["scan_config"]


# ===========================================================================
# T035: Scan on minimal project
# ===========================================================================


def _patch_host_detections():
    """Return a list of mock patches that suppress host-level detections.

    The infrastructure scanner checks ~/.aws/config, ~/.config/gcloud, etc.
    The orchestration scanner checks ~/.kube/config and KUBECONFIG env var.
    These detect host-level CLI configs that are not project-specific.
    We patch them out so minimal-project tests are host-independent.
    """
    return [
        patch(
            "tools.scan.scanners.infrastructure.InfrastructureScanner"
            "._detect_providers_from_cli_configs",
            lambda self, providers: None,
        ),
        patch(
            "tools.scan.scanners.infrastructure.InfrastructureScanner"
            "._detect_providers_from_env_vars",
            lambda self, providers: None,
        ),
        patch(
            "tools.scan.scanners.orchestration.OrchestrationScanner"
            "._find_kubeconfig",
            lambda self: None,
        ),
    ]


class TestMinimalProjectScan:
    """Integration test: scan on an empty or minimal project."""

    def test_empty_directory_completes_without_error(
        self, tmp_path: Path
    ) -> None:
        """Scan on empty directory completes without errors or crashes."""
        output_path = tmp_path / "output" / "project-context.json"
        orch = _make_orchestrator(tmp_path, output_path)
        result = orch.run(project_root=tmp_path)

        assert isinstance(result, ScanOutput)
        assert len(result.errors) == 0

    def test_empty_directory_has_environment_os(
        self, tmp_path: Path
    ) -> None:
        """Empty directory scan still populates environment.os."""
        output_path = tmp_path / "output" / "project-context.json"
        orch = _make_orchestrator(tmp_path, output_path)
        result = orch.run(project_root=tmp_path)

        sections = result.context["sections"]
        assert "environment" in sections
        assert "os" in sections["environment"]
        assert sections["environment"]["os"]["platform"] in ("linux", "darwin", "win32")

    def test_empty_directory_has_project_identity(
        self, tmp_path: Path
    ) -> None:
        """Empty directory has project_identity with type 'unknown' and dir name."""
        output_path = tmp_path / "output" / "project-context.json"
        orch = _make_orchestrator(tmp_path, output_path)
        result = orch.run(project_root=tmp_path)

        sections = result.context["sections"]
        assert "project_identity" in sections
        identity = sections["project_identity"]
        assert identity["type"] == "unknown"
        # Name should fall back to directory name
        assert identity["name"] == tmp_path.name

    def test_empty_directory_has_stack_with_empty_languages(
        self, tmp_path: Path
    ) -> None:
        """Empty directory has stack section with empty languages list."""
        output_path = tmp_path / "output" / "project-context.json"
        orch = _make_orchestrator(tmp_path, output_path)
        result = orch.run(project_root=tmp_path)

        sections = result.context["sections"]
        assert "stack" in sections
        assert sections["stack"]["languages"] == []

    def test_empty_directory_has_git_with_null_platform(
        self, tmp_path: Path
    ) -> None:
        """Empty directory has git section with platform null."""
        output_path = tmp_path / "output" / "project-context.json"
        orch = _make_orchestrator(tmp_path, output_path)
        result = orch.run(project_root=tmp_path)

        sections = result.context["sections"]
        assert "git" in sections
        assert sections["git"]["platform"] is None

    def test_empty_directory_no_infrastructure(
        self, tmp_path: Path
    ) -> None:
        """Empty directory has no infrastructure section (project-level only).

        Host-level CLI configs (e.g., ~/.aws/config, ~/.config/gcloud) are
        patched out so this test only checks for project-level indicators.
        """
        output_path = tmp_path / "output" / "project-context.json"
        patches = _patch_host_detections()
        for p in patches:
            p.start()
        try:
            orch = _make_orchestrator(tmp_path, output_path)
            result = orch.run(project_root=tmp_path)
        finally:
            for p in patches:
                p.stop()

        sections = result.context["sections"]
        assert "infrastructure" not in sections

    def test_empty_directory_no_orchestration(
        self, tmp_path: Path
    ) -> None:
        """Empty directory has no orchestration section (project-level only).

        Host-level kubeconfig detection is patched out so this test only
        checks for project-level Kubernetes/GitOps indicators.
        """
        output_path = tmp_path / "output" / "project-context.json"
        patches = _patch_host_detections()
        for p in patches:
            p.start()
        try:
            orch = _make_orchestrator(tmp_path, output_path)
            result = orch.run(project_root=tmp_path)
        finally:
            for p in patches:
                p.stop()

        sections = result.context["sections"]
        assert "orchestration" not in sections

    def test_minimal_readme_only_project(
        self, tmp_path: Path
    ) -> None:
        """Minimal project with only README.md produces same base structure.

        Host-level detections are patched out so dynamic sections (infrastructure,
        orchestration) are only produced when project files indicate them.
        """
        (tmp_path / "README.md").write_text("# My Project\n")
        output_path = tmp_path / "output" / "project-context.json"
        patches = _patch_host_detections()
        for p in patches:
            p.start()
        try:
            orch = _make_orchestrator(tmp_path, output_path)
            result = orch.run(project_root=tmp_path)
        finally:
            for p in patches:
                p.stop()

        sections = result.context["sections"]
        # Same base sections as empty directory
        assert "project_identity" in sections
        assert "stack" in sections
        assert "git" in sections
        assert "environment" in sections
        # No dynamic sections
        assert "infrastructure" not in sections
        assert "orchestration" not in sections

    def test_minimal_project_writes_valid_json(
        self, tmp_path: Path
    ) -> None:
        """Minimal project writes valid JSON to disk."""
        (tmp_path / "README.md").write_text("# My Project\n")
        output_path = tmp_path / "output" / "project-context.json"
        orch = _make_orchestrator(tmp_path, output_path)
        orch.run(project_root=tmp_path)

        assert output_path.is_file()
        written = json.loads(output_path.read_text())
        assert "metadata" in written
        assert "sections" in written


# ===========================================================================
# T036: Idempotency test
# ===========================================================================


class TestIdempotency:
    """Integration test: running scan twice produces identical results.

    True idempotency is measured from stabilized state: run 2 vs run 3,
    both of which read back the written context before scanning.
    """

    def test_consecutive_scans_produce_identical_sections(
        self, full_devops_project: Path, tmp_path: Path
    ) -> None:
        """Stabilized scans (run 2 and run 3) produce identical sections."""
        output_path = tmp_path / "output" / "project-context.json"
        orch = _make_orchestrator(full_devops_project, output_path)

        # Run 1: initial scan (creates context from scratch)
        orch.run(project_root=full_devops_project)
        # Run 2: reads back context, merges -- this is the stabilized state
        result2 = orch.run(project_root=full_devops_project)
        # Run 3: should be identical to run 2
        result3 = orch.run(project_root=full_devops_project)

        assert result2.context["sections"] == result3.context["sections"]

    def test_only_timestamps_differ_between_scans(
        self, full_devops_project: Path, tmp_path: Path
    ) -> None:
        """Only metadata timestamps differ between stabilized scans."""
        output_path = tmp_path / "output" / "project-context.json"
        orch = _make_orchestrator(full_devops_project, output_path)

        # Stabilize by running twice first
        orch.run(project_root=full_devops_project)
        result2 = orch.run(project_root=full_devops_project)
        result3 = orch.run(project_root=full_devops_project)

        ctx2 = copy.deepcopy(result2.context)
        ctx3 = copy.deepcopy(result3.context)

        # Remove timestamp fields
        for ctx in (ctx2, ctx3):
            ctx["metadata"].pop("last_updated", None)
            if "scan_config" in ctx["metadata"]:
                ctx["metadata"]["scan_config"].pop("last_scan", None)

        assert ctx2 == ctx3

    def test_clean_slate_scans_are_deterministic(
        self, full_devops_project: Path, tmp_path: Path
    ) -> None:
        """Two independent clean-slate scans produce identical sections.

        Each scan writes to a separate output path so there is no existing
        context to merge with, isolating scanner-level determinism.
        """
        output1 = tmp_path / "out1" / "project-context.json"
        output2 = tmp_path / "out2" / "project-context.json"

        orch1 = _make_orchestrator(full_devops_project, output1)
        orch2 = _make_orchestrator(full_devops_project, output2)

        result1 = orch1.run(project_root=full_devops_project)
        result2 = orch2.run(project_root=full_devops_project)

        assert result1.context["sections"] == result2.context["sections"]

    def test_agent_enriched_data_preserved_between_scans(
        self,
        full_devops_project: Path,
        existing_agent_context: Dict[str, Any],
        tmp_path: Path,
    ) -> None:
        """Agent-enriched data is preserved across multiple scans."""
        output_path = tmp_path / "output" / "project-context.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Write existing context with agent-enriched data
        output_path.write_text(json.dumps(existing_agent_context, indent=2))

        orch = _make_orchestrator(full_devops_project, output_path)

        # Run scan twice
        result1 = orch.run(project_root=full_devops_project)
        result2 = orch.run(project_root=full_devops_project)

        # Agent-enriched sections must survive both scans
        for result in (result1, result2):
            sections = result.context["sections"]
            assert "cluster_details" in sections
            assert sections["cluster_details"]["cluster_name"] == "prod-us-central1"
            assert "operational_guidelines" in sections
            assert sections["operational_guidelines"]["deployment_strategy"] == "blue-green"

    def test_no_random_ordering_in_output(
        self, full_devops_project: Path, tmp_path: Path
    ) -> None:
        """Scanner output is deterministic -- no random ordering in lists.

        After stabilization (run 1 + run 2), subsequent runs produce
        byte-identical sections.
        """
        output_path = tmp_path / "output" / "project-context.json"
        orch = _make_orchestrator(full_devops_project, output_path)

        # Stabilize
        orch.run(project_root=full_devops_project)
        orch.run(project_root=full_devops_project)

        # Run 3 more times and compare
        results = []
        for _ in range(3):
            result = orch.run(project_root=full_devops_project)
            results.append(result.context["sections"])

        assert results[0] == results[1]
        assert results[1] == results[2]


# ===========================================================================
# T037: Scanner failure isolation
# ===========================================================================


class _FailingScanner(BaseScanner):
    """A scanner that always raises RuntimeError for testing failure isolation."""

    @property
    def SCANNER_NAME(self) -> str:
        return "stack"

    @property
    def SCANNER_VERSION(self) -> str:
        return "1.0.0"

    @property
    def OWNED_SECTIONS(self):
        return ["project_identity", "stack"]

    def scan(self, root: Path) -> ScanResult:
        raise RuntimeError("Simulated scanner failure for testing")


class TestScannerFailureIsolation:
    """Integration test: single scanner failure does not abort entire scan."""

    def _build_orchestrator_with_failing_stack(
        self, project_root: Path, output_path: Path
    ) -> ScanOrchestrator:
        """Build an orchestrator with the stack scanner replaced by a failing one."""
        config = ScanConfig(
            project_root=project_root,
            output_path=output_path,
            parallel=False,
        )
        registry = ScannerRegistry()

        # Replace the real stack scanner with the failing one
        failing = _FailingScanner()
        if "stack" in registry._scanners:
            # Remove the existing stack scanner and its section ownership
            old_scanner = registry._scanners.pop("stack")
            for section in old_scanner.OWNED_SECTIONS:
                registry._section_owners.pop(section, None)

        # Register the failing scanner
        registry._scanners[failing.SCANNER_NAME] = failing
        for section in failing.OWNED_SECTIONS:
            registry._section_owners[section] = failing.SCANNER_NAME

        return ScanOrchestrator(registry=registry, config=config)

    def test_scan_completes_despite_scanner_failure(
        self, full_devops_project: Path, tmp_path: Path
    ) -> None:
        """Scan completes without raising when one scanner fails."""
        output_path = tmp_path / "output" / "project-context.json"
        orch = self._build_orchestrator_with_failing_stack(
            full_devops_project, output_path
        )

        # Should NOT raise
        result = orch.run(project_root=full_devops_project)
        assert isinstance(result, ScanOutput)

    def test_warning_recorded_for_failed_scanner(
        self, full_devops_project: Path, tmp_path: Path
    ) -> None:
        """ScanOutput.warnings includes a message about the failed scanner."""
        output_path = tmp_path / "output" / "project-context.json"
        orch = self._build_orchestrator_with_failing_stack(
            full_devops_project, output_path
        )
        result = orch.run(project_root=full_devops_project)

        # Should have a warning about the stack scanner failure
        warning_text = " ".join(result.warnings)
        assert "stack" in warning_text.lower() or "RuntimeError" in warning_text

    def test_failed_scanner_sections_absent(
        self, full_devops_project: Path, tmp_path: Path
    ) -> None:
        """project_identity and stack sections are absent (failed scanner's sections)."""
        output_path = tmp_path / "output" / "project-context.json"
        orch = self._build_orchestrator_with_failing_stack(
            full_devops_project, output_path
        )
        result = orch.run(project_root=full_devops_project)

        sections = result.context["sections"]
        # The stack scanner failed, so its owned sections should not be present
        assert "project_identity" not in sections
        assert "stack" not in sections

    def test_other_scanners_still_produce_output(
        self, full_devops_project: Path, tmp_path: Path
    ) -> None:
        """git, environment, infrastructure, and orchestration still produce output."""
        output_path = tmp_path / "output" / "project-context.json"
        orch = self._build_orchestrator_with_failing_stack(
            full_devops_project, output_path
        )
        result = orch.run(project_root=full_devops_project)

        sections = result.context["sections"]

        # Other scanners should still produce their sections
        assert "git" in sections
        assert sections["git"]["platform"] == "github"

        assert "environment" in sections
        assert "os" in sections["environment"]

        assert "infrastructure" in sections
        assert "orchestration" in sections

    def test_scanner_results_include_failed_scanner(
        self, full_devops_project: Path, tmp_path: Path
    ) -> None:
        """scanner_results includes the failed scanner with empty sections."""
        output_path = tmp_path / "output" / "project-context.json"
        orch = self._build_orchestrator_with_failing_stack(
            full_devops_project, output_path
        )
        result = orch.run(project_root=full_devops_project)

        assert "stack" in result.scanner_results
        failed_result = result.scanner_results["stack"]
        assert failed_result.sections == {}
        assert len(failed_result.warnings) > 0
