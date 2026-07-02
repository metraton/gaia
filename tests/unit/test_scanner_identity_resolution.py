"""
Identity resolution across the populator + writer boundary (post
inference-removal).

Two distinct "identity" concepts live here, and the deterministic scan overhaul
separated them cleanly:

  * ``populate_project`` returns ``identity = workspace`` -- the caller-provided
    workspace name. The scan no longer derives a per-project workspace identity
    from the git remote (that was the removed inference layer); the deterministic
    ``--workspace`` classifier decides the workspace, and the populator records
    it verbatim.
  * ``workspaces.identity`` (the DB column) is still resolved by the WRITER
    (``gaia.store.writer._resolve_identity``), which is unchanged: when the
    workspace root is ITSELF a git repo (``workspace_path/.git`` exists), the
    column captures the git-remote-derived canonical form; otherwise it defaults
    to the workspace name. That writer behavior is a separate layer from the
    removed scan inference and remains covered below.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


def _grant(con, table, agent):
    con.execute(
        "INSERT OR REPLACE INTO agent_permissions (table_name, agent_name, allow_write) "
        "VALUES (?, ?, 1)",
        (table, agent),
    )
    con.commit()


def test_populate_project_identity_is_the_workspace(tmp_db, tmp_path, monkeypatch):
    """populate_project returns identity = the caller-provided workspace name
    (deterministic). Separately, the writer still resolves workspaces.identity
    from the git remote when the workspace root is a git repo."""
    from gaia.store.writer import _connect

    # Grant developer write on projects
    con = _connect(tmp_db)
    _grant(con, "projects", "developer")
    con.close()

    # Build a fake project with an "Application" marker and a .git dir so the
    # WRITER's _resolve_identity treats the workspace root as a git project and
    # resolves the remote into workspaces.identity.
    fake_repo = tmp_path / "fake-repo"
    fake_repo.mkdir()
    (fake_repo / "package.json").write_text("{}")
    (fake_repo / ".git").mkdir()

    # The writer resolves workspaces.identity via gaia.project.current().
    expected_ws_identity = "github.com/metraton/fake-repo"
    import gaia.project as project_mod
    monkeypatch.setattr(project_mod, "current", lambda cwd=None: expected_ws_identity)

    from tools.scan.store_populator import populate_project

    res = populate_project(
        workspace="my-workspace",
        project_path=fake_repo,
        agent="developer",
        db_path=tmp_db,
    )

    assert res["applied"] == 1, f"upsert_project not applied: {res}"
    # NEW deterministic contract: identity is the workspace, not a remote-derived
    # per-project value.
    assert res["identity"] == "my-workspace"
    assert res["name"] == "fake-repo"
    assert res["role"] == "application"

    # The WRITER still captures the git-remote identity in workspaces.identity
    # (this layer is unchanged by the scan inference-removal).
    con = _connect(tmp_db)
    row = con.execute(
        "SELECT identity FROM workspaces WHERE name = ?",
        ("my-workspace",),
    ).fetchone()
    con.close()
    assert row is not None
    assert row["identity"] == expected_ws_identity


def test_populate_project_identity_is_workspace_when_no_git(
    tmp_db, tmp_path, monkeypatch
):
    """When the workspace root is NOT a git repo, populate_project still returns
    identity = the workspace name, and the writer defaults workspaces.identity to
    the workspace name too (no remote to derive from)."""
    from gaia.store.writer import _connect

    con = _connect(tmp_db)
    _grant(con, "projects", "developer")
    con.close()

    fake_repo = tmp_path / "no-remote-repo"
    fake_repo.mkdir()
    (fake_repo / "pyproject.toml").write_text("[tool.poetry]\nname = \"x\"\n")

    # current() would return a basename, but with no .git the writer never calls
    # it -- it defaults workspaces.identity to the workspace name.
    import gaia.project as project_mod
    monkeypatch.setattr(project_mod, "current", lambda cwd=None: "no-remote-repo")

    from tools.scan.store_populator import populate_project

    res = populate_project(
        workspace="ws-fallback",
        project_path=fake_repo,
        agent="developer",
        db_path=tmp_db,
    )

    assert res["applied"] == 1
    # Deterministic: identity is the workspace name.
    assert res["identity"] == "ws-fallback"

    # Writer defaults workspaces.identity to the workspace name (no .git root).
    con = _connect(tmp_db)
    row = con.execute(
        "SELECT identity FROM workspaces WHERE name = ?",
        ("ws-fallback",),
    ).fetchone()
    con.close()
    assert row is not None
    assert row["identity"] == "ws-fallback"
