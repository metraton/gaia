"""
test_context_update_workspace_derivation.py -- AC-7 verification.

Agents do NOT pass `workspace` in CONTEXT_UPDATE; the writer derives it
via gaia.project.current().
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
        "INSERT OR IGNORE INTO projects (name, identity, created_at) VALUES ('derived-ws', 'derived-ws', '2026-01-01T00:00:00Z')"
    )
    con.execute(
        "INSERT OR IGNORE INTO repos (project, name, scanner_ts) VALUES ('derived-ws', 'r1', '2026-01-01T00:00:00Z')"
    )
    con.commit()
    con.close()
    return db


def test_writer_derives_workspace_from_identity(tmp_db: Path):
    """Writer must derive workspace via gaia.project.current() when not supplied in update."""
    from hooks.modules.context.context_writer import process_agent_output, _permissions_cache

    _permissions_cache.clear()

    agent_output = """
Some preamble here.

CONTEXT_UPDATE:
{"table": "apps", "rows": [{"repo": "r1", "name": "a1", "kind": "service"}]}
"""

    with patch("hooks.modules.context.context_writer._derive_workspace", return_value="derived-ws"):
        result = process_agent_output(
            agent_output,
            {"agent_type": "developer", "db_path": tmp_db},
        )

    assert result["updated"] is True, result
    assert result["table"] == "apps"
    assert result["rows_applied"] == 1

    # Verify the row was inserted under workspace="derived-ws"
    con = sqlite3.connect(str(tmp_db))
    rows = con.execute(
        "SELECT project, repo, name FROM apps WHERE name='a1'"
    ).fetchall()
    con.close()
    assert rows == [("derived-ws", "r1", "a1")]
