"""Edge cases of the group level found by verification: odd group names, unknown and shared groups, wording, nested scans."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.conftest import copy_bootstrapped_db

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "bin", _REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import cli.context as context_mod  # noqa: E402
import cli.memory as memory_mod  # noqa: E402
import cli.scan as scan_mod  # noqa: E402
from modules.security.gaia_cli_only_guard import _validate_known_safe_flags  # noqa: E402

WS = "acme"


@pytest.fixture()
def db(tmp_path, monkeypatch, bootstrapped_db_template):
    data = tmp_path / "data"
    db = copy_bootstrapped_db(bootstrapped_db_template, data / "gaia.db")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(db))
    monkeypatch.chdir(tmp_path)
    return db


def _seed(db, projects, memory):
    con = sqlite3.connect(db)
    try:
        for ws in {p[0] for p in projects}:
            con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES (?, ?)", (ws, ws))
        for ws, name, identity, group in projects:
            con.execute(
                "INSERT INTO projects (workspace, name, path, project_identity, group_name, status) "
                "VALUES (?, ?, ?, ?, ?, 'active')", (ws, name, f"/work/{ws}/{name}", identity, group),
            )
        for ws, name, initiative in memory:
            con.execute(
                "INSERT INTO memory (workspace, name, type, description, body, updated_at, "
                "initiative, class, status) VALUES (?, ?, 'decision', ?, ?, "
                "'2026-10-28T00:00:00Z', ?, 'thread', 'open')",
                (ws, name, f"desc {name}", f"body {name}", initiative),
            )
        con.commit()
    finally:
        con.close()


def _relevant(**overrides):
    base = dict(workspace=WS, limit=8, max_chars=800, types=None, sections=None,
                initiative=None, project=None, group=None, json=True, no_pointer=False)
    base.update(overrides)
    return SimpleNamespace(**base)


def _names(out: str) -> set[str]:
    return {item["name"] for item in json.loads(out)["items"]}


def test_group_named_like_the_no_initiative_bucket_is_a_group(db, capsys):
    _seed(db, [(WS, "x-tool", "github.com/acme/x-tool", "otros")],
          [(WS, "t_otros_key", "otros"), (WS, "t_member", "x_tool"), (WS, "t_null", None)])
    assert memory_mod._cmd_get_relevant(_relevant(group="otros")) == 0
    assert _names(capsys.readouterr().out) == {"t_otros_key", "t_member"}
    assert memory_mod._cmd_get_relevant(_relevant(initiative="otros")) == 0
    assert "t_null" in _names(capsys.readouterr().out)


def test_unknown_group_says_so_with_the_closest_names_and_fails(db, capsys):
    _seed(db, [(WS, "x-tool", "github.com/acme/x-tool", "bildwiz"),
               (WS, "y-tool", "github.com/acme/y-tool", "rnd")], [])
    rc = memory_mod._cmd_get_relevant(_relevant(group="buildwiz", json=False))
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert "no group named 'buildwiz'" in captured.err
    assert "Closest groups: bildwiz" in captured.err


def test_group_in_two_workspaces_without_workspace_reads_both_and_says_so(db, capsys):
    _seed(db, [(WS, "a-iac", "github.com/acme/a-iac", "shared"),
               ("other", "b-iac", "github.com/other/b-iac", "shared")],
          [(WS, "t_a", "a_iac"), ("other", "t_b", "b_iac")])
    assert memory_mod._cmd_get_relevant(_relevant(group="shared", workspace=None)) == 0
    captured = capsys.readouterr()
    assert _names(captured.out) == {"t_a", "t_b"}
    assert "group 'shared' exists in workspaces acme, other" in captured.err

    assert memory_mod._cmd_get_relevant(_relevant(group="shared", workspace="other")) == 0
    captured = capsys.readouterr()
    assert _names(captured.out) == {"t_b"}
    assert captured.err == ""


def test_unknown_group_under_json_prints_the_error_body_and_fails(db, capsys):
    _seed(db, [(WS, "x-tool", "github.com/acme/x-tool", "bildwiz")], [])
    rc = memory_mod._cmd_get_relevant(_relevant(group="buildwiz", json=True))
    assert rc == 1
    assert json.loads(capsys.readouterr().out) == {
        "error": "not_found", "name": "buildwiz",
        "candidates": [{"workspace": WS, "name": "bildwiz"}],
    }


def test_workspace_given_and_group_elsewhere_names_that_workspace(db, capsys):
    _seed(db, [("other", "b-iac", "github.com/other/b-iac", "only-other")], [])
    rc = memory_mod._cmd_get_relevant(_relevant(group="only-other", workspace=WS, json=False))
    err = capsys.readouterr().err
    assert rc == 1
    assert "no group named 'only-other' in workspace acme" in err
    assert "It exists in workspace other." in err


def test_workspace_narrows_members_but_the_group_key_is_read_everywhere(db, capsys):
    _seed(db, [(WS, "a-iac", "github.com/acme/a-iac", "shared"),
               ("other", "b-iac", "github.com/other/b-iac", "shared")],
          [(WS, "t_key_in_acme", "shared"), ("other", "t_key_in_other", "shared"),
           (WS, "t_a", "a_iac"), ("other", "t_b", "b_iac")])
    assert memory_mod._cmd_get_relevant(_relevant(group="shared", workspace="other")) == 0
    assert _names(capsys.readouterr().out) == {"t_key_in_acme", "t_key_in_other", "t_b"}


def test_a_memory_initiative_no_folder_carries_points_at_initiative(db, capsys):
    _seed(db, [(WS, "x-tool", "github.com/acme/x-tool", "bildwiz")], [(WS, "t_logical", "branchkinect")])
    rc = memory_mod._cmd_get_relevant(_relevant(group="branchkinect", json=False))
    captured = capsys.readouterr()
    assert rc == 1 and captured.out == ""
    assert "use --initiative branchkinect" in captured.err


def test_a_misspelled_group_that_is_also_a_stray_initiative_shows_both(db, capsys):
    _seed(db, [(WS, f"bildwiz-{n}", f"github.com/acme/bildwiz-{n}", "bildwiz") for n in ("iac", "web")],
          [(WS, "t_stray", "buildwiz")])
    assert memory_mod._cmd_get_relevant(_relevant(group="buildwiz", json=False)) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines()[1:] == [
        "Closest groups: bildwiz",
        "'buildwiz' is a memory initiative no folder group carries: use --initiative buildwiz.",
    ]

    assert memory_mod._cmd_get_relevant(_relevant(group="buildwiz", json=True)) == 1
    body = json.loads(capsys.readouterr().out)
    assert body["candidates"] == [{"workspace": WS, "name": "bildwiz"}]
    assert "use --initiative buildwiz" in body["hint"]


def test_group_member_keys_include_folder_and_remote_spellings(db, capsys):
    _seed(db, [(WS, "folder-name", "github.com/acme/remote-name", "g")],
          [(WS, "t_folder", "folder_name"), (WS, "t_remote", "remote_name")])
    assert memory_mod._cmd_get_relevant(_relevant(group="g")) == 0
    assert _names(capsys.readouterr().out) == {"t_folder", "t_remote"}


def test_orchestrator_guard_admits_group():
    phrase = ("memory", "get-relevant")
    assert _validate_known_safe_flags(phrase + ("--group", "bildwiz"), phrase) is None
    assert _validate_known_safe_flags(phrase + ("--group=bildwiz",), phrase) is None


def test_single_member_group_of_same_name_reads_in_the_singular(db, capsys):
    _seed(db, [(WS, "solo", "github.com/acme/solo", "solo")], [])
    assert context_mod._cmd_project(SimpleNamespace(name="solo", workspace=None, json=False)) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert "holds 1 project " in first, first


def _declared_tree(db, tmp_path, *repos):
    root = tmp_path / "acme-root"
    for segments in repos:
        (root.joinpath(*segments) / ".git").mkdir(parents=True)
    con = sqlite3.connect(db)
    try:
        con.execute("INSERT INTO workspaces (name, identity, root_path) VALUES (?, ?, ?)", (WS, WS, str(root)))
        con.commit()
    finally:
        con.close()
    return root


def _scan_out(root, capsys, **kw):
    assert scan_mod.cmd_scan(argparse.Namespace(workspace=WS, root=str(root),
                                                dry_run=kw.get("dry_run", False), json=False)) == 0
    out = capsys.readouterr().out
    return out[out.index("Workspace "):]


def test_scan_nested_group_names_the_declared_root(db, tmp_path, capsys):
    root = _declared_tree(db, tmp_path, ("a", "b", "p1"), ("a", "b", "p2"), ("c", "p3"), ("loose",))
    summary = _scan_out(root, capsys)
    assert summary.splitlines()[0] == f"Workspace {WS} ({root}): 4 projects in 2 groups."
    assert "  a/b (2): p1, p2" in summary.splitlines()
    assert "  ungrouped (1): loose" in summary.splitlines()
    assert not re.search(r"\brepos?\b", summary)


def test_scan_only_ungrouped_names_the_declared_root(db, tmp_path, capsys):
    root = _declared_tree(db, tmp_path, ("l1",), ("l2",))
    summary = _scan_out(root, capsys, dry_run=True)
    assert summary.splitlines()[:2] == [
        f"Workspace {WS} ({root}): would index 2 projects in 0 groups.",
        "  ungrouped (2): l1, l2",
    ]
