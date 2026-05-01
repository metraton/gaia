"""
test_context_writer_topic_key.py -- AC-8 verification.

Writer accepts optional `topic_key` in upserts when the table supports it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.store.writer import _connect


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    db = db_path()
    con = _connect(db)
    con.execute(
        "INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('apps', 'developer', 1)"
    )
    con.execute(
        "INSERT OR IGNORE INTO projects (name, identity, created_at) VALUES ('topic-ws', 'topic-ws', '2026-01-01T00:00:00Z')"
    )
    con.execute(
        "INSERT OR IGNORE INTO repos (project, name, scanner_ts) VALUES ('topic-ws', 'r1', '2026-01-01T00:00:00Z')"
    )
    con.commit()
    con.close()
    return db


def test_upsert_with_topic_key_accepted(tmp_db: Path):
    """The store accepts topic_key as optional row field for tables that support it."""
    from hooks.modules.context.context_writer import process_agent_output, _permissions_cache

    _permissions_cache.clear()

    agent_output = """
CONTEXT_UPDATE:
{"table": "apps", "rows": [{"repo": "r1", "name": "a-topic", "kind": "service", "topic_key": "scope-x"}]}
"""

    with patch("hooks.modules.context.context_writer._derive_workspace", return_value="topic-ws"):
        result = process_agent_output(
            agent_output,
            {"agent_type": "developer", "db_path": tmp_db},
        )

    assert result["updated"] is True, result
    assert result["rows_applied"] == 1

    con = sqlite3.connect(str(tmp_db))
    row = con.execute(
        "SELECT name, topic_key FROM apps WHERE name='a-topic'"
    ).fetchone()
    con.close()
    assert row == ("a-topic", "scope-x")
