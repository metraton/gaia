"""`gaia context project <group>` answers with each member's sections and the verb that reads one."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from tests.conftest import copy_bootstrapped_db

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "bin"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from cli.context import _cmd_project  # noqa: E402

WS = "acme"
API = "github.com/acme/grp-api"
IAC = "github.com/acme/grp-iac"


class _Args:
    def __init__(self, name, json_output=False):
        self.name = name
        self.workspace = None
        self.json = json_output


@pytest.fixture()
def db(tmp_path, monkeypatch, bootstrapped_db_template):
    data = tmp_path / "data"
    db = copy_bootstrapped_db(bootstrapped_db_template, data / "gaia.db")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(db))
    monkeypatch.chdir(tmp_path)
    con = sqlite3.connect(db)
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES (?, ?)", (WS, WS))
        for name, identity in (("grp-api", API), ("grp-iac", IAC)):
            con.execute(
                "INSERT INTO projects (workspace, name, path, project_identity, group_name, status) "
                "VALUES (?, ?, ?, ?, 'grp', 'active')", (WS, name, f"/work/{name}", identity),
            )
        for identity, section, updated in (
            (API, "stack", "2026-10-02T00:00:00Z"),
            (IAC, "cluster_details", "2026-10-03T00:00:00Z"),
        ):
            con.execute(
                "INSERT INTO project_context (project_identity, contract_name, payload, updated_at) "
                "VALUES (?, ?, ?, ?)", (identity, section, json.dumps({"body": "SECRET-BODY"}), updated),
            )
        con.execute(
            "INSERT INTO project_context_contracts (workspace, contract_name, payload, updated_at) "
            "VALUES (?, 'environment', ?, '2026-09-01T00:00:00Z')", (WS, json.dumps({"cloud": "gcp"})),
        )
        con.commit()
    finally:
        con.close()
    return db


def test_group_lists_members_sections_and_the_read_verb_without_bodies(db, capsys):
    rc = _cmd_project(_Args("grp"))
    out = capsys.readouterr().out

    assert rc == 0
    assert out.index("grp-api") < out.index("stack") < out.index("grp-iac") < out.index("cluster_details")
    assert "2026-10-02T00:00:00Z" in out and "2026-10-03T00:00:00Z" in out
    assert f"gaia context get-contract --project {API} --section <s>" in out
    assert f"gaia context get-contract --project {IAC} --section <s>" in out
    assert "environment" in out
    assert "SECRET-BODY" not in out


def test_single_project_resolves_case_insensitively(db, capsys):
    rc = _cmd_project(_Args("GRP-IAC"))
    out = capsys.readouterr().out

    assert rc == 0
    assert "name             : grp-iac" in out
    assert "group of" not in out


def test_group_resolves_case_insensitively(db, capsys):
    assert _cmd_project(_Args("GRP")) == 0
    assert capsys.readouterr().out.startswith("GRP: group of 2 projects")


def test_names_differing_only_by_case_stay_ambiguous(db, capsys):
    con = sqlite3.connect(db)
    try:
        for name in ("Dup", "dup"):
            con.execute(
                "INSERT INTO projects (workspace, name, path, status) VALUES (?, ?, ?, 'active')",
                (WS, name, f"/work/case-{name}"),
            )
        con.commit()
    finally:
        con.close()

    assert _cmd_project(_Args("dup")) == 0
    capsys.readouterr()
    assert _cmd_project(_Args("DUP")) == 1
    err = capsys.readouterr().err
    assert "ambiguous" in err and f"{WS}/Dup" in err and f"{WS}/dup" in err


@pytest.mark.parametrize("name", ["gr", "grp-", "api", "grp api", "grp-apix", "iac"])
def test_partial_words_resolve_to_nothing(db, capsys, name):
    assert _cmd_project(_Args(name)) == 1
    assert "group of" not in capsys.readouterr().out


def test_group_json_carries_members_and_sections(db, capsys):
    rc = _cmd_project(_Args("grp", json_output=True))
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert out["group"] == "grp"
    assert [m["name"] for m in out["members"]] == ["grp-api", "grp-iac"]
    assert out["members"][1]["sections"] == [
        {"name": "cluster_details", "updated_at": "2026-10-03T00:00:00Z"},
    ]
    assert out["members"][0]["read_command"] == f"gaia context get-contract --project {API} --section <s>"
