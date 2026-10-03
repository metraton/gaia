"""A by-name memory verb acts only on the row its caller named.

An explicit --workspace is never swapped for another workspace that happens to
hold the slug, a delete acts only on the workspace its command names, and no
verb appends to or reclassifies a deleted row.

Uses the real CLI handlers and writer against a temporary GAIA_DATA_DIR.
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

SLUG = "thread_bildwiz_resume_live_test"
OTHER = "thread_bildwiz_follow_up"


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


def _call(capsys, func, **fields):
    rc = memory_mod.cmd_memory(argparse.Namespace(func=func, json=True, **fields))
    return rc, _json.loads(capsys.readouterr().out)


def _add(capsys, name, workspace="me"):
    rc, out = _call(
        capsys, memory_mod._cmd_add, name=name, type="project", body="stored under me",
        body_file=None, description="desc", workspace=workspace, class_="thread",
        status="carry_forward", project=None, project_ref=None, audience=None,
        initiative="bildwiz",
    )
    assert rc == 0, out


def _row(db_path: Path, name: str = SLUG) -> tuple:
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(
            "SELECT workspace, body, class, status, deleted_at FROM memory WHERE name = ?",
            (name,),
        ).fetchone()
    finally:
        con.close()


def _link_count(db_path: Path) -> int:
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0]
    finally:
        con.close()


@pytest.fixture()
def row_in_me(tmp_db, capsys):
    _add(capsys, SLUG)
    _add(capsys, OTHER)
    return _row(tmp_db)


def test_show_with_an_explicit_workspace_lacking_the_slug_reports_not_found(
    tmp_db, row_in_me, capsys,
):
    rc, out = _call(capsys, memory_mod._cmd_curated_show, name=SLUG,
                    workspace="aaxis", links=False, history=False)

    assert rc == 1
    assert "not found in workspace 'aaxis'" in out["error"]


def test_append_with_an_explicit_workspace_lacking_the_slug_writes_nothing(
    tmp_db, row_in_me, capsys,
):
    rc, _ = _call(capsys, memory_mod._cmd_append, name=SLUG, body="more",
                  body_file=None, workspace="aaxis")

    assert rc == 1
    assert _row(tmp_db) == row_in_me


def test_reclassify_with_an_explicit_workspace_lacking_the_slug_writes_nothing(
    tmp_db, row_in_me, capsys,
):
    rc, _ = _call(capsys, memory_mod._cmd_reclassify, name=SLUG, class_="anchor",
                  status=None, workspace="aaxis")

    assert rc == 1
    assert _row(tmp_db) == row_in_me


def test_link_with_an_explicit_workspace_lacking_the_slugs_writes_nothing(
    tmp_db, row_in_me, capsys,
):
    rc, _ = _call(capsys, memory_mod._cmd_link, src_name=OTHER, dst_name=SLUG,
                  kind="derived_from", delete=False, workspace="aaxis")

    assert rc == 1
    assert _link_count(tmp_db) == 0


@pytest.mark.parametrize("hard", [False, True])
@pytest.mark.parametrize("workspace_flag,env_workspace", [
    ("aaxis", None),
    (None, "ws"),
])
def test_delete_never_reaches_a_workspace_its_command_does_not_name(
    tmp_db, row_in_me, capsys, monkeypatch, hard, workspace_flag, env_workspace,
):
    if env_workspace:
        monkeypatch.setenv("GAIA_WORKSPACE", env_workspace)

    rc, out = _call(capsys, memory_mod._cmd_delete, name=SLUG, yes=True, hard=hard,
                    workspace=workspace_flag)

    assert rc == 1
    assert _row(tmp_db) == row_in_me
    if workspace_flag is None:
        assert out["code"] == "workspace_not_named"
        assert "--workspace me" in out["error"]


def test_delete_acts_when_its_command_names_the_holding_workspace(
    tmp_db, row_in_me, capsys,
):
    rc, out = _call(capsys, memory_mod._cmd_delete, name=SLUG, yes=True, hard=False,
                    workspace="me")

    assert rc == 0, out
    assert _row(tmp_db)[4] is not None


def test_append_to_a_deleted_name_is_refused(tmp_db, row_in_me, capsys):
    assert _call(capsys, memory_mod._cmd_delete, name=SLUG, yes=True, hard=False,
                 workspace="me")[0] == 0
    tombstone = _row(tmp_db)

    rc, out = _call(capsys, memory_mod._cmd_append, name=SLUG, body="more",
                    body_file=None, workspace="me")

    assert rc == 1
    assert "deleted" in out["error"]
    assert _row(tmp_db) == tombstone


def test_reclassify_of_a_deleted_name_is_refused(tmp_db, row_in_me, capsys):
    assert _call(capsys, memory_mod._cmd_delete, name=SLUG, yes=True, hard=False,
                 workspace="me")[0] == 0
    tombstone = _row(tmp_db)

    rc, _ = _call(capsys, memory_mod._cmd_reclassify, name=SLUG, class_="anchor",
                  status=None, workspace="me")

    assert rc == 1
    assert _row(tmp_db) == tombstone


def test_link_delete_with_an_abbreviated_flag_is_refused_by_the_parser():
    """The signature matches the literal --delete; `--del` must not reach it."""
    parser = argparse.ArgumentParser()
    memory_mod.register(parser.add_subparsers(dest="subcommand"))

    with pytest.raises(SystemExit):
        parser.parse_args(["memory", "link", OTHER, SLUG, "--kind=supersedes", "--del"])
