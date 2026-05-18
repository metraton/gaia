"""
Tests for gaia.store.writer.wipe_workspace.

Verifies FK CASCADE deletion: wiping a workspace removes all child rows in
projects, apps, integrations, and other workspace-scoped tables.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


def test_wipe_workspace_cascades(tmp_db):
    """wipe_workspace('me') deletes rows in projects, apps, integrations cascading
    from workspaces via FK ON DELETE CASCADE."""
    from gaia.store import upsert_project, upsert_app, wipe_workspace
    from gaia.store.writer import _connect

    # Allow developer to write to projects and apps; allow scanner to write to integrations
    con = _connect(tmp_db)
    con.executemany(
        "INSERT OR REPLACE INTO agent_permissions (table_name, agent_name, allow_write) VALUES (?, ?, 1)",
        [
            ("projects", "developer"),
            ("apps", "developer"),
            ("integrations", "scanner"),
        ],
    )
    con.commit()

    # Populate
    assert upsert_project("me", "gaia", {"role": "infra"}, agent="developer", db_path=tmp_db)["status"] == "applied"
    assert upsert_app("me", "gaia", "hello", {"kind": "service"}, agent="developer", db_path=tmp_db)["status"] == "applied"
    con.execute(
        "INSERT INTO integrations (workspace, name, kind, version) VALUES (?, ?, ?, ?)",
        ("me", "datadog", "monitoring", "7.0"),
    )
    con.commit()

    # Pre-condition: rows exist
    assert con.execute("SELECT COUNT(*) FROM projects WHERE workspace='me'").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM apps WHERE workspace='me'").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM integrations WHERE workspace='me'").fetchone()[0] == 1
    con.close()

    # Wipe
    wipe_workspace("me", db_path=tmp_db)

    # Post-condition: all rows gone
    con = _connect(tmp_db)
    assert con.execute("SELECT COUNT(*) FROM workspaces WHERE name='me'").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM projects WHERE workspace='me'").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM apps WHERE workspace='me'").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM integrations WHERE workspace='me'").fetchone()[0] == 0

    # Other workspaces would not be affected (sanity check: no cross-workspace rows existed,
    # so this just confirms the table is reachable)
    other = con.execute("SELECT COUNT(*) FROM projects WHERE workspace='other-ws'").fetchone()[0]
    assert other == 0
    con.close()


def test_wipe_idempotent(tmp_db):
    """Wiping a non-existent workspace is a no-op."""
    from gaia.store import wipe_workspace
    # Should not raise
    wipe_workspace("nonexistent", db_path=tmp_db)
