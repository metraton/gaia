"""The group level (workspace > group > project) on the CLI: memory --group, context get/show, scan's closing summary."""

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
for _path in (_REPO_ROOT, _REPO_ROOT / "bin"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import cli.context as context_mod  # noqa: E402
import cli.memory as memory_mod  # noqa: E402
import cli.scan as scan_mod  # noqa: E402

WS = "acme"
PROJECTS = (
    ("bildwiz-iac", "github.com/acme/bildwiz-iac", "bildwiz"),
    ("newco-pitot", "github.com/acme/newco-pitot", "bildwiz"),
    ("other-iac", "github.com/acme/other-iac", "other"),
    ("loose", "github.com/acme/loose", None),
)
TEACHING = (
    "A workspace is the folder you declared. Each folder inside it is a group: "
    "the name of a product or client. Each git repository inside a group is a "
    "project, named after its folder.\n"
    "To rename a group, rename the folder and run gaia scan again.\n"
    "One project: gaia context project NAME. "
    "Memory for a group: gaia memory get-relevant --group NAME.\n"
)


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


@pytest.fixture()
def seeded(db):
    con = sqlite3.connect(db)
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES (?, ?)", (WS, WS))
        for name, identity, group in PROJECTS:
            con.execute(
                "INSERT INTO projects (workspace, name, path, project_identity, group_name, status) "
                "VALUES (?, ?, ?, ?, ?, 'active')", (WS, name, f"/work/{name}", identity, group),
            )
        for name, initiative, project_ref, class_, status in (
            ("t_group_key", "bildwiz", None, "thread", "open"),
            ("t_member_initiative", "bildwiz_iac", None, "thread", "open"),
            ("t_member_ref", None, "github.com/acme/newco-pitot", "thread", "carry_forward"),
            ("t_other_group", "other_iac", None, "thread", "open"),
            ("a_member_anchor", "newco_pitot", None, "anchor", None),
        ):
            con.execute(
                "INSERT INTO memory (workspace, name, type, description, body, updated_at, "
                "initiative, project_ref, class, status) VALUES (?, ?, 'decision', ?, ?, "
                "'2026-10-28T00:00:00Z', ?, ?, ?, ?)",
                (WS, name, f"desc {name}", f"body {name}", initiative, project_ref, class_, status),
            )
        con.commit()
    finally:
        con.close()
    return db


def _relevant(**overrides):
    base = dict(workspace=WS, limit=8, max_chars=800, types=None, sections=None,
                initiative=None, project=None, group=None, json=True, no_pointer=False)
    base.update(overrides)
    return SimpleNamespace(**base)


def _names(capsys) -> set[str]:
    return {item["name"] for item in json.loads(capsys.readouterr().out)["items"]}


def test_group_sweeps_the_group_key_and_every_member_key(seeded, capsys):
    assert memory_mod._cmd_get_relevant(_relevant(group="bildwiz")) == 0
    assert _names(capsys) == {"t_group_key", "t_member_initiative", "t_member_ref"}


def test_group_is_matched_case_insensitively_and_brings_member_anchors(seeded, capsys):
    assert memory_mod._cmd_get_relevant(_relevant(group="BildWiz", sections="anchor")) == 0
    assert _names(capsys) == {"a_member_anchor"}


def test_initiative_alone_still_reads_only_its_own_key(seeded, capsys):
    assert memory_mod._cmd_get_relevant(_relevant(initiative="bildwiz")) == 0
    assert _names(capsys) == {"t_group_key"}


def test_context_get_json_names_workspace_group_and_project(seeded, capsys):
    args = SimpleNamespace(workspace=WS, section=None, text=False, include_missing=False, json=False)
    assert context_mod._cmd_get(args) == 0
    projects = json.loads(capsys.readouterr().out)["workspace"]["projects"]
    assert {(p["workspace"], p["group"], p["name"]) for p in projects} == {
        (WS, group, name) for name, _, group in PROJECTS
    }


def test_context_show_lists_each_project_with_its_group(seeded, capsys, monkeypatch):
    import gaia.project
    monkeypatch.setattr(gaia.project, "current", lambda *a, **k: WS)

    assert context_mod._cmd_show(SimpleNamespace(section=None, json=False)) == 0
    out = capsys.readouterr().out
    assert re.search(r"^  bildwiz-iac\s+group: bildwiz$", out, re.M)
    assert re.search(r"^  loose\s+group: \(none\)$", out, re.M)

    assert context_mod._cmd_show(SimpleNamespace(section=None, json=True)) == 0
    projects = json.loads(capsys.readouterr().out)["workspace"]["projects"]
    assert {p["name"]: p["group"] for p in projects}["other-iac"] == "other"


def _declared_tree(db, tmp_path, *repos: tuple[str, ...]) -> Path:
    root = tmp_path / "acme-root"
    for segments in repos:
        (root.joinpath(*segments) / ".git").mkdir(parents=True)
    con = sqlite3.connect(db)
    try:
        con.execute("INSERT INTO workspaces (name, identity, root_path) VALUES (?, ?, ?)",
                    (WS, WS, str(root)))
        con.commit()
    finally:
        con.close()
    return root


def _scan(root: Path, *, dry_run: bool = False, as_json: bool = False) -> int:
    return scan_mod.cmd_scan(argparse.Namespace(
        workspace=WS, root=str(root), dry_run=dry_run, json=as_json))


def _summary(out: str) -> str:
    return out[out.index("Workspace "):]


def test_scan_apply_closes_with_groups_and_the_ungrouped(db, tmp_path, capsys):
    root = _declared_tree(db, tmp_path, ("bildwiz", "bildwiz-iac"), ("bildwiz", "newco-pitot"),
                          ("loose",))
    assert _scan(root) == 0
    summary = _summary(capsys.readouterr().out)
    assert summary == (
        f"Workspace {WS} ({root}): 3 projects in 1 group.\n"
        "  bildwiz (2): bildwiz-iac, newco-pitot\n"
        "  ungrouped (1): loose\n" + TEACHING
    )
    assert not re.search(r"\brepos?\b", summary)


def test_scan_dry_run_says_would_index_and_omits_an_empty_ungrouped(db, tmp_path, capsys):
    root = _declared_tree(db, tmp_path, ("bildwiz", "bildwiz-iac"), ("aos", "aos"))
    assert _scan(root, dry_run=True) == 0
    assert _summary(capsys.readouterr().out) == (
        f"Workspace {WS} ({root}): would index 2 projects in 2 groups.\n"
        "  aos (1): aos\n"
        "  bildwiz (1): bildwiz-iac\n" + TEACHING
    )


def test_scan_json_carries_no_summary(db, tmp_path, capsys):
    root = _declared_tree(db, tmp_path, ("loose",))
    assert _scan(root, as_json=True) == 0
    assert json.loads(capsys.readouterr().out)["resolved_workspace"] == WS
