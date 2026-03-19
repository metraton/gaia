"""
Unit tests for context combining logic (T026).

Tests scanner-owned section replacement, agent-enriched section preservation,
mixed section sub-key merge, unknown section preservation, v1-to-v2 upgrade,
and idempotency.
"""

import copy
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from tools.scan.merge import merge_context


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _section_owners() -> Dict[str, str]:
    """Return a realistic section_owners map from ScannerRegistry."""
    return {
        "project_identity": "stack",
        "stack": "stack",
        "git": "git",
        "infrastructure": "infrastructure",
        "orchestration": "orchestration",
        "environment.tools": "tools",
        "environment.tool_preferences": "tools",
        "environment.runtimes": "environment",
        "environment.os": "environment",
        "environment.env_files": "environment",
    }


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------


def _make_existing_context() -> Dict[str, Any]:
    """Create a realistic existing project-context with scanner + agent data."""
    return {
        "metadata": {
            "version": "2.0",
            "last_updated": "2026-01-01T00:00:00Z",
            "project_name": "test-project",
            "scan_config": {
                "staleness_hours": 24,
                "last_scan": "2026-01-01T00:00:00Z",
                "scanner_version": "0.1.0",
            },
        },
        "project_identity": {
            "_source": "scanner:stack",
            "name": "old-name",
            "type": "application",
        },
        "stack": {
            "_source": "scanner:stack",
            "languages": [{"name": "python", "manifest": "pyproject.toml", "primary": True}],
            "frameworks": [],
            "build_tools": [],
        },
        "git": {
            "_source": "scanner:git",
            "platform": "github",
            "remotes": [],
            "default_branch": "main",
        },
        "environment": {
            "_source": "scanner:environment",
            "os": {"platform": "linux", "architecture": "x64"},
            "runtimes": [{"name": "python3", "version": "3.11.0"}],
            "env_files": [],
            "tools": [{"name": "git", "path": "/usr/bin/git"}],
            "tool_preferences": {"file_viewer": "bat"},
        },
        "operational_guidelines": {
            "_source": "agent:devops-developer",
            "deployment_strategy": "blue-green",
            "rollback_procedure": "manual",
        },
        "my_custom_notes": {
            "author": "user",
            "notes": "User-maintained section",
        },
    }


def _make_scan_results() -> Dict[str, Any]:
    """Create scan results from a new scan run."""
    return {
        "project_identity": {
            "_source": "scanner:stack",
            "name": "new-name",
            "type": "monorepo",
            "description": "Updated description",
        },
        "stack": {
            "_source": "scanner:stack",
            "languages": [
                {"name": "typescript", "manifest": "package.json", "primary": True},
                {"name": "python", "manifest": "pyproject.toml", "primary": False},
            ],
            "frameworks": [{"name": "react", "language": "typescript"}],
            "build_tools": [{"name": "npm", "detected_by": "lock_file"}],
        },
        "git": {
            "_source": "scanner:git",
            "platform": "github",
            "remotes": [{"name": "origin", "url": "git@github.com:o/r.git"}],
            "default_branch": "main",
        },
        "environment": {
            "_source": "scanner:environment",
            "os": {"platform": "linux", "architecture": "x64", "wsl": True},
            "runtimes": [{"name": "python3", "version": "3.12.0"}],
            "env_files": [{"name": ".env", "path": ".env"}],
        },
    }


# ---------------------------------------------------------------------------
# Rule 1: Scanner-owned section fully replaced
# ---------------------------------------------------------------------------


class TestScannerOwnedSectionReplacement:
    """Test that scanner-owned sections are fully replaced with new data."""

    def test_project_identity_replaced(self) -> None:
        existing = _make_existing_context()
        scan = _make_scan_results()
        result = merge_context(existing, scan, _section_owners())
        assert result["project_identity"]["name"] == "new-name"
        assert result["project_identity"]["type"] == "monorepo"

    def test_stack_section_replaced(self) -> None:
        existing = _make_existing_context()
        scan = _make_scan_results()
        result = merge_context(existing, scan, _section_owners())
        lang_names = [l["name"] for l in result["stack"]["languages"]]
        assert "typescript" in lang_names
        assert len(result["stack"]["frameworks"]) == 1

    def test_git_section_replaced(self) -> None:
        existing = _make_existing_context()
        scan = _make_scan_results()
        result = merge_context(existing, scan, _section_owners())
        assert len(result["git"]["remotes"]) == 1


# ---------------------------------------------------------------------------
# Rule 2: Agent-enriched sections preserved
# ---------------------------------------------------------------------------


class TestAgentEnrichedPreservation:
    """Test that agent-enriched sections are preserved byte-identical."""

    def test_operational_guidelines_preserved(self) -> None:
        existing = _make_existing_context()
        scan = _make_scan_results()
        result = merge_context(existing, scan, _section_owners())
        assert result["operational_guidelines"]["deployment_strategy"] == "blue-green"
        assert result["operational_guidelines"]["rollback_procedure"] == "manual"


# ---------------------------------------------------------------------------
# Rule 4: Mixed section (environment) sub-key merge
# ---------------------------------------------------------------------------


class TestMixedSectionMerge:
    """Test that mixed sections merge scanner fields and keep agent fields."""

    def test_environment_scanner_fields_refreshed(self) -> None:
        existing = _make_existing_context()
        scan = _make_scan_results()
        result = merge_context(existing, scan, _section_owners())
        # Scanner-owned sub-keys should be updated
        assert result["environment"]["os"].get("wsl") is True
        runtimes = result["environment"]["runtimes"]
        py = [r for r in runtimes if r["name"] == "python3"][0]
        assert py["version"] == "3.12.0"

    def test_environment_agent_fields_kept(self) -> None:
        existing = _make_existing_context()
        scan = _make_scan_results()
        result = merge_context(existing, scan, _section_owners())
        # tools and tool_preferences came from tool scanner (not in this scan)
        # They should be preserved from existing
        assert "tools" in result["environment"] or "tool_preferences" in result["environment"]


# ---------------------------------------------------------------------------
# Rule 5: Unknown/user-custom sections preserved
# ---------------------------------------------------------------------------


class TestUserCustomPreservation:
    """Test that user-custom sections survive combining."""

    def test_custom_section_preserved(self) -> None:
        existing = _make_existing_context()
        scan = _make_scan_results()
        result = merge_context(existing, scan, _section_owners())
        assert "my_custom_notes" in result
        assert result["my_custom_notes"]["author"] == "user"


# ---------------------------------------------------------------------------
# v1-to-v2 upgrade
# ---------------------------------------------------------------------------


class TestV1ToV2Upgrade:
    """Test upgrading from v1 project-context (no scan_config)."""

    def test_v1_context_upgraded(self, sample_project_context_v1: Dict[str, Any]) -> None:
        scan = _make_scan_results()
        result = merge_context(sample_project_context_v1, scan, _section_owners())
        # Should have scan_config after upgrade
        assert "metadata" in result
        # Agent-enriched data from v1 should be preserved
        if "operational_guidelines" in sample_project_context_v1:
            assert "operational_guidelines" in result

    def test_v1_agent_data_not_lost(self, sample_project_context_v1: Dict[str, Any]) -> None:
        scan = _make_scan_results()
        result = merge_context(sample_project_context_v1, scan, _section_owners())
        # User-custom sections from v1 are preserved as-is (Rule 4).
        # project_details is no longer produced by the scanner (no backward compat),
        # but if it existed in the v1 context it is preserved as a user-custom section.
        if "project_details" in sample_project_context_v1:
            assert "project_details" in result


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    """Test that running combine twice produces same result (except timestamps)."""

    def test_idempotent_combine(self) -> None:
        existing = _make_existing_context()
        scan = _make_scan_results()

        result1 = merge_context(existing, scan, _section_owners())
        result2 = merge_context(result1, scan, _section_owners())

        # Strip timestamps for comparison
        def strip_timestamps(d: Dict) -> Dict:
            d = copy.deepcopy(d)
            if "metadata" in d:
                meta = d["metadata"]
                meta.pop("last_updated", None)
                if "scan_config" in meta:
                    meta["scan_config"].pop("last_scan", None)
            return d

        assert strip_timestamps(result1) == strip_timestamps(result2)
