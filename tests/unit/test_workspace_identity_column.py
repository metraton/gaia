"""
Tests for the workspaces.identity column population.

Verifies that when upsert_project (or any path that touches _ensure_workspace_row)
inserts a fresh workspace row, the identity column is populated from the git
remote (normalized lowercase) when available, with fallback to the workspace
name when no git remote is detectable.

M2-T7 (AC-9): the writer reads the git remote DIRECTLY
(``gaia.project._git_remote_origin`` + ``_normalize_remote``), not via
``gaia.project.current()`` (which is now PATH-based). The remote-derived
identity intent is unchanged and proven here with a REAL remote on the repo.
"""

from __future__ import annotations

import subprocess

import pytest


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


def test_identity_populated_from_remote(tmp_db, tmp_path, monkeypatch):
    """When the workspace root is a git repo with an origin remote, the
    identity column is populated with the normalized remote form -- read
    DIRECTLY by the writer (M2-T7), not via current()."""
    # Allow developer to write to projects for the upsert path
    from gaia.store.writer import _connect
    con = _connect(tmp_db)
    con.execute(
        "INSERT OR REPLACE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('projects', 'developer', 1)"
    )
    con.commit()
    con.close()

    # Create a fake workspace root as a REAL git repo with an origin remote so
    # _resolve_identity treats it as git-bearing and reads the remote directly.
    fake_workspace = tmp_path / "foo-workspace"
    fake_workspace.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=str(fake_workspace), check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:Metraton/foo.git"],
        cwd=str(fake_workspace), check=True,
    )

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


def test_identity_fallback_to_name(tmp_db):
    """When the upsert has no git-bearing workspace_path (no remote to read),
    identity falls back to the workspace name (lowercase). M2-T7: the writer
    no longer consults current(), so no monkeypatch is needed."""
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
