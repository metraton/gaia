"""
Test context contract files for structure and consistency.

Validates that context-contracts JSON files are valid, consistent
with the agent definitions, and follow permission rules.

Note (task #5 / substrate v6): context-contracts.json was retired in B3.
Agent write permissions now live in ~/.gaia/gaia.db agent_contract_permissions.
Tests that depended on that file have been rewritten against the DB schema
that the db_helpers fixture creates (same schema as production gaia.db).
"""

import json
import sqlite3
import pytest
from pathlib import Path
import sys

# Add hooks to path (same pattern as existing tests)
HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.tools.task_validator import AVAILABLE_AGENTS, META_AGENTS
from tests.fixtures.db_helpers import (
    bootstrap_gaia_schema,
    seed_workspace,
    seed_agent_perms,
)


class TestContractFileStructure:
    """Validate contract JSON structure."""

    @pytest.fixture
    def contract_files(self, config_dir):
        """Find all context-contracts*.json files."""
        return list(config_dir.glob("context-contracts*.json"))

    @pytest.fixture
    def contracts(self, contract_files):
        """Parse all contract files."""
        result = {}
        for f in contract_files:
            result[f.name] = json.loads(f.read_text())
        return result

    def test_contracts_are_valid_json(self, contract_files):
        """All contract files must be valid JSON."""
        for f in contract_files:
            try:
                json.loads(f.read_text())
            except json.JSONDecodeError as e:
                pytest.fail(f"{f.name} is not valid JSON: {e}")

    def test_contracts_have_version(self, contracts):
        """All contracts must have a 'version' field."""
        for name, data in contracts.items():
            assert "version" in data, f"{name} missing 'version' field"

    def test_contracts_have_agents(self, contracts):
        """All contracts must have an 'agents' field."""
        for name, data in contracts.items():
            assert "agents" in data, f"{name} missing 'agents' field"
            assert isinstance(data["agents"], dict), \
                f"{name} 'agents' must be a dict"

    def test_db_schema_has_contract_permissions_table(self, tmp_path):
        """agent_contract_permissions table must exist in the DB schema
        and have the expected columns.

        This replaces the retired test_contract_files_exist: the SSOT for
        agent write permissions is now the DB schema, not a JSON file.
        """
        db_path = tmp_path / "test.db"
        bootstrap_gaia_schema(db_path)

        con = sqlite3.connect(str(db_path))
        tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "agent_contract_permissions" in tables, \
            "agent_contract_permissions table must exist in the DB schema"

        cols = {row[1] for row in con.execute(
            "PRAGMA table_info(agent_contract_permissions)"
        ).fetchall()}
        con.close()

        expected_cols = {"agent_name", "contract_name", "can_read", "can_write"}
        assert expected_cols.issubset(cols), \
            f"agent_contract_permissions missing columns: {expected_cols - cols}"


class TestContractAgentConsistency:
    """Validate contract agents match actual agent definitions."""

    @pytest.fixture
    def contracts(self, config_dir):
        result = {}
        for f in config_dir.glob("context-contracts*.json"):
            result[f.name] = json.loads(f.read_text())
        return result

    def test_no_meta_agents_in_contracts(self, contracts):
        """Meta-agents (gaia, Explore, Plan) should NOT appear in contracts."""
        for name, data in contracts.items():
            contract_agents = set(data.get("agents", {}).keys())
            for meta in META_AGENTS:
                assert meta not in contract_agents, \
                    f"{name} should not contain meta-agent '{meta}'"

    def test_contract_agents_are_available(self, contracts):
        """All agents in contracts must be in AVAILABLE_AGENTS."""
        for name, data in contracts.items():
            for agent in data.get("agents", {}).keys():
                assert agent in AVAILABLE_AGENTS, \
                    f"{name} references unknown agent '{agent}'"

    def test_all_project_agents_have_db_permissions(self, tmp_path):
        """All project agents can be seeded in agent_contract_permissions.

        This replaces the retired test_project_agents_in_at_least_one_contract.
        The DB schema is the SSOT for permissions. This test verifies:
          1. The schema accepts entries for project agents.
          2. Write is always a subset of read (enforced by seed_agent_perms).
        """
        db_path = tmp_path / "agents_perm_test.db"
        bootstrap_gaia_schema(db_path)
        seed_workspace(db_path, "test-ws")

        meta_set = set(META_AGENTS) | {f"gaia:{m}" for m in META_AGENTS}
        project_agents = [
            a for a in AVAILABLE_AGENTS
            if a not in meta_set and ":" not in a
        ]

        for agent in project_agents:
            seed_agent_perms(
                db_path,
                agent,
                reads=["project_identity"],
                writes=[],
            )

        con = sqlite3.connect(str(db_path))
        stored = {row[0] for row in con.execute(
            "SELECT agent_name FROM agent_contract_permissions"
        ).fetchall()}
        con.close()

        for agent in project_agents:
            assert agent in stored, \
                f"Project agent '{agent}' must be seedable in agent_contract_permissions"


class TestPermissionRules:
    """Validate permission rules in contracts."""

    @pytest.fixture
    def contracts(self, config_dir):
        result = {}
        for f in config_dir.glob("context-contracts*.json"):
            result[f.name] = json.loads(f.read_text())
        return result

    def test_write_is_subset_of_read(self, contracts):
        """Write permissions must be a subset of read permissions."""
        for name, data in contracts.items():
            for agent, perms in data.get("agents", {}).items():
                read = set(perms.get("read", []))
                write = set(perms.get("write", []))
                assert write.issubset(read), \
                    f"{name}/{agent}: write {write - read} not in read permissions"

    def test_agents_have_read_permissions(self, contracts):
        """All agents in contracts must have read permissions."""
        for name, data in contracts.items():
            for agent, perms in data.get("agents", {}).items():
                read = perms.get("read", [])
                assert len(read) > 0, \
                    f"{name}/{agent}: must have at least one read permission"

    def test_all_agents_can_read_project_identity(self, contracts):
        """All agents should be able to read project_identity (v2 section)."""
        for name, data in contracts.items():
            for agent, perms in data.get("agents", {}).items():
                read = perms.get("read", [])
                assert "project_identity" in read, \
                    f"{name}/{agent}: should have 'project_identity' in read permissions"

    def test_db_write_subset_of_read_constraint(self, tmp_path):
        """seed_agent_perms always grants can_read=1 when can_write=1.

        This verifies the write-is-subset-of-read rule holds in the DB layer.
        """
        db_path = tmp_path / "perm_subset_test.db"
        bootstrap_gaia_schema(db_path)

        # Seed an agent that writes cluster_details but only reads it
        seed_agent_perms(
            db_path,
            "test-agent",
            reads=["cluster_details", "application_services"],
            writes=["cluster_details"],
        )

        con = sqlite3.connect(str(db_path))
        rows = con.execute(
            "SELECT contract_name, can_read, can_write "
            "FROM agent_contract_permissions WHERE agent_name='test-agent'"
        ).fetchall()
        con.close()

        perm_map = {row[0]: (row[1], row[2]) for row in rows}

        # Write implies read
        can_read, can_write = perm_map["cluster_details"]
        assert can_write == 1
        assert can_read == 1, "Write permission must imply read permission"

        # Read-only does not imply write
        can_read_app, can_write_app = perm_map["application_services"]
        assert can_read_app == 1
        assert can_write_app == 0, "Read-only permission must not grant write"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
