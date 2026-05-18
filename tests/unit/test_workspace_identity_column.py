"""
Tests for the workspaces.identity column population.

Verifies that when upsert_project (or any path that touches _ensure_workspace_row)
inserts a fresh workspace row, the identity column is populated from the git
remote (normalized lowercase) when available, with fallback to the workspace
name when no git remote is detectable.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


def test_identity_populated_from_remote(tmp_db, tmp_path, monkeypatch):
    """When gaia.project.current() returns a normalized remote, the identity
    column is populated with that value."""
    # Mock gaia.project.current to return a known canonical form.
    monkeypatch.setattr(
        "gaia.project.current",
        lambda *a, **kw: "github.com/metraton/foo",
    )

    # Allow developer to write to projects for the upsert path
    from gaia.store.writer import _connect
    con = _connect(tmp_db)
    con.execute(
        "INSERT OR REPLACE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('projects', 'developer', 1)"
    )
    con.commit()
    con.close()

    # Create a fake workspace root with a .git dir so _resolve_identity
    # treats it as a git-bearing workspace and calls gaia.project.current.
    fake_workspace = tmp_path / "foo-workspace"
    fake_workspace.mkdir()
    (fake_workspace / ".git").mkdir()

    from gaia.store import upsert_project
    res = upsert_project("foo", "main-project", {"role": "primary"}, agent="developer",
                         db_path=tmp_db, workspace_path=fake_workspace)
    assert res["status"] == "applied"

    con = _connect(tmp_db)
    row = con.execute(
        "SELECT name, identity FROM workspaces WHERE name = ?",
        ("foo",),
    ).fetchone()
    con.close()

    assert row is not None
    assert row["name"] == "foo"
    assert row["identity"] == "github.com/metraton/foo"


def test_identity_fallback_to_name(tmp_db, monkeypatch):
    """When gaia.project.current() returns 'global' (no remote), identity
    falls back to the workspace name (lowercase)."""
    monkeypatch.setattr("gaia.project.current", lambda *a, **kw: "global")

    from gaia.store.writer import _connect
    con = _connect(tmp_db)
    con.execute(
        "INSERT OR REPLACE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('projects', 'developer', 1)"
    )
    con.commit()
    con.close()

    from gaia.store import upsert_project
    res = upsert_project("MyWorkspace", "r1", {}, agent="developer", db_path=tmp_db)
    assert res["status"] == "applied"

    con = _connect(tmp_db)
    row = con.execute(
        "SELECT name, identity FROM workspaces WHERE name = ?",
        ("MyWorkspace",),
    ).fetchone()
    con.close()

    assert row is not None
    # Workspace name preserved as PK (case-sensitive match), identity lowercased fallback
    assert row["name"] == "MyWorkspace"
    assert row["identity"] == "myworkspace"
