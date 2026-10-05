"""`gaia brief set-project`: who may tag, a clean refusal, and a project found by identity."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    data = tmp_path / "gaia_data"
    data.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    from gaia.paths import db_path
    from gaia.store.writer import _connect

    path = db_path()
    _connect(path).close()
    _sql(path, "INSERT INTO workspaces (name) VALUES ('ws'), ('me')")
    _sql(path, "INSERT INTO projects (workspace, name, project_identity, status) VALUES "
               "('ws', 'local', 'github.com/o/local', 'active'), "
               "('me', 'gaia', 'github.com/o/gaia', 'active')")
    _sql(path, "INSERT INTO briefs (workspace, name, status) VALUES ('ws', 'feat', 'open')")
    return path


def _sql(db: Path, statement: str, params=()) -> list[tuple]:
    con = sqlite3.connect(str(db))
    try:
        rows = con.execute(statement, params).fetchall()
        con.commit()
        return rows
    finally:
        con.close()


def _set_project(project: str | None, **kw) -> int:
    from bin.cli.brief import _cmd_set_project

    args = {"name": "feat", "project": project, "workspace": "ws", "clear": False,
            "force": False, "dry_run": False, "json": False, **kw}
    return _cmd_set_project(argparse.Namespace(**args))


@pytest.mark.parametrize("agent", ["gaia-system", "gaia-operator"])
def test_a_specialist_tags_a_brief_and_its_dry_run_writes_nothing(db, monkeypatch, capsys, agent):
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", agent)

    assert _set_project("local", dry_run=True) == 0
    assert "would belong to project github.com/o/local" in capsys.readouterr().out
    assert _sql(db, "SELECT project FROM briefs") == [(None,)]

    assert _set_project("local") == 0
    assert _sql(db, "SELECT project FROM briefs") == [("github.com/o/local",)]


def test_an_agent_not_allowed_to_tag_gets_a_clean_error(db, monkeypatch, capsys):
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")

    assert _set_project("local", dry_run=True) == 1
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and "developer" in err
    assert _sql(db, "SELECT project FROM briefs") == [(None,)]


def test_a_project_of_another_workspace_is_found_by_its_identity(db, capsys):
    assert _set_project("github.com/o/gaia") == 0
    assert _sql(db, "SELECT workspace, project FROM briefs") == [("ws", "github.com/o/gaia")]

    assert _set_project("gaia", force=True) == 1
    assert "neither a project of workspace 'ws' nor a known project identity" in (
        capsys.readouterr().err
    )
