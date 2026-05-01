"""
Tests for tools.scan.role_detector.detect_role.

AC-1: detect_role returns 'iac' for bildwiz-iac/, 'gitops' for bildwiz-gitops/,
'application' for bildwiz-api/.
"""

from __future__ import annotations

from pathlib import Path

import pytest


FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_detect_role_fixtures():
    """detect_role classifies the three canonical fixtures correctly."""
    from tools.scan.role_detector import detect_role

    assert detect_role(FIXTURES / "bildwiz-iac") == "iac"
    assert detect_role(FIXTURES / "bildwiz-gitops") == "gitops"
    assert detect_role(FIXTURES / "bildwiz-api") == "application"


def test_detect_role_missing_path_falls_back_to_application(tmp_path):
    """When the path does not exist or is empty, return 'application' as a safe default."""
    from tools.scan.role_detector import detect_role

    nonexistent = tmp_path / "does-not-exist"
    assert detect_role(nonexistent) == "application"


def test_detect_role_monorepo(tmp_path):
    """A directory with pnpm-workspace.yaml is classified as monorepo."""
    from tools.scan.role_detector import detect_role

    (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - apps/*\n")
    (tmp_path / "package.json").write_text("{}")
    assert detect_role(tmp_path) == "monorepo"


def test_detect_role_terraform_only(tmp_path):
    """Terraform files alone classify as iac."""
    from tools.scan.role_detector import detect_role

    (tmp_path / "main.tf").write_text("provider \"aws\" {}\n")
    assert detect_role(tmp_path) == "iac"


def test_detect_role_helm_chart(tmp_path):
    """Chart.yaml alone classifies as gitops."""
    from tools.scan.role_detector import detect_role

    (tmp_path / "Chart.yaml").write_text("apiVersion: v2\nname: test\nversion: 0.1.0\n")
    assert detect_role(tmp_path) == "gitops"
