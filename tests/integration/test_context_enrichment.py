#!/usr/bin/env python3
"""
TDD integration tests for context enrichment pipeline.

Validates the full flow:
  Parsed contract with update_contracts -> process_update_contracts records a
  proposal and writes nothing -> apply_proposal (behind `gaia context accept`)
  writes it to gaia.db

Modules under test:
  - hooks/modules/context/context_writer.py (process_update_contracts, apply_proposal)

DB helpers (from tests.fixtures.db_helpers):
  - bootstrap_gaia_schema, seed_workspace, seed_workspace_contracts
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
    seed_workspace_contracts,
)

WORKSPACE = "test-ws"


# ---------------------------------------------------------------------------
# Lazy import: context_writer
# ---------------------------------------------------------------------------

def _import_process_update_contracts():
    """Import process_update_contracts at call time so pytest can collect tests.

    Imported via the full package path so the module's ``..agents`` relative
    import (parse_update_contracts) resolves; the bare ``context_writer`` path
    breaks that relative import and silently yields zero entries.
    """
    from hooks.modules.context.context_writer import process_update_contracts
    return process_update_contracts


def _accept(contract: dict, db_path: Path) -> dict:
    """Apply a contract's update_contracts clause the way `gaia context accept` does."""
    from hooks.modules.context.context_writer import apply_proposal
    outcome = apply_proposal(contract["update_contracts"], WORKSPACE, db_path=db_path)
    assert outcome["errors"] == []
    return outcome


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


def _build_task_info(agent_type: str, db_path: Path, workspace: str = WORKSPACE) -> dict:
    """Build the task_info dict expected by process_update_contracts (DB-backed)."""
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
    """Isolated DB with the schema and the ``test-ws`` workspace."""
    db_path = tmp_path / "gaia_test.db"
    bootstrap_gaia_schema(db_path)
    seed_workspace(db_path, WORKSPACE)
    return db_path


# ============================================================================
# Scenario 1: Fresh install - first enrichment
# ============================================================================

class TestFreshInstallFirstEnrichment:
    """Scenario 1: agent discovers namespaces; the stop proposes, accept writes."""

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

        assert result["updated"] is False
        assert result["rejected"] == []
        assert result["proposed"] == [
            {"contract": "cluster_details", "workspace": WORKSPACE, "keys": ["namespaces"]}
        ]
        assert read_contract(ctx_db, WORKSPACE, "cluster_details") is None

        _accept(contract, ctx_db)

        namespaces = read_contract(ctx_db, WORKSPACE, "cluster_details")["namespaces"]
        assert sorted(namespaces["application"]) == ["adm", "dev", "test"]
        assert sorted(namespaces["infrastructure"]) == ["cert-manager", "flux-system"]
        assert sorted(namespaces["system"]) == ["kube-public", "kube-system"]


# ============================================================================
# Scenario 2: Incremental enrichment - an accepted write merges into the section
# ============================================================================

class TestIncrementalEnrichment:
    """Scenario 2: accepting a write to a contract that already has data merges into it."""

    def test_incremental_enrichment_new_data(self, ctx_db):
        # Seed initial contract value
        seed_workspace_contracts(ctx_db, WORKSPACE, {
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

        outcome = _accept(make_contract("cluster_details", new_payload), ctx_db)

        assert outcome["applied"] == [{"contract": "cluster_details", "workspace": WORKSPACE}]

        stored = read_contract(ctx_db, WORKSPACE, "cluster_details")
        assert stored is not None
        # The mentioned list is restated wholesale...
        app_ns = stored["namespaces"]["application"]
        assert "nova-auth-dev" in app_ns
        assert "adm" in app_ns
        # ...while the sibling keys the payload never mentioned survive.
        assert sorted(stored["namespaces"]["infrastructure"]) == ["cert-manager", "flux-system"]
        assert stored["namespaces"]["system"] == ["kube-system"]

    def test_partial_write_preserves_unmentioned_sections_of_the_payload(self, ctx_db):
        """A one-key payload must not collapse a multi-entry section.

        Regression for the measured incident where a partial write to
        `project_identity` deleted every entry it did not name.
        """
        seed_workspace_contracts(ctx_db, WORKSPACE, {
            "cluster_details": {
                "cluster_name": "prod-cluster",
                "region": "us-central1",
                "namespaces": {"application": ["adm"]},
                "node_pools": [{"name": "default", "size": 3}],
            }
        })

        _accept(make_contract("cluster_details", {"kubernetes_version": "1.30.0"}), ctx_db)

        stored = read_contract(ctx_db, WORKSPACE, "cluster_details")
        assert stored == {
            "cluster_name": "prod-cluster",
            "region": "us-central1",
            "namespaces": {"application": ["adm"]},
            "node_pools": [{"name": "default", "size": 3}],
            "kubernetes_version": "1.30.0",
        }


# ============================================================================
# Scenario 3: Drift detection - version update
# ============================================================================

class TestDriftDetection:
    """Scenario 3: accepting a new payload for a section with existing data."""

    def test_drift_detection_version_update(self, ctx_db):
        seed_workspace_contracts(ctx_db, WORKSPACE, {
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

        outcome = _accept(make_contract("cluster_details", update_payload), ctx_db)

        assert outcome["applied"] == [{"contract": "cluster_details", "workspace": WORKSPACE}]

        stored = read_contract(ctx_db, WORKSPACE, "cluster_details")
        assert stored is not None
        assert stored["helm_releases"][0]["chart_version"] == "0.54.0"


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
        assert result["proposed"] == []


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
        assert result["proposed"] == []
        assert result["errors"], "the skipped entry must be named, not dropped silently"


# ============================================================================
# Scenario 7: Multi-section update (two update_contracts entries in one clause)
# ============================================================================

class TestMultiSectionUpdate:
    """Scenario 7: N entries in one clause are proposed together and accepted together."""

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

        assert result["updated"] is False
        assert [p["contract"] for p in result["proposed"]] == [
            "cluster_details", "infrastructure_topology",
        ]
        assert read_contract(ctx_db, WORKSPACE, "cluster_details") is None
        assert read_contract(ctx_db, WORKSPACE, "infrastructure_topology") is None

        outcome = _accept(contract, ctx_db)

        assert len(outcome["applied"]) == 2
        stored_cluster = read_contract(ctx_db, WORKSPACE, "cluster_details")
        stored_topology = read_contract(ctx_db, WORKSPACE, "infrastructure_topology")

        assert "staging" in stored_cluster["namespaces"]["application"]
        assert "10.0.0.0/24" in stored_topology["subnets"]


# ============================================================================
# Scenario 9: Multi-entry envelope variants
# ============================================================================

class TestLLMRealisticOutput:
    """Scenario 9: nested payloads and scalar payloads survive an accepted write intact."""

    def test_nested_payload_enrichment(self, ctx_db):
        """A deeply-nested payload is persisted verbatim under the contract."""
        contract = make_contract(
            "cluster_details",
            {
                "cluster_name": "oci-pos-dev-cluster-01",
                "namespaces_inspected": {"test": {"pod_count": 1}},
            },
        )

        _accept(contract, ctx_db)

        stored = read_contract(ctx_db, WORKSPACE, "cluster_details")
        assert stored["cluster_name"] == "oci-pos-dev-cluster-01"
        assert stored["namespaces_inspected"]["test"]["pod_count"] == 1

    def test_scalar_payload_enrichment(self, ctx_db):
        """A flat scalar payload is persisted through an accepted write."""
        _accept(make_contract("cluster_details", {"status": "RUNNING"}), ctx_db)

        stored = read_contract(ctx_db, WORKSPACE, "cluster_details")
        assert stored["status"] == "RUNNING"
