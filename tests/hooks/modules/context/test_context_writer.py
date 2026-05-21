"""
Tests for hooks.modules.context.context_writer.

Validates the contract-based CONTEXT_UPDATE flow:
  1. Parse: extracts {contract, payload} blocks from agent output
  2. Validate: enforces agent_contract_permissions (contract-scoped, per cloud_scope)
  3. Apply: upserts to project_context_contracts in ~/.gaia/gaia.db
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from hooks.modules.context.context_writer import (
    _permissions_cache,
    apply_update,
    parse_context_update,
    process_agent_output,
    validate_permission,
)


# ---------------------------------------------------------------------------
# Schema bootstrap helper
# ---------------------------------------------------------------------------

def _bootstrap_schema(db_path: Path) -> None:
    """Create the minimal schema this module reads/writes against."""
    con = sqlite3.connect(str(db_path))
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS workspaces (
            name        TEXT PRIMARY KEY,
            identity    TEXT NOT NULL,
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS project_context_contracts (
            workspace     TEXT NOT NULL,
            contract_name TEXT NOT NULL,
            payload       TEXT NOT NULL,
            metadata      TEXT,
            updated_at    TEXT,
            PRIMARY KEY (workspace, contract_name),
            FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS agent_contract_permissions (
            agent_name    TEXT NOT NULL,
            contract_name TEXT NOT NULL,
            can_read      INTEGER NOT NULL DEFAULT 0,
            can_write     INTEGER NOT NULL DEFAULT 0,
            cloud_scope   TEXT,
            PRIMARY KEY (agent_name, contract_name, cloud_scope)
        );
        """
    )
    con.commit()
    con.close()


def _seed_permission(
    db_path: Path,
    agent_name: str,
    contract_name: str,
    can_write: int,
    cloud_scope: str | None = None,
) -> None:
    con = sqlite3.connect(str(db_path))
    con.execute(
        """
        INSERT OR REPLACE INTO agent_contract_permissions
            (agent_name, contract_name, can_read, can_write, cloud_scope)
        VALUES (?, ?, 1, ?, ?)
        """,
        (agent_name, contract_name, can_write, cloud_scope),
    )
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    """Provide a fresh DB with the contract tables bootstrapped."""
    db = tmp_path / "gaia.db"
    _bootstrap_schema(db)
    return db


@pytest.fixture(autouse=True)
def _clear_cache():
    """Permissions cache must not leak between tests."""
    _permissions_cache.clear()
    yield
    _permissions_cache.clear()


# ---------------------------------------------------------------------------
# 1. parse_context_update
# ---------------------------------------------------------------------------

class TestParseContextUpdate:
    def test_valid_block(self):
        agent_output = (
            "Investigation complete.\n"
            "CONTEXT_UPDATE:\n"
            '{"contract": "application_services", "payload": {"name": "api"}}'
        )
        result = parse_context_update(agent_output)
        assert result == {
            "contract": "application_services",
            "payload": {"name": "api"},
        }

    def test_no_marker(self):
        assert parse_context_update("No marker here.") is None

    def test_malformed_json(self):
        agent_output = "CONTEXT_UPDATE:\n{not valid json"
        assert parse_context_update(agent_output) is None

    def test_non_dict_root(self):
        agent_output = "CONTEXT_UPDATE:\n[1, 2, 3]"
        assert parse_context_update(agent_output) is None

    def test_missing_contract_key(self):
        agent_output = (
            "CONTEXT_UPDATE:\n"
            '{"payload": {"foo": "bar"}}'
        )
        assert parse_context_update(agent_output) is None

    def test_missing_payload_key(self):
        agent_output = (
            "CONTEXT_UPDATE:\n"
            '{"contract": "stack"}'
        )
        assert parse_context_update(agent_output) is None

    def test_json_fenced(self):
        agent_output = (
            "CONTEXT_UPDATE:\n"
            "```json\n"
            '{"contract": "stack", "payload": {"languages": ["python"]}}\n'
            "```"
        )
        result = parse_context_update(agent_output)
        assert result is not None
        assert result["contract"] == "stack"
        assert result["payload"]["languages"] == ["python"]

    def test_plain_fenced(self):
        agent_output = (
            "CONTEXT_UPDATE:\n"
            "```\n"
            '{"contract": "stack", "payload": {"languages": []}}\n'
            "```"
        )
        result = parse_context_update(agent_output)
        assert result is not None
        assert result["contract"] == "stack"


# ---------------------------------------------------------------------------
# 2. validate_permission
# ---------------------------------------------------------------------------

class TestValidatePermission:
    def test_allowed_when_can_write(self, tmp_db: Path):
        """Agent with can_write=1 for the contract is allowed."""
        _seed_permission(tmp_db, "developer", "application_services", can_write=1)

        allowed, msg = validate_permission(
            {"contract": "application_services", "payload": {}},
            "developer",
            db_path=tmp_db,
        )
        assert allowed is True
        assert msg == ""

    def test_blocked_when_can_write_zero(self, tmp_db: Path):
        """Agent with can_write=0 is blocked with a deterministic message."""
        _seed_permission(tmp_db, "cloud-troubleshooter", "application_services", can_write=0)
        _seed_permission(tmp_db, "cloud-troubleshooter", "cluster_details", can_write=0)

        allowed, msg = validate_permission(
            {"contract": "application_services", "payload": {}},
            "cloud-troubleshooter",
            db_path=tmp_db,
        )
        assert allowed is False
        assert "cloud-troubleshooter" in msg
        assert "application_services" in msg
        # When the agent has no can_write=1 rows, the writable list is empty.
        assert "(none)" in msg

    def test_blocked_when_agent_unknown(self, tmp_db: Path):
        """Agent with no row at all gets the same rejection treatment."""
        allowed, msg = validate_permission(
            {"contract": "stack", "payload": {}},
            "nonexistent-agent",
            db_path=tmp_db,
        )
        assert allowed is False
        assert "nonexistent-agent" in msg
        assert "stack" in msg

    def test_blocked_when_contract_unknown_for_agent(self, tmp_db: Path):
        """An agent writing to a contract it has no row for is rejected, and
        the message lists the contracts it CAN write."""
        _seed_permission(tmp_db, "developer", "application_services", can_write=1)

        allowed, msg = validate_permission(
            {"contract": "infrastructure", "payload": {}},
            "developer",
            db_path=tmp_db,
        )
        assert allowed is False
        assert "developer" in msg
        assert "infrastructure" in msg
        assert "application_services" in msg  # listed as writable

    def test_cloud_scope_null_is_permissive(self, tmp_db: Path):
        """A permission row with cloud_scope=NULL matches every caller scope."""
        _seed_permission(
            tmp_db, "developer", "application_services",
            can_write=1, cloud_scope=None,
        )

        for scope in (None, "gcp", "aws"):
            allowed, msg = validate_permission(
                {"contract": "application_services", "payload": {}},
                "developer",
                cloud_scope=scope,
                db_path=tmp_db,
            )
            assert allowed is True, f"NULL scope should match {scope!r}; got msg={msg}"

    def test_cloud_scope_specific_is_enforced(self, tmp_db: Path):
        """A permission row with cloud_scope='gcp' must NOT match cloud_scope='aws'."""
        _seed_permission(
            tmp_db, "developer", "application_services",
            can_write=1, cloud_scope="gcp",
        )

        # Same scope: allowed.
        allowed_gcp, _ = validate_permission(
            {"contract": "application_services", "payload": {}},
            "developer",
            cloud_scope="gcp",
            db_path=tmp_db,
        )
        assert allowed_gcp is True

        # Mismatched scope: rejected.
        allowed_aws, msg = validate_permission(
            {"contract": "application_services", "payload": {}},
            "developer",
            cloud_scope="aws",
            db_path=tmp_db,
        )
        assert allowed_aws is False
        assert "application_services" in msg


# ---------------------------------------------------------------------------
# 3. apply_update
# ---------------------------------------------------------------------------

class TestApplyUpdate:
    def test_inserts_new_row(self, tmp_db: Path):
        update = {"contract": "stack", "payload": {"languages": ["python"]}}
        audit = apply_update(update, "developer", workspace="me", db_path=tmp_db)

        assert audit["success"] is True
        assert audit["contract"] == "stack"
        assert audit["workspace"] == "me"

        con = sqlite3.connect(str(tmp_db))
        row = con.execute(
            "SELECT workspace, contract_name, payload FROM project_context_contracts "
            "WHERE workspace='me' AND contract_name='stack'"
        ).fetchone()
        con.close()
        assert row is not None
        assert row[0] == "me"
        assert row[1] == "stack"
        assert json.loads(row[2]) == {"languages": ["python"]}

    def test_upsert_is_idempotent(self, tmp_db: Path):
        """A second apply for (workspace, contract) updates payload, no duplicate row."""
        first = {"contract": "stack", "payload": {"languages": ["python"]}}
        second = {"contract": "stack", "payload": {"languages": ["python", "node"]}}

        apply_update(first, "developer", workspace="me", db_path=tmp_db)
        apply_update(second, "developer", workspace="me", db_path=tmp_db)

        con = sqlite3.connect(str(tmp_db))
        rows = con.execute(
            "SELECT payload FROM project_context_contracts "
            "WHERE workspace='me' AND contract_name='stack'"
        ).fetchall()
        con.close()
        assert len(rows) == 1, "upsert must not create duplicate rows"
        assert json.loads(rows[0][0]) == {"languages": ["python", "node"]}

    def test_db_missing_returns_error(self, tmp_path: Path):
        missing_db = tmp_path / "does-not-exist.db"
        audit = apply_update(
            {"contract": "stack", "payload": {}},
            "developer",
            workspace="me",
            db_path=missing_db,
        )
        assert audit["success"] is False
        assert "gaia.db not found" in audit["error"]


# ---------------------------------------------------------------------------
# 4. process_agent_output -- end-to-end
# ---------------------------------------------------------------------------

class TestProcessAgentOutput:
    def test_happy_path_allowed_agent_writes(self, tmp_db: Path):
        """developer with can_write=1 on application_services -> row in DB."""
        _seed_permission(tmp_db, "developer", "application_services", can_write=1)

        agent_output = (
            "Investigation complete.\n"
            "CONTEXT_UPDATE:\n"
            '{"contract": "application_services", "payload": '
            '{"services": [{"name": "api", "kind": "service"}]}}'
        )
        result = process_agent_output(
            agent_output,
            {"agent_type": "developer", "db_path": tmp_db, "workspace": "me"},
        )

        assert result["updated"] is True
        assert result["contract"] == "application_services"
        assert result["rejected"] == []
        assert result["error"] is None

        con = sqlite3.connect(str(tmp_db))
        row = con.execute(
            "SELECT payload FROM project_context_contracts "
            "WHERE workspace='me' AND contract_name='application_services'"
        ).fetchone()
        con.close()
        assert row is not None
        assert json.loads(row[0])["services"][0]["name"] == "api"

    def test_unauthorized_agent_rejected_with_clear_message(self, tmp_db: Path):
        """cloud-troubleshooter with no can_write=1 rows -> blocked, no DB row."""
        # Seed only read permissions (can_write=0) so writable set is empty.
        _seed_permission(tmp_db, "cloud-troubleshooter", "cluster_details", can_write=0)
        _seed_permission(tmp_db, "cloud-troubleshooter", "infrastructure", can_write=0)

        agent_output = (
            "CONTEXT_UPDATE:\n"
            '{"contract": "application_services", "payload": {"x": 1}}'
        )
        result = process_agent_output(
            agent_output,
            {"agent_type": "cloud-troubleshooter", "db_path": tmp_db, "workspace": "me"},
        )

        assert result["updated"] is False
        assert result["rejected"] == ["application_services"]
        assert result["error"] is not None
        assert "cloud-troubleshooter" in result["error"]
        assert "application_services" in result["error"]
        assert "(none)" in result["error"]

        # Confirm nothing was written.
        con = sqlite3.connect(str(tmp_db))
        count = con.execute(
            "SELECT COUNT(*) FROM project_context_contracts"
        ).fetchone()[0]
        con.close()
        assert count == 0

    def test_malformed_block_is_silent_noop(self, tmp_db: Path):
        """A block missing 'contract' or 'payload' parses to None -> no-op."""
        _seed_permission(tmp_db, "developer", "stack", can_write=1)

        agent_output = (
            "CONTEXT_UPDATE:\n"
            '{"section_name": {"foo": "bar"}}'  # legacy schema, no longer accepted
        )
        result = process_agent_output(
            agent_output,
            {"agent_type": "developer", "db_path": tmp_db, "workspace": "me"},
        )

        assert result["updated"] is False
        assert result["rejected"] == []
        assert result["error"] is None

    def test_no_marker_is_silent_noop(self, tmp_db: Path):
        result = process_agent_output(
            "Investigation summary, no updates emitted.",
            {"agent_type": "developer", "db_path": tmp_db, "workspace": "me"},
        )
        assert result == {
            "updated": False,
            "contract": None,
            "rejected": [],
            "error": None,
        }

    def test_workspace_defaults_to_derive_when_absent(self, tmp_db: Path):
        """When task_info has no 'workspace', _derive_workspace() is used."""
        _seed_permission(tmp_db, "developer", "stack", can_write=1)

        agent_output = (
            "CONTEXT_UPDATE:\n"
            '{"contract": "stack", "payload": {"languages": ["go"]}}'
        )
        with patch(
            "hooks.modules.context.context_writer._derive_workspace",
            return_value="derived-ws",
        ):
            result = process_agent_output(
                agent_output,
                {"agent_type": "developer", "db_path": tmp_db},  # no workspace
            )

        assert result["updated"] is True
        con = sqlite3.connect(str(tmp_db))
        row = con.execute(
            "SELECT workspace FROM project_context_contracts WHERE contract_name='stack'"
        ).fetchone()
        con.close()
        assert row[0] == "derived-ws"
