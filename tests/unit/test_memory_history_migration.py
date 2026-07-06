"""Regression tests for scan-v2 SV3 memory-resilience schema (v25 -> v26).

Covers the DDL the migration introduces:

  * `memory.deleted_at` (nullable TEXT, defaults NULL) -- tombstone marker.
  * `memory_history` table + `trg_memory_history` trigger: before/after audit
    trail for `memory` mutations, mirroring the project_history / trg_project_history
    and project_context_contracts_history / trg_pcc_history patterns.
  * project_ref backfill UPDATE (validated against a synthetic temp DB only --
    never the real DB).

Group 1 exercises the REAL schema.sql (via gaia.store.writer._connect, which
materializes it on a fresh DB) so drift between schema.sql and these tests is
impossible by construction. Group 2 applies the standalone migration file to a
synthetic v25-shaped DB to prove the in-place upgrade path (including the
project_ref backfill).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = _REPO_ROOT / "scripts" / "migrations" / "v25_to_v26.sql"


# ---------------------------------------------------------------------------
# Group 1: fresh install via the real schema.sql (gaia.store.writer._connect)
# ---------------------------------------------------------------------------

@pytest.fixture()
def fresh_db(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    from gaia.store.writer import _connect

    path = db_path()
    con = _connect(path)
    con.close()
    return path


def _columns(con: sqlite3.Connection, table: str) -> dict[str, None]:
    return {row["name"]: None for row in con.execute(f"PRAGMA table_info({table})")}


def _count(con: sqlite3.Connection, table: str) -> int:
    return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_memory_deleted_at_column_exists_and_defaults_null(fresh_db: Path) -> None:
    con = sqlite3.connect(str(fresh_db))
    con.row_factory = sqlite3.Row
    try:
        assert "deleted_at" in _columns(con, "memory")
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        con.execute(
            "INSERT INTO memory (workspace, name, type, body) "
            "VALUES ('me', 'project_x', 'project', 'b')"
        )
        con.commit()
        row = con.execute(
            "SELECT deleted_at FROM memory WHERE workspace='me' AND name='project_x'"
        ).fetchone()
        assert row["deleted_at"] is None
    finally:
        con.close()


def test_memory_history_table_and_trigger_exist(fresh_db: Path) -> None:
    con = sqlite3.connect(str(fresh_db))
    con.row_factory = sqlite3.Row
    try:
        tbl = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_history'"
        ).fetchone()
        assert tbl is not None
        trg = con.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name='trg_memory_history'"
        ).fetchone()
        assert trg is not None
    finally:
        con.close()


def test_trigger_archives_body_on_update(fresh_db: Path) -> None:
    """Vector 1 at the SQL layer: an UPDATE that changes body archives the
    previous body under before_body."""
    con = sqlite3.connect(str(fresh_db))
    con.row_factory = sqlite3.Row
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        con.execute(
            "INSERT INTO memory (workspace, name, type, body) "
            "VALUES ('me', 'project_x', 'project', 'v1 body')"
        )
        con.commit()

        con.execute(
            "UPDATE memory SET body='v2 body' WHERE workspace='me' AND name='project_x'"
        )
        con.commit()

        assert _count(con, "memory_history") == 1
        row = con.execute("SELECT * FROM memory_history").fetchone()
        assert row["before_body"] == "v1 body"
        assert row["after_body"] == "v2 body"
    finally:
        con.close()


def test_trigger_records_workspace_relocation(fresh_db: Path) -> None:
    """Vector 3 at the SQL layer: a workspace re-key records origin."""
    con = sqlite3.connect(str(fresh_db))
    con.row_factory = sqlite3.Row
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        con.execute("INSERT INTO workspaces (name) VALUES ('other')")
        con.execute(
            "INSERT INTO memory (workspace, name, type, body) "
            "VALUES ('me', 'project_x', 'project', 'b')"
        )
        con.commit()

        con.execute(
            "UPDATE memory SET workspace='other' WHERE workspace='me' AND name='project_x'"
        )
        con.commit()

        row = con.execute(
            "SELECT * FROM memory_history WHERE name='project_x'"
        ).fetchone()
        assert row["before_workspace"] == "me"
        assert row["after_workspace"] == "other"
    finally:
        con.close()


def test_trigger_records_tombstone_transition(fresh_db: Path) -> None:
    """Vector 2 at the SQL layer: stamping deleted_at lands a history row."""
    con = sqlite3.connect(str(fresh_db))
    con.row_factory = sqlite3.Row
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        con.execute(
            "INSERT INTO memory (workspace, name, type, body) "
            "VALUES ('me', 'project_x', 'project', 'b')"
        )
        con.commit()

        con.execute(
            "UPDATE memory SET deleted_at='2026-07-06T00:00:00Z' "
            "WHERE workspace='me' AND name='project_x'"
        )
        con.commit()

        row = con.execute("SELECT * FROM memory_history").fetchone()
        assert row["before_deleted_at"] is None
        assert row["after_deleted_at"] == "2026-07-06T00:00:00Z"
    finally:
        con.close()


def test_trigger_does_not_fire_on_unrelated_column_change(fresh_db: Path) -> None:
    """An UPDATE touching only updated_at / origin_session_id (not in the WHEN
    clause) must NOT insert a history row."""
    con = sqlite3.connect(str(fresh_db))
    con.row_factory = sqlite3.Row
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        con.execute(
            "INSERT INTO memory (workspace, name, type, body, updated_at) "
            "VALUES ('me', 'project_x', 'project', 'b', '2026-01-01T00:00:00Z')"
        )
        con.commit()

        con.execute(
            "UPDATE memory SET updated_at='2026-02-02T00:00:00Z' "
            "WHERE workspace='me' AND name='project_x'"
        )
        con.commit()

        assert _count(con, "memory_history") == 0
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Group 2: standalone migration file applied to a synthetic v25-shaped DB
# ---------------------------------------------------------------------------

_V25_MINIMAL_SCHEMA = """
CREATE TABLE workspaces (
    name        TEXT NOT NULL PRIMARY KEY,
    identity    TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE projects (
    workspace        TEXT NOT NULL,
    name             TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'active',
    project_identity TEXT,
    superseded_by    TEXT,
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
    project_ref       TEXT,
    PRIMARY KEY (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE TABLE schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    description TEXT
);
INSERT INTO schema_version (version, applied_at, description)
VALUES (25, '2026-01-01T00:00:00Z', 'synthetic v25 baseline');
"""


@pytest.fixture()
def v25_db(tmp_path) -> Path:
    db_path = tmp_path / "v25.db"
    con = sqlite3.connect(str(db_path))
    con.executescript(_V25_MINIMAL_SCHEMA)
    con.commit()
    con.close()
    return db_path


def _apply_migration_sql(con: sqlite3.Connection) -> None:
    sql = _MIGRATION_PATH.read_text(encoding="utf-8")
    con.executescript(sql)


def test_migration_file_exists() -> None:
    assert _MIGRATION_PATH.is_file(), f"missing {_MIGRATION_PATH}"


def test_migration_applies_cleanly_to_v25_shaped_db(v25_db: Path) -> None:
    con = sqlite3.connect(str(v25_db))
    con.row_factory = sqlite3.Row
    try:
        _apply_migration_sql(con)
        con.commit()

        assert "deleted_at" in _columns(con, "memory")
        assert con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_history'"
        ).fetchone() is not None
        assert con.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name='trg_memory_history'"
        ).fetchone() is not None
    finally:
        con.close()


def test_migration_trigger_fires_after_upgrade(v25_db: Path) -> None:
    con = sqlite3.connect(str(v25_db))
    con.row_factory = sqlite3.Row
    try:
        _apply_migration_sql(con)
        con.commit()

        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        con.execute(
            "INSERT INTO memory (workspace, name, type, body) "
            "VALUES ('me', 'project_x', 'project', 'v1')"
        )
        con.commit()
        con.execute(
            "UPDATE memory SET body='v2' WHERE workspace='me' AND name='project_x'"
        )
        con.commit()

        assert _count(con, "memory_history") == 1
    finally:
        con.close()


def test_project_ref_backfill_unambiguous(v25_db: Path) -> None:
    """A type='project' memory in a workspace with exactly one active project
    carrying a project_identity is anchored to that identity."""
    con = sqlite3.connect(str(v25_db))
    con.row_factory = sqlite3.Row
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        con.execute(
            "INSERT INTO projects (workspace, name, status, project_identity) "
            "VALUES ('me', 'gaia', 'active', 'github.com/x/gaia')"
        )
        con.execute(
            "INSERT INTO memory (workspace, name, type, body) "
            "VALUES ('me', 'project_notes', 'project', 'b')"
        )
        con.commit()

        _apply_migration_sql(con)
        con.commit()

        row = con.execute(
            "SELECT project_ref FROM memory WHERE workspace='me' AND name='project_notes'"
        ).fetchone()
        assert row["project_ref"] == "github.com/x/gaia"
    finally:
        con.close()


def test_project_ref_backfill_ambiguous_left_null(v25_db: Path) -> None:
    """A workspace with >1 active project carrying an identity is ambiguous;
    project_ref is left NULL (never guessed)."""
    con = sqlite3.connect(str(v25_db))
    con.row_factory = sqlite3.Row
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        con.execute(
            "INSERT INTO projects (workspace, name, status, project_identity) "
            "VALUES ('me', 'a', 'active', 'id-a')"
        )
        con.execute(
            "INSERT INTO projects (workspace, name, status, project_identity) "
            "VALUES ('me', 'b', 'active', 'id-b')"
        )
        con.execute(
            "INSERT INTO memory (workspace, name, type, body) "
            "VALUES ('me', 'project_notes', 'project', 'b')"
        )
        con.commit()

        _apply_migration_sql(con)
        con.commit()

        row = con.execute(
            "SELECT project_ref FROM memory WHERE workspace='me' AND name='project_notes'"
        ).fetchone()
        assert row["project_ref"] is None
    finally:
        con.close()


def test_project_ref_backfill_only_project_type(v25_db: Path) -> None:
    """Non-project memory is never anchored, even in an unambiguous workspace."""
    con = sqlite3.connect(str(v25_db))
    con.row_factory = sqlite3.Row
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        con.execute(
            "INSERT INTO projects (workspace, name, status, project_identity) "
            "VALUES ('me', 'gaia', 'active', 'id-gaia')"
        )
        con.execute(
            "INSERT INTO memory (workspace, name, type, body) "
            "VALUES ('me', 'user_pref', 'user', 'b')"
        )
        con.commit()

        _apply_migration_sql(con)
        con.commit()

        row = con.execute(
            "SELECT project_ref FROM memory WHERE workspace='me' AND name='user_pref'"
        ).fetchone()
        assert row["project_ref"] is None
    finally:
        con.close()
