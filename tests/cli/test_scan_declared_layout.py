"""gaia scan over a layout shaped like the real disk: declared workspaces nested
inside an enclosing declared workspace, groups of any depth, dot-folders, and a
second clone of one remote."""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from gaia.store.writer import declare_workspace
from tools.scan import classify

REMOTE = "git@github.com:example/auto-claude-sleep.git"


def _marker_repo(path: Path) -> Path:
    (path / ".git").mkdir(parents=True)
    return path


def _cloned_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", REMOTE], check=True)
    return path


@pytest.fixture()
def layout(tmp_path):
    ws = tmp_path / "ws"
    aaxis = ws / "aaxis"
    me = ws / "me"
    _marker_repo(aaxis / "bildwiz" / "sub" / "repo-three")
    _marker_repo(aaxis / "l1" / "l2" / "l3" / "l4" / "l5" / "deep-six")
    _marker_repo(aaxis / ".archive" / "old-repo")
    _marker_repo(aaxis / ".terraform" / "modules" / "cached")
    (aaxis / "notes").mkdir(parents=True)
    (aaxis / "notes" / "readme.md").write_text("not a repo\n")
    _marker_repo(me / "gaia")
    _marker_repo(me / ".claude" / "plugin" / "bundled")
    _marker_repo(ws / "github-repos" / "engram")
    _cloned_repo(ws / "github-repos" / "auto-claude-sleep")
    _cloned_repo(ws / "github-repos" / "_duplicados" / "auto-claude-sleep-copy")

    db = tmp_path / "gaia.db"
    for name, root in (("ws", ws), ("aaxis", aaxis), ("me", me)):
        declare_workspace(name, root, db_path=db)
    return {"ws": ws, "aaxis": aaxis, "me": me, "db": db}


def _rows(db: Path, sql: str, *params) -> list[tuple]:
    con = sqlite3.connect(db)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def _groups(db: Path, workspace: str) -> dict[str, str | None]:
    rows = _rows(db, "SELECT name, group_name FROM projects WHERE workspace = ?", workspace)
    return dict(rows)


def test_repos_at_any_depth_keep_the_full_group_path(layout):
    report = classify.scan(layout["aaxis"], "aaxis", db_path=layout["db"], apply=True)

    assert report.error is None
    assert _groups(layout["db"], "aaxis") == {
        "repo-three": "bildwiz/sub",
        "deep-six": "l1/l2/l3/l4/l5",
        "old-repo": ".archive",
    }


def test_tool_dot_folders_and_plain_folders_hold_no_project(layout):
    report = classify.scan(layout["ws"], "ws", db_path=layout["db"], apply=False)

    found = {Path(r["path"]).name for r in report.repos_found}
    assert "cached" not in found
    assert "bundled" not in found
    assert "notes" not in found


def test_each_repo_belongs_to_the_nearest_declared_workspace(layout):
    report = classify.scan(layout["ws"], "ws", db_path=layout["db"], apply=True)

    assert _groups(layout["db"], "ws") == {
        "engram": "github-repos",
        "auto-claude-sleep": "github-repos",
        "auto-claude-sleep-copy": "github-repos/_duplicados",
    }
    foreign = {f["repo"]: f["workspace"] for f in report.foreign_repos}
    assert foreign == {
        "repo-three": "aaxis",
        "deep-six": "aaxis",
        "old-repo": "aaxis",
        "gaia": "me",
    }


def test_a_second_clone_of_one_remote_is_found_where_it_sits(layout):
    classify.scan(layout["ws"], "ws", db_path=layout["db"], apply=True)

    clones = _rows(
        layout["db"],
        "SELECT path FROM projects WHERE remote_url = ? ORDER BY path",
        REMOTE,
    )
    assert [Path(path).relative_to(layout["ws"]).as_posix() for (path,) in clones] == [
        "github-repos/_duplicados/auto-claude-sleep-copy",
        "github-repos/auto-claude-sleep",
    ]


def test_scanning_never_records_a_workspace_root(layout):
    before = _rows(layout["db"], "SELECT name, root_path FROM workspaces ORDER BY name")
    undeclared = layout["ws"] / "github-repos"

    refused = classify.scan(undeclared, "github-repos", db_path=layout["db"], apply=True)
    classify.scan(layout["ws"], "ws", db_path=layout["db"], apply=True)

    assert "not declared" in refused.error
    assert _rows(layout["db"], "SELECT name, root_path FROM workspaces ORDER BY name") == before
