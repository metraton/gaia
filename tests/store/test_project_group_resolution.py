"""A project named by its slug resolves to it; a group name resolves to its members and never to one of them."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from tests.conftest import copy_bootstrapped_db

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from gaia.store import project_context  # noqa: E402
from gaia.store.project_context import resolve_project  # noqa: E402
from modules.context.context_writer import _route  # noqa: E402
from modules.context.kernel_builder import (  # noqa: E402
    KERNEL_BUDGET,
    build_group_context_block,
    build_kernel_context,
)
from tools.context.context_provider import resolve_project_by_name  # noqa: E402

WS = "acme"
API = "github.com/acme/grp-api"
IAC = "github.com/acme/grp-iac"


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
        for name, identity, group in (
            ("grp-api", API, "grp"), ("grp-iac", IAC, "grp"), ("loner", "github.com/acme/loner", None),
        ):
            con.execute(
                "INSERT INTO projects (workspace, name, path, project_identity, group_name, status) "
                "VALUES (?, ?, ?, ?, ?, 'active')", (WS, name, f"/work/{name}", identity, group),
            )
        for identity, section, payload, updated in (
            (API, "project_identity", {"grp": {"name": "grp", "local_path": "/work/grp-api"}}, "2026-10-01T00:00:00Z"),
            (API, "stack", {"language": "go"}, "2026-10-02T00:00:00Z"),
            (IAC, "project_identity", {"grp_2": {"name": "grp-2", "local_path": "/work/grp-iac"}}, "2026-10-01T00:00:00Z"),
            (IAC, "cluster_details", {"cluster": "dev-gke"}, "2026-10-03T00:00:00Z"),
        ):
            con.execute(
                "INSERT INTO project_context (project_identity, contract_name, payload, updated_at) "
                "VALUES (?, ?, ?, ?)", (identity, section, json.dumps(payload), updated),
            )
        con.commit()
    finally:
        con.close()
    return db


def _con(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(db)


def test_slug_resolves_the_project_that_holds_it_in_either_spelling(db):
    con = _con(db)
    try:
        assert resolve_project(con, "grp-2", "me")["identity"] == IAC
        assert resolve_project(con, "grp_2", "me")["identity"] == IAC
    finally:
        con.close()


def test_group_name_resolves_to_members_and_never_to_one_member(db):
    con = _con(db)
    try:
        assert [m["name"] for m in project_context.resolve_group(con, "grp", "me")] == ["grp-api", "grp-iac"]
        assert resolve_project(con, "grp", "me") is None
        assert project_context.resolve_group(con, "loner", "me") == []
    finally:
        con.close()


def test_dispatch_token_naming_a_group_keeps_the_group(db):
    assert resolve_project_by_name(WS, "grp", db_path=db) == "grp"


def test_kernel_for_a_group_points_at_each_members_readable_sections(db):
    row = {
        "contract_id": "a0123456789abcdef.beefcafe0123",
        "agent_id": "a0123456789abcdef",
        "workspace": "me",
        "dispatch_prompt": "check the cluster",
        "dispatch_project": "grp",
        "kernel_sections": json.dumps(
            {"role": "primary", "surface": "live_runtime", "can_read": ["stack", "cluster_details"]},
        ),
    }

    kernel = build_kernel_context(row, agent_name="cloud-troubleshooter", db_path=db)

    assert "# Project context" in kernel
    assert f"`gaia context get-contract --section stack --workspace {WS} --project {API}`" in kernel
    assert f"`gaia context get-contract --section cluster_details --workspace {WS} --project {IAC}`" in kernel
    assert len(kernel) <= KERNEL_BUDGET


def test_kernel_for_a_member_dispatch_renders_only_that_member(db):
    row = {
        "contract_id": "a0123456789abcdef.beefcafe0123",
        "agent_id": "a0123456789abcdef",
        "workspace": "me",
        "dispatch_prompt": "project=grp-iac check the plan",
        "dispatch_project": resolve_project_by_name(WS, "grp-iac", db_path=db),
        "kernel_sections": json.dumps(
            {"role": "primary", "surface": "live_runtime", "can_read": ["stack", "cluster_details"]},
        ),
    }

    kernel = build_kernel_context(row, agent_name="cloud-troubleshooter", db_path=db)

    assert '"cluster": "dev-gke"' in kernel
    assert "is a group" not in kernel
    assert API not in kernel


def test_a_space_ends_the_dispatch_token(db):
    from modules.agents.dispatch_binding import extract_dispatch_binding

    token = extract_dispatch_binding({"prompt": "project=grp iac look", "subagent_type": "cloud-troubleshooter"})["project"]

    assert token == "grp"
    assert resolve_project_by_name(WS, token, db_path=db) == "grp"


_MEMBERS = [
    ({"name": "grp-api", "workspace": WS, "identity": API}, [("stack", 120)]),
    ({"name": "grp-iac", "workspace": WS, "identity": IAC}, [("cluster_details", 900)]),
]


def test_group_block_over_budget_is_one_line_naming_the_group():
    full = build_group_context_block("grp", _MEMBERS, max_chars=10_000)
    short = build_group_context_block("grp", _MEMBERS, max_chars=len(full) - 1)

    assert full.count("gaia context get-contract --section ") == 2
    assert "get-contract" not in short
    assert short.splitlines()[-1] == "grp is a group of 2 projects with context — `gaia context project grp`"
    assert build_group_context_block("grp", [], max_chars=10_000) == ""


def test_update_contracts_under_a_group_dispatch_names_the_members(db):
    con = _con(db)
    try:
        routed, refusals = _route({"contract": "stack", "payload": {"language": "rust"}}, "me", "grp", con)
    finally:
        con.close()
    assert routed == []
    assert len(refusals) == 1
    assert "grp-api" in refusals[0] and "grp-iac" in refusals[0]
    assert '"project"' in refusals[0]
