"""
Unit tests for the Infrastructure Scanner (T021).

Tests cloud provider detection, IaC tool detection, container detection,
CI/CD platform detection, and empty project behavior.
"""

import json
import os
import textwrap
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from tools.scan.scanners.infrastructure import InfrastructureScanner


@pytest.fixture
def scanner() -> InfrastructureScanner:
    """Create an InfrastructureScanner instance."""
    return InfrastructureScanner()


# ---------------------------------------------------------------------------
# Scanner basics
# ---------------------------------------------------------------------------


class TestInfraScannerBasics:
    """Test scanner metadata and basic contract."""

    def test_scanner_name(self, scanner: InfrastructureScanner) -> None:
        assert scanner.SCANNER_NAME == "infrastructure"

    def test_scanner_version(self, scanner: InfrastructureScanner) -> None:
        assert scanner.SCANNER_VERSION == "1.0.0"

    def test_owned_sections(self, scanner: InfrastructureScanner) -> None:
        assert scanner.OWNED_SECTIONS == ["infrastructure"]

    def test_source_tag(self, scanner: InfrastructureScanner) -> None:
        assert scanner.source_tag == "scanner:infrastructure"


# ---------------------------------------------------------------------------
# Cloud provider detection
# ---------------------------------------------------------------------------


class TestCloudProviderDetection:
    """Test cloud provider detection from .tf files and env vars."""

    def test_detect_gcp_from_terraform(
        self, scanner: InfrastructureScanner, terraform_gcp_project: Path
    ) -> None:
        result = scanner.scan(terraform_gcp_project)
        infra = result.sections["infrastructure"]
        provider_names = [p["name"] for p in infra["cloud_providers"]]
        assert "gcp" in provider_names

    def test_detect_aws_from_terraform(
        self, scanner: InfrastructureScanner, terraform_aws_project: Path
    ) -> None:
        result = scanner.scan(terraform_aws_project)
        infra = result.sections["infrastructure"]
        provider_names = [p["name"] for p in infra["cloud_providers"]]
        assert "aws" in provider_names

    def test_detect_azure_from_terraform(
        self, scanner: InfrastructureScanner, terraform_azure_project: Path
    ) -> None:
        result = scanner.scan(terraform_azure_project)
        infra = result.sections["infrastructure"]
        provider_names = [p["name"] for p in infra["cloud_providers"]]
        assert "azure" in provider_names

    def test_detect_multicloud(
        self, scanner: InfrastructureScanner, terraform_multicloud_project: Path
    ) -> None:
        result = scanner.scan(terraform_multicloud_project)
        infra = result.sections["infrastructure"]
        provider_names = [p["name"] for p in infra["cloud_providers"]]
        assert "gcp" in provider_names
        assert "aws" in provider_names

    def test_detect_aws_from_env_var(
        self, scanner: InfrastructureScanner, tmp_path: Path
    ) -> None:
        # Need something to trigger a non-empty section (env var alone + empty project)
        # Create a Dockerfile so the section is produced
        (tmp_path / "Dockerfile").write_text("FROM alpine\n")
        with patch.dict(os.environ, {"AWS_DEFAULT_REGION": "us-east-1"}):
            result = scanner.scan(tmp_path)
        infra = result.sections["infrastructure"]
        provider_names = [p["name"] for p in infra["cloud_providers"]]
        assert "aws" in provider_names

    def test_gcp_detected_by_terraform_provider(
        self, scanner: InfrastructureScanner, terraform_gcp_project: Path
    ) -> None:
        result = scanner.scan(terraform_gcp_project)
        infra = result.sections["infrastructure"]
        gcp = [p for p in infra["cloud_providers"] if p["name"] == "gcp"][0]
        assert gcp["detected_by"] == "terraform_provider"

    def test_terraform_in_subdirectory(
        self, scanner: InfrastructureScanner, tmp_path: Path
    ) -> None:
        tf_dir = tmp_path / "terraform"
        tf_dir.mkdir()
        (tf_dir / "main.tf").write_text('provider "google" {\n  project = "x"\n}\n')
        result = scanner.scan(tmp_path)
        infra = result.sections["infrastructure"]
        provider_names = [p["name"] for p in infra["cloud_providers"]]
        assert "gcp" in provider_names


# ---------------------------------------------------------------------------
# IaC tool detection
# ---------------------------------------------------------------------------


class TestIaCDetection:
    """Test IaC tool detection from file presence."""

    def test_detect_terraform(
        self, scanner: InfrastructureScanner, terraform_gcp_project: Path
    ) -> None:
        result = scanner.scan(terraform_gcp_project)
        infra = result.sections["infrastructure"]
        iac_tools = [t["tool"] for t in infra["iac"]]
        assert "terraform" in iac_tools

    def test_detect_terragrunt(
        self, scanner: InfrastructureScanner, tmp_path: Path
    ) -> None:
        (tmp_path / "terragrunt.hcl").write_text('include {\n  path = "root"\n}\n')
        result = scanner.scan(tmp_path)
        infra = result.sections["infrastructure"]
        iac_tools = [t["tool"] for t in infra["iac"]]
        assert "terragrunt" in iac_tools

    def test_detect_pulumi(
        self, scanner: InfrastructureScanner, tmp_path: Path
    ) -> None:
        (tmp_path / "Pulumi.yaml").write_text("name: test\nruntime: python\n")
        result = scanner.scan(tmp_path)
        infra = result.sections["infrastructure"]
        iac_tools = [t["tool"] for t in infra["iac"]]
        assert "pulumi" in iac_tools

    def test_detect_cdk(
        self, scanner: InfrastructureScanner, tmp_path: Path
    ) -> None:
        (tmp_path / "cdk.json").write_text('{"app": "npx ts-node bin/app.ts"}')
        result = scanner.scan(tmp_path)
        infra = result.sections["infrastructure"]
        iac_tools = [t["tool"] for t in infra["iac"]]
        assert "cdk" in iac_tools


# ---------------------------------------------------------------------------
# Container detection
# ---------------------------------------------------------------------------


class TestContainerDetection:
    """Test container tooling detection."""

    def test_detect_docker_from_dockerfile(
        self, scanner: InfrastructureScanner, tmp_path: Path
    ) -> None:
        (tmp_path / "Dockerfile").write_text("FROM node:20-alpine\n")
        result = scanner.scan(tmp_path)
        infra = result.sections["infrastructure"]
        container_tools = [c["tool"] for c in infra["containers"]]
        assert "docker" in container_tools

    def test_detect_docker_compose_yml(
        self, scanner: InfrastructureScanner, tmp_path: Path
    ) -> None:
        (tmp_path / "docker-compose.yml").write_text(
            "version: '3'\nservices:\n  app:\n    image: node:20\n"
        )
        result = scanner.scan(tmp_path)
        infra = result.sections["infrastructure"]
        container_tools = [c["tool"] for c in infra["containers"]]
        assert "docker-compose" in container_tools

    def test_detect_docker_compose_yaml(
        self, scanner: InfrastructureScanner, tmp_path: Path
    ) -> None:
        (tmp_path / "docker-compose.yaml").write_text(
            "version: '3'\nservices:\n  app:\n    image: node:20\n"
        )
        result = scanner.scan(tmp_path)
        infra = result.sections["infrastructure"]
        container_tools = [c["tool"] for c in infra["containers"]]
        assert "docker-compose" in container_tools

    def test_detect_both_docker_and_compose(
        self, scanner: InfrastructureScanner, tmp_path: Path
    ) -> None:
        (tmp_path / "Dockerfile").write_text("FROM node:20\n")
        (tmp_path / "docker-compose.yml").write_text("version: '3'\nservices: {}\n")
        result = scanner.scan(tmp_path)
        infra = result.sections["infrastructure"]
        container_tools = [c["tool"] for c in infra["containers"]]
        assert "docker" in container_tools
        assert "docker-compose" in container_tools


# ---------------------------------------------------------------------------
# CI/CD detection
# ---------------------------------------------------------------------------


class TestCICDDetection:
    """Test CI/CD platform detection."""

    def test_detect_github_actions(
        self, scanner: InfrastructureScanner, tmp_path: Path
    ) -> None:
        workflows_dir = tmp_path / ".github" / "workflows"
        workflows_dir.mkdir(parents=True)
        (workflows_dir / "ci.yml").write_text("name: CI\n")
        result = scanner.scan(tmp_path)
        infra = result.sections["infrastructure"]
        cicd_platforms = [c["platform"] for c in infra["ci_cd"]]
        assert "github-actions" in cicd_platforms

    def test_detect_gitlab_ci(
        self, scanner: InfrastructureScanner, tmp_path: Path
    ) -> None:
        (tmp_path / ".gitlab-ci.yml").write_text("stages:\n  - test\n")
        result = scanner.scan(tmp_path)
        infra = result.sections["infrastructure"]
        cicd_platforms = [c["platform"] for c in infra["ci_cd"]]
        assert "gitlab-ci" in cicd_platforms

    def test_detect_jenkins(
        self, scanner: InfrastructureScanner, tmp_path: Path
    ) -> None:
        (tmp_path / "Jenkinsfile").write_text("pipeline {\n  agent any\n}\n")
        result = scanner.scan(tmp_path)
        infra = result.sections["infrastructure"]
        cicd_platforms = [c["platform"] for c in infra["ci_cd"]]
        assert "jenkins" in cicd_platforms

    def test_detect_circleci(
        self, scanner: InfrastructureScanner, tmp_path: Path
    ) -> None:
        circleci_dir = tmp_path / ".circleci"
        circleci_dir.mkdir()
        (circleci_dir / "config.yml").write_text("version: 2.1\n")
        result = scanner.scan(tmp_path)
        infra = result.sections["infrastructure"]
        cicd_platforms = [c["platform"] for c in infra["ci_cd"]]
        assert "circleci" in cicd_platforms


# ---------------------------------------------------------------------------
# Empty project
# ---------------------------------------------------------------------------


class TestEmptyProject:
    """Test that empty projects return empty infrastructure section.

    Must mock both env vars AND CLI config file existence to prevent
    detection from the host system leaking into the test.
    """

    def _clean_env(self) -> Dict[str, str]:
        """Return env dict without cloud-related vars."""
        return {
            k: v for k, v in os.environ.items()
            if not k.startswith((
                "AWS_", "GOOGLE_", "GCLOUD_", "GCP_", "CLOUDSDK_", "AZURE_", "ARM_",
            ))
        }

    def test_empty_project_returns_empty_sections(
        self, scanner: InfrastructureScanner, empty_project: Path
    ) -> None:
        with patch.dict(os.environ, self._clean_env(), clear=True):
            with patch(
                "tools.scan.scanners.infrastructure.Path.home",
                return_value=empty_project / "_fake_home",
            ):
                result = scanner.scan(empty_project)
        assert result.sections == {}

    def test_empty_project_no_source_tag(
        self, scanner: InfrastructureScanner, empty_project: Path
    ) -> None:
        with patch.dict(os.environ, self._clean_env(), clear=True):
            with patch(
                "tools.scan.scanners.infrastructure.Path.home",
                return_value=empty_project / "_fake_home",
            ):
                result = scanner.scan(empty_project)
        assert "infrastructure" not in result.sections


# ---------------------------------------------------------------------------
# ScanResult contract
# ---------------------------------------------------------------------------


class TestInfraResultContract:
    """Test scan result follows expected contract."""

    def test_source_tag_present(
        self, scanner: InfrastructureScanner, terraform_gcp_project: Path
    ) -> None:
        result = scanner.scan(terraform_gcp_project)
        assert result.sections["infrastructure"]["_source"] == "scanner:infrastructure"

    def test_result_has_duration(
        self, scanner: InfrastructureScanner, terraform_gcp_project: Path
    ) -> None:
        result = scanner.scan(terraform_gcp_project)
        assert result.duration_ms >= 0

    def test_devops_project_full_detection(
        self, scanner: InfrastructureScanner, devops_project: Path
    ) -> None:
        result = scanner.scan(devops_project)
        infra = result.sections["infrastructure"]
        assert len(infra["cloud_providers"]) >= 1
        assert len(infra["iac"]) >= 1
        assert len(infra["containers"]) >= 1
        assert len(infra["ci_cd"]) >= 1
