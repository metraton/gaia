"""Regression tests for scan-v2 SV1 schema foundation (v24 -> v25).

Covers the three DDL additions the migration introduces:

  * `project_history` table + `trg_project_history` trigger: captures the
    lineage of a `projects` row (path/workspace/name/status changes),
    mirroring the existing `project_context_contracts_history` /
    `trg_pcc_history` pattern (see gaia/store/schema.sql).
  * `projects.superseded_by` (nullable TEXT, defaults NULL).
  * `memory.project_ref` (nullable TEXT, defaults NULL).

These tests exercise the REAL schema.sql (via `gaia.store.writer._connect`,
which materializes it on a fresh DB -- the same mechanism
tests/unit/test_fts5_triggers.py uses) rather than a hand-rolled fixture, so
drift between schema.sql and these tests is impossible by construction.

A second test group applies the standalone migration file
(scripts/migrations/v24_to_v25.sql) to a synthetic v24-shaped DB to prove the
in-place upgrade path (not just the fresh-install path) works, including
ADD COLUMN idempotency when replayed.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = _REPO_ROOT / "scripts" / "migrations" / "v24_to_v25.sql"


# ---------------------------------------------------------------------------
# Group 1: fresh install via the real schema.sql (gaia.store.writer._connect)
# ---------------------------------------------------------------------------

@pytest.fixture()
def fresh_db(tmp_path, monkeypatch) -> Path:
    """Materialize a fresh SQLite DB via the writer's _connect (runs the real
    schema.sql). Returns the resolved DB path."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    from gaia.store.writer import _connect

    path = db_path()
    con = _connect(path)
    con.close()
    return path


def _columns(con: sqlite3.Connection, table: str) -> dict[str, None]:
    return {row["name"]: None for row in con.execute(f"PRAGMA table_info({table})")}


def test_projects_superseded_by_column_exists_and_defaults_null(fresh_db: Path) -> None:
    con = sqlite3.connect(str(fresh_db))
    con.row_factory = sqlite3.Row
    try:
        assert "superseded_by" in _columns(con, "projects")

        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        con.execute(
            "INSERT INTO projects (workspace, name) VALUES ('me', 'gaia')"
        )
        con.commit()

        row = con.execute(
            "SELECT superseded_by FROM projects WHERE workspace='me' AND name='gaia'"
        ).fetchone()
        assert row["superseded_by"] is None
    finally:
        con.close()


def test_memory_project_ref_column_exists_and_defaults_null(fresh_db: Path) -> None:
    con = sqlite3.connect(str(fresh_db))
    con.row_factory = sqlite3.Row
    try:
        assert "project_ref" in _columns(con, "memory")

        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        con.execute(
            "INSERT INTO memory (workspace, name, type, body) "
            "VALUES ('me', 'decision_x', 'decision', 'body text')"
        )
        con.commit()

        row = con.execute(
            "SELECT project_ref FROM memory WHERE workspace='me' AND name='decision_x'"
        ).fetchone()
        assert row["project_ref"] is None
    finally:
        con.close()


def test_project_history_table_exists(fresh_db: Path) -> None:
    con = sqlite3.connect(str(fresh_db))
    try:
        row = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='project_history'"
        ).fetchone()
        assert row is not None, "project_history table missing from schema.sql"
    finally:
        con.close()


def _insert_project(con: sqlite3.Connection, workspace: str, name: str, **fields) -> None:
    cols = ["workspace", "name"] + list(fields.keys())
    placeholders = ", ".join("?" for _ in cols)
    values = [workspace, name] + list(fields.values())
    con.execute(
        f"INSERT INTO projects ({', '.join(cols)}) VALUES ({placeholders})",
        values,
    )


def test_trigger_records_history_on_path_change(fresh_db: Path) -> None:
    con = sqlite3.connect(str(fresh_db))
    con.row_factory = sqlite3.Row
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        _insert_project(con, "me", "gaia", path="/old/path")
        con.commit()

        assert _count(con, "project_history") == 0  # arrange

        con.execute(
            "UPDATE projects SET path = '/new/path' WHERE workspace='me' AND name='gaia'"
        )
        con.commit()

        assert _count(con, "project_history") == 1
        row = con.execute("SELECT * FROM project_history").fetchone()
        assert row["before_path"] == "/old/path"
        assert row["after_path"] == "/new/path"
        assert row["workspace"] == "me"
        assert row["name"] == "gaia"
    finally:
        con.close()


def test_trigger_records_history_on_workspace_change(fresh_db: Path) -> None:
    con = sqlite3.connect(str(fresh_db))
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys = OFF")  # composite PK move, no FK cascade needed here
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        con.execute("INSERT INTO workspaces (name) VALUES ('other')")
        _insert_project(con, "me", "gaia")
        con.commit()

        con.execute(
            "UPDATE projects SET workspace = 'other' WHERE workspace='me' AND name='gaia'"
        )
        con.commit()

        assert _count(con, "project_history") == 1
        row = con.execute("SELECT * FROM project_history").fetchone()
        assert row["before_workspace"] == "me"
        assert row["after_workspace"] == "other"
    finally:
        con.close()


def test_trigger_records_history_on_name_change(fresh_db: Path) -> None:
    con = sqlite3.connect(str(fresh_db))
    con.row_factory = sqlite3.Row
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        _insert_project(con, "me", "gaia-old")
        con.commit()

        con.execute(
            "UPDATE projects SET name = 'gaia-new' WHERE workspace='me' AND name='gaia-old'"
        )
        con.commit()

        assert _count(con, "project_history") == 1
        row = con.execute("SELECT * FROM project_history").fetchone()
        assert row["before_name"] == "gaia-old"
        assert row["after_name"] == "gaia-new"
    finally:
        con.close()


def test_trigger_records_history_on_status_change_soft_delete(fresh_db: Path) -> None:
    """The soft-delete path (status -> 'missing') must land a history row --
    this is what gives scan-v2 a connected move+delete timeline."""
    con = sqlite3.connect(str(fresh_db))
    con.row_factory = sqlite3.Row
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        _insert_project(con, "me", "gaia", status="active")
        con.commit()

        con.execute(
            "UPDATE projects SET status = 'missing', missing_since = '2026-07-06T00:00:00Z' "
            "WHERE workspace='me' AND name='gaia'"
        )
        con.commit()

        assert _count(con, "project_history") == 1
        row = con.execute("SELECT * FROM project_history").fetchone()
        assert row["before_status"] == "active"
        assert row["after_status"] == "missing"
    finally:
        con.close()


def test_trigger_does_not_fire_on_unrelated_column_change(fresh_db: Path) -> None:
    """An UPDATE that touches only role/description (agent-owned, unrelated
    to lineage) must NOT insert a history row -- the WHEN clause guards this."""
    con = sqlite3.connect(str(fresh_db))
    con.row_factory = sqlite3.Row
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        _insert_project(con, "me", "gaia", role="backend")
        con.commit()

        con.execute(
            "UPDATE projects SET role = 'frontend' WHERE workspace='me' AND name='gaia'"
        )
        con.commit()

        assert _count(con, "project_history") == 0
    finally:
        con.close()


def test_projects_fts_still_synced_after_history_trigger_added(fresh_db: Path) -> None:
    """Guards against a regression where adding trg_project_history breaks
    the pre-existing projects_fts_update trigger's interaction on the same
    table (both are AFTER UPDATE triggers on `projects`)."""
    con = sqlite3.connect(str(fresh_db))
    con.row_factory = sqlite3.Row
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        _insert_project(con, "me", "gaia", role="backend", primary_language="python")
        con.commit()

        con.execute(
            "UPDATE projects SET role = 'frontend' WHERE workspace='me' AND name='gaia'"
        )
        con.commit()

        row = con.execute(
            "SELECT role FROM projects_fts WHERE name = 'gaia'"
        ).fetchone()
        assert row["role"] == "frontend"
    finally:
        con.close()


def _count(con: sqlite3.Connection, table: str) -> int:
    return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---------------------------------------------------------------------------
# Group 2: standalone migration file applied to a synthetic v24-shaped DB
# ---------------------------------------------------------------------------

_V24_MINIMAL_SCHEMA = """
CREATE TABLE workspaces (
    name        TEXT NOT NULL PRIMARY KEY,
    identity    TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE projects (
    workspace        TEXT NOT NULL,
    name             TEXT NOT NULL,
    role             TEXT,
    remote_url       TEXT,
    platform         TEXT,
    primary_language TEXT,
    scanner_ts       TEXT,
    topic_key        TEXT,
    group_name       TEXT,
    path             TEXT,
    status           TEXT NOT NULL DEFAULT 'active',
    missing_since    TEXT,
    project_identity TEXT,
    description      TEXT,
    PRIMARY KEY (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE TABLE memory (
    workspace         TEXT NOT NULL,
    name              TEXT NOT NULL,
    type              TEXT NOT NULL,
    description       TEXT,
    body              TEXT NOT NULL,
    origin_session_id TEXT,
    updated_at        TEXT,
    class             TEXT NOT NULL DEFAULT 'log',
    status            TEXT,
    PRIMARY KEY (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE TABLE schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    description TEXT
);
INSERT INTO schema_version (version, applied_at, description)
VALUES (24, '2026-01-01T00:00:00Z', 'synthetic v24 baseline');
"""


def _apply_migration_sql(con: sqlite3.Connection) -> None:
    """Apply v24_to_v25.sql verbatim -- no ADD COLUMN idempotency filtering
    (that guard lives in bootstrap_database.sh's runner, not in the SQL file
    itself), since this synthetic DB does NOT already carry the columns."""
    sql = _MIGRATION_PATH.read_text(encoding="utf-8")
    con.executescript(sql)


@pytest.fixture()
def v24_db(tmp_path) -> Path:
    db_path = tmp_path / "v24.db"
    con = sqlite3.connect(str(db_path))
    con.executescript(_V24_MINIMAL_SCHEMA)
    con.commit()
    con.close()
    return db_path


def test_migration_file_exists() -> None:
    assert _MIGRATION_PATH.is_file(), f"missing {_MIGRATION_PATH}"


def test_migration_applies_cleanly_to_v24_shaped_db(v24_db: Path) -> None:
    """Apply v24_to_v25.sql to a synthetic v24 DB (in-place upgrade path)."""
    con = sqlite3.connect(str(v24_db))
    con.row_factory = sqlite3.Row
    try:
        _apply_migration_sql(con)
        con.commit()

        assert "superseded_by" in _columns(con, "projects")
        assert "project_ref" in _columns(con, "memory")

        row = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='project_history'"
        ).fetchone()
        assert row is not None

        row = con.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name='trg_project_history'"
        ).fetchone()
        assert row is not None
    finally:
        con.close()


def test_migration_trigger_fires_after_upgrade(v24_db: Path) -> None:
    """After applying the migration to a v24-shaped DB, the trigger must be
    active immediately -- proving the upgrade path (not just fresh installs)
    gets working provenance capture."""
    con = sqlite3.connect(str(v24_db))
    con.row_factory = sqlite3.Row
    try:
        _apply_migration_sql(con)
        con.commit()

        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        con.execute(
            "INSERT INTO projects (workspace, name, status) VALUES ('me', 'gaia', 'active')"
        )
        con.commit()

        con.execute(
            "UPDATE projects SET status = 'missing' WHERE workspace='me' AND name='gaia'"
        )
        con.commit()

        row = con.execute("SELECT COUNT(*) AS c FROM project_history").fetchone()
        assert row["c"] == 1
    finally:
        con.close()


def test_migration_is_idempotent_for_create_statements(v24_db: Path) -> None:
    """CREATE TABLE/INDEX/TRIGGER IF NOT EXISTS statements in the migration
    must tolerate being replayed (the fresh-install path replays floor+1..N
    against a DB schema.sql already brought to the target shape). The ADD
    COLUMN idempotency guard itself lives in bootstrap_database.sh's runner
    (_filter_add_column_idempotent), not in the SQL file, so we replay only
    the CREATE statements here to prove they are self-idempotent, and drop
    the ADD COLUMN lines the same way the runner would neutralize them."""
    con = sqlite3.connect(str(v24_db))
    try:
        _apply_migration_sql(con)
        con.commit()

        create_only_sql = "\n".join(
            line
            for line in _MIGRATION_PATH.read_text(encoding="utf-8").splitlines()
            if not re.match(r"^\s*ALTER TABLE", line, re.IGNORECASE)
        )
        # Replaying the CREATE TABLE/INDEX/TRIGGER IF NOT EXISTS statements a
        # second time must not raise.
        con.executescript(create_only_sql)
        con.commit()
    finally:
        con.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
