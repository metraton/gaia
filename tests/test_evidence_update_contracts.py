"""
Integration tests for M3: evidence clauses in update_contracts envelope.

Tests the full path from process_update_contracts() through to rows in the
evidence table, verifying:
  - Single clause routing
  - Multi-clause batch
  - Fail-together on malformed batches (D8)
  - Mixed evidence + project_context types processed independently
  - bypass_dispatch_guard used by hook path (GAIA_DISPATCH_AGENT=subagent still inserts)
  - Mutex field rejection (text AND artifact_path)
  - Type enum validation

All tests use a temporary SQLite DB so production ~/.gaia/gaia.db is never touched.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Make gaia package and hooks package importable
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent
_HOOKS_DIR = _REPO_ROOT / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from modules.context.context_writer import process_update_contracts
from gaia.store.writer import _connect


# ---------------------------------------------------------------------------
# DB schema for tests -- includes evidence + briefs + project_context tables
# ---------------------------------------------------------------------------

_EVIDENCE_TEST_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS workspaces (
    name        TEXT NOT NULL PRIMARY KEY,
    identity    TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS briefs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace    TEXT NOT NULL,
    name         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'draft',
    title        TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS evidence (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id       INTEGER NOT NULL,
    ac_id          TEXT NOT NULL,
    task_id        TEXT,
    type           TEXT NOT NULL CHECK (type IN ('text', 'file', 'command_output', 'url', 'screenshot')),
    text           TEXT,
    artifact_path  TEXT,
    size_bytes     INTEGER,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    created_by_agent TEXT,
    FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE
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


@pytest.fixture()
def evidence_db(tmp_path):
    """Isolated SQLite DB with evidence + briefs + context tables."""
    db_path = tmp_path / "evidence_test.db"
    con = sqlite3.connect(str(db_path))
    con.executescript(_EVIDENCE_TEST_SCHEMA)
    # Seed a workspace
    con.execute(
        "INSERT INTO workspaces (name, identity) VALUES ('test-ws', 'test-ws')"
    )
    # Seed a brief (brief_id=1)
    con.execute(
        "INSERT INTO briefs (workspace, name, title) VALUES ('test-ws', 'test-brief', 'Test Brief')"
    )
    # Seed agent_contract_permissions for test-agent to write app_services
    con.execute(
        """
        INSERT INTO agent_contract_permissions
            (agent_name, contract_name, can_read, can_write, cloud_scope)
        VALUES ('test-agent', 'app_services', 1, 1, NULL)
        """
    )
    con.commit()
    con.close()
    return db_path


def _count_evidence(db_path: Path) -> int:
    """Return the number of rows in the evidence table."""
    con = sqlite3.connect(str(db_path))
    count = con.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
    con.close()
    return count


def _get_evidence_rows(db_path: Path) -> list:
    """Return all evidence rows as dicts."""
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM evidence ORDER BY id ASC").fetchall()
    con.close()
    return [dict(r) for r in rows]


def _get_context_contract(db_path: Path, workspace: str, contract_name: str):
    """Return the project_context_contracts row or None."""
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM project_context_contracts WHERE workspace = ? AND contract_name = ?",
        (workspace, contract_name),
    ).fetchone()
    con.close()
    return dict(row) if row else None


def _make_task_info(db_path: Path, agent: str = "test-agent", workspace: str = "test-ws") -> dict:
    return {
        "agent": agent,
        "db_path": db_path,
        "workspace": workspace,
    }


# ---------------------------------------------------------------------------
# T1: Single evidence clause -> 1 row inserted
# ---------------------------------------------------------------------------

def test_evidence_clause_routes_to_store(evidence_db):
    """Single valid evidence clause inserts one row and appears in result['contracts']."""
    contract_dict = {
        "update_contracts": [
            {
                "contract": "evidence",
                "payload": {
                    "brief_id": 1,
                    "ac_id": "AC-M3",
                    "type": "command_output",
                    "text": "pytest passed: 5 passed in 0.12s",
                    "task_id": "T7",
                    "created_by_agent": "gaia-system",
                },
            }
        ]
    }
    result = process_update_contracts(contract_dict, _make_task_info(evidence_db))

    assert result["updated"] is True
    assert _count_evidence(evidence_db) == 1
    rows = _get_evidence_rows(evidence_db)
    assert rows[0]["ac_id"] == "AC-M3"
    assert rows[0]["type"] == "command_output"
    assert rows[0]["text"] == "pytest passed: 5 passed in 0.12s"
    # result["contracts"] contains "evidence:<id>"
    assert any("evidence:" in c for c in result["contracts"]), (
        f"Expected evidence:<id> in contracts, got: {result['contracts']}"
    )


# ---------------------------------------------------------------------------
# T2: Multi-clause (3+) -> 3 rows in a single call
# ---------------------------------------------------------------------------

def test_multiple_evidence_clauses(evidence_db):
    """Three valid evidence clauses produce three rows in the evidence table."""
    contract_dict = {
        "update_contracts": [
            {
                "contract": "evidence",
                "payload": {
                    "brief_id": 1,
                    "ac_id": "AC-1",
                    "type": "text",
                    "text": "evidence row 1",
                },
            },
            {
                "contract": "evidence",
                "payload": {
                    "brief_id": 1,
                    "ac_id": "AC-1",
                    "type": "command_output",
                    "text": "evidence row 2",
                },
            },
            {
                "contract": "evidence",
                "payload": {
                    "brief_id": 1,
                    "ac_id": "AC-2",
                    "type": "url",
                    "text": "https://example.com/report",
                },
            },
        ]
    }
    result = process_update_contracts(contract_dict, _make_task_info(evidence_db))

    assert result["updated"] is True
    assert _count_evidence(evidence_db) == 3
    evidence_refs = [c for c in result["contracts"] if "evidence:" in c]
    assert len(evidence_refs) == 3


# ---------------------------------------------------------------------------
# T3: Fail-together on malformed batch (1 malformed in 3 -> 0 inserted, D8)
# ---------------------------------------------------------------------------

def test_malformed_evidence_clause_rejects_all(evidence_db):
    """One malformed clause in a batch of three causes zero rows to be inserted."""
    contract_dict = {
        "update_contracts": [
            {
                "contract": "evidence",
                "payload": {
                    "brief_id": 1,
                    "ac_id": "AC-1",
                    "type": "text",
                    "text": "valid row 1",
                },
            },
            {
                "contract": "evidence",
                "payload": {
                    "brief_id": 1,
                    "ac_id": "AC-2",
                    "type": "text",
                    "text": "valid row 2",
                },
            },
            {
                "contract": "evidence",
                "payload": {
                    # brief_id is missing -- malformed
                    "ac_id": "AC-3",
                    "type": "text",
                    "text": "valid row 3 but whole batch is tainted",
                },
            },
        ]
    }
    result = process_update_contracts(contract_dict, _make_task_info(evidence_db))

    # Zero rows inserted (fail-together)
    assert _count_evidence(evidence_db) == 0, (
        f"Expected 0 rows, got {_count_evidence(evidence_db)}"
    )
    # Rejected list is non-empty
    assert len(result["rejected"]) > 0
    # Errors list is non-empty
    assert len(result["errors"]) > 0


# ---------------------------------------------------------------------------
# T4: Mixed evidence + other contract types -> evidence inserted, others unaffected
# ---------------------------------------------------------------------------

def test_mixed_contracts_evidence_and_context(evidence_db):
    """Evidence clause and project_context clause both succeed independently."""
    contract_dict = {
        "update_contracts": [
            {
                "contract": "evidence",
                "payload": {
                    "brief_id": 1,
                    "ac_id": "AC-M3",
                    "type": "text",
                    "text": "some result",
                },
            },
            {
                "contract": "app_services",
                "payload": {"service": "web-app", "version": "1.0"},
            },
        ]
    }
    result = process_update_contracts(contract_dict, _make_task_info(evidence_db))

    # Evidence row inserted
    assert _count_evidence(evidence_db) == 1
    # Context contract upserted
    ctx_row = _get_context_contract(evidence_db, "test-ws", "app_services")
    assert ctx_row is not None, "project_context_contracts row should have been upserted"
    assert result["updated"] is True


# ---------------------------------------------------------------------------
# T5: Malformed evidence does NOT block other contract types
# ---------------------------------------------------------------------------

def test_malformed_evidence_does_not_block_other_contracts(evidence_db):
    """Malformed evidence clause fails (0 rows), but project_context clause still applies."""
    contract_dict = {
        "update_contracts": [
            {
                "contract": "evidence",
                "payload": {
                    # Missing brief_id -> malformed
                    "ac_id": "AC-1",
                    "type": "text",
                    "text": "some text",
                },
            },
            {
                "contract": "app_services",
                "payload": {"service": "web-app"},
            },
        ]
    }
    result = process_update_contracts(contract_dict, _make_task_info(evidence_db))

    # Zero evidence rows (rejected)
    assert _count_evidence(evidence_db) == 0
    # But context contract still applied
    ctx_row = _get_context_contract(evidence_db, "test-ws", "app_services")
    assert ctx_row is not None, (
        "project_context_contracts should still be upserted even when evidence is rejected"
    )
    # Errors contain evidence rejection
    assert any("brief_id" in e or "evidence" in e for e in result["errors"]), (
        f"Expected evidence rejection in errors, got: {result['errors']}"
    )


# ---------------------------------------------------------------------------
# T6: bypass_dispatch_guard path -- GAIA_DISPATCH_AGENT=developer still inserts
# ---------------------------------------------------------------------------

def test_bypass_dispatch_guard_used(evidence_db):
    """process_update_contracts inserts evidence even when GAIA_DISPATCH_AGENT=developer.

    This confirms the hook path bypasses the dispatch guard correctly (D7).
    The guard would block direct insert_evidence() calls from a subagent, but
    process_update_contracts() calls insert_evidence(bypass_dispatch_guard=True).
    """
    contract_dict = {
        "update_contracts": [
            {
                "contract": "evidence",
                "payload": {
                    "brief_id": 1,
                    "ac_id": "AC-M3",
                    "type": "text",
                    "text": "hook-initiated evidence with subagent identity",
                    "created_by_agent": "developer",
                },
            }
        ]
    }
    env_with_subagent = {**os.environ, "GAIA_DISPATCH_AGENT": "developer"}

    with patch.dict(os.environ, {"GAIA_DISPATCH_AGENT": "developer"}):
        result = process_update_contracts(contract_dict, _make_task_info(evidence_db))

    # Row inserted despite subagent identity (bypass_dispatch_guard=True in hook path)
    assert _count_evidence(evidence_db) == 1, (
        f"Expected 1 row inserted via hook bypass, got {_count_evidence(evidence_db)}"
    )
    assert result["updated"] is True


# ---------------------------------------------------------------------------
# T7: Mutex field rejection (both text AND artifact_path -> rejected)
# ---------------------------------------------------------------------------

def test_evidence_text_artifact_mutex_in_batch(evidence_db):
    """Batch with text+artifact_path simultaneously set is rejected with informative error."""
    contract_dict = {
        "update_contracts": [
            {
                "contract": "evidence",
                "payload": {
                    "brief_id": 1,
                    "ac_id": "AC-1",
                    "type": "file",
                    "text": "some inline text",
                    "artifact_path": "/tmp/also_a_file.txt",  # mutex violation
                },
            }
        ]
    }
    result = process_update_contracts(contract_dict, _make_task_info(evidence_db))

    assert _count_evidence(evidence_db) == 0
    assert len(result["errors"]) > 0
    assert any(
        "mutually exclusive" in e or "text" in e and "artifact_path" in e
        for e in result["errors"]
    ), f"Expected mutex error, got: {result['errors']}"


# ---------------------------------------------------------------------------
# Bonus: neither text nor artifact_path -> rejected
# ---------------------------------------------------------------------------

def test_evidence_neither_text_nor_artifact(evidence_db):
    """Payload with neither text nor artifact_path is rejected."""
    contract_dict = {
        "update_contracts": [
            {
                "contract": "evidence",
                "payload": {
                    "brief_id": 1,
                    "ac_id": "AC-1",
                    "type": "screenshot",
                    # neither text nor artifact_path provided
                },
            }
        ]
    }
    result = process_update_contracts(contract_dict, _make_task_info(evidence_db))

    assert _count_evidence(evidence_db) == 0
    assert len(result["errors"]) > 0
