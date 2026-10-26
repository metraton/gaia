"""Contract fields no code reads leave the envelope; rows that carry them still read (plan 94, T15)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (_REPO_ROOT, _REPO_ROOT / "bin", _REPO_ROOT / "hooks"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

WORKSPACE = "me"
AGENT = "a" + "2" * 16
DROPPED = {
    "work_phase": "framing",
    "rollback_executed": "git revert abc",
    "context_consumption": {"tokens_used": 1200, "pct_window": 0.4},
    "memory_suggestions": ["remember the port"],
}


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
        con.commit()
    finally:
        con.close()
    return db_path()


def _create(slug: str) -> dict:
    return {"slug": slug, "type": "atom", "description": f"Fact: {slug}.", "body": "body"}


def _close(contract_id: str, envelope: dict) -> int:
    from gaia.store.writer import finalize_agent_contract_handoff, insert_dispatched_handoff

    insert_dispatched_handoff(contract_id, AGENT, WORKSPACE)
    return finalize_agent_contract_handoff(
        contract_id=contract_id, agent_id=AGENT, workspace=WORKSPACE,
        agent_state="COMPLETE", raw_handoff_json=json.dumps(envelope),
    )["handoff_id"]


def _cli(capsys, *argv) -> tuple[int, str]:
    from cli.contract import register

    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="subcommand"))
    args = parser.parse_args(["contract", *argv])
    rc = args.func(args)
    captured = capsys.readouterr()
    return rc, captured.out + captured.err


def test_contract_unread_fields_dropped_from_validator_and_parsers():
    from gaia.contract import validator
    from modules.agents import contract_validator

    for field in DROPPED:
        assert field not in validator.AGENT_WRITABLE_TOP_LEVEL_KEYS
        assert field not in validator.TOP_LEVEL_ENVELOPE_KEYS
    assert "worktree_scope" not in validator.AGENT_WRITABLE_TOP_LEVEL_KEYS
    assert "worktree_scope" in validator.SYSTEM_WRITTEN_ENVELOPE_KEYS
    assert not hasattr(validator, "VALID_WORK_PHASES")
    assert not hasattr(validator.FormErrorCode, "WORK_PHASE_SHAPE")
    for parser in ("parse_rollback_executed", "parse_context_consumption", "parse_memory_suggestions"):
        assert not hasattr(contract_validator, parser)


@pytest.mark.parametrize("field", sorted(DROPPED))
def test_contract_unread_fields_dropped_a_new_write_is_refused(field):
    from gaia.contract.drafts import initial_envelope
    from gaia.contract.validator import FormErrorCode, validate_form

    envelope = dict(initial_envelope(AGENT), **{field: DROPPED[field]})
    result = validate_form(envelope)

    assert [(e.code, e.field) for e in result.errors] == [(FormErrorCode.UNKNOWN_FIELD, field)]


def test_contract_unread_fields_dropped_from_initial_envelope():
    from gaia.contract.drafts import initial_envelope
    from gaia.contract.validator import validate_form

    envelope = initial_envelope(AGENT)
    assert set(envelope).isdisjoint(set(DROPPED) | {"worktree_scope"})
    assert validate_form(envelope).ok


def test_contract_unread_fields_dropped_both_memory_lanes_land_as_one_kind(db):
    from gaia.store.proposals import list_proposals

    envelope = {
        "agent_status": {"agent_state": "COMPLETE", "agent_id": AGENT,
                         "pending_steps": [], "next_action": "done"},
        "memory_delta": {"version": 1, "proposals": [
            {"operation": "create", "create": _create("atom_from_delta")}]},
        "memorialize_suggestions": [_create("atom_from_memorialize")],
    }
    _close(f"{AGENT}.000000000001", envelope)

    pending = list_proposals(db_path=db)
    assert [(p["kind"], p["payload"]["create"]["slug"]) for p in pending] == [
        ("memory", "atom_from_delta"), ("memory", "atom_from_memorialize")]


def test_contract_unread_fields_dropped_an_old_row_still_reads(db, capsys):
    from gaia.store.proposals import list_proposals

    contract_id = f"{AGENT}.000000000002"
    old_row = dict(
        DROPPED,
        agent_status={"agent_state": "COMPLETE", "agent_id": AGENT,
                      "pending_steps": [], "next_action": "done"},
        evidence_report={key: [] for key in (
            "patterns_checked", "files_checked", "commands_run", "key_outputs",
            "verbatim_outputs", "cross_layer_impacts", "open_gaps")},
        worktree_scope="managed",
        memorialize_suggestions=[_create("atom_old_row")],
        user_facing_summary="An old row closed before the rename.",
    )
    _close(contract_id, old_row)

    rc, out = _cli(capsys, "view", "--draft-id", contract_id, "--json")
    assert rc == 0, out
    assert '"rollback_executed": "git revert abc"' in out
    assert '"work_phase": "framing"' in out

    rc, out = _cli(capsys, "fill-rate", "--since", "1d", "--json")
    assert rc == 0, out
    assert json.loads(out)

    assert [p["payload"]["create"]["slug"] for p in list_proposals(db_path=db)] == ["atom_old_row"]
