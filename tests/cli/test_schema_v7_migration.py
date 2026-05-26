"""Tests for schema v7 migration: workspaces.last_scan_at column.

Coverage:
  * test_v7_ledger_advances
      After bootstrap on a v6 DB, schema_version MAX(version) >= 7.

  * test_workspaces_has_last_scan_at_column_post_migration
      After bootstrap, pragma_table_info(workspaces) includes last_scan_at TEXT.

  * test_bootstrap_case_7_guard_probe_works
      Calling bootstrap on a DB already at v7 skips the migration (idempotent).

  * test_fresh_install_includes_last_scan_at
      Fresh bootstrap (no prior DB) results in v7 schema with last_scan_at present.

  * test_set_workspace_last_scan_at_writer
      set_workspace_last_scan_at() updates workspaces.last_scan_at in the DB.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP_SH = _REPO_ROOT / "scripts" / "bootstrap_database.sh"
_SCHEMA_SQL = _REPO_ROOT / "gaia" / "store" / "schema.sql"
_MIGRATIONS_DIR = _REPO_ROOT / "scripts" / "migrations"

# Add gaia package to path for writer import.
_GAIA_PKG = _REPO_ROOT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_bootstrap(workspace: Path, env_overrides: dict | None = None) -> subprocess.CompletedProcess:
    """Invoke bootstrap_database.sh with GAIA_DB inside the workspace."""
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


def _run_bootstrap_with_db(db_path: Path, workspace: Path) -> subprocess.CompletedProcess:
    """Invoke bootstrap with an explicit pre-existing DB path."""
    env = os.environ.copy()
    env["GAIA_DB"] = str(db_path)
    env["WORKSPACE"] = str(workspace)
    return subprocess.run(
        ["bash", str(_BOOTSTRAP_SH)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


# Minimal v6-state DB stub: workspaces without last_scan_at, ledger at v6.
_V6_SCHEMA_STUB = """
CREATE TABLE workspaces (
    name        TEXT NOT NULL PRIMARY KEY,
    identity    TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    description TEXT
);

CREATE TABLE memory (
    workspace         TEXT NOT NULL,
    name              TEXT NOT NULL,
    type              TEXT NOT NULL,
    description       TEXT,
    body              TEXT NOT NULL,
    origin_session_id TEXT,
    updated_at        TEXT,
    class             TEXT,
    status            TEXT,
    PRIMARY KEY (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE INDEX idx_memory_class_status ON memory(workspace, class, status);

CREATE TABLE memory_links (
    workspace  TEXT NOT NULL,
    src_name   TEXT NOT NULL,
    dst_name   TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('relates_to', 'supersedes', 'derived_from', 'graduated_to')),
    created_at TEXT,
    PRIMARY KEY (workspace, src_name, dst_name, kind),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE TABLE project_context_contracts (
    workspace     TEXT NOT NULL,
    contract_name TEXT NOT NULL,
    payload       TEXT NOT NULL,
    metadata      TEXT,
    updated_at    TEXT,
    PRIMARY KEY (workspace, contract_name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE TABLE agent_contract_permissions (
    agent_name    TEXT NOT NULL,
    contract_name TEXT NOT NULL,
    can_read      INTEGER NOT NULL DEFAULT 0,
    can_write     INTEGER NOT NULL DEFAULT 0,
    cloud_scope   TEXT,
    PRIMARY KEY (agent_name, contract_name, cloud_scope)
);

INSERT INTO workspaces (name, identity) VALUES ('me', 'me');

INSERT INTO schema_version (version, applied_at, description) VALUES
    (1, '2026-01-01T00:00:00Z', 'initial schema'),
    (2, '2026-01-01T00:00:00Z', 'v2 memory widen'),
    (3, '2026-01-01T00:00:00Z', 'v3 contracts rename'),
    (4, '2026-01-01T00:00:00Z', 'v4 memory class+status+links'),
    (5, '2026-01-01T00:00:00Z', 'v5 ac+milestone status'),
    (6, '2026-01-01T00:00:00Z', 'v6 placeholder');
"""


def _build_v6_db(db_path: Path) -> None:
    """Build a minimal v6-state DB at db_path (workspaces lacks last_scan_at)."""
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(_V6_SCHEMA_STUB)
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSchemaV7Migration:

    def setup_method(self):
        if not _BOOTSTRAP_SH.is_file():
            pytest.skip(f"bootstrap script not found at {_BOOTSTRAP_SH}")
        if not _SCHEMA_SQL.is_file():
            pytest.skip(f"schema.sql not found at {_SCHEMA_SQL}")
        migration_file = _MIGRATIONS_DIR / "v6_to_v7.sql"
        if not migration_file.is_file():
            pytest.skip(f"v6_to_v7.sql migration not found at {migration_file}")

    def test_v7_ledger_advances(self):
        """After bootstrap on a v6 DB, schema_version MAX(version) >= 7."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db_path = workspace / "tmp_gaia.db"
            _build_v6_db(db_path)

            res = _run_bootstrap_with_db(db_path, workspace)
            assert res.returncode == 0, (
                f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
            )

            con = sqlite3.connect(str(db_path))
            try:
                max_version = con.execute(
                    "SELECT MAX(version) FROM schema_version"
                ).fetchone()[0]
            finally:
                con.close()

            assert max_version >= 7, (
                f"expected schema_version MAX>=7 after v6->v7 migration, got {max_version}"
            )

    def test_workspaces_has_last_scan_at_column_post_migration(self):
        """After v6->v7 migration, workspaces has last_scan_at TEXT column."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db_path = workspace / "tmp_gaia.db"
            _build_v6_db(db_path)

            res = _run_bootstrap_with_db(db_path, workspace)
            assert res.returncode == 0, (
                f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
            )

            con = sqlite3.connect(str(db_path))
            try:
                col_info = {
                    row[1]: row[2]
                    for row in con.execute("PRAGMA table_info(workspaces)")
                }
            finally:
                con.close()

            assert "last_scan_at" in col_info, (
                f"workspaces.last_scan_at missing after migration; columns={list(col_info.keys())}"
            )
            assert col_info["last_scan_at"].upper() == "TEXT", (
                f"workspaces.last_scan_at expected TEXT, got {col_info['last_scan_at']!r}"
            )

    def test_bootstrap_case_7_guard_probe_works(self):
        """Bootstrap on a DB already at v7 skips the migration (idempotent)."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            # First run: fresh install reaches v7.
            res1 = _run_bootstrap(workspace)
            assert res1.returncode == 0, (
                f"first bootstrap failed:\nstdout:\n{res1.stdout}\nstderr:\n{res1.stderr}"
            )
            db_path = workspace / "tmp_gaia.db"

            # Capture ledger count after first run.
            con = sqlite3.connect(str(db_path))
            try:
                count_after_first = con.execute(
                    "SELECT COUNT(*) FROM schema_version WHERE version = 7"
                ).fetchone()[0]
            finally:
                con.close()

            assert count_after_first == 1, (
                f"expected 1 v7 ledger row after first bootstrap, got {count_after_first}"
            )

            # Second run: must be a no-op for v7.
            res2 = _run_bootstrap_with_db(db_path, workspace)
            assert res2.returncode == 0, (
                f"second bootstrap failed:\nstdout:\n{res2.stdout}\nstderr:\n{res2.stderr}"
            )

            con = sqlite3.connect(str(db_path))
            try:
                count_after_second = con.execute(
                    "SELECT COUNT(*) FROM schema_version WHERE version = 7"
                ).fetchone()[0]
            finally:
                con.close()

            assert count_after_second == 1, (
                f"second bootstrap duplicated v7 ledger row; count={count_after_second}"
            )

    def test_fresh_install_includes_last_scan_at(self):
        """Fresh bootstrap (no prior DB) produces v7 schema with last_scan_at column."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            res = _run_bootstrap(workspace)
            assert res.returncode == 0, (
                f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
            )

            db_path = workspace / "tmp_gaia.db"
            con = sqlite3.connect(str(db_path))
            try:
                col_names = {
                    row[1]
                    for row in con.execute("PRAGMA table_info(workspaces)")
                }
                max_version = con.execute(
                    "SELECT MAX(version) FROM schema_version"
                ).fetchone()[0]
            finally:
                con.close()

            assert "last_scan_at" in col_names, (
                f"fresh install: workspaces.last_scan_at missing; columns={col_names}"
            )
            assert max_version >= 7, (
                f"fresh install: expected schema_version MAX>=7, got {max_version}"
            )

    def test_set_workspace_last_scan_at_writer(self):
        """set_workspace_last_scan_at() writes the timestamp into workspaces.last_scan_at."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            res = _run_bootstrap(workspace)
            assert res.returncode == 0, (
                f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
            )

            db_path = workspace / "tmp_gaia.db"

            # Import the writer module.
            if str(_GAIA_PKG) not in sys.path:
                sys.path.insert(0, str(_GAIA_PKG))
            from gaia.store.writer import set_workspace_last_scan_at  # noqa: PLC0415

            ts = "2026-05-23T12:00:00Z"
            set_workspace_last_scan_at("me", ts, db_path=db_path)

            con = sqlite3.connect(str(db_path))
            try:
                result = con.execute(
                    "SELECT last_scan_at FROM workspaces WHERE name = 'me'"
                ).fetchone()
            finally:
                con.close()

            assert result is not None, "workspaces row for 'me' not found"
            assert result[0] == ts, (
                f"expected last_scan_at={ts!r}, got {result[0]!r}"
            )
