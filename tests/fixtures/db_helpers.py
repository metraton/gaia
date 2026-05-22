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
