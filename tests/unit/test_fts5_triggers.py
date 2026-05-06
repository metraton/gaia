"""
Regression tests for FTS5 sync triggers (B1 M1.b).

The schema declares FTS5 mirror tables (repos_fts, apps_fts, services_fts,
briefs_fts) plus AFTER INSERT / DELETE / UPDATE triggers that keep the
mirrors aligned with their base tables. If a trigger ever drops out of
schema.sql -- or if the writer's `_connect()` stops applying the schema
on a fresh DB -- the mirror diverges silently and FTS-backed `gaia search`
commands return zero rows.

These tests exercise INSERT and UPDATE on the base tables and assert that
the mirror sees the change. They run against a fresh tmp DB so a healthy
schema produces all triggers automatically.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch) -> Path:
    """Materialize a fresh SQLite DB via the writer's _connect (which runs
    schema.sql). Returns the resolved DB path."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    from gaia.store.writer import _connect

    path = db_path()
    con = _connect(path)
    con.close()
    return path


def _count(con: sqlite3.Connection, table: str) -> int:
    return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_repos_fts_insert_trigger_fires(fresh_db: Path) -> None:
    """Inserting a row into `repos` propagates to `repos_fts`."""
    con = sqlite3.connect(str(fresh_db))
    try:
        # Base row required by FK chain: projects -> repos
        con.execute("INSERT INTO projects (name) VALUES ('me')")
        assert _count(con, "repos_fts") == 0  # arrange

        con.execute(
            "INSERT INTO repos (project, name, role, primary_language) "
            "VALUES ('me', 'gaia', 'infra', 'python')"
        )  # act
        con.commit()

        assert _count(con, "repos_fts") == 1  # assert
        row = con.execute(
            "SELECT name, role, primary_language FROM repos_fts"
        ).fetchone()
        assert row == ("gaia", "infra", "python")
    finally:
        con.close()


def test_repos_fts_update_trigger_replaces_row(fresh_db: Path) -> None:
    """Updating a column in `repos` re-indexes the `repos_fts` row."""
    con = sqlite3.connect(str(fresh_db))
    try:
        con.execute("INSERT INTO projects (name) VALUES ('me')")
        con.execute(
            "INSERT INTO repos (project, name, role) VALUES ('me', 'gaia', 'infra')"
        )
        con.commit()

        con.execute(
            "UPDATE repos SET role = 'tooling' WHERE project = 'me' AND name = 'gaia'"
        )  # act
        con.commit()

        assert _count(con, "repos_fts") == 1  # assert
        role = con.execute("SELECT role FROM repos_fts").fetchone()[0]
        assert role == "tooling"
    finally:
        con.close()


def test_apps_fts_insert_trigger_fires(fresh_db: Path) -> None:
    """Inserting a row into `apps` propagates to `apps_fts`."""
    con = sqlite3.connect(str(fresh_db))
    try:
        con.execute("INSERT INTO projects (name) VALUES ('me')")
        con.execute(
            "INSERT INTO repos (project, name) VALUES ('me', 'gaia')"
        )
        con.execute(
            "INSERT INTO apps (project, repo, name, description, topic_key) "
            "VALUES ('me', 'gaia', 'orchestrator', 'meta-agent', 'core')"
        )  # act
        con.commit()

        assert _count(con, "apps_fts") == 1  # assert
        row = con.execute(
            "SELECT name, description, topic_key FROM apps_fts"
        ).fetchone()
        assert row == ("orchestrator", "meta-agent", "core")
    finally:
        con.close()


def test_services_fts_insert_trigger_fires(fresh_db: Path) -> None:
    """Inserting a row into `services` propagates to `services_fts`."""
    con = sqlite3.connect(str(fresh_db))
    try:
        con.execute("INSERT INTO projects (name) VALUES ('me')")
        con.execute("INSERT INTO repos (project, name) VALUES ('me', 'gaia')")
        con.execute(
            "INSERT INTO services (project, repo, name, description, topic_key) "
            "VALUES ('me', 'gaia', 'pubsub', 'event bus', 'messaging')"
        )  # act
        con.commit()

        assert _count(con, "services_fts") == 1  # assert
        row = con.execute(
            "SELECT name, description, topic_key FROM services_fts"
        ).fetchone()
        assert row == ("pubsub", "event bus", "messaging")
    finally:
        con.close()


def test_all_expected_triggers_present(fresh_db: Path) -> None:
    """The 12 FTS5 triggers declared by schema.sql exist after _connect()."""
    expected = {
        "repos_fts_insert", "repos_fts_delete", "repos_fts_update",
        "apps_fts_insert", "apps_fts_delete", "apps_fts_update",
        "services_fts_insert", "services_fts_delete", "services_fts_update",
        "briefs_ai", "briefs_ad", "briefs_au",
    }
    con = sqlite3.connect(str(fresh_db))
    try:
        actual = {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).fetchall()
        }
        missing = expected - actual
        assert not missing, f"missing FTS5 triggers: {sorted(missing)}"
    finally:
        con.close()
