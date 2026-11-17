"""A dispatched specialist withdraws only the approval requests its own turn made, under the identity the hook exports."""

from __future__ import annotations

import argparse
import io
import json
import re
import shlex
import sqlite3
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _path in (_ROOT, _ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from gaia.store import writer  # noqa: E402

SESSION = "ses-specialist-turns"
OTHER_SESSION = "ses-another-session"
AGENT_TYPE = "gaia-system"
TURN_A = "a1111111111111111"
TURN_B = "b2222222222222222"
TURN_OTHER = "c3333333333333333"
IDENTITY_VARIABLES = ("GAIA_DISPATCH_AGENT", "GAIA_DISPATCH_TURN")


@pytest.fixture
def host(tmp_path, monkeypatch):
    from modules.core.paths import clear_path_cache

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
    for name in (*IDENTITY_VARIABLES, "GAIA_HOST_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    con = sqlite3.connect(data_dir / "gaia.db")
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()
    clear_path_cache()
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    monkeypatch.setattr("modules.core.state.find_claude_dir", lambda: claude_dir)
    return {"cwd": str(tmp_path)}


def _hook_response(command: str, *, session: str, turn: str, cwd: str):
    from adapters.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter()
    event = adapter.parse_event(json.dumps({
        "session_id": session, "transcript_path": "/nonexistent/transcript.jsonl", "cwd": cwd,
        "permission_mode": "acceptEdits", "hook_event_name": "PreToolUse", "agent_id": turn,
        "agent_type": AGENT_TYPE, "tool_name": "Bash", "tool_input": {"command": command},
        "tool_use_id": f"toolu-{turn}-{abs(hash(command))}",
    }))
    return adapter.adapt_pre_tool_use(event)


def _pre_tool_use(command: str, *, session: str, turn: str, cwd: str) -> dict:
    output = _hook_response(command, session=session, turn=turn, cwd=cwd).output
    return (output or {}).get("hookSpecificOutput", {}) if isinstance(output, dict) else {}


def _held(command: str, *, session: str, turn: str, cwd: str) -> str:
    decision = _pre_tool_use(command, session=session, turn=turn, cwd=cwd)
    assert decision.get("permissionDecision") == "deny", decision
    return re.search(r"approval_id: (P-[0-9a-f]{32})", decision["permissionDecisionReason"]).group(1)


def _exported_identity(*, turn: str, cwd: str) -> dict:
    decision = _pre_tool_use("gaia approvals pending", session=SESSION, turn=turn, cwd=cwd)
    runs = decision["updatedInput"]["command"]
    prefix = runs.split("; ", 1)[0]
    words = shlex.split(prefix)
    assert words[0] == "export"
    return {name: value for name, _, value in (word.partition("=") for word in words[1:]) if name != "TMPDIR"}


@pytest.fixture
def held(host):
    return {
        "a": _held("gh pr merge 41 --squash", session=SESSION, turn=TURN_A, cwd=host["cwd"]),
        "b": _held("gh pr merge 42 --squash", session=SESSION, turn=TURN_B, cwd=host["cwd"]),
        "other": _held("gh pr merge 43 --squash", session=OTHER_SESSION, turn=TURN_OTHER, cwd=host["cwd"]),
    }


@pytest.fixture
def turn_a_shell(host, held, monkeypatch):
    for name, value in _exported_identity(turn=TURN_A, cwd=host["cwd"]).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    return held


def _status(approval_id: str) -> str:
    from gaia.approvals import store

    return store.get_by_id(approval_id)["status"]


def _call(func, **values) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = func(argparse.Namespace(**values))
    return code, out.getvalue() + err.getvalue()


def _reject_all(**_):
    from bin.cli.approvals import cmd_reject_all

    return _call(cmd_reject_all, dry_run=False, workspace=None, json=False)


def _reject_dash_all(**_):
    from bin.cli.approvals import cmd_reject

    return _call(cmd_reject, approval_id=None, all=True, reason=None, json=False)


BULK = {"reject-all": _reject_all, "reject --all": _reject_dash_all}


def test_the_first_tool_call_of_an_unclaimed_turn_exports_its_harness_turn(host):
    assert _exported_identity(turn=TURN_A, cwd=host["cwd"]) == {
        "GAIA_DISPATCH_AGENT": AGENT_TYPE, "GAIA_DISPATCH_TURN": TURN_A,
    }


@pytest.mark.parametrize("verb", sorted(BULK))
def test_bulk_rejection_from_a_turn_withdraws_only_its_own(turn_a_shell, verb):
    code, out = BULK[verb]()

    assert code == 0, out
    assert _status(turn_a_shell["a"]) == "rejected"
    assert _status(turn_a_shell["b"]) == "pending"
    assert _status(turn_a_shell["other"]) == "pending"
    assert re.search(r"[Ll]eft 2 pending", out), out
    assert "1 of another turn in this session" in out and "1 of another session" in out, out


@pytest.mark.parametrize("target", ["b", "other"])
def test_rejecting_another_turns_request_fails_naming_why(turn_a_shell, target):
    from bin.cli.approvals import cmd_reject

    code, out = _call(cmd_reject, approval_id=turn_a_shell[target], all=False, reason=None, json=False)

    assert code == 1
    assert "not made by this turn" in out, out
    assert _status(turn_a_shell[target]) == "pending"


def test_revoking_another_turns_request_is_refused(turn_a_shell):
    from bin.cli.approvals import cmd_revoke

    code, out = _call(cmd_revoke, approval_id=turn_a_shell["b"], yes=True, reason=None, json=False)

    assert code == 1
    assert _status(turn_a_shell["b"]) == "pending"


def test_a_turn_rejects_its_own_request_by_id(turn_a_shell):
    from bin.cli.approvals import cmd_reject

    code, out = _call(cmd_reject, approval_id=turn_a_shell["a"], all=False, reason=None, json=False)

    assert code == 0, out
    assert _status(turn_a_shell["a"]) == "rejected"


def test_a_request_sealed_without_a_turn_stays_for_the_user(turn_a_shell, monkeypatch):
    from gaia.approvals import store

    legacy = store.insert_requested(
        {"commands": ["gh pr merge 44"], "exact_content": "gh pr merge 44",
         "operation": "MUTATIVE command intercepted: merge",
         "requested_by": {"session_id": SESSION, "agent_id": AGENT_TYPE}},
        agent_id=AGENT_TYPE, session_id=SESSION,
    )
    _reject_all()
    assert _status(legacy) == "pending"

    for name in IDENTITY_VARIABLES:
        monkeypatch.delenv(name)
    _reject_all()
    assert _status(legacy) == "rejected"


@pytest.mark.parametrize("verb", sorted(BULK))
def test_without_a_dispatch_identity_bulk_rejection_withdraws_all(held, verb):
    code, out = BULK[verb]()

    assert code == 0, out
    assert {_status(approval_id) for approval_id in held.values()} == {"rejected"}


@pytest.mark.parametrize("argv", [
    ["reject-all", "--agent-id", "gaia-orchestrator"],
    ["reject", "--all", "--session-id", OTHER_SESSION],
    ["reject", "--all", "--agent-id", AGENT_TYPE],
])
def test_requester_flags_from_a_dispatched_shell_are_refused_and_widen_nothing(turn_a_shell, argv):
    from bin.cli.approvals import register

    parser = argparse.ArgumentParser(prog="gaia")
    register(parser.add_subparsers(dest="command"))
    with redirect_stderr(io.StringIO()), pytest.raises(SystemExit) as refused:
        args = parser.parse_args(["approvals", *argv])
        args.func(args)
    assert refused.value.code != 0
    assert {_status(turn_a_shell[key]) for key in ("a", "b", "other")} == {"pending"}


@pytest.mark.parametrize("command", [
    f"export GAIA_DISPATCH_TURN={TURN_B}; gaia approvals reject-all",
    f"GAIA_DISPATCH_TURN={TURN_B} gaia approvals reject-all",
    "env -u GAIA_DISPATCH_TURN gaia approvals reject-all",
    "unset GAIA_DISPATCH_TURN; gaia approvals reject-all",
    "GAIA_DISPATCH_TURN= gaia approvals reject-all",
])
def test_a_subagent_cannot_touch_its_turn_variable(host, command):
    response = _hook_response(command, session=SESSION, turn=TURN_A, cwd=host["cwd"])

    assert response.exit_code == 2, response.output
    assert "floor.dispatch-identity" in str(response.output)
