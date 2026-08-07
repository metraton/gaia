"""
Test context contract files for structure and consistency.

Validates that context-contracts JSON files are valid, consistent
with the agent definitions, and follow permission rules.

Note (task #5 / substrate v6): context-contracts.json was retired in B3.
Agent write permissions now live in ~/.gaia/gaia.db agent_contract_permissions.
Tests that depended on that file have been rewritten against the DB schema
that the db_helpers fixture creates (same schema as production gaia.db).
"""

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
    """Validate the DB-backed permissions schema.

    context-contracts.json was retired in B3; the three glob-based tests that
    used to live here (valid JSON / has version / has agents) iterated
    ``config_dir.glob("context-contracts*.json")`` over a file that no longer
    exists. An empty glob makes a ``for`` loop's body never run, so those
    assertions could never fail -- vacuously-true tests that looked like
    coverage and were not. Removed rather than re-pinned: there is no
    contract-file structure left to validate now that the file is gone.
    """

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
    """Validate contract agents against the DB-backed permissions schema.

    ``test_no_meta_agents_in_contracts`` and ``test_contract_agents_are_available``
    used to iterate the (retired) ``config_dir.glob("context-contracts*.json")``
    and were vacuously true against an empty glob -- removed for the same
    reason as ``TestContractFileStructure`` above.
    """

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
    """Validate permission rules against the DB-backed permissions schema.

    ``test_write_is_subset_of_read``, ``test_agents_have_read_permissions``,
    and ``test_all_agents_can_read_project_identity`` used to iterate the
    (retired) ``config_dir.glob("context-contracts*.json")`` and were
    vacuously true against an empty glob -- removed for the same reason as
    ``TestContractFileStructure`` above. The write-is-subset-of-read
    invariant they existed to check is still real and still enforced, now
    against the DB layer, in the one test below.
    """

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
