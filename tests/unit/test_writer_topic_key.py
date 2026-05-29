"""Tests for the topic_key field persistence in gaia.store.writer.upsert_app.

This file replaces coverage previously provided by
tests/unit/test_context_writer_topic_key.py, which exercised the deprecated
{table, rows} context-update schema. The topic_key feature lives in
gaia.store.writer.upsert_app and is exercised here directly, not through
the update_contracts pipeline.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch) -> Path:
    """Provide a fresh DB with the schema bootstrapped via gaia.store.writer."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    from gaia.store.writer import _connect

    db = db_path()
    con = _connect(db)
    # Grant the test agent write permission on apps.
    con.execute(
        "INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) "
        "VALUES ('apps', 'developer', 1)"
    )
    con.commit()
    con.close()
    return db


def _read_topic_key(db: Path, name: str) -> str | None:
    con = sqlite3.connect(str(db))
    row = con.execute(
        "SELECT topic_key FROM apps WHERE name = ?", (name,)
    ).fetchone()
    con.close()
    return row[0] if row else None


def test_topic_key_persisted_when_supplied(tmp_db: Path):
    """upsert_app(..., topic_key='X') writes 'X' to the apps.topic_key column."""
    from gaia.store import upsert_app

    res = upsert_app(
        workspace="me",
        project="r1",
        name="app-with-topic",
        fields={"kind": "service"},
        agent="developer",
        topic_key="scope-x",
        db_path=tmp_db,
    )
    assert res == {"status": "applied"}, res
    assert _read_topic_key(tmp_db, "app-with-topic") == "scope-x"


def test_topic_key_null_when_omitted(tmp_db: Path):
    """upsert_app without topic_key leaves the column NULL."""
    from gaia.store import upsert_app

    res = upsert_app(
        workspace="me",
        project="r1",
        name="app-without-topic",
        fields={"kind": "service"},
        agent="developer",
        db_path=tmp_db,
    )
    assert res == {"status": "applied"}, res
    assert _read_topic_key(tmp_db, "app-without-topic") is None


def test_topic_key_second_upsert_wins(tmp_db: Path):
    """A second upsert with a different topic_key replaces the first value."""
    from gaia.store import upsert_app

    upsert_app(
        workspace="me", project="r1", name="app-overwrite",
        fields={"kind": "service"}, agent="developer",
        topic_key="first", db_path=tmp_db,
    )
    assert _read_topic_key(tmp_db, "app-overwrite") == "first"

    upsert_app(
        workspace="me", project="r1", name="app-overwrite",
        fields={"kind": "service"}, agent="developer",
        topic_key="second", db_path=tmp_db,
    )
    assert _read_topic_key(tmp_db, "app-overwrite") == "second"
