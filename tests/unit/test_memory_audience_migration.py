"""Regression tests for the v44 -> v45 `memory.audience` migration.

`memory.audience` (v45) is orthogonal to type/class/status/project_ref/
initiative: it names WHICH AGENT ROLE a curated memory row's content is FOR
('orchestrator' / 'executor' / 'any'). This is wave 1 of the kernel-injection
redesign -- adding the column and its CLI read/write surface. No row is
tagged by this change and the kernel builder is untouched (that is wave 2).

Group 1 exercises the REAL schema.sql (via gaia.store.writer._connect, which
materializes it on a fresh DB) so drift between schema.sql and these tests is
impossible by construction -- mirrors
tests/unit/test_memory_initiative_migration.py's Group 1.

Group 2 applies the REAL migration runner mechanism
(scripts/bootstrap_database.py::_filter_add_column_idempotent) to a synthetic
v44-shaped DB, proving: the column is genuinely absent before the migration,
the migration adds it with the exact CHECK + DEFAULT, pre-existing rows land
on 'any' (no behavior change from the mere act of migrating), the CHECK
rejects an out-of-enum value, and replaying the migration on an
already-migrated DB (the fresh-install path, since schema.sql already
produced the v45 shape) is a no-op -- mirrors
tests/cli/test_migration_v39_to_v40.py's structure.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = _REPO_ROOT / "scripts" / "migrations" / "v44_to_v45.sql"
_BOOTSTRAP_PY = _REPO_ROOT / "scripts" / "bootstrap_database.py"


def _columns(con: sqlite3.Connection, table: str) -> dict[str, None]:
    return {row["name"]: None for row in con.execute(f"PRAGMA table_info({table})")}


# ---------------------------------------------------------------------------
# Group 1: fresh install via the real schema.sql
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


def test_audience_column_exists_and_defaults_any(fresh_db: Path) -> None:
    con = sqlite3.connect(str(fresh_db))
    con.row_factory = sqlite3.Row
    try:
        assert "audience" in _columns(con, "memory")
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        con.execute(
            "INSERT INTO memory (workspace, name, type, body) "
            "VALUES ('me', 'atom_x', 'atom', 'b')"
        )
        con.commit()
        row = con.execute(
            "SELECT audience FROM memory WHERE workspace='me' AND name='atom_x'"
        ).fetchone()
        assert row["audience"] == "any"
    finally:
        con.close()


def test_audience_check_rejects_invalid_value_on_real_schema(fresh_db: Path) -> None:
    """The CHECK is a real SQL constraint, not merely app-level validation."""
    con = sqlite3.connect(str(fresh_db))
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        con.commit()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO memory (workspace, name, type, body, audience) "
                "VALUES ('me', 'atom_bad', 'atom', 'b', 'not_a_real_audience')"
            )
    finally:
        con.close()


def test_audience_accepts_each_enum_value_on_real_schema(fresh_db: Path) -> None:
    con = sqlite3.connect(str(fresh_db))
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        con.commit()
        for i, value in enumerate(("orchestrator", "executor", "any")):
            con.execute(
                "INSERT INTO memory (workspace, name, type, body, audience) "
                "VALUES ('me', ?, 'atom', 'b', ?)",
                (f"atom_aud_{i}", value),
            )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Group 2: standalone migration file applied via the REAL bootstrap runner
# ---------------------------------------------------------------------------

# The v44 shape of `memory`, verbatim from schema.sql at HEAD minus the
# `audience` column this migration adds -- deliberately WITHOUT it.
_V44_SCHEMA = """
CREATE TABLE workspaces (
    name        TEXT NOT NULL PRIMARY KEY,
    identity    TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE memory (
    workspace         TEXT NOT NULL,
    name              TEXT NOT NULL,
    type              TEXT NOT NULL CHECK (type IN ('project', 'user', 'feedback', 'atom', 'decision', 'negative')),
    description       TEXT,
    body              TEXT NOT NULL,
    origin_session_id TEXT,
    updated_at        TEXT,
    class             TEXT NOT NULL DEFAULT 'log' CHECK (class IN ('anchor', 'thread', 'log')),
    status            TEXT,
    project_ref       TEXT,
    deleted_at        TEXT,
    initiative        TEXT,
    PRIMARY KEY (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE TABLE schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    description TEXT
);
INSERT INTO schema_version (version, applied_at, description)
VALUES (44, '2026-01-01T00:00:00Z', 'synthetic v44 baseline');
"""


@pytest.fixture()
def v44_db(tmp_path) -> Path:
    db_path = tmp_path / "v44.db"
    con = sqlite3.connect(str(db_path))
    con.executescript(_V44_SCHEMA)
    con.commit()
    con.close()
    return db_path


def _load_bootstrap_module():
    """Import scripts/bootstrap_database.py so the migration is applied
    through the real runner's ADD COLUMN idempotency guard, exactly as
    bootstrap does -- no mock of the mechanism under test."""
    spec = importlib.util.spec_from_file_location("gaia_bootstrap_db", _BOOTSTRAP_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _apply_migration(con: sqlite3.Connection, bootstrap) -> None:
    mig_sql = bootstrap._filter_add_column_idempotent(con, _MIGRATION_PATH)
    con.executescript(f"BEGIN;\n{mig_sql}\nCOMMIT;")


def _seed(con: sqlite3.Connection, name: str) -> None:
    con.execute(
        "INSERT INTO memory (workspace, name, type, body) "
        "VALUES ('me', ?, 'atom', 'preexisting body')",
        (name,),
    )


def _audience(con: sqlite3.Connection, name: str):
    con.row_factory = sqlite3.Row
    return con.execute(
        "SELECT audience FROM memory WHERE workspace='me' AND name=?", (name,)
    ).fetchone()["audience"]


def test_migration_file_exists() -> None:
    assert _MIGRATION_PATH.is_file(), f"missing {_MIGRATION_PATH}"


def test_column_is_absent_before_the_migration(v44_db: Path) -> None:
    con = sqlite3.connect(str(v44_db))
    con.row_factory = sqlite3.Row
    try:
        assert "audience" not in _columns(con, "memory")
    finally:
        con.close()


def test_migration_adds_column_and_preexisting_rows_default_to_any(v44_db: Path) -> None:
    """Toda fila preexistente queda en 'any' -- nada cambio de comportamiento
    por el solo hecho de migrar."""
    bootstrap = _load_bootstrap_module()
    con = sqlite3.connect(str(v44_db))
    con.row_factory = sqlite3.Row
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        _seed(con, "atom_preexisting_1")
        _seed(con, "atom_preexisting_2")
        con.commit()

        _apply_migration(con, bootstrap)

        assert "audience" in _columns(con, "memory")
        assert _audience(con, "atom_preexisting_1") == "any"
        assert _audience(con, "atom_preexisting_2") == "any"
    finally:
        con.close()


def test_check_rejects_invalid_value_after_migration(v44_db: Path) -> None:
    bootstrap = _load_bootstrap_module()
    con = sqlite3.connect(str(v44_db))
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        con.commit()
        _apply_migration(con, bootstrap)

        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO memory (workspace, name, type, body, audience) "
                "VALUES ('me', 'atom_bad', 'atom', 'b', 'nonsense')"
            )
    finally:
        con.close()


def test_replay_on_an_already_migrated_db_is_a_noop(v44_db: Path) -> None:
    """The fresh-install path: schema.sql creates the column BEFORE the
    migration is replayed, so applying it again must not fail (idempotency,
    run twice)."""
    bootstrap = _load_bootstrap_module()
    con = sqlite3.connect(str(v44_db))
    con.row_factory = sqlite3.Row
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        _seed(con, "atom_stable")
        con.commit()

        _apply_migration(con, bootstrap)
        first_columns = _columns(con, "memory")

        _apply_migration(con, bootstrap)  # must not raise "duplicate column name"

        assert _columns(con, "memory") == first_columns
        assert _audience(con, "atom_stable") == "any"
    finally:
        con.close()


def test_migration_default_matches_schema_sql_default() -> None:
    """The migration's ADD COLUMN clause is byte-identical (CHECK + DEFAULT)
    to the one in schema.sql, so a migrated DB and a fresh install converge
    on the same shape."""
    from_migration = _MIGRATION_PATH.read_text(encoding="utf-8")
    assert (
        "audience TEXT CHECK (audience IN ('orchestrator', 'executor', 'any')) "
        "DEFAULT 'any'"
    ) in from_migration

    schema_sql = (_REPO_ROOT / "gaia" / "store" / "schema.sql").read_text(encoding="utf-8")
    assert (
        "audience          TEXT CHECK (audience IN ('orchestrator', 'executor', 'any')) DEFAULT 'any'"
    ) in schema_sql


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
