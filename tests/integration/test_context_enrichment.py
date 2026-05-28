#!/usr/bin/env python3
"""
TDD integration tests for context enrichment pipeline.

Validates the full flow:
  Agent output with CONTEXT_UPDATE -> process_agent_output -> gaia.db updated

Modules under test:
  - hooks/modules/context/context_writer.py (process_agent_output)

DB helpers (from tests.fixtures.db_helpers):
  - bootstrap_gaia_schema, seed_workspace, seed_workspace_contracts, seed_agent_perms
"""

import sys
import json
import pytest
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup (follows existing project conventions)
# ---------------------------------------------------------------------------
HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"

sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(HOOKS_DIR / "modules" / "context"))
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(TOOLS_DIR / "context"))

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
from tests.fixtures.db_helpers import (
    bootstrap_gaia_schema,
    seed_workspace,
    seed_agent_perms,
    seed_workspace_contracts,
)


# ---------------------------------------------------------------------------
# Lazy import: context_writer
# ---------------------------------------------------------------------------

def _import_process_agent_output():
    """Import process_agent_output at call time so pytest can collect tests."""
    # Clear the permissions cache so each test gets a fresh DB read.
    import context_writer as _cw
    _cw._permissions_cache.clear()
    from context_writer import process_agent_output
    return process_agent_output


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_agent_output(contract_name: str, payload: dict) -> str:
    """Build agent output with a CONTEXT_UPDATE block using the new format."""
    output = "## Agent Execution Complete\n\nTask completed successfully.\n\n"
    output += "CONTEXT_UPDATE:\n"
    output += json.dumps({"contract": contract_name, "payload": payload}, indent=2)
    return output


def _build_task_info(agent_type: str, db_path: Path, workspace: str = "test-ws") -> dict:
    """Build the task_info dict expected by process_agent_output (DB-backed)."""
    return {
        "agent_type": agent_type,
        "db_path": db_path,
        "workspace": workspace,
    }


def read_contract(db_path: Path, workspace: str, contract_name: str):
    """Read back a contract payload from the DB; returns parsed dict or None."""
    import sqlite3
    con = sqlite3.connect(str(db_path))
    row = con.execute(
        "SELECT payload FROM project_context_contracts WHERE workspace=? AND contract_name=?",
        (workspace, contract_name),
    ).fetchone()
    con.close()
    return json.loads(row[0]) if row else None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ctx_db(tmp_path):
    """Isolated DB with schema + cloud-troubleshooter permissions seeded."""
    db_path = tmp_path / "gaia_test.db"
    bootstrap_gaia_schema(db_path)
    seed_workspace(db_path, "test-ws")
    seed_agent_perms(
        db_path,
        "cloud-troubleshooter",
        reads=["cluster_details", "infrastructure_topology",
               "application_services", "monitoring_observability",
               "architecture_overview"],
        writes=["cluster_details", "infrastructure_topology",
                "application_services", "monitoring_observability",
                "architecture_overview"],
    )
    return db_path


# ============================================================================
# Scenario 1: Fresh install - first enrichment
# ============================================================================

class TestFreshInstallFirstEnrichment:
    """Scenario 1: agent discovers namespaces and writes them for the first time."""

    def test_fresh_install_first_enrichment(self, ctx_db):
        process_agent_output = _import_process_agent_output()

        payload = {
            "namespaces": {
                "application": ["adm", "dev", "test"],
                "infrastructure": ["flux-system", "cert-manager"],
                "system": ["kube-system", "kube-public"]
            }
        }
        agent_output = make_agent_output("cluster_details", payload)

        result = process_agent_output(
            agent_output,
            _build_task_info("cloud-troubleshooter", ctx_db),
        )

        assert result["updated"] is True
        assert result["contract"] == "cluster_details"
        assert result["rejected"] == []

        stored = read_contract(ctx_db, "test-ws", "cluster_details")
        assert stored is not None
        namespaces = stored["namespaces"]
        assert sorted(namespaces["application"]) == ["adm", "dev", "test"]
        assert sorted(namespaces["infrastructure"]) == ["cert-manager", "flux-system"]
        assert sorted(namespaces["system"]) == ["kube-public", "kube-system"]


# ============================================================================
# Scenario 2: Incremental enrichment - second write overwrites with new payload
# ============================================================================

class TestIncrementalEnrichment:
    """Scenario 2: writing to a contract that already has data replaces it."""

    def test_incremental_enrichment_new_data(self, ctx_db):
        process_agent_output = _import_process_agent_output()

        # Seed initial contract value
        seed_workspace_contracts(ctx_db, "test-ws", {
            "cluster_details": {
                "namespaces": {
                    "application": ["adm", "dev", "test"],
                    "infrastructure": ["flux-system", "cert-manager"],
                    "system": ["kube-system"]
                }
            }
        })

        # New payload with updated application list
        new_payload = {
            "namespaces": {
                "application": ["adm", "dev", "test", "nova-auth-dev"]
            }
        }
        agent_output = make_agent_output("cluster_details", new_payload)

        result = process_agent_output(
            agent_output,
            _build_task_info("cloud-troubleshooter", ctx_db),
        )

        assert result["updated"] is True
        assert result["contract"] == "cluster_details"

        stored = read_contract(ctx_db, "test-ws", "cluster_details")
        assert stored is not None
        # The payload replaces the previous value (upsert semantics)
        app_ns = stored["namespaces"]["application"]
        assert "nova-auth-dev" in app_ns
        assert "adm" in app_ns


# ============================================================================
# Scenario 3: Drift detection - version update
# ============================================================================

class TestDriftDetection:
    """Scenario 3: agent writes a new payload for a section with existing data."""

    def test_drift_detection_version_update(self, ctx_db):
        process_agent_output = _import_process_agent_output()

        seed_workspace_contracts(ctx_db, "test-ws", {
            "cluster_details": {
                "helm_releases": [
                    {"name": "orders-service", "chart_version": "0.53.0"},
                    {"name": "payments-api", "chart_version": "1.2.0"},
                ]
            }
        })

        update_payload = {
            "helm_releases": [
                {"name": "orders-service", "chart_version": "0.54.0"}
            ]
        }
        agent_output = make_agent_output("cluster_details", update_payload)

        result = process_agent_output(
            agent_output,
            _build_task_info("cloud-troubleshooter", ctx_db),
        )

        assert result["updated"] is True
        assert result["contract"] == "cluster_details"

        stored = read_contract(ctx_db, "test-ws", "cluster_details")
        assert stored is not None
        assert stored["helm_releases"][0]["chart_version"] == "0.54.0"


# ============================================================================
# Scenario 4: Permission rejection
# ============================================================================

class TestPermissionRejection:
    """Scenario 4: agent tries to write a section it has no write access to."""

    def test_permission_rejection(self, ctx_db):
        process_agent_output = _import_process_agent_output()

        # cloud-troubleshooter cannot write gitops_configuration
        update_payload = {
            "repo_url": "https://evil.example.com/gitops",
            "tool": "evil-tool"
        }
        agent_output = make_agent_output("gitops_configuration", update_payload)

        result = process_agent_output(
            agent_output,
            _build_task_info("cloud-troubleshooter", ctx_db),
        )

        assert result["updated"] is False
        assert "gitops_configuration" in result["rejected"]

        # Contract must NOT be written to DB
        stored = read_contract(ctx_db, "test-ws", "gitops_configuration")
        assert stored is None


# ============================================================================
# Scenario 5: No CONTEXT_UPDATE - backward compatibility
# ============================================================================

class TestBackwardCompatibility:
    """Scenario 5: agent output contains no CONTEXT_UPDATE marker."""

    def test_no_context_update_backward_compat(self, ctx_db):
        process_agent_output = _import_process_agent_output()

        agent_output = (
            "## Agent Execution Complete\n\n"
            "Checked all pods. Everything looks healthy.\n"
            "No issues found.\n"
        )

        result = process_agent_output(
            agent_output,
            _build_task_info("cloud-troubleshooter", ctx_db),
        )

        assert result["updated"] is False


# ============================================================================
# Scenario 6: Malformed JSON - graceful handling
# ============================================================================

class TestMalformedJson:
    """Scenario 6: agent output has CONTEXT_UPDATE with invalid JSON."""

    def test_malformed_json_graceful(self, ctx_db):
        process_agent_output = _import_process_agent_output()

        agent_output = (
            "## Agent Execution Complete\n\n"
            "Task completed.\n\n"
            "CONTEXT_UPDATE:\n"
            '{invalid json, "missing": brackets'
        )

        # Must not raise an exception
        result = process_agent_output(
            agent_output,
            _build_task_info("cloud-troubleshooter", ctx_db),
        )

        assert result["updated"] is False


# ============================================================================
# Scenario 7: Multi-section update (two CONTEXT_UPDATE blocks sequentially)
# ============================================================================

class TestMultiSectionUpdate:
    """Scenario 7: agent updates two sections via two successive calls
    (the new DB-backed API is single-contract-per-call)."""

    def test_two_contract_updates(self, ctx_db):
        process_agent_output = _import_process_agent_output()

        output_cluster = make_agent_output("cluster_details", {
            "namespaces": {"application": ["dev", "staging"]}
        })
        output_topology = make_agent_output("infrastructure_topology", {
            "subnets": ["10.0.0.0/24", "10.0.1.0/24"]
        })

        r1 = process_agent_output(output_cluster, _build_task_info("cloud-troubleshooter", ctx_db))
        r2 = process_agent_output(output_topology, _build_task_info("cloud-troubleshooter", ctx_db))

        assert r1["updated"] is True
        assert r1["contract"] == "cluster_details"
        assert r2["updated"] is True
        assert r2["contract"] == "infrastructure_topology"

        stored_cluster = read_contract(ctx_db, "test-ws", "cluster_details")
        stored_topology = read_contract(ctx_db, "test-ws", "infrastructure_topology")

        assert "staging" in stored_cluster["namespaces"]["application"]
        assert "10.0.0.0/24" in stored_topology["subnets"]


# ============================================================================
# Scenario 8: Skill file existence and content
# ============================================================================

class TestSkillFileExists:
    """Scenario 8: verify the context-updater skill exists and documents
    the CONTEXT_UPDATE format agents must follow."""

    def test_skill_loaded_correctly(self):
        skill_file = SKILLS_DIR / "context-updater" / "SKILL.md"

        assert skill_file.exists(), (
            f"Skill file not found at {skill_file}. "
            "This file must be created as part of the context enrichment feature."
        )

        content = skill_file.read_text()

        # Must document the CONTEXT_UPDATE format
        assert "CONTEXT_UPDATE" in content, (
            "SKILL.md must document the CONTEXT_UPDATE format "
            "that agents use to emit context updates."
        )


# ============================================================================
# Scenario 9: LLM-realistic output with markdown code fences
# ============================================================================

class TestLLMRealisticOutput:
    """Scenario 9: LLMs wrap CONTEXT_UPDATE JSON in markdown code fences."""

    def test_markdown_json_fence_enrichment(self, ctx_db):
        """CONTEXT_UPDATE with ```json fence must be parsed and applied."""
        process_agent_output = _import_process_agent_output()

        # Real LLM output format with ```json fence
        agent_output = (
            "## Investigation Complete\n\n"
            "Found 1 pod in test namespace.\n\n"
            "CONTEXT_UPDATE:\n"
            "```json\n"
            "{\n"
            '  "contract": "cluster_details",\n'
            '  "payload": {\n'
            '    "cluster_name": "oci-pos-dev-cluster-01",\n'
            '    "namespaces_inspected": {\n'
            '      "test": {\n'
            '        "pod_count": 1\n'
            "      }\n"
            "    }\n"
            "  }\n"
            "}\n"
            "```\n\n"
            "```agent_contract_handoff\n"
            "{\n"
            '  "agent_status": {\n'
            '    "plan_status": "COMPLETE",\n'
            '    "agent_id": "cloud-troubleshooter",\n'
            '    "pending_steps": [],\n'
            '    "next_action": "done"\n'
            "  },\n"
            '  "evidence_report": {\n'
            '    "patterns_checked": [],\n'
            '    "files_checked": [],\n'
            '    "commands_run": [],\n'
            '    "key_outputs": [],\n'
            '    "verbatim_outputs": [],\n'
            '    "cross_layer_impacts": [],\n'
            '    "open_gaps": []\n'
            "  },\n"
            '  "consolidation_report": null\n'
            "}\n"
            "```\n"
        )

        result = process_agent_output(
            agent_output,
            _build_task_info("cloud-troubleshooter", ctx_db),
        )

        assert result["updated"] is True, (
            "CONTEXT_UPDATE with ```json fence must be parsed — "
            "this is the actual format LLMs produce"
        )
        assert result["contract"] == "cluster_details"

        stored = read_contract(ctx_db, "test-ws", "cluster_details")
        assert stored["cluster_name"] == "oci-pos-dev-cluster-01"
        assert stored["namespaces_inspected"]["test"]["pod_count"] == 1

    def test_markdown_plain_fence_enrichment(self, ctx_db):
        """CONTEXT_UPDATE with plain ``` fence must also be handled."""
        process_agent_output = _import_process_agent_output()

        agent_output = (
            "CONTEXT_UPDATE:\n"
            "```\n"
            '{"contract": "cluster_details", "payload": {"status": "RUNNING"}}\n'
            "```\n"
        )

        result = process_agent_output(
            agent_output,
            _build_task_info("cloud-troubleshooter", ctx_db),
        )

        assert result["updated"] is True, (
            "CONTEXT_UPDATE with plain ``` fence must be parsed"
        )

        stored = read_contract(ctx_db, "test-ws", "cluster_details")
        assert stored["status"] == "RUNNING"
