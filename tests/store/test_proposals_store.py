"""The pending-proposal store: what a closed turn proposed waits, indexed, for the orchestrator.

Measured defect (plan 94, F4): a ``memory_delta`` or ``update_contracts``
proposal lived only in the row's JSON and in one PostToolUse notice, so a
compaction, a background dispatch or a cut row lost its only index, and no verb
listed, accepted or dismissed it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (_REPO_ROOT, _REPO_ROOT / "bin", _REPO_ROOT / "hooks"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

WORKSPACE = "me"
PROJECT_ID = "github.com/metraton/gaia"
DISPATCH_PROJECT = "gaia (/repos/gaia)"
AGENT = "a" + "1" * 16


@pytest.fixture()
def db(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for key in ("GAIA_DB", "GAIA_DISPATCH_AGENT", "GAIA_WORKSPACE", "GAIA_SESSION_ID"):
        monkeypatch.delenv(key, raising=False)
    from gaia.paths import db_path
    from gaia.store.writer import _connect

    con = _connect()
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES (?, ?)",
                    (WORKSPACE, WORKSPACE))
        con.execute(
            "INSERT INTO projects (workspace, name, path, project_identity) VALUES (?, ?, ?, ?)",
            (WORKSPACE, "gaia", "/repos/gaia", PROJECT_ID),
        )
        con.commit()
    finally:
        con.close()
    return db_path()


def _memory(slug: str) -> dict:
    return {"operation": "create", "create": {
        "slug": slug, "type": "project", "description": f"Fact: {slug}.", "body": "body"}}


def _envelope(state: str, *, memory=(), context=None) -> str:
    envelope = {
        "agent_status": {"agent_state": state, "agent_id": AGENT,
                         "pending_steps": [], "next_action": "done"},
        "memory_delta": {"version": 1, "proposals": [_memory(s) for s in memory]},
    }
    if context is not None:
        envelope["update_contracts"] = context
    return json.dumps(envelope)


def _close(contract_id: str, state: str, raw: str, *, dispatch_project=DISPATCH_PROJECT,
           cut_reason=None, born=True) -> int:
    from gaia.store.writer import finalize_agent_contract_handoff, insert_dispatched_handoff

    if born:
        insert_dispatched_handoff(contract_id, AGENT, WORKSPACE,
                                  dispatch_project=dispatch_project)
    return finalize_agent_contract_handoff(
        contract_id=contract_id, agent_id=AGENT, workspace=WORKSPACE,
        agent_state=state, raw_handoff_json=raw, cut_reason=cut_reason,
    )["handoff_id"]


def _cli(capsys, *argv) -> tuple[int, str]:
    from cli.contract import register

    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="subcommand"))
    args = parser.parse_args(["contract", "proposals", *argv])
    rc = args.func(args)
    captured = capsys.readouterr()
    return rc, captured.out + captured.err


def test_proposals_store_records_each_kind_at_finalize(db):
    from gaia.store.proposals import list_proposals

    handoff_id = _close(f"{AGENT}.000000000001", "COMPLETE", _envelope(
        "COMPLETE", memory=["atom_one"],
        context=[{"contract": "stack", "payload": {"languages": ["python"]}}]))

    pending = list_proposals(db_path=db)
    assert [(p["kind"], p["handoff_id"], p["project"]) for p in pending] == [
        ("memory", handoff_id, PROJECT_ID), ("context", handoff_id, PROJECT_ID)]
    assert pending[0]["payload"]["create"]["slug"] == "atom_one"
    assert pending[1]["payload"] == {"contract": "stack", "payload": {"languages": ["python"]}}


def test_proposals_store_records_on_a_cut_lane(db):
    from gaia.state import CUT_REASON_BACKSTOP_CAPTURE
    from gaia.store.proposals import list_proposals

    _close(f"{AGENT}.000000000002", "IN_PROGRESS", _envelope("IN_PROGRESS", memory=["atom_cut"]),
           cut_reason=CUT_REASON_BACKSTOP_CAPTURE)

    assert [p["payload"]["create"]["slug"] for p in list_proposals(db_path=db)] == ["atom_cut"]


def test_proposals_store_refinalize_replaces_pending_and_keeps_decisions(db):
    from gaia.store.proposals import decide_proposal, list_proposals

    contract_id = f"{AGENT}.000000000003"
    _close(contract_id, "NEEDS_INPUT", _envelope("NEEDS_INPUT", memory=["atom_a", "atom_b"]))
    first = list_proposals(db_path=db)[0]
    decide_proposal(first["id"], "dismissed", decided_by="orchestrator", db_path=db)

    _close(contract_id, "COMPLETE", _envelope("COMPLETE", memory=["atom_a", "atom_c"]), born=False)

    every = list_proposals(status=None, db_path=db)
    assert [(p["payload"]["create"]["slug"], p["status"]) for p in every] == [
        ("atom_a", "dismissed"), ("atom_c", "pending")]


def test_proposals_store_context_accept_writes_and_records_the_decision(db, capsys):
    from gaia.store.proposals import list_proposals

    _close(f"{AGENT}.000000000004", "COMPLETE", _envelope(
        "COMPLETE", context=[{"contract": "stack", "payload": {"languages": ["go"]}}]))
    proposal = list_proposals(db_path=db)[0]

    rc, out = _cli(capsys, "accept", str(proposal["id"]))

    assert rc == 0, out
    con = sqlite3.connect(str(db))
    try:
        stored = con.execute(
            "SELECT payload FROM project_context WHERE project_identity = ? "
            "AND contract_name = 'stack'", (PROJECT_ID,)).fetchone()
        decision = con.execute(
            "SELECT p.status, p.project, p.decided_by, h.contract_id FROM proposals p "
            "JOIN agent_contract_handoffs h ON h.id = p.handoff_id WHERE p.id = ?",
            (proposal["id"],)).fetchone()
    finally:
        con.close()
    assert json.loads(stored[0]) == {"languages": ["go"]}
    assert decision == ("accepted", PROJECT_ID, "orchestrator", f"{AGENT}.000000000004")


def test_proposals_store_a_subagent_cannot_decide(db, capsys, monkeypatch):
    from gaia.store.proposals import list_proposals

    _close(f"{AGENT}.000000000005", "COMPLETE", _envelope("COMPLETE", memory=["atom_mine"]))
    proposal = list_proposals(db_path=db)[0]
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")

    for verb in ("accept", "dismiss"):
        rc, out = _cli(capsys, verb, str(proposal["id"]))
        assert rc == 1
        assert "orchestrator" in out
    assert list_proposals(db_path=db)[0]["status"] == "pending"


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_proposals_store_backfill_runs_on_a_copy_and_leaves_the_source(db, tmp_path):
    from gaia.store.proposals import backfill_proposals, list_proposals

    con = sqlite3.connect(str(db))
    try:
        for days, slug in ((3, "atom_recent"), (45, "atom_old")):
            con.execute(
                "INSERT INTO agent_contract_handoffs (contract_id, agent_id, workspace, "
                "agent_state, raw_handoff_json, created_at) VALUES (?, ?, ?, 'COMPLETE', ?, ?)",
                (f"{AGENT}.{slug}", AGENT, WORKSPACE,
                 _envelope("COMPLETE", memory=[slug]), _ago(days)))
        con.commit()
    finally:
        con.close()
    copy = tmp_path / "copy.db"
    shutil.copyfile(db, copy)

    dry = backfill_proposals(since_days=30, apply=False, db_path=copy)
    assert (dry["rows_with_proposals"], dry["proposals"], dry["inserted"]) == (1, 1, 0)
    assert list_proposals(db_path=copy) == []

    applied = backfill_proposals(since_days=30, apply=True, db_path=copy)
    again = backfill_proposals(since_days=30, apply=True, db_path=copy)

    assert applied["inserted"] == 1 and again["inserted"] == 0
    assert [p["payload"]["create"]["slug"] for p in list_proposals(db_path=copy)] == ["atom_recent"]
    assert list_proposals(db_path=db) == []


def test_proposals_store_orchestrator_lane_admits_the_verbs():
    from modules.security.gaia_cli_only_guard import (
        ALLOWED_READ_PHRASES, ALLOWED_WRITE_PHRASES)

    assert ("contract", "proposals", "list") in ALLOWED_READ_PHRASES
    assert {("contract", "proposals", "accept"), ("contract", "proposals", "dismiss")} <= (
        ALLOWED_WRITE_PHRASES)
    assert not any(phrase[:3] == ("contract", "proposals", "backfill")
                   for phrase in ALLOWED_READ_PHRASES | ALLOWED_WRITE_PHRASES)


@pytest.mark.parametrize("command", [
    "gaia contract proposals list --json",
    "gaia contract proposals accept 7 --project github.com/metraton/gaia",
    "gaia contract proposals dismiss 7 --reason duplicate",
])
def test_proposals_store_verbs_are_not_t3(command):
    from modules.security.tiers import SecurityTier, classify_command_tier

    assert classify_command_tier(command) != SecurityTier.T3_BLOCKED
