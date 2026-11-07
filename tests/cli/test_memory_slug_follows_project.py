"""A by-slug memory verb reaches a project's row from any workspace.

Memory follows the project it belongs to, not the workspace that wrote it:
``get-relevant --initiative`` already lists a project's rows from every
workspace, so the slug it prints must open from the same vantage. A
workspace-owned row is reached the same way, and a slug held by rows in two
other workspaces is refused rather than guessed.

Uses the real writer/CLI path against a temporary GAIA_DATA_DIR.
"""

from __future__ import annotations

import argparse
import json as _json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (_REPO_ROOT, _REPO_ROOT / "bin"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from cli import memory as memory_mod  # noqa: E402
from cli.memory_story import _cmd_story  # noqa: E402

SLUG = "thread_bildwiz_resume_live_test"


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Route the substrate DB into tmp_path -- never the real ~/.gaia/gaia.db."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for key in ("GAIA_DISPATCH_AGENT", "GAIA_SESSION_ID", "GAIA_WORKSPACE",
                "GAIA_DISPATCH_WORKSPACE", "GAIA_DB"):
        monkeypatch.delenv(key, raising=False)
    from gaia.paths import db_path
    return db_path()


def _add(capsys, workspace, name, *, initiative="bildwiz", body="body text", kind="pending"):
    rc = memory_mod._cmd_add(argparse.Namespace(
        name=name, type="project", body=body, body_file=None,
        description="desc", workspace=workspace, class_="thread",
        status="carry_forward", project=None, project_ref=None,
        audience=None, initiative=initiative, kind=kind, json=True,
    ))
    assert rc == 0, capsys.readouterr().out
    capsys.readouterr()


def _show(capsys, name, workspace=None):
    rc = memory_mod.cmd_memory(argparse.Namespace(
        func=memory_mod._cmd_curated_show, name=name, workspace=workspace,
        links=False, history=False, json=True,
    ))
    return rc, _json.loads(capsys.readouterr().out)


def _bodies(db_path: Path) -> dict[tuple[str, str], str]:
    con = sqlite3.connect(str(db_path))
    try:
        return {
            (ws, name): body for ws, name, body in con.execute(
                "SELECT workspace, name, body FROM memory WHERE deleted_at IS NULL"
            )
        }
    finally:
        con.close()


def test_show_from_another_workspace_finds_the_projects_row(
    tmp_db, monkeypatch, capsys,
):
    _add(capsys, "me", SLUG, body="stored under me")
    monkeypatch.setenv("GAIA_WORKSPACE", "ws")

    rc, out = _show(capsys, SLUG)

    assert rc == 0, out
    assert out["name"] == SLUG
    assert out["body"] == "stored under me"


def test_show_and_move_reach_a_workspace_owned_row_elsewhere(
    tmp_db, monkeypatch, capsys,
):
    _add(capsys, "me", "project_owned_by_me", initiative=None, kind="fact")
    monkeypatch.setenv("GAIA_WORKSPACE", "ws")

    rc, out = _show(capsys, "project_owned_by_me")
    assert rc == 0, out
    assert out["body"] == "body text"

    parser = argparse.ArgumentParser()
    memory_mod.register(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args(["memory", "move", "project_owned_by_me", "--to",
                              "group:me/bildwiz", "--json"])
    assert args.func(args) == 0, capsys.readouterr().out
    assert _json.loads(capsys.readouterr().out)["from_workspace"] == "me"


def test_a_workspace_owned_slug_in_two_other_workspaces_is_refused(
    tmp_db, monkeypatch, capsys,
):
    _add(capsys, "me", "project_owned_twice", initiative=None, kind="fact")
    _add(capsys, "aaxis", "project_owned_twice", initiative=None, kind="fact")
    monkeypatch.setenv("GAIA_WORKSPACE", "ws")

    rc, out = _show(capsys, "project_owned_twice")

    assert (rc, out["code"], out["workspaces"]) == (1, "ambiguous_slug", ["aaxis", "me"])


def test_callers_own_row_wins_over_another_workspaces_project_row(
    tmp_db, monkeypatch, capsys,
):
    _add(capsys, "me", SLUG, body="stored under me")
    _add(capsys, "ws", SLUG, body="stored under ws")
    monkeypatch.setenv("GAIA_WORKSPACE", "ws")

    rc, out = _show(capsys, SLUG)

    assert rc == 0
    assert out["body"] == "stored under ws"


def test_slug_held_by_project_rows_in_two_other_workspaces_is_refused(
    tmp_db, monkeypatch, capsys,
):
    _add(capsys, "me", SLUG, body="stored under me")
    _add(capsys, "aaxis", SLUG, initiative="aos", body="stored under aaxis")
    monkeypatch.setenv("GAIA_WORKSPACE", "ws")

    rc, out = _show(capsys, SLUG)

    assert rc == 1
    assert out["code"] == "ambiguous_slug"
    assert out["workspaces"] == ["aaxis", "me"]

    rc, out = _show(capsys, SLUG, workspace="me")
    assert rc == 0
    assert out["body"] == "stored under me"


def test_story_append_and_link_reach_the_projects_row_from_another_workspace(
    tmp_db, monkeypatch, capsys,
):
    _add(capsys, "me", SLUG, body="stored under me")
    _add(capsys, "me", "thread_bildwiz_follow_up")
    monkeypatch.setenv("GAIA_WORKSPACE", "ws")

    assert memory_mod._cmd_append(argparse.Namespace(
        name=SLUG, body="appended from ws", body_file=None,
        workspace=None, json=True,
    )) == 0
    assert memory_mod._cmd_link(argparse.Namespace(
        src_name="thread_bildwiz_follow_up", dst_names=[SLUG],
        kind="derived_from", delete=False, workspace=None, json=True,
    )) == 0
    capsys.readouterr()
    assert _cmd_story(argparse.Namespace(
        name=SLUG, max_depth=5, workspace=None, json=True,
    )) == 0

    assert "thread_bildwiz_follow_up" in capsys.readouterr().out
    bodies = _bodies(tmp_db)
    assert bodies[("me", SLUG)].endswith("appended from ws")
    assert not any(ws == "ws" for ws, _ in bodies)
