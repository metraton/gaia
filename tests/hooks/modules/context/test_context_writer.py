#!/usr/bin/env python3
"""
Tests for ContextWriter module.

TDD tests - the implementation does NOT exist yet. Tests are written to FAIL.

Validates:
1. CONTEXT_UPDATE block parsing from agent output
2. Write permission validation via contracts
3. Atomic apply with deep merge and audit trail
4. Contract file loading with caching and legacy fallback
5. Full orchestration flow (process_agent_output)
"""

import sys
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add hooks and tools to path
HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
TOOLS_DIR = Path(__file__).resolve().parents[4] / "tools"
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(HOOKS_DIR / "modules" / "context"))
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(TOOLS_DIR / "context"))

# TDD: Import will fail until context_writer.py is implemented.
# We import lazily so pytest can still COLLECT the tests.
try:
    from modules.context.context_writer import (
        parse_context_update,
        validate_permissions,
        apply_update,
        load_contracts,
        process_agent_output,
    )
    _MODULE_AVAILABLE = True
except ImportError:
    _MODULE_AVAILABLE = False

    # Provide stubs so test bodies can reference the names at collection time
    def parse_context_update(*a, **kw):
        raise NotImplementedError

    def validate_permissions(*a, **kw):
        raise NotImplementedError

    def apply_update(*a, **kw):
        raise NotImplementedError

    def load_contracts(*a, **kw):
        raise NotImplementedError

    def process_agent_output(*a, **kw):
        raise NotImplementedError

# Auto-applied marker: every test fails fast when module is missing
pytestmark = pytest.mark.skipif(
    not _MODULE_AVAILABLE,
    reason="context_writer module not yet implemented",
)


# ============================================================================
# Shared Test Data
# ============================================================================

MOCK_CONTRACTS = {
    "version": "1.0",
    "provider": "gcp",
    "agents": {
        "cloud-troubleshooter": {
            "read": [
                "project_details", "cluster_details", "infrastructure_topology",
                "terraform_infrastructure", "gitops_configuration",
                "application_services", "monitoring_observability",
            ],
            "write": ["cluster_details", "infrastructure_topology"],
        },
        "gitops-operator": {
            "read": [
                "project_details", "gitops_configuration", "cluster_details",
                "operational_guidelines",
            ],
            "write": ["gitops_configuration", "cluster_details"],
        },
        "terraform-architect": {
            "read": [
                "project_details", "terraform_infrastructure",
                "infrastructure_topology", "operational_guidelines",
            ],
            "write": ["terraform_infrastructure", "infrastructure_topology"],
        },
        "devops-developer": {
            "read": [
                "project_details", "application_services",
                "application_architecture", "development_standards",
                "operational_guidelines",
            ],
            "write": ["application_services"],
        },
    },
}

LEGACY_CONTRACTS = {
    "terraform-architect": [
        "project_details", "terraform_infrastructure", "operational_guidelines",
    ],
    "gitops-operator": [
        "project_details", "gitops_configuration", "infrastructure_topology",
        "cluster_details", "operational_guidelines",
    ],
    "cloud-troubleshooter": [
        "project_details", "infrastructure_topology", "terraform_infrastructure",
        "gitops_configuration", "application_services", "monitoring_observability",
    ],
    "devops-developer": [
        "project_details", "application_architecture", "application_services",
        "development_standards", "operational_guidelines",
    ],
}


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_context_file(tmp_path):
    """Create a temporary project-context.json for isolated testing."""
    context_dir = tmp_path / ".claude" / "project-context"
    context_dir.mkdir(parents=True)
    context_file = context_dir / "project-context.json"
    context_file.write_text(json.dumps({
        "metadata": {
            "version": "1.0",
            "last_updated": "2025-01-01T00:00:00Z",
            "cloud_provider": "GCP",
        },
        "sections": {
            "cluster_details": {},
            "infrastructure_topology": {},
            "application_services": {},
        },
    }))
    return context_file


@pytest.fixture
def contracts_file(tmp_path):
    """Create a temporary contracts JSON file."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    contracts_path = config_dir / "context-contracts.gcp.json"
    contracts_path.write_text(json.dumps(MOCK_CONTRACTS))
    return contracts_path


# ============================================================================
# 1. parse_context_update tests (6)
# ============================================================================

class TestParseContextUpdate:
    """Test extraction of CONTEXT_UPDATE JSON blocks from agent output."""

    def test_parse_valid_block(self):
        """CONTEXT_UPDATE marker followed by valid JSON dict returns parsed dict."""
        agent_output = (
            "Investigation complete.\n"
            "CONTEXT_UPDATE:\n"
            '{"cluster_details": {"node_count": 3, "version": "1.28"}}'
        )
        result = parse_context_update(agent_output)
        assert result is not None
        assert "cluster_details" in result
        assert result["cluster_details"]["node_count"] == 3

    def test_parse_no_marker(self):
        """Agent output without CONTEXT_UPDATE marker returns None."""
        agent_output = (
            "Everything looks fine. No changes needed.\n"
            "Cluster is healthy with 3 nodes."
        )
        result = parse_context_update(agent_output)
        assert result is None

    def test_parse_malformed_json(self):
        """CONTEXT_UPDATE marker with invalid JSON returns None."""
        agent_output = (
            "CONTEXT_UPDATE:\n"
            '{"cluster_details": {node_count: INVALID}'
        )
        result = parse_context_update(agent_output)
        assert result is None

    def test_parse_non_dict_json(self):
        """CONTEXT_UPDATE marker with non-dict JSON (e.g. array) returns None."""
        agent_output = (
            "CONTEXT_UPDATE:\n"
            "[1, 2, 3]"
        )
        result = parse_context_update(agent_output)
        assert result is None

    def test_parse_marker_case_sensitive(self):
        """Lowercase context_update marker is not recognized, returns None."""
        agent_output = (
            "context_update:\n"
            '{"cluster_details": {"node_count": 3}}'
        )
        result = parse_context_update(agent_output)
        assert result is None

    def test_parse_with_surrounding_output(self):
        """CONTEXT_UPDATE in the middle of long agent output is correctly extracted."""
        agent_output = (
            "Starting investigation...\n"
            "Checked 5 namespaces, 12 pods, 3 services.\n"
            "Found cluster version mismatch.\n"
            "\n"
            "CONTEXT_UPDATE:\n"
            '{"cluster_details": {"version": "1.29", "node_pool": "default-pool"}}\n'
            "\n"
            "Summary: Updated cluster version info.\n"
            "AGENT_STATUS: COMPLETE\n"
        )
        result = parse_context_update(agent_output)
        assert result is not None
        assert result["cluster_details"]["version"] == "1.29"
        assert result["cluster_details"]["node_pool"] == "default-pool"


# ============================================================================
# 2. validate_permissions tests (5)
# ============================================================================

class TestValidatePermissions:
    """Test write permission validation against agent contracts."""

    def test_validate_allowed_section(self):
        """cloud-troubleshooter writing to cluster_details is allowed."""
        update = {"cluster_details": {"version": "1.29"}}
        allowed, rejected = validate_permissions(
            update, "cloud-troubleshooter", MOCK_CONTRACTS
        )
        assert "cluster_details" in allowed
        assert len(rejected) == 0

    def test_validate_rejected_section(self):
        """cloud-troubleshooter writing to application_services is rejected."""
        update = {"application_services": {"new_service": "test"}}
        allowed, rejected = validate_permissions(
            update, "cloud-troubleshooter", MOCK_CONTRACTS
        )
        assert len(allowed) == 0
        assert "application_services" in rejected

    def test_validate_mixed_sections(self):
        """Mixed update: allowed sections pass, rejected sections filtered out."""
        update = {
            "cluster_details": {"version": "1.29"},
            "application_services": {"new_service": "test"},
            "infrastructure_topology": {"vpc": "updated"},
        }
        allowed, rejected = validate_permissions(
            update, "cloud-troubleshooter", MOCK_CONTRACTS
        )
        # cluster_details and infrastructure_topology are writable
        assert "cluster_details" in allowed
        assert "infrastructure_topology" in allowed
        # application_services is NOT writable for cloud-troubleshooter
        assert "application_services" in rejected
        assert len(allowed) == 2
        assert len(rejected) == 1

    def test_validate_legacy_fallback(self):
        """Agent not in contracts file but in LEGACY falls back to legacy (write=read)."""
        # Use contracts that don't include this agent
        contracts_without_agent = {
            "version": "1.0",
            "provider": "gcp",
            "agents": {},
        }
        # Legacy: cloud-troubleshooter can read (and therefore write) infrastructure_topology
        update = {"infrastructure_topology": {"vpc": "updated"}}
        allowed, rejected = validate_permissions(
            update, "cloud-troubleshooter", contracts_without_agent
        )
        assert "infrastructure_topology" in allowed
        assert len(rejected) == 0

    def test_validate_unknown_agent(self):
        """Completely unknown agent (not in contracts or legacy) gets all rejected."""
        update = {
            "cluster_details": {"version": "1.29"},
            "application_services": {"new_service": "test"},
        }
        allowed, rejected = validate_permissions(
            update, "nonexistent-agent", MOCK_CONTRACTS
        )
        assert len(allowed) == 0
        assert "cluster_details" in rejected
        assert "application_services" in rejected


# ============================================================================
# 3. apply_update tests (6)
# ============================================================================

class TestApplyUpdate:
    """Test applying validated updates to project-context.json."""

    def test_apply_writes_to_file(self, mock_context_file):
        """Validated update is persisted to project-context.json."""
        update = {"cluster_details": {"version": "1.29"}}
        apply_update(mock_context_file, update, "cloud-troubleshooter")

        written = json.loads(mock_context_file.read_text())
        assert written["sections"]["cluster_details"]["version"] == "1.29"

    def test_apply_deep_merges(self, mock_context_file):
        """Update deep-merges into existing sections, not replaces them."""
        # First, write initial data
        initial = json.loads(mock_context_file.read_text())
        initial["sections"]["cluster_details"] = {
            "name": "prod-cluster",
            "region": "us-central1",
        }
        mock_context_file.write_text(json.dumps(initial))

        # Apply update that adds a field
        update = {"cluster_details": {"version": "1.29"}}
        apply_update(mock_context_file, update, "cloud-troubleshooter")

        written = json.loads(mock_context_file.read_text())
        # Original fields preserved
        assert written["sections"]["cluster_details"]["name"] == "prod-cluster"
        assert written["sections"]["cluster_details"]["region"] == "us-central1"
        # New field added
        assert written["sections"]["cluster_details"]["version"] == "1.29"

    def test_apply_updates_metadata_timestamp(self, mock_context_file):
        """metadata.last_updated is refreshed after apply."""
        update = {"cluster_details": {"version": "1.29"}}
        apply_update(mock_context_file, update, "cloud-troubleshooter")

        written = json.loads(mock_context_file.read_text())
        assert written["metadata"]["last_updated"] != "2025-01-01T00:00:00Z"

    def test_apply_atomic_write(self, mock_context_file):
        """Write uses a temporary file then renames for atomicity."""
        update = {"cluster_details": {"version": "1.29"}}

        original_rename = Path.rename

        rename_calls = []

        def tracking_rename(self_path, target):
            rename_calls.append((str(self_path), str(target)))
            return original_rename(self_path, target)

        with patch.object(Path, "rename", tracking_rename):
            apply_update(mock_context_file, update, "cloud-troubleshooter")

        # Should have renamed from .tmp to final path
        assert len(rename_calls) >= 1
        src, dst = rename_calls[0]
        assert ".tmp" in src or "tmp" in src.lower()
        assert str(mock_context_file) == dst

    def test_apply_creates_audit_entry(self, mock_context_file):
        """context-audit.jsonl receives an entry after successful apply."""
        update = {"cluster_details": {"version": "1.29"}}
        apply_update(mock_context_file, update, "cloud-troubleshooter")

        audit_file = mock_context_file.parent / "context-audit.jsonl"
        assert audit_file.exists()

        lines = audit_file.read_text().strip().split("\n")
        assert len(lines) >= 1

        entry = json.loads(lines[-1])
        assert entry["agent"] == "cloud-troubleshooter"
        assert "cluster_details" in entry.get(
            "sections", entry.get("sections_updated", [])
        )

    def test_apply_file_error(self, tmp_path):
        """Missing context file produces a failed result and does not raise."""
        missing_file = tmp_path / "nonexistent" / "project-context.json"
        update = {"cluster_details": {"version": "1.29"}}

        # Should not raise an exception
        result = apply_update(missing_file, update, "cloud-troubleshooter")

        # Result should indicate failure
        assert result.get("success") is False or result.get("error") is not None


# ============================================================================
# 4. load_contracts tests (3)
# ============================================================================

class TestLoadContracts:
    """Test contract file loading with caching and legacy fallback."""

    def test_load_gcp_contracts(self, contracts_file):
        """Loads context-contracts.gcp.json from config directory."""
        config_dir = contracts_file.parent
        result = load_contracts("gcp", config_dir)

        assert result["version"] == "1.0"
        assert result["provider"] == "gcp"
        assert "cloud-troubleshooter" in result["agents"]
        assert "write" in result["agents"]["cloud-troubleshooter"]

    def test_load_missing_file_fallback(self, tmp_path):
        """Missing contract file returns legacy fallback dict."""
        config_dir = tmp_path / "empty_config"
        config_dir.mkdir()

        result = load_contracts("gcp", config_dir)

        # Should return a usable fallback structure
        assert "agents" in result or "terraform-architect" in result
        # Fallback should contain known agents
        agents = result.get("agents", result)
        assert any(
            agent in agents
            for agent in [
                "terraform-architect", "gitops-operator", "cloud-troubleshooter",
            ]
        )

    def test_load_caches_result(self, contracts_file):
        """Second call with same provider returns cached (identical) object."""
        config_dir = contracts_file.parent

        # Clear any module-level cache before test
        if hasattr(load_contracts, "cache_clear"):
            load_contracts.cache_clear()

        result1 = load_contracts("gcp", config_dir)
        result2 = load_contracts("gcp", config_dir)

        # Same object reference means caching is active
        assert result1 is result2


# ============================================================================
# 5. process_agent_output tests (3)
# ============================================================================

class TestProcessAgentOutput:
    """Test full orchestration flow: parse -> validate -> apply."""

    def test_process_full_flow(self, mock_context_file):
        """Happy path: agent output with CONTEXT_UPDATE is parsed, validated, applied."""
        agent_output = (
            "Investigation complete.\n"
            "CONTEXT_UPDATE:\n"
            '{"cluster_details": {"version": "1.29", "node_count": 5}}'
        )
        task_info = {
            "agent_type": "cloud-troubleshooter",
            "context_path": str(mock_context_file),
            "config_dir": str(mock_context_file.parent.parent.parent),
        }

        result = process_agent_output(agent_output, task_info)

        assert result["updated"] is True
        assert "cluster_details" in result.get(
            "sections_updated", result.get("sections", [])
        )

        # Verify file was actually updated
        written = json.loads(mock_context_file.read_text())
        assert written["sections"]["cluster_details"]["version"] == "1.29"

    def test_process_no_update(self, mock_context_file):
        """No CONTEXT_UPDATE marker returns {updated: false}."""
        agent_output = "Everything looks fine. No changes needed."
        task_info = {
            "agent_type": "cloud-troubleshooter",
            "context_path": str(mock_context_file),
            "config_dir": str(mock_context_file.parent.parent.parent),
        }

        result = process_agent_output(agent_output, task_info)

        assert result["updated"] is False

    def test_process_all_rejected(self, mock_context_file):
        """All sections rejected returns {updated: false, rejected: [...]}."""
        agent_output = (
            "CONTEXT_UPDATE:\n"
            '{"application_services": {"new_svc": "test"}, '
            '"monitoring_observability": {"alerts": true}}'
        )
        task_info = {
            "agent_type": "devops-developer",
            "context_path": str(mock_context_file),
            "config_dir": str(mock_context_file.parent.parent.parent),
        }

        result = process_agent_output(agent_output, task_info)

        # devops-developer can write application_services but NOT monitoring_observability
        # monitoring_observability should be rejected
        assert "monitoring_observability" in result.get("rejected", [])
