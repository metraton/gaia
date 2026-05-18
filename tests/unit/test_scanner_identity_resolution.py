"""
AC-6: scanners call store with `identity` resolved from the git remote of
the scanned project (cross-ref with B0 ``gaia.project.current()``).
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


def test_upsert_project_uses_git_remote_identity(tmp_db, tmp_path, monkeypatch):
    """populate_project passes identity derived from gaia.project.current()
    (the git remote of project_path) into store.upsert_project via the workspaces
    row. The workspaces.identity column captures the canonical form.
    """
    from gaia.store.writer import _connect

    # Grant developer write on projects
    con = _connect(tmp_db)
    _grant(con, "projects", "developer")
    con.close()

    # Build a fake project with an "Application" marker and a .git dir so
    # _resolve_identity treats it as a git-bearing workspace and calls current().
    fake_repo = tmp_path / "fake-repo"
    fake_repo.mkdir()
    (fake_repo / "package.json").write_text("{}")
    (fake_repo / ".git").mkdir()

    # Monkeypatch gaia.project.current to return a deterministic identity.
    expected_identity = "github.com/metraton/fake-repo"
    import gaia.project as project_mod
    monkeypatch.setattr(project_mod, "current", lambda cwd=None: expected_identity)

    # Also monkeypatch the writer's late-binding import resolver.
    # gaia.store.writer._resolve_identity imports gaia.project at call time;
    # patching gaia.project.current is sufficient.

    from tools.scan.store_populator import populate_project

    res = populate_project(
        workspace="my-workspace",
        project_path=fake_repo,
        agent="developer",
        db_path=tmp_db,
    )

    assert res["applied"] == 1, f"upsert_project not applied: {res}"
    assert res["identity"] == expected_identity
    assert res["name"] == "fake-repo"
    assert res["role"] == "application"

    # Check the workspaces.identity row carries the resolved identity.
    con = _connect(tmp_db)
    row = con.execute(
        "SELECT identity FROM workspaces WHERE name = ?",
        ("my-workspace",),
    ).fetchone()
    con.close()
    assert row is not None
    assert row["identity"] == expected_identity


def test_upsert_project_falls_back_to_path_basename_when_no_git(
    tmp_db, tmp_path, monkeypatch
):
    """When gaia.project.current() returns the directory basename (no git
    remote), populate_project records that as the identity."""
    from gaia.store.writer import _connect

    con = _connect(tmp_db)
    _grant(con, "projects", "developer")
    con.close()

    fake_repo = tmp_path / "no-remote-repo"
    fake_repo.mkdir()
    (fake_repo / "pyproject.toml").write_text("[tool.poetry]\nname = \"x\"\n")

    # Real fall-back path: gaia.project.current returns lowercase basename
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
    assert res["identity"] == "no-remote-repo"
