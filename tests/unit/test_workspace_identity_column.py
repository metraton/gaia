"""
Tests for the projects.identity column population.

Verifies that when upsert_repo (or any path that touches _ensure_project_row)
inserts a fresh project row, the identity column is populated from the git
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


def test_identity_populated_from_remote(tmp_db, monkeypatch):
    """When gaia.project.current() returns a normalized remote, the identity
    column is populated with that value."""
    # Mock gaia.project.current to return a known canonical form.
    import gaia.store.writer as writer_mod
    monkeypatch.setattr(
        "gaia.project.current",
        lambda *a, **kw: "github.com/metraton/foo",
    )
    # Also patch within writer module's namespace -- writer imports lazily,
    # so module-level patch is enough. Verify by triggering project insert.

    # Allow developer to write to repos for the upsert path
    from gaia.store.writer import _connect
    con = _connect(tmp_db)
    con.execute(
        "INSERT OR REPLACE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('repos', 'developer', 1)"
    )
    con.commit()
    con.close()

    from gaia.store import upsert_repo
    res = upsert_repo("foo", "main-repo", {"role": "primary"}, agent="developer", db_path=tmp_db)
    assert res["status"] == "applied"

    con = _connect(tmp_db)
    row = con.execute(
        "SELECT name, identity FROM projects WHERE name = ?",
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
        "INSERT OR REPLACE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('repos', 'developer', 1)"
    )
    con.commit()
    con.close()

    from gaia.store import upsert_repo
    res = upsert_repo("MyWorkspace", "r1", {}, agent="developer", db_path=tmp_db)
    assert res["status"] == "applied"

    con = _connect(tmp_db)
    row = con.execute(
        "SELECT name, identity FROM projects WHERE name = ?",
        ("MyWorkspace",),
    ).fetchone()
    con.close()

    assert row is not None
    # Workspace name preserved as PK (case-sensitive match), identity lowercased fallback
    assert row["name"] == "MyWorkspace"
    assert row["identity"] == "myworkspace"
