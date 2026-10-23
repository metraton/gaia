"""Relocating workspace-keyed context rows under their projects from a reviewed manifest."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from gaia.store.context_relocation import RelocationError, apply_relocation, plan_relocation


@pytest.fixture()
def db(tmp_path, monkeypatch):
    data = tmp_path / "gaia_data"
    data.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    from gaia.paths import db_path
    from gaia.store.writer import _connect

    path = db_path()
    con = _connect(path)
    for ws in ("ws", "me"):
        con.execute("INSERT INTO workspaces (name, identity, created_at) VALUES (?, ?, ?)",
                    (ws, ws, "2026-01-01T00:00:00Z"))
    for name, identity, status in (("gaia", "github.com/o/gaia", "active"),
                                   ("infra", "github.com/o/infra", "active"),
                                   ("gone", "github.com/o/gone", "missing")):
        con.execute("INSERT INTO projects (workspace, name, path, project_identity, status, scanner_ts) "
                    "VALUES ('me', ?, ?, ?, ?, '2026-01-01T00:00:00Z')",
                    (name, f"/r/{name}", identity, status))
    rows = (("ws", "stack", {"languages": []}),
            ("ws", "infrastructure", {"infra_a": 1, "gaia_b": 2, "org_c": 3}),
            ("me", "project_identity", {"gaia": {"name": "gaia"}, "infra": {"name": "infra"}}))
    for ws, section, payload in rows:
        con.execute("INSERT INTO project_context_contracts (workspace, contract_name, payload, updated_at) "
                    "VALUES (?, ?, ?, '2026-01-01T00:00:00Z')", (ws, section, json.dumps(payload)))
    con.commit()
    con.close()
    return path


def _manifest(**over):
    manifest = {
        "version": 1,
        "moves": [
            {"workspace": "ws", "section": "stack", "to": "github.com/o/gaia", "reason": "r"},
            {"workspace": "ws", "section": "infrastructure", "keys": ["infra_a"], "to": "github.com/o/infra", "reason": "r"},
            {"workspace": "ws", "section": "infrastructure", "keys": ["gaia_b"], "to": "github.com/o/gaia", "reason": "r"},
            {"workspace": "me", "section": "project_identity", "keys": ["gaia"], "to": "github.com/o/gaia", "reason": "r"},
            {"workspace": "me", "section": "project_identity", "keys": ["infra"], "to": "github.com/o/infra", "reason": "r"},
        ],
        "unowned": [
            {"workspace": "ws", "section": "infrastructure", "keys": ["org_c"], "candidates": [], "why": "w"},
        ],
    }
    manifest.update(over)
    return manifest


def _rows(path, table):
    con = sqlite3.connect(str(path))
    try:
        return con.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
    finally:
        con.close()


def _plan(path, manifest):
    con = sqlite3.connect(str(path))
    try:
        return plan_relocation(con, manifest)
    finally:
        con.close()


def test_plan_slices_payloads_by_key_and_writes_nothing(db):
    before = _rows(db, "project_context")
    plan = _plan(db, _manifest())
    assert plan["errors"] == []
    inserts = {(i["project"], i["section"]): json.loads(i["payload"]) for i in plan["inserts"]}
    assert inserts == {
        ("github.com/o/gaia", "stack"): {"languages": []},
        ("github.com/o/infra", "infrastructure"): {"infra_a": 1},
        ("github.com/o/gaia", "infrastructure"): {"gaia_b": 2},
        ("github.com/o/gaia", "project_identity"): {"gaia": {"name": "gaia"}},
        ("github.com/o/infra", "project_identity"): {"infra": {"name": "infra"}},
    }
    assert _rows(db, "project_context") == before


def test_a_key_left_out_of_the_manifest_is_an_error(db):
    manifest = _manifest(unowned=[])
    plan = _plan(db, manifest)
    assert any("ws/infrastructure" in e and "org_c" in e for e in plan["errors"])


def test_a_key_claimed_twice_is_an_error(db):
    manifest = _manifest()
    manifest["unowned"].append({"workspace": "ws", "section": "stack", "candidates": [], "why": "w"})
    assert any("ws/stack" in e for e in _plan(db, manifest)["errors"])


def test_an_inactive_or_unknown_project_is_an_error(db):
    manifest = _manifest()
    manifest["moves"][0]["to"] = "github.com/o/gone"
    manifest["moves"][1]["to"] = "github.com/o/nowhere"
    errors = _plan(db, manifest)["errors"]
    assert any("github.com/o/gone" in e for e in errors)
    assert any("github.com/o/nowhere" in e for e in errors)


def test_two_moves_into_one_target_section_is_an_error(db):
    manifest = _manifest()
    manifest["moves"][2]["to"] = "github.com/o/infra"
    assert any("github.com/o/infra/infrastructure" in e for e in _plan(db, manifest)["errors"])


def test_a_different_payload_already_at_the_target_is_refused_not_overwritten(db):
    con = sqlite3.connect(str(db))
    con.execute("INSERT INTO project_context (project_identity, contract_name, payload) "
                "VALUES ('github.com/o/gaia', 'stack', '{\"languages\": [\"go\"]}')")
    con.commit()
    con.close()
    assert any("github.com/o/gaia/stack" in e for e in _plan(db, _manifest())["errors"])
    with pytest.raises(RelocationError):
        apply_relocation(_manifest(), db_path=db)
    assert ("github.com/o/gaia", "stack", '{"languages": ["go"]}') == _rows(db, "project_context")[0][:3]


def test_apply_backs_up_inserts_only_and_keeps_the_old_table(db):
    old = _rows(db, "project_context_contracts")
    report = apply_relocation(_manifest(), db_path=db)
    backup = Path(report["backup"])
    assert backup.parent == db.parent / "backups" and backup.exists()
    assert _rows(backup, "project_context") == []
    assert _rows(db, "project_context_contracts") == old
    assert len(_rows(db, "project_context")) == 5
    assert len(_rows(db, "project_context_history")) == 5
    assert report["inserted"] == 5


def test_rerunning_an_applied_manifest_skips_identical_rows(db):
    apply_relocation(_manifest(), db_path=db)
    plan = _plan(db, _manifest())
    assert plan["errors"] == [] and plan["inserts"] == [] and len(plan["already_present"]) == 5


def test_moved_section_reads_through_the_project(db):
    apply_relocation(_manifest(), db_path=db)
    from gaia.store.project_context import project_sections, resolve_project

    con = sqlite3.connect(str(db))
    try:
        sections = project_sections(con, resolve_project(con, "github.com/o/infra"))
    finally:
        con.close()
    assert sections["infrastructure"]["payload"] == {"infra_a": 1}
    assert sections["infrastructure"]["source"] == "project:github.com/o/infra"
