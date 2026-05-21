"""Tests for schema v3 migration: rename context_contracts + add agent_contract_permissions.

Coverage:
  * test_fresh_install_creates_project_context_contracts
      After bootstrap on an empty DB, `project_context_contracts` exists and
      `context_contracts` does not.

  * test_fresh_install_creates_agent_contract_permissions
      After bootstrap, `agent_contract_permissions` table exists with the
      expected columns and the PK (agent_name, contract_name, cloud_scope).

  * test_v2_db_migrates_to_v3_preserving_rows
      Synthesize a v2-state DB (with `context_contracts` rows).  Run bootstrap.
      Assert the table is renamed, rows survive, and the old name is gone.

  * test_agent_contract_permissions_index_exists
      After bootstrap the index `idx_agent_contract_perms_agent` is present in
      sqlite_master.

  * test_bootstrap_idempotent_at_v3
      Running bootstrap twice on a v3 DB: second run is a no-op and the
      schema_version count remains at 3 (not duplicated).
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP_SH = _REPO_ROOT / "scripts" / "bootstrap_database.sh"
_SCHEMA_SQL = _REPO_ROOT / "gaia" / "store" / "schema.sql"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_bootstrap(workspace: Path, env_overrides: dict | None = None) -> subprocess.CompletedProcess:
    tmp_db = workspace / "tmp_gaia.db"
    env = os.environ.copy()
    env["GAIA_DB"] = str(tmp_db)
    env["WORKSPACE"] = str(workspace)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["bash", str(_BOOTSTRAP_SH)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


_V2_SCHEMA_STUB = """
CREATE TABLE IF NOT EXISTS workspaces (
    name     TEXT PRIMARY KEY,
    identity TEXT
);
INSERT INTO workspaces (name, identity) VALUES ('test-ws', 'test-ws');

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT,
    description TEXT
);
INSERT INTO schema_version (version, applied_at, description)
VALUES (1, '2026-01-01T00:00:00Z', 'initial schema');
INSERT INTO schema_version (version, applied_at, description)
VALUES (2, '2026-01-01T00:00:00Z', 'v2 memory widen');

CREATE TABLE IF NOT EXISTS context_contracts (
    workspace    TEXT NOT NULL,
    section_name TEXT NOT NULL,
    payload      TEXT NOT NULL,
    metadata     TEXT,
    updated_at   TEXT,
    PRIMARY KEY (workspace, section_name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_context_contracts_workspace ON context_contracts(workspace);
"""


def _build_v2_db(db_path: Path) -> list[tuple]:
    """Materialise a minimal v2-state DB and return the inserted rows."""
    rows = [
        ("test-ws", "project_identity", '{"name": "gaia"}', None, "2026-01-01T00:00:00Z"),
        ("test-ws", "stack", '{"runtime": "node"}', None, "2026-01-01T00:00:00Z"),
    ]
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(_V2_SCHEMA_STUB)
        for row in rows:
            con.execute(
                "INSERT INTO context_contracts "
                "(workspace, section_name, payload, metadata, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                row,
            )
        con.commit()
    finally:
        con.close()
    return rows


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSchemaV3:

    def setup_method(self):
        if not _BOOTSTRAP_SH.is_file():
            pytest.skip(f"bootstrap script not found at {_BOOTSTRAP_SH}")
        if not _SCHEMA_SQL.is_file():
            pytest.skip(f"schema.sql not found at {_SCHEMA_SQL}")

    def test_fresh_install_creates_project_context_contracts(self):
        """Fresh bootstrap creates project_context_contracts; context_contracts absent."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            res = _run_bootstrap(workspace)
            assert res.returncode == 0, (
                f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
            )
            db = workspace / "tmp_gaia.db"
            con = sqlite3.connect(str(db))
            try:
                tables = {
                    row[0]
                    for row in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            finally:
                con.close()

            assert "project_context_contracts" in tables
            assert "context_contracts" not in tables

    def test_fresh_install_creates_agent_contract_permissions(self):
        """Fresh bootstrap creates agent_contract_permissions with correct columns."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            res = _run_bootstrap(workspace)
            assert res.returncode == 0, (
                f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
            )
            db = workspace / "tmp_gaia.db"
            con = sqlite3.connect(str(db))
            try:
                ddl_row = con.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='table' AND name='agent_contract_permissions'"
                ).fetchone()
            finally:
                con.close()

            assert ddl_row is not None, "agent_contract_permissions table not found after bootstrap"
            ddl = ddl_row[0]
            assert "agent_name" in ddl
            assert "contract_name" in ddl
            assert "can_read" in ddl
            assert "can_write" in ddl
            assert "cloud_scope" in ddl

    def test_v2_db_migrates_to_v3_preserving_rows(self):
        """v2 DB: context_contracts renamed to project_context_contracts; rows survive."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db = workspace / "tmp_gaia.db"
            pre_rows = _build_v2_db(db)

            # Verify pre-state: old table name, old column name.
            con = sqlite3.connect(str(db))
            try:
                tables_before = {
                    row[0]
                    for row in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                old_rows = con.execute(
                    "SELECT workspace, section_name, payload FROM context_contracts ORDER BY section_name"
                ).fetchall()
            finally:
                con.close()

            assert "context_contracts" in tables_before
            assert len(old_rows) == len(pre_rows)

            res = _run_bootstrap(workspace)
            assert res.returncode == 0, (
                f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
            )

            con = sqlite3.connect(str(db))
            try:
                tables_after = {
                    row[0]
                    for row in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                new_rows = con.execute(
                    "SELECT workspace, contract_name, payload "
                    "FROM project_context_contracts ORDER BY contract_name"
                ).fetchall()
                versions = [
                    r[0]
                    for r in con.execute(
                        "SELECT version FROM schema_version ORDER BY version"
                    )
                ]
            finally:
                con.close()

            assert "project_context_contracts" in tables_after
            assert "context_contracts" not in tables_after

            # Row data should be preserved (column renamed, data intact).
            assert len(new_rows) == len(pre_rows)
            for (ws, contract_name, payload) in new_rows:
                assert ws == "test-ws"
                assert payload  # non-empty

            # Ledger should include v3.
            assert 3 in versions

    def test_agent_contract_permissions_index_exists(self):
        """After bootstrap, idx_agent_contract_perms_agent index is present."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            res = _run_bootstrap(workspace)
            assert res.returncode == 0, (
                f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
            )
            db = workspace / "tmp_gaia.db"
            con = sqlite3.connect(str(db))
            try:
                idx_row = con.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND name='idx_agent_contract_perms_agent'"
                ).fetchone()
            finally:
                con.close()

            assert idx_row is not None, "idx_agent_contract_perms_agent not found after bootstrap"

    def test_bootstrap_idempotent_at_v3(self):
        """Running bootstrap twice does not duplicate schema_version rows."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            res1 = _run_bootstrap(workspace)
            assert res1.returncode == 0, res1.stderr

            res2 = _run_bootstrap(workspace)
            assert res2.returncode == 0, res2.stderr

            db = workspace / "tmp_gaia.db"
            con = sqlite3.connect(str(db))
            try:
                count = con.execute(
                    "SELECT COUNT(*) FROM schema_version"
                ).fetchone()[0]
                max_ver = con.execute(
                    "SELECT MAX(version) FROM schema_version"
                ).fetchone()[0]
            finally:
                con.close()

            assert count == 3, f"Expected 3 schema_version rows (v1+v2+v3), got {count}"
            assert max_ver == 3
            assert "up-to-date" in res2.stdout
