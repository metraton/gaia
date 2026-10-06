"""Reads by project and by brief name follow the project, not the cwd workspace.

Brief ``una-gaia-cualquier-instalacion``, AC-12: a project's memory and briefs
written in workspace A are read from workspace B with no ``--workspace``, and a
brief name held by two workspaces is an error naming both rather than a silent
pick. Each case runs the real ``bin/gaia`` from a project directory of another
workspace against a temporary HOME, GAIA_DATA_DIR and GAIA_DB.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_GAIA_BIN = _REPO_ROOT / "bin" / "gaia"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Workspaces ws_a, ws_b and ws_c, each with one registered project dir.

    ws_a holds the ``gaia`` project's pending threads (one keyed by initiative,
    one only by a git-common-dir project_ref) and the brief ``shared-brief``;
    ``dup-brief`` exists in both ws_a and ws_b; the user row written from ws_a
    lands in the ``_gaia_user`` sentinel.
    """
    data = tmp_path / "data"
    env_home = tmp_path / "home"
    data.mkdir()
    env_home.mkdir()
    monkeypatch.setenv("HOME", str(env_home))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(data / "gaia.db"))
    for key in ("GAIA_DISPATCH_WORKSPACE", "GAIA_WORKSPACE",
                "GAIA_DISPATCH_AGENT", "GAIA_SESSION_ID"):
        monkeypatch.delenv(key, raising=False)

    from gaia.briefs import upsert_brief
    from gaia.paths import db_path
    from gaia.store.writer import (
        _connect, reclassify_memory, upsert_memory, upsert_plan,
    )

    dirs = {}
    con = _connect(db_path())
    try:
        for ws in ("ws_a", "ws_b", "ws_c"):
            root = tmp_path / ws
            project = root / f"proj_{ws}"
            project.mkdir(parents=True)
            dirs[ws] = project
            con.execute(
                "INSERT INTO workspaces (name, root_path) VALUES (?, ?)",
                (ws, str(root)),
            )
            con.execute(
                "INSERT INTO projects (workspace, name, path, status) "
                "VALUES (?, ?, ?, 'active')",
                (ws, f"proj_{ws}", str(project)),
            )
        con.commit()
    finally:
        con.close()

    for name, keys in (
        ("gaia-by-initiative", {"initiative": "gaia"}),
        ("gaia-by-project-ref", {"project_ref": "/elsewhere/gaia/.git"}),
        ("unrelated-thread", {"initiative": "other"}),
    ):
        upsert_memory("ws_a", name, type="project", body="body",
                      description=name, **keys)
        reclassify_memory("ws_a", name, class_="thread", status="open")
    upsert_memory("ws_a", "who-the-user-is", type="user", body="body")

    upsert_brief("ws_a", "shared-brief", {"title": "Shared", "status": "draft"})
    upsert_plan("ws_a", "shared-brief", content="## Plan")
    upsert_brief("ws_a", "dup-brief", {"title": "Dup A", "status": "draft"})
    upsert_brief("ws_b", "dup-brief", {"title": "Dup B", "status": "draft"})
    return dirs


def _gaia(cwd: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_GAIA_BIN), *argv],
        cwd=str(cwd), env=dict(os.environ),
        capture_output=True, text=True, timeout=60,
    )


@pytest.mark.parametrize("flag", ["--initiative", "--project"])
def test_project_sweep_from_another_workspace_returns_its_rows(home, flag):
    result = _gaia(home["ws_b"], "memory", "get-relevant", flag, "gaia", "--json")

    assert result.returncode == 0, result.stderr
    names = {item["name"] for item in json.loads(result.stdout)["items"]}
    assert names == {"gaia-by-initiative", "gaia-by-project-ref"}


@pytest.mark.parametrize("argv", [
    ("brief", "show", "shared-brief", "--json"),
    ("task", "list", "shared-brief", "--json"),
])
def test_brief_found_by_name_from_another_workspace(home, argv):
    result = _gaia(home["ws_b"], *argv)

    assert result.returncode == 0, result.stdout + result.stderr


def test_brief_name_in_two_workspaces_is_an_error_naming_both(home):
    result = _gaia(home["ws_c"], "task", "list", "dup-brief")

    assert result.returncode != 0
    assert "ws_a" in result.stderr and "ws_b" in result.stderr, result.stderr


def test_memory_delete_reaches_a_user_row_from_any_workspace(home):
    result = _gaia(home["ws_b"], "memory", "delete", "who-the-user-is",
                   "--yes", "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["workspace"] == "_gaia_user"
