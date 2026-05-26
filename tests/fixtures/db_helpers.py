"""
Shared DB fixture helpers for test modules that cannot use conftest fixtures directly.

These functions are pure Python (no pytest dependencies) and can be imported
by any test module regardless of its directory. The conftest.py root fixtures
delegate to these helpers so there is one implementation, no duplication.

Usage in test functions:
    from tests.fixtures.db_helpers import (
        bootstrap_gaia_schema, seed_workspace, seed_workspace_contracts, seed_agent_perms
    )
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional


_MINIMAL_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS workspaces (
    name        TEXT NOT NULL PRIMARY KEY,
    identity    TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
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


def bootstrap_gaia_schema(db_path: Path) -> None:
    """Apply the minimal v3 schema (workspaces + context tables) to db_path.

    Idempotent: safe to call on a DB that already has the tables.
    """
    con = sqlite3.connect(str(db_path))
    con.executescript(_MINIMAL_SCHEMA)
    con.commit()
    con.close()


def seed_workspace(
    db_path: Path,
    name: str = "test-ws",
    **kwargs,
) -> str:
    """Insert a workspace row. Returns the name for chaining.

    Args:
        db_path: Path to the SQLite DB.
        name: workspace name (PRIMARY KEY).
        **kwargs: optional identity, created_at overrides.
    """
    identity = kwargs.get("identity", name)
    created_at = kwargs.get("created_at", "2026-01-01T00:00:00Z")
    con = sqlite3.connect(str(db_path))
    con.execute(
        "INSERT OR IGNORE INTO workspaces (name, identity, created_at) VALUES (?, ?, ?)",
        (name, identity, created_at),
    )
    con.commit()
    con.close()
    return name


def seed_workspace_contracts(
    db_path: Path,
    workspace: str,
    contracts: dict,
) -> None:
    """Seed project_context_contracts rows for a workspace.

    Args:
        db_path: Path to the SQLite DB.
        workspace: workspace name (must already exist in workspaces).
        contracts: dict mapping contract_name -> payload_dict.
                   JSON serialization is handled internally.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys = ON")
    for contract_name, payload_dict in contracts.items():
        con.execute(
            """
            INSERT INTO project_context_contracts
                (workspace, contract_name, payload, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(workspace, contract_name) DO UPDATE SET
                payload    = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (workspace, contract_name, json.dumps(payload_dict), now),
        )
    con.commit()
    con.close()


def seed_agent_perms(
    db_path: Path,
    agent_name: str,
    reads: list,
    writes: list,
    cloud_scope: Optional[str] = None,
) -> None:
    """Seed agent_contract_permissions rows.

    Args:
        db_path: Path to the SQLite DB.
        agent_name: name of the agent (e.g. 'cloud-troubleshooter').
        reads: list of contract_name strings the agent can read.
        writes: list of contract_name strings the agent can write.
                Write contracts automatically get can_read=1 too.
        cloud_scope: optional cloud provider scope. None means all-providers.
    """
    read_set = set(reads)
    write_set = set(writes)
    all_contracts = read_set | write_set

    con = sqlite3.connect(str(db_path))
    for contract in all_contracts:
        can_read = 1 if contract in read_set else 0
        can_write = 1 if contract in write_set else 0
        con.execute(
            """
            INSERT OR REPLACE INTO agent_contract_permissions
                (agent_name, contract_name, can_read, can_write, cloud_scope)
            VALUES (?, ?, ?, ?, ?)
            """,
            (agent_name, contract, can_read, can_write, cloud_scope),
        )
    con.commit()
    con.close()


def bootstrap_m4_schema(db_path: Path) -> None:
    """Apply M4-specific schema extensions (v9) on top of minimal schema.

    Adds M4 tables needed for agent_contract_handoff tests:
    - briefs (parent table for agent_contract_handoffs FK)
    - agent_contract_handoffs
    - agent_contract_handoff_approvals
    - approval_grants
    - project_context_contracts_history + trigger
    - updates project_context_contracts with proper column names

    Idempotent: safe to call on a DB that already has the tables.
    """
    con = sqlite3.connect(str(db_path))
    con.executescript("""
    PRAGMA foreign_keys = ON;

    -- briefs table (parent for agent_contract_handoffs FK)
    CREATE TABLE IF NOT EXISTS briefs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace    TEXT NOT NULL,
        name         TEXT NOT NULL,
        status       TEXT NOT NULL DEFAULT 'draft',
        surface_type TEXT,
        title        TEXT,
        objective    TEXT,
        context      TEXT,
        approach     TEXT,
        out_of_scope TEXT,
        topic_key    TEXT,
        created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        UNIQUE (workspace, name),
        FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_briefs_workspace ON briefs(workspace);
    CREATE INDEX IF NOT EXISTS idx_briefs_status ON briefs(status);
    CREATE INDEX IF NOT EXISTS idx_briefs_topic_key ON briefs(topic_key);

    -- Ensure project_context_contracts has correct columns (v9 schema)
    -- If it was created by bootstrap_gaia_schema with wrong names, this is a no-op (table exists)
    CREATE TABLE IF NOT EXISTS project_context_contracts (
        workspace     TEXT NOT NULL,
        contract_name TEXT NOT NULL,
        payload       TEXT NOT NULL,
        metadata      TEXT,
        updated_at    TEXT,
        PRIMARY KEY (workspace, contract_name),
        FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_project_context_contracts_workspace
        ON project_context_contracts(workspace);

    -- approval_grants table (v7 / M3, needed as FK for handoff_approvals)
    CREATE TABLE IF NOT EXISTS approval_grants (
        approval_id          TEXT PRIMARY KEY,
        agent_id             TEXT,
        session_id           TEXT,
        command_set_json     TEXT NOT NULL,
        scope                TEXT NOT NULL DEFAULT 'COMMAND_SET',
        created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        expires_at           TEXT,
        status               TEXT NOT NULL DEFAULT 'PENDING',
        consumed_indexes_json TEXT,
        consumed_at          TEXT,
        revoked_at           TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_approval_grants_agent   ON approval_grants(agent_id);
    CREATE INDEX IF NOT EXISTS idx_approval_grants_session ON approval_grants(session_id);
    CREATE INDEX IF NOT EXISTS idx_approval_grants_status  ON approval_grants(status);

    -- agent_contract_handoffs table (v9/M4)
    CREATE TABLE IF NOT EXISTS agent_contract_handoffs (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id         TEXT NOT NULL,
        session_id       TEXT,
        workspace        TEXT NOT NULL,
        brief_id         INTEGER,
        task_status      TEXT NOT NULL,
        raw_handoff_json TEXT NOT NULL,
        created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        FOREIGN KEY (workspace) REFERENCES workspaces(name),
        FOREIGN KEY (brief_id)  REFERENCES briefs(id)
    );

    CREATE INDEX IF NOT EXISTS idx_agent_contract_handoffs_workspace ON agent_contract_handoffs(workspace);
    CREATE INDEX IF NOT EXISTS idx_agent_contract_handoffs_brief     ON agent_contract_handoffs(brief_id);
    CREATE INDEX IF NOT EXISTS idx_agent_contract_handoffs_session   ON agent_contract_handoffs(session_id);

    -- agent_contract_handoff_approvals table (v9/M4)
    CREATE TABLE IF NOT EXISTS agent_contract_handoff_approvals (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        handoff_id  INTEGER NOT NULL,
        approval_id TEXT NOT NULL,
        decision    TEXT NOT NULL CHECK (decision IN ('APPROVED', 'REJECTED', 'EXPIRED', 'REVOKED')),
        decided_at  TEXT NOT NULL,
        FOREIGN KEY (handoff_id)  REFERENCES agent_contract_handoffs(id) ON DELETE CASCADE,
        FOREIGN KEY (approval_id) REFERENCES approval_grants(approval_id)
    );

    CREATE INDEX IF NOT EXISTS idx_agent_contract_handoff_approvals_handoff
        ON agent_contract_handoff_approvals(handoff_id);

    -- project_context_contracts_history + trigger (v9/M4)
    CREATE TABLE IF NOT EXISTS project_context_contracts_history (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_key        TEXT NOT NULL,
        workspace           TEXT NOT NULL,
        before_payload_json TEXT,
        after_payload_json  TEXT NOT NULL,
        changed_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        changed_by_agent    TEXT,
        FOREIGN KEY (workspace) REFERENCES workspaces(name)
    );

    CREATE INDEX IF NOT EXISTS idx_pcc_history_contract ON project_context_contracts_history(contract_key);

    CREATE TRIGGER IF NOT EXISTS trg_pcc_history
    AFTER UPDATE ON project_context_contracts
    BEGIN
        INSERT INTO project_context_contracts_history (
            contract_key, workspace, before_payload_json, after_payload_json, changed_at
        ) VALUES (
            OLD.contract_name,
            OLD.workspace,
            OLD.payload,
            NEW.payload,
            strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
        );
    END;
    """)
    con.commit()
    con.close()
