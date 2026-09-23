"""Contract of the host-neutral approval core (plan 76, task 1).

Every host adapter calls ``gaia.approvals.core`` to seal, present, decide,
consume and withdraw a signature. These tests pin the invariants the brief
decides (D2 declared exits, D3 sealed directory, D4 one 30-minute window,
D5 withdraw-never-approve, D6 session+agent binding, D14 one key per request
and per command) against the neutral entry points, never against an adapter.
"""

from __future__ import annotations

import argparse
import io
import json
import sqlite3
from contextlib import redirect_stdout

import pytest

from gaia.store import writer

COMMANDS = ["git push origin feat/x", "gh pr create --base main --fill"]
REPO = "/tmp/approval-core-repo"
SESSION = "ses-requester"
AGENT = "gaia-system"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DB", raising=False)
    con = sqlite3.connect(tmp_path / "gaia.db")
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()
    return tmp_path / "gaia.db"


def _row(db_path, sql, *args):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(sql, args).fetchone()
        return dict(row) if row is not None else None
    finally:
        con.close()


def _payload(db_path, approval_id):
    return json.loads(_row(db_path, "SELECT payload_json FROM approvals WHERE id=?", approval_id)["payload_json"])


def _set_items(expect_exit=None):
    expect_exit = expect_exit or {}
    return [
        {"command": command, "cwd": REPO, "expect_exit": expect_exit.get(i, [])}
        for i, command in enumerate(COMMANDS)
    ]


def _approved_set(db_path, *, expect_exit=None):
    from gaia.approvals import core

    approval_id = core.request_command_set(
        _set_items(expect_exit), what="Publish the branch and open the PR",
        session_id=SESSION, agent_id=AGENT,
    )
    core.record_presentation(approval_id, native_ref="toolu_present", session_id="ses-orch", agent_id="orchestrator")
    result = core.decide(native_ref="toolu_present", option_key="approve", session_id="ses-orch")
    assert result.status == "activated", result
    return approval_id


# --------------------------------------------------------------------------- #
# Window (D4)
# --------------------------------------------------------------------------- #

def test_approval_core_contract_single_30_minute_window():
    from gaia.approvals import core
    from modules.security import approval_grants

    assert core.WINDOW_MINUTES == 30
    assert writer.APPROVAL_GRANT_TTL_MINUTES == 30
    assert writer.PLAN_COMMAND_SET_TTL_MINUTES == 30
    assert writer.FILE_PATH_GRANT_TTL_MINUTES == 30
    assert approval_grants.DEFAULT_COMMAND_SET_TTL_MINUTES == 30
    assert approval_grants.DEFAULT_GRANT_TTL_MINUTES == 30


# --------------------------------------------------------------------------- #
# One constructor (seal)
# --------------------------------------------------------------------------- #

def test_approval_core_contract_seal_carries_every_field():
    from gaia.approvals import core
    from gaia.approvals.command_set import command_fingerprint

    payload = core.seal_request(
        "command_set", _set_items({1: [1]}), what="Publish the branch",
        session_id=SESSION, agent_id=AGENT, rollback=None, verification="gh pr view",
    )
    assert payload["what"] == "Publish the branch"
    assert payload["window_minutes"] == 30
    assert payload["window_starts"] == "decision"
    assert payload["requested_by"] == {"session_id": SESSION, "agent_id": AGENT}
    assert payload["rollback_hint"] is None
    items = payload["items"]
    assert [item["position"] for item in items] == [0, 1]
    assert [item["cwd"] for item in items] == [REPO, REPO]
    assert items[1]["expect_exit"] == [1] and items[0]["expect_exit"] == []
    assert [item["fingerprint"] for item in items] == [command_fingerprint(c) for c in COMMANDS]
    assert [item["key"] for item in items] == [
        core.command_key(i, command_fingerprint(c)) for i, c in enumerate(COMMANDS)
    ]
    assert payload["command_set"] == items
    assert payload["request_key"]


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"what": "  "}, "what"),
        ({"session_id": ""}, "session"),
        ({"agent_id": None}, "agent"),
        ({"items": [{"command": COMMANDS[0], "cwd": "relative/dir", "expect_exit": []}]}, "cwd"),
        ({"items": [{"command": COMMANDS[0], "cwd": REPO, "expect_exit": [0]}]}, "exit"),
    ],
)
def test_approval_core_contract_seal_refuses_incomplete_requests(overrides, message):
    from gaia.approvals import core

    kwargs = {"items": _set_items(), "what": "Publish", "session_id": SESSION, "agent_id": AGENT}
    kwargs.update(overrides)
    items = kwargs.pop("items")
    with pytest.raises(core.SealError, match=message):
        core.seal_request("command_set", items, **kwargs)


def test_approval_core_contract_every_request_kind_is_sealed_alike(db, tmp_path):
    """Reactive Bash, request-set and protected write carry the same sealed shape."""
    from gaia.approvals import core
    from bin.cli.approvals import cmd_request_set
    from modules.tools.bash_validator import _build_sealed_payload

    reactive = _build_sealed_payload(
        COMMANDS[0], "push", "MUTATIVE", agent_type=AGENT, cwd=REPO, session_id=SESSION,
    )

    args = argparse.Namespace(
        command=list(COMMANDS), cwd=[REPO], expect_exit=["2=1"], what="Publish the branch",
        rationale=None, verification=None, rollback=None,
        agent_id=AGENT, session_id=SESSION, json=True,
    )
    out = io.StringIO()
    with redirect_stdout(out):
        assert cmd_request_set(args) == 0
    planned = _payload(db, json.loads(out.getvalue())["approval_id"])
    assert planned["items"][1]["expect_exit"] == [1]

    target = tmp_path / ".claude" / "settings.json"
    verdict = core.protected_write_verdict(str(target), session_id=SESSION, agent_id=AGENT)
    written = _payload(db, verdict["approval_id"])

    for payload in (reactive, planned, written):
        assert payload["window_minutes"] == 30
        assert payload["what"]
        assert payload["requested_by"] == {"session_id": SESSION, "agent_id": AGENT}
        for position, item in enumerate(payload["items"]):
            assert item["position"] == position
            assert item["fingerprint"] and item["key"]
            assert "cwd" in item and "expect_exit" in item
    assert reactive["items"][0]["cwd"] == REPO


# --------------------------------------------------------------------------- #
# Decide (structured decision bound to a recorded presentation, D6)
# --------------------------------------------------------------------------- #

def test_approval_core_contract_decision_needs_a_recorded_presentation(db):
    from gaia.approvals import core

    approval_id = core.request_command_set(
        _set_items(), what="Publish", session_id=SESSION, agent_id=AGENT,
    )
    unbound = core.decide(native_ref="toolu_never_shown", option_key="approve", session_id="ses-orch")
    assert unbound.status == "no_decision"

    core.record_presentation(approval_id, native_ref="toolu_q", session_id="ses-orch", agent_id="orchestrator")
    assert core.decide(native_ref="toolu_q", free_text="yes please", session_id="ses-orch").status == "no_decision"
    assert core.decide(native_ref="toolu_q", option_key="details", session_id="ses-orch").status == "no_decision"
    assert _row(db, "SELECT status FROM approvals WHERE id=?", approval_id)["status"] == "pending"

    result = core.decide(native_ref="toolu_q", option_key="approve", session_id="ses-orch")
    assert result.status == "activated" and result.approval_id == approval_id
    grant = _row(db, "SELECT session_id, agent_id, expires_at, created_at FROM approval_grants WHERE approval_id=?", approval_id)
    assert (grant["session_id"], grant["agent_id"]) == (SESSION, AGENT)


def test_approval_core_contract_reject_answer_rejects_only_its_request(db):
    from gaia.approvals import core

    first = core.request_command_set(_set_items(), what="Publish", session_id=SESSION, agent_id=AGENT)
    second = core.request_command_set(
        [{"command": "git push origin feat/y", "cwd": REPO, "expect_exit": []}],
        what="Publish y", session_id=SESSION, agent_id=AGENT,
    )
    core.record_presentation(first, native_ref="toolu_batch", position=0, session_id="ses-orch", agent_id="orchestrator")
    core.record_presentation(second, native_ref="toolu_batch", position=1, session_id="ses-orch", agent_id="orchestrator")
    assert core.decide(native_ref="toolu_batch", position=0, option_key="reject", session_id="ses-orch").status == "rejected"
    assert _row(db, "SELECT status FROM approvals WHERE id=?", first)["status"] == "rejected"
    assert _row(db, "SELECT status FROM approvals WHERE id=?", second)["status"] == "pending"


# --------------------------------------------------------------------------- #
# Consume (sealed directory, byte for byte, in order, no skips, binding)
# --------------------------------------------------------------------------- #

def test_approval_core_contract_command_matches_only_in_its_sealed_directory(db):
    from gaia.approvals import core

    approval_id = _approved_set(db)
    assert core.match_command(COMMANDS[0], cwd="/elsewhere", session_id=SESSION, agent_id=AGENT, tool_use_id="t1") is None
    assert core.match_command(COMMANDS[0], cwd=REPO, session_id=SESSION, agent_id=AGENT, tool_use_id="t2") == {
        "approval_id": approval_id, "index": 0,
    }


def test_approval_core_contract_byte_exact_in_order_without_skips(db):
    from gaia.approvals import core

    _approved_set(db)
    assert core.match_command(COMMANDS[1], cwd=REPO, session_id=SESSION, agent_id=AGENT, tool_use_id="t1") is None
    assert core.match_command(COMMANDS[0] + " ", cwd=REPO, session_id=SESSION, agent_id=AGENT, tool_use_id="t2") is None
    assert core.match_command(COMMANDS[0], cwd=REPO, session_id=SESSION, agent_id=AGENT, tool_use_id="t3")["index"] == 0


def test_approval_core_contract_consumption_bound_to_requesting_session_and_agent(db):
    from gaia.approvals import core

    _approved_set(db)
    assert core.match_command(COMMANDS[0], cwd=REPO, session_id=SESSION, agent_id="developer", tool_use_id="t1") is None
    assert core.match_command(COMMANDS[0], cwd=REPO, session_id="ses-other", agent_id=AGENT, tool_use_id="t2") is None
    assert core.match_command(COMMANDS[0], cwd=REPO, session_id=SESSION, agent_id=AGENT, tool_use_id="t3") is not None


def test_approval_core_contract_declared_nonzero_exit_advances_the_set(db):
    from gaia.approvals import core

    approval_id = _approved_set(db, expect_exit={0: [1]})
    assert core.match_command(COMMANDS[0], cwd=REPO, session_id=SESSION, agent_id=AGENT, tool_use_id="t1")
    assert core.close_command(approval_id, session_id=SESSION, tool_use_id="t1", exit_code=1) == "executed"
    grant = _row(db, "SELECT status, next_index FROM approval_grants WHERE approval_id=?", approval_id)
    assert (grant["status"], grant["next_index"]) == ("PENDING", 1)

    assert core.match_command(COMMANDS[1], cwd=REPO, session_id=SESSION, agent_id=AGENT, tool_use_id="t2")
    assert core.close_command(approval_id, session_id=SESSION, tool_use_id="t2", exit_code=1) == "failed"
    grant = _row(db, "SELECT status, failed_index FROM approval_grants WHERE approval_id=?", approval_id)
    assert (grant["status"], grant["failed_index"]) == ("FAILED", 1)


def test_approval_core_contract_grant_window_counts_from_the_decision(db):
    from datetime import datetime

    approval_id = _approved_set(db)
    grant = _row(db, "SELECT created_at, expires_at FROM approval_grants WHERE approval_id=?", approval_id)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    span = datetime.strptime(grant["expires_at"], fmt) - datetime.strptime(grant["created_at"], fmt)
    assert span.total_seconds() == 30 * 60


# --------------------------------------------------------------------------- #
# Withdraw (D5)
# --------------------------------------------------------------------------- #

def test_approval_core_contract_orchestrator_withdraws_but_never_approves(db):
    from gaia.approvals import core
    from modules.security.gaia_cli_only_guard import (
        ALLOWED_PHRASES,
        EXPLICITLY_DENIED_PHRASES,
        match_allowed_phrase,
    )

    pending = core.request_command_set(_set_items(), what="Publish", session_id=SESSION, agent_id=AGENT)
    with pytest.raises(core.WithdrawError):
        core.withdraw(pending, action="approve", session_id="ses-orch")
    assert core.withdraw(pending, action="reject", session_id="ses-orch") == "rejected"
    assert _row(db, "SELECT status FROM approvals WHERE id=?", pending)["status"] == "rejected"

    for verb in ("reject", "revoke"):
        candidate = ("approvals", verb, "P-" + "a" * 32)
        assert match_allowed_phrase(candidate, ALLOWED_PHRASES) is not None
    approve = ("approvals", "approve", "P-" + "a" * 32)
    assert match_allowed_phrase(approve, ALLOWED_PHRASES) is None
    assert match_allowed_phrase(approve, EXPLICITLY_DENIED_PHRASES) is not None


# --------------------------------------------------------------------------- #
# Protected path request is neutral (OpenCode has something to call)
# --------------------------------------------------------------------------- #

def test_approval_core_contract_protected_write_lives_in_the_core(db, tmp_path):
    from gaia.approvals import core

    target = str(tmp_path / ".claude" / "settings.json")
    first = core.protected_write_verdict(target, session_id=SESSION, agent_id=AGENT)
    assert first["decision"] == "block" and first["window_minutes"] == 30
    again = core.protected_write_verdict(target, session_id=SESSION, agent_id=AGENT)
    assert again["approval_id"] == first["approval_id"]

    core.record_presentation(first["approval_id"], native_ref="toolu_fw", session_id="ses-orch", agent_id="orchestrator")
    assert core.decide(native_ref="toolu_fw", option_key="approve", session_id="ses-orch").status == "activated"
    assert core.protected_write_verdict(target, session_id=SESSION, agent_id=AGENT)["decision"] == "allow"
    assert core.protected_write_verdict(target, session_id=SESSION, agent_id="developer")["decision"] == "block"


def test_approval_core_contract_request_set_under_opencode_seals_the_exported_session(tmp_path, monkeypatch, bootstrapped_db_template):
    """PD6: the OpenCode plugin exports the session to the shell; request-set reads it."""
    import os
    import subprocess
    import sys

    from tests.integration import test_opencode_consent_retry_e2e as e2e

    env, db_path = e2e._isolated_env(tmp_path / "state", bootstrapped_db_template)
    monkeypatch.chdir(env["WORKSPACE"])
    driven = e2e._drive(env, [
        e2e._before("read", "git status"),
        {"kind": "shell-env", "label": "identity", "sessionID": e2e.SESSION_ID, "callID": e2e.CALL_ID},
    ])
    exported = next(step for step in driven["steps"] if step["label"] == "identity")["env"]

    shell = {k: v for k, v in {**env, **exported}.items() if not k.startswith("CLAUDE")}
    result = subprocess.run(
        [sys.executable, str(e2e.GAIA_CLI), "approvals", "request-set", "--command", COMMANDS[0],
         "--cwd", env["WORKSPACE"], "--what", "Publish the branch", "--json"],
        cwd=env["WORKSPACE"], env=shell, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    approval_id = json.loads(result.stdout.strip().splitlines()[-1])["approval_id"]
    assert _payload(db_path, approval_id)["requested_by"] == {
        "session_id": e2e.SESSION_ID, "agent_id": exported["GAIA_DISPATCH_AGENT"],
    }
    assert os.environ.get("CLAUDE_CODE_SESSION_ID") is None


def test_approval_core_contract_orchestrator_reject_all_flag_is_denied_like_reject_all(monkeypatch):
    from modules.security import gaia_cli_only_guard as guard

    gaia = "/abs/path/bin/gaia"
    monkeypatch.setattr(guard, "is_trusted_gaia_binary", lambda token: token == gaia)
    sweep = guard.check(f"{gaia} approvals reject-all", {})
    assert sweep[0] is False
    for command in (
        f"{gaia} approvals reject --all",
        f"{gaia} approvals reject --all --reason stale",
        f"{gaia} approvals reject --al",
        f"{gaia} approvals reject P-{'a' * 32} --all",
    ):
        assert guard.check(command, {}) == sweep, command
    assert guard.check(f"{gaia} approvals reject P-{'a' * 32} --reason stale", {}) == (True, None)
    assert guard.check(f"{gaia} approvals revoke P-{'a' * 32}", {}) == (True, None)


def _approve_reactive(session_id, agent_id, *, native_ref):
    from gaia.approvals import core, store
    from modules.tools.bash_validator import _build_sealed_payload

    payload = _build_sealed_payload(
        COMMANDS[0], "push", "MUTATIVE", agent_type=agent_id, cwd=REPO, session_id=session_id,
    )
    approval_id = store.insert_requested(payload, agent_id=agent_id, session_id=session_id)
    core.record_presentation(approval_id, native_ref=native_ref, session_id="ses-orch", agent_id="orchestrator")
    assert core.decide(native_ref=native_ref, option_key="approve", session_id="ses-orch").status == "activated"
    return approval_id


def _subagent_event(session_id=SESSION, agent_type=AGENT):
    return {"session_id": session_id, "agent_id": "a1b2c3", "agent_type": agent_type,
            "cwd": REPO, "tool_use_id": "toolu_retry"}


def test_approval_core_contract_foreign_first_grant_does_not_hide_the_own_grant(db):
    from modules.tools.bash_validator import BashValidator

    own = _approve_reactive(SESSION, AGENT, native_ref="toolu_own")
    foreign = _approve_reactive("ses-foreign", "developer", native_ref="toolu_foreign")
    con = sqlite3.connect(db)
    con.execute("UPDATE approval_grants SET created_at='2999-01-01T00:00:00Z' WHERE approval_id=?", (foreign,))
    con.commit()
    con.close()

    result = BashValidator().validate(
        COMMANDS[0], is_subagent=True, session_id=SESSION, agent_type=AGENT,
        hook_payload=_subagent_event(),
    )
    assert result.allowed, result.reason
    assert _row(db, "SELECT status FROM approval_grants WHERE approval_id=?", own)["status"] == "CONSUMED"
    assert _row(db, "SELECT status FROM approval_grants WHERE approval_id=?", foreign)["status"] == "PENDING"


def test_approval_core_contract_reactive_seal_without_event_session_is_refused(db, monkeypatch):
    from modules.tools.bash_validator import BashValidator

    monkeypatch.setenv("CLAUDE_SESSION_ID", "default")
    result = BashValidator().validate(
        COMMANDS[0], is_subagent=True, session_id="", agent_type=AGENT,
        hook_payload=_subagent_event(session_id=""),
    )
    assert not result.allowed
    assert "session" in (result.reason or "").lower()
    assert "P-" not in json.dumps(result.block_response or {})
    assert _row(db, "SELECT COUNT(*) AS n FROM approvals")["n"] == 0


def test_approval_core_contract_subagent_event_without_agent_is_refused_before_consent(db):
    """A subagent event naming no agent_type is refused; the same event with one reaches consent."""
    from adapters.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter()
    anonymous = {**_subagent_event(), "tool_name": "Bash", "tool_input": {"command": COMMANDS[0]}}
    del anonymous["agent_type"]
    refused = adapter._adapt_bash("Bash", {"command": COMMANDS[0]}, hook_data=anonymous)
    assert "carries no agent" in json.dumps(refused.output)
    assert _row(db, "SELECT COUNT(*) AS n FROM approvals")["n"] == 0

    identified = {**anonymous, "agent_type": AGENT}
    adapter._adapt_bash("Bash", {"command": COMMANDS[0]}, hook_data=identified)
    pending = _row(db, "SELECT id FROM approvals")
    assert pending is not None
    assert _payload(db, pending["id"])["requested_by"] == {"session_id": SESSION, "agent_id": AGENT}


def test_approval_core_contract_primary_session_binds_to_the_adapter_primary_identity(db, monkeypatch):
    """A main-session command carries no agent: the adapter names the primary explicitly.

    The orchestrator-only CLI guard is a separate layer keyed on the raw event
    and would deny ``git`` first; it is neutralized so the identity is observed.
    """
    from adapters.claude_code import ClaudeCodeAdapter
    from modules.security import gaia_cli_only_guard
    from modules.tools import bash_validator

    monkeypatch.setattr(gaia_cli_only_guard, "check", lambda command, payload: (True, None))
    seen = []
    real_validate = bash_validator.BashValidator.validate

    def spy(self, command, **kwargs):
        seen.append(kwargs.get("agent_type"))
        return real_validate(self, command, **kwargs)

    monkeypatch.setattr(bash_validator.BashValidator, "validate", spy)
    primary_event = {"session_id": SESSION, "cwd": REPO, "tool_use_id": "toolu_main",
                     "tool_name": "Bash", "tool_input": {"command": COMMANDS[0]}}
    adapter = ClaudeCodeAdapter()
    adapter._adapt_bash("Bash", {"command": COMMANDS[0]}, hook_data=primary_event)
    primary = seen[-1]
    assert primary and primary != "unattributed"

    approval_id = _approve_reactive(SESSION, primary, native_ref="toolu_primary")
    assert _payload(db, approval_id)["requested_by"] == {"session_id": SESSION, "agent_id": primary}
    retry = adapter._adapt_bash("Bash", {"command": COMMANDS[0]}, hook_data={**primary_event, "tool_use_id": "toolu_main2"})
    assert retry.exit_code == 0
    assert _row(db, "SELECT status FROM approval_grants WHERE approval_id=?", approval_id)["status"] == "CONSUMED"


def test_approval_core_contract_claude_adapter_delegates_protected_write(monkeypatch, tmp_path):
    from gaia.approvals import core
    from adapters.claude_code import ClaudeCodeAdapter

    calls = []

    def fake_verdict(path, *, session_id, agent_id):
        calls.append((path, session_id, agent_id))
        return {"decision": "allow", "path": path}

    monkeypatch.setattr(core, "protected_write_verdict", fake_verdict)
    target = str(tmp_path / ".claude" / "settings.json")
    response = ClaudeCodeAdapter()._adapt_write_edit(
        "Write", {"file_path": target}, session_id=SESSION, is_subagent=True,
        agent_id="a123", agent_type=AGENT,
    )
    assert calls and calls[0][1:] == (SESSION, AGENT)
    assert response.exit_code == 0
