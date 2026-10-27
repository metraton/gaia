"""What a closed turn proposed waits, indexed, until the orchestrator decides it (plan 94, F4)."""

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
        "slug": slug, "type": "atom" if slug.startswith("atom_") else "project",
        "description": f"Fact: {slug}.", "body": "body"}}


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


def test_proposals_store_accept_runs_when_contract_py_is_a_script(db):
    import os
    import subprocess

    from gaia.store.proposals import list_proposals

    _close(f"{AGENT}.000000000008", "COMPLETE", _envelope("COMPLETE", memory=["project_script"]))
    proposal_id = list_proposals(db_path=db)[0]["id"]
    env = {k: v for k, v in os.environ.items() if k != "GAIA_DISPATCH_AGENT"}
    done = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "bin" / "cli" / "contract.py"),
         "proposals", "accept", str(proposal_id)],
        capture_output=True, text=True, env=env, timeout=120)

    assert done.returncode == 0, done.stderr
    assert list_proposals(db_path=db) == []


def test_proposals_store_orchestrator_lane_admits_the_verbs():
    from modules.security.gaia_cli_only_guard import (
        ALLOWED_READ_PHRASES, ALLOWED_WRITE_PHRASES)

    assert ("contract", "proposals", "list") in ALLOWED_READ_PHRASES
    assert {("contract", "proposals", "accept"), ("contract", "proposals", "dismiss")} <= (
        ALLOWED_WRITE_PHRASES)
    assert not any(phrase[:3] == ("contract", "proposals", "backfill")
                   for phrase in ALLOWED_READ_PHRASES | ALLOWED_WRITE_PHRASES)


def test_proposals_store_prune_keeps_a_row_until_its_proposal_is_decided(db):
    from gaia.store.proposals import decide_proposal, list_proposals
    from gaia.store.writer import prune_handoffs

    pending_row = _close(f"{AGENT}.000000000006", "COMPLETE",
                         _envelope("COMPLETE", memory=["atom_pending"]))
    plain_row = _close(f"{AGENT}.000000000007", "COMPLETE", _envelope("COMPLETE"))
    con = sqlite3.connect(str(db))
    try:
        con.execute("UPDATE agent_contract_handoffs SET created_at = ? WHERE id IN (?, ?)",
                    (_ago(120), pending_row, plain_row))
        con.commit()
    finally:
        con.close()

    assert prune_handoffs(cutoff_days=90, db_path=db)["deleted"] == 1
    kept = list_proposals(db_path=db)
    assert [(p["handoff_id"], p["payload"]["create"]["slug"]) for p in kept] == [
        (pending_row, "atom_pending")]

    decide_proposal(kept[0]["id"], "dismissed", decided_by="orchestrator", db_path=db)
    assert prune_handoffs(cutoff_days=90, db_path=db)["deleted"] == 1
    assert list_proposals(status=None, db_path=db) == []


@pytest.mark.parametrize("agent", ["developer", "gaia-operator"])
@pytest.mark.parametrize("command", [
    "gaia contract proposals accept 7 --project github.com/metraton/gaia",
    "cd /repo && gaia contract proposals --json dismiss 7",
    "python3 /x/bin/gaia contract proposals accept 7",
])
def test_proposals_store_hook_guard_refuses_a_subagent_decision(command, agent):
    from modules.security.subagent_memory_write_guard import check

    allowed, reason = check(command, is_subagent=True, agent_type=agent)
    assert not allowed and "orchestrator" in reason
    assert check(command, is_subagent=False) == (True, None)


@pytest.mark.parametrize("command", [
    "gaia contract proposals list --json",
    "gaia contract proposals accept --help",
    "gaia contract view --draft-id x",
])
def test_proposals_store_hook_guard_lets_a_subagent_read(command):
    from modules.security.subagent_memory_write_guard import check

    assert check(command, is_subagent=True, agent_type="developer") == (True, None)


CONTRACT_PY = _REPO_ROOT / "bin" / "cli" / "contract.py"
MEMORY_ADD = "memory add --name user_x --type user --body b --description d"
DECISION_ENTRY_POINTS = [
    "gaia contract proposals accept 1",
    f"python3 {_REPO_ROOT}/bin/gaia contract proposals accept 1",
    f"python3 {CONTRACT_PY} proposals accept 1",
    f"env -u GAIA_DISPATCH_AGENT python3 {CONTRACT_PY} proposals dismiss 1",
    f"GAIA_DISPATCH_AGENT= python3 -W ignore {CONTRACT_PY} proposals accept 1",
    f"{CONTRACT_PY} proposals --json dismiss 1",
    f"cd {_REPO_ROOT}/bin && python3 -m cli.contract proposals accept 1",
    "python3 -I -mcli.contract proposals dismiss 1",
    f"cd {_REPO_ROOT}/bin/cli && python3 contract.py proposals accept 1",
    f"uv run {CONTRACT_PY} proposals accept 1",
    f"timeout 5 nice python3 {CONTRACT_PY} proposals accept 1",
    "{gaia_link} contract proposals accept 1",
    "env -u GAIA_DISPATCH_AGENT {gaia_link} contract proposals dismiss 1",
    "python3 {contract_link} proposals accept 1",
    f"{{gaia_link}} {MEMORY_ADD}",
    f"env -u GAIA_DISPATCH_AGENT {{gaia_link}} {MEMORY_ADD}",
]


@pytest.fixture()
def links(tmp_path):
    gaia_link, contract_link = tmp_path / "g", tmp_path / "helper"
    gaia_link.symlink_to(_REPO_ROOT / "bin" / "gaia")
    contract_link.symlink_to(CONTRACT_PY)
    return {"gaia_link": str(gaia_link), "contract_link": str(contract_link)}


def _subagent_verdict(command: str, agent: str):
    from modules.tools.bash_validator import BashValidator

    return BashValidator().validate(command, is_subagent=True, session_id="s", agent_type=agent,
                                    hook_payload={"agent_type": agent, "agent_id": "a1"})


@pytest.mark.parametrize("agent", ["developer", "gaia-operator"])
@pytest.mark.parametrize("command", DECISION_ENTRY_POINTS)
def test_proposals_store_every_cli_entry_point_is_refused_to_a_subagent(command, agent, links):
    if agent == "gaia-operator" and "memory add" in command:
        pytest.skip("gaia-operator is the sanctioned memory writer")
    result = _subagent_verdict(command.format(**links), agent)

    assert result.allowed is False, (result.tier, result.reason)


@pytest.mark.parametrize("command", [
    "gaia contract proposals list --json",
    "{gaia_link} contract proposals list",
    f"cat {CONTRACT_PY}",
    "{gaia_link} memory search proposals",
])
def test_proposals_store_a_subagent_still_reads_through_any_entry_point(command, links):
    assert _subagent_verdict(command.format(**links), "developer").allowed is True


@pytest.mark.parametrize("command", [
    f"python3 {CONTRACT_PY} proposals list --json",
    f"python3 {CONTRACT_PY} proposals accept --help",
])
def test_proposals_store_a_by_path_read_from_a_dev_checkout_asks_for_a_signature(command):
    """By path from a git checkout the script is judged by its content; the `gaia` CLI is the read path."""
    assert _subagent_verdict(command, "developer").allowed is False


COMPUTED_OR_IDENTITY_CLEARING = [
    "echo accept 1 | xargs env -u GAIA_DISPATCH_AGENT gaia contract proposals",
    "echo accept 1 | xargs gaia contract proposals",
    "xargs -a /tmp/verb gaia contract proposals",
    "gaia contract proposals $(echo dismiss) 1",
    "gaia contract proposals `echo dismiss` 1",
    "V=accept; gaia contract proposals \"$V\" 1",
    "gaia contract proposals {accept,} 1",
    "gaia contract proposals acc?pt 1",
    "G=gaia; $G contract proposals $(echo dismiss) 1",
    f"python3 {CONTRACT_PY} proposals ${{VERB}} 1",
    "gaia memory $(printf add) --name user_x --type user --body b --description d",
    "env -u GAIA_DISPATCH_AGENT gaia contract view",
    "env --unset=GAIA_DISPATCH_AGENT python3 tool.py",
    "unset GAIA_DISPATCH_AGENT; gaia contract proposals list",
    "GAIA_DISPATCH_AGENT= gaia contract view",
    "export GAIA_DISPATCH_AGENT=gaia-orchestrator",
    "env -i PATH=/usr/bin gaia contract view",
]


@pytest.mark.parametrize("agent", ["developer", "gaia-operator"])
@pytest.mark.parametrize("command", COMPUTED_OR_IDENTITY_CLEARING)
def test_proposals_store_computed_words_or_a_cleared_identity_are_refused(command, agent):
    from modules.security.subagent_memory_write_guard import check

    assert _subagent_verdict(command, agent).allowed is False
    assert check(command, is_subagent=False) == (True, None)


@pytest.mark.parametrize("command", [
    "gaia contract add --draft-id x evidence_report.key_outputs 'literal $not expanded'",
    "gaia contract fill --draft-id x --json-file - <<'EOF'\n{\"a\": \"$x `y`\"}\nEOF",
    "grep -rn GAIA_DISPATCH_AGENT /repo/hooks",
    "echo $HOME",
    "find . -name '*.py' | xargs grep -n foo",
])
def test_proposals_store_literal_gaia_words_and_plain_reads_stay_free(command):
    assert _subagent_verdict(command, "developer").allowed is True


@pytest.mark.parametrize("command, refused", [
    ("env -i PATH=/usr/bin gaia contract view", True),
    ("env -S 'gaia contract view'", False),
    ("env -iS 'gaia contract view'", True),
    ("export -n GAIA_DISPATCH_AGENT", True),
    ("env -u GAIA_DISPATCH_AGENT ls", True),
    ("env -i ls", False),
    ("env -S '-i FOO=1 ls'", False),
])
def test_proposals_store_clearing_the_environment_is_refused_only_around_gaia(command, refused):
    from modules.security.subagent_memory_write_guard import IDENTITY_REJECTION, check

    result = _subagent_verdict(command, "developer")
    assert (check(command, is_subagent=True, agent_type="developer")[1] == IDENTITY_REJECTION) is refused
    assert (result.rule == "floor.dispatch-identity") is refused


@pytest.mark.parametrize("command", [
    "gaia contract proposals \"$(cat <<'EOF'\naccept\nEOF\n)\" 1",
    "gaia contract proposals \"$(cat <<\"EOF\"\ndismiss\nEOF\n)\" 1",
])
def test_proposals_store_a_verb_printed_by_a_quoted_cat_heredoc_is_still_the_decision(command):
    """A quoted cat heredoc is read as the literal value it prints, so a decision verb inside it is refused."""
    from modules.security.subagent_memory_write_guard import PROPOSAL_DECISION_REJECTION, check

    assert check(command, is_subagent=True, agent_type="developer") == (False, PROPOSAL_DECISION_REJECTION)
    assert _subagent_verdict(command, "gaia-operator").allowed is False


@pytest.mark.parametrize("command", [
    "gaia contract add x \"$(cat <<EOF\nvalue\nEOF\n)\"",
    "gaia contract proposals $(echo dismiss) 1",
])
def test_proposals_store_computed_gaia_words_are_refused_under_their_own_rule(command):
    """An unquoted heredoc delimiter still expands, so its word stays computed; the refusal names its own rule."""
    from modules.security.subagent_memory_write_guard import COMPUTED_WORDS_REJECTION, check

    assert check(command, is_subagent=True, agent_type="developer") == (False, COMPUTED_WORDS_REJECTION)
    assert _subagent_verdict(command, "developer").rule == "floor.computed-gaia-words"


@pytest.mark.parametrize("command", [
    "$(which python3) deploy.py",
    "timeout 5 $(which tool) list",
    "G=gaia; $G contract proposals $(echo dismiss) 1",
    "[ $X = foo ]",
])
def test_proposals_store_a_computed_program_word_keeps_the_validator_verdict(command):
    from modules.security.subagent_memory_write_guard import COMPUTED_WORDS_REJECTION

    result = _subagent_verdict(command, "developer")
    assert COMPUTED_WORDS_REJECTION not in (result.reason or "")
    assert result.allowed is ("$X" in command)


def _accept_namespace(proposal_id: int):
    return argparse.Namespace(proposal_id=proposal_id, project=PROJECT_ID, workspace=None,
                              json=True)


def test_proposals_store_accept_that_loses_a_race_writes_nothing(db, monkeypatch, capsys):
    from cli import contract_proposals
    from gaia.store import proposals as store

    _close(f"{AGENT}.000000000009", "COMPLETE", _envelope("COMPLETE", memory=["project_raced"]))
    pending = store.list_proposals(db_path=db)[0]
    assert store.decide_proposal(pending["id"], "dismissed", decided_by="orchestrator")
    monkeypatch.setattr(store, "get_proposal", lambda _pid, **_kw: pending)

    rc = contract_proposals._cmd_accept(_accept_namespace(pending["id"]))

    con = sqlite3.connect(str(db))
    try:
        written = con.execute("SELECT count(*) FROM memory WHERE name = 'project_raced'").fetchone()
        status = con.execute("SELECT status FROM proposals WHERE id = ?", (pending["id"],)).fetchone()
    finally:
        con.close()
    assert (rc, written[0], status[0]) == (1, 0, "dismissed"), capsys.readouterr().out


@pytest.mark.parametrize("tampered", [
    {"slug": "atom_typed_project"},
    {"type": "fact"},
])
def test_proposals_store_an_accept_whose_write_fails_or_exits_stays_pending(db, tampered):
    from cli import contract_proposals
    from gaia.store.proposals import list_proposals

    _close(f"{AGENT}.00000000000a", "COMPLETE", _envelope("COMPLETE", memory=["project_fails"]))
    proposal = list_proposals(db_path=db)[0]
    payload = dict(proposal["payload"], create=dict(proposal["payload"]["create"], **tampered))
    con = sqlite3.connect(str(db))
    try:
        con.execute("UPDATE proposals SET payload = ? WHERE id = ?",
                    (json.dumps(payload), proposal["id"]))
        con.commit()
    finally:
        con.close()

    assert contract_proposals._cmd_accept(_accept_namespace(proposal["id"])) == 1
    after = list_proposals(status=None, db_path=db)[0]
    assert (after["status"], after["project"], after["decided_at"]) == (
        "pending", proposal["project"], None)


@pytest.mark.parametrize("create", [
    {"slug": "atom_typed_fact", "type": "fact"},
    {"slug": "bad slug with spaces!", "type": "atom"},
])
def test_proposals_store_intake_refuses_what_memory_cannot_write(db, create, capsys):
    from gaia.store.proposals import list_proposals

    raw = json.loads(_envelope("COMPLETE", memory=["project_kept"]))
    raw["memory_delta"]["proposals"].append({"operation": "create", "create": dict(
        create, description="Fact: x.", body="x")})
    _close(f"{AGENT}.00000000000c", "COMPLETE", json.dumps(raw))

    assert [p["payload"]["create"]["slug"] for p in list_proposals(db_path=db)] == ["project_kept"]
    assert create["slug"] in capsys.readouterr().err


def test_proposals_store_a_refused_proposal_is_recorded_in_the_row_once(db):
    from gaia.store.writer import _connect

    raw = json.loads(_envelope("COMPLETE", memory=["project_kept"]))
    raw["memory_delta"]["proposals"].append({"operation": "create", "create": {
        "slug": "atom_typed_fact", "type": "fact", "description": "Fact: x.", "body": "x"}})
    contract_id = f"{AGENT}.00000000000d"
    handoff_id = _close(contract_id, "COMPLETE", json.dumps(raw))
    _close(contract_id, "COMPLETE", json.dumps(raw), born=False)

    con = _connect()
    try:
        stored = json.loads(con.execute(
            "SELECT raw_handoff_json FROM agent_contract_handoffs WHERE id = ?",
            (handoff_id,)).fetchone()[0])
    finally:
        con.close()
    gaps = [g for g in stored["evidence_report"]["open_gaps"] if "atom_typed_fact" in g]
    assert len(gaps) == 1 and "fact" in gaps[0]


@pytest.mark.parametrize("command", [
    "gaia contract proposals list --json",
    "gaia contract proposals accept 7 --project github.com/metraton/gaia",
    "gaia contract proposals dismiss 7 --reason duplicate",
])
def test_proposals_store_verbs_are_not_t3(command):
    from modules.security.tiers import SecurityTier, classify_command_tier

    assert classify_command_tier(command) != SecurityTier.T3_BLOCKED
