#!/usr/bin/env python3
"""
TDD integration tests for context enrichment pipeline.

Validates the full flow:
  Parsed contract with update_contracts -> process_update_contracts -> gaia.db updated

Modules under test:
  - hooks/modules/context/context_writer.py (process_update_contracts)

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

def _import_process_update_contracts():
    """Import process_update_contracts at call time so pytest can collect tests.

    Imported via the full package path so the module's ``..agents`` relative
    import (parse_update_contracts) resolves; the bare ``context_writer`` path
    breaks that relative import and silently yields zero entries.
    """
    # Clear the permissions cache so each test gets a fresh DB read.
    from hooks.modules.context import context_writer as _cw
    _cw._permissions_cache.clear()
    from hooks.modules.context.context_writer import process_update_contracts
    return process_update_contracts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_contract(contract_name: str, payload: dict) -> dict:
    """Build a parsed contract dict carrying a one-entry update_contracts clause.

    This is the live envelope shape consumed by process_update_contracts:
    ``update_contracts`` is an array of ``{contract, payload}`` objects.
    """
    return {
        "agent_status": {"plan_status": "COMPLETE"},
        "update_contracts": [
            {"contract": contract_name, "payload": payload},
        ],
    }


def first_contract(result: dict):
    """Return the single applied contract name from a process result, or None."""
    contracts = result.get("contracts") or []
    return contracts[0] if contracts else None


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
        process_update_contracts = _import_process_update_contracts()

        payload = {
            "namespaces": {
                "application": ["adm", "dev", "test"],
                "infrastructure": ["flux-system", "cert-manager"],
                "system": ["kube-system", "kube-public"]
            }
        }
        contract = make_contract("cluster_details", payload)

        result = process_update_contracts(
            contract,
            _build_task_info("cloud-troubleshooter", ctx_db),
        )

        assert result["updated"] is True
        assert first_contract(result) == "cluster_details"
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
        process_update_contracts = _import_process_update_contracts()

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
        contract = make_contract("cluster_details", new_payload)

        result = process_update_contracts(
            contract,
            _build_task_info("cloud-troubleshooter", ctx_db),
        )

        assert result["updated"] is True
        assert first_contract(result) == "cluster_details"

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
        process_update_contracts = _import_process_update_contracts()

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
        contract = make_contract("cluster_details", update_payload)

        result = process_update_contracts(
            contract,
            _build_task_info("cloud-troubleshooter", ctx_db),
        )

        assert result["updated"] is True
        assert first_contract(result) == "cluster_details"

        stored = read_contract(ctx_db, "test-ws", "cluster_details")
        assert stored is not None
        assert stored["helm_releases"][0]["chart_version"] == "0.54.0"


# ============================================================================
# Scenario 4: Permission rejection
# ============================================================================

class TestPermissionRejection:
    """Scenario 4: agent tries to write a section it has no write access to."""

    def test_permission_rejection(self, ctx_db):
        process_update_contracts = _import_process_update_contracts()

        # cloud-troubleshooter cannot write gitops_configuration
        update_payload = {
            "repo_url": "https://evil.example.com/gitops",
            "tool": "evil-tool"
        }
        contract = make_contract("gitops_configuration", update_payload)

        result = process_update_contracts(
            contract,
            _build_task_info("cloud-troubleshooter", ctx_db),
        )

        assert result["updated"] is False
        assert "gitops_configuration" in result["rejected"]

        # Contract must NOT be written to DB
        stored = read_contract(ctx_db, "test-ws", "gitops_configuration")
        assert stored is None


# ============================================================================
# Scenario 5: No update_contracts clause - backward compatibility
# ============================================================================

class TestBackwardCompatibility:
    """Scenario 5: contract dict carries no update_contracts clause."""

    def test_no_context_update_backward_compat(self, ctx_db):
        process_update_contracts = _import_process_update_contracts()

        contract = {
            "agent_status": {"plan_status": "COMPLETE"},
            "evidence_report": {"key_outputs": ["Everything healthy"]},
        }

        result = process_update_contracts(
            contract,
            _build_task_info("cloud-troubleshooter", ctx_db),
        )

        assert result["updated"] is False


# ============================================================================
# Scenario 6: Malformed update_contracts entry - graceful handling
# ============================================================================

class TestMalformedJson:
    """Scenario 6: an update_contracts entry missing required keys is skipped."""

    def test_malformed_json_graceful(self, ctx_db):
        process_update_contracts = _import_process_update_contracts()

        # Entry lacks the required 'payload' key -> parse_update_contracts skips it.
        contract = {
            "agent_status": {"plan_status": "COMPLETE"},
            "update_contracts": [
                {"contract": "cluster_details"},
            ],
        }

        # Must not raise an exception
        result = process_update_contracts(
            contract,
            _build_task_info("cloud-troubleshooter", ctx_db),
        )

        assert result["updated"] is False


# ============================================================================
# Scenario 7: Multi-section update (two update_contracts entries in one clause)
# ============================================================================

class TestMultiSectionUpdate:
    """Scenario 7: the envelope path applies N atomic entries in a single call."""

    def test_two_contract_updates(self, ctx_db):
        process_update_contracts = _import_process_update_contracts()

        contract = {
            "agent_status": {"plan_status": "COMPLETE"},
            "update_contracts": [
                {
                    "contract": "cluster_details",
                    "payload": {"namespaces": {"application": ["dev", "staging"]}},
                },
                {
                    "contract": "infrastructure_topology",
                    "payload": {"subnets": ["10.0.0.0/24", "10.0.1.0/24"]},
                },
            ],
        }

        result = process_update_contracts(
            contract, _build_task_info("cloud-troubleshooter", ctx_db)
        )

        assert result["updated"] is True
        assert "cluster_details" in result["contracts"]
        assert "infrastructure_topology" in result["contracts"]

        stored_cluster = read_contract(ctx_db, "test-ws", "cluster_details")
        stored_topology = read_contract(ctx_db, "test-ws", "infrastructure_topology")

        assert "staging" in stored_cluster["namespaces"]["application"]
        assert "10.0.0.0/24" in stored_topology["subnets"]


# ============================================================================
# Scenario 8: Skill file existence and content
# ============================================================================

class TestSkillFileExists:
    """Scenario 8: verify the agent-contract-handoff skill exists and documents
    the update_contracts clause agents use to enrich project-context.

    The retired context-updater skill was replaced by agent-contract-handoff as
    the source of truth for the update_contracts envelope clause.
    """

    def test_skill_loaded_correctly(self):
        skill_file = SKILLS_DIR / "agent-contract-handoff" / "SKILL.md"

        assert skill_file.exists(), (
            f"Skill file not found at {skill_file}. "
            "This file documents the update_contracts envelope clause."
        )

        content = skill_file.read_text()

        # Must document the update_contracts clause
        assert "update_contracts" in content, (
            "SKILL.md must document the update_contracts clause "
            "that agents use to emit context updates."
        )


# ============================================================================
# Scenario 9: Multi-entry envelope variants
# ============================================================================

class TestLLMRealisticOutput:
    """Scenario 9: nested payloads and scalar payloads travel through the
    envelope path intact."""

    def test_nested_payload_enrichment(self, ctx_db):
        """A deeply-nested payload is persisted verbatim under the contract."""
        process_update_contracts = _import_process_update_contracts()

        contract = make_contract(
            "cluster_details",
            {
                "cluster_name": "oci-pos-dev-cluster-01",
                "namespaces_inspected": {"test": {"pod_count": 1}},
            },
        )

        result = process_update_contracts(
            contract,
            _build_task_info("cloud-troubleshooter", ctx_db),
        )

        assert result["updated"] is True
        assert first_contract(result) == "cluster_details"

        stored = read_contract(ctx_db, "test-ws", "cluster_details")
        assert stored["cluster_name"] == "oci-pos-dev-cluster-01"
        assert stored["namespaces_inspected"]["test"]["pod_count"] == 1

    def test_scalar_payload_enrichment(self, ctx_db):
        """A flat scalar payload is persisted through the envelope path."""
        process_update_contracts = _import_process_update_contracts()

        contract = make_contract("cluster_details", {"status": "RUNNING"})

        result = process_update_contracts(
            contract,
            _build_task_info("cloud-troubleshooter", ctx_db),
        )

        assert result["updated"] is True

        stored = read_contract(ctx_db, "test-ws", "cluster_details")
        assert stored["status"] == "RUNNING"
