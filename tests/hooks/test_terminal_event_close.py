"""A signed Claude Code call closes only on its own terminal event (plan 76, task 3, PD3).

The terminal event of a Bash call is PostToolUse on success and
PostToolUseFailure on a non-zero exit or an interrupt, both carrying the
tool_use_id of the call's PreToolUse (captured on Claude Code 2.1.273). Nothing
else may advance or freeze a signed set: an orchestrator Stop, which fires while
a subagent under the same session_id is still running a command, least of all.

Every event here is host-shaped and runs through the adapter's real Pre, Post
and Stop entry points against a scratch database.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from gaia.store import writer  # noqa: E402

SESSION = "ses-orchestrator"
AGENT_ID = "a0f1e2d3c4b5a6978"
AGENT_TYPE = "gaia-system"
COMMANDS = ["git push origin feat/x", "gh pr create --base main --fill"]


@pytest.fixture
def host(tmp_path, monkeypatch):
    from modules.core.paths import clear_path_cache

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
    con = sqlite3.connect(data_dir / "gaia.db")
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()

    clear_path_cache()
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    monkeypatch.setattr("modules.core.state.find_claude_dir", lambda: claude_dir)
    repo = tmp_path / "repo"
    repo.mkdir()
    return {"db": data_dir / "gaia.db", "repo": str(repo)}


def _adapter():
    from adapters.claude_code import ClaudeCodeAdapter

    return ClaudeCodeAdapter()


def _approved_set(repo: str, expect_exit: dict | None = None) -> str:
    from gaia.approvals import core

    items = [
        {"command": command, "cwd": repo, "expect_exit": (expect_exit or {}).get(i, []),
         "does": "Publica un paso del cambio.", "impact": "Queda visible en el remoto."}
        for i, command in enumerate(COMMANDS)
    ]
    approval_id = core.request_command_set(
        items, what="Publicar la rama y abrir el PR", question="¿Publico la rama?",
        session_id=SESSION, agent_id=AGENT_TYPE,
    )
    core.record_presentation(
        approval_id, native_ref="toolu_question", session_id=SESSION, agent_id="gaia-orchestrator",
    )
    decision = core.decide(native_ref="toolu_question", option_key="approve", session_id=SESSION)
    assert decision.status == "activated", decision
    return approval_id


def _subagent_event(adapter, event_name: str, command: str, tool_use_id: str, repo: str, **fields):
    payload = {
        "session_id": SESSION,
        "transcript_path": "/nonexistent/transcript.jsonl",
        "cwd": repo,
        "permission_mode": "acceptEdits",
        "hook_event_name": event_name,
        "agent_id": AGENT_ID,
        "agent_type": AGENT_TYPE,
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_use_id": tool_use_id,
        **fields,
    }
    return adapter.parse_event(json.dumps(payload))


def _pre(adapter, command: str, tool_use_id: str, repo: str) -> str | None:
    """Run PreToolUse; return the command the host will run, or None when denied."""
    response = adapter.adapt_pre_tool_use(
        _subagent_event(adapter, "PreToolUse", command, tool_use_id, repo)
    )
    if response.exit_code != 0 or not isinstance(response.output, dict):
        return None
    decision = response.output.get("hookSpecificOutput", {})
    if not decision:
        return command
    if decision.get("permissionDecision") != "allow":
        return None
    return decision.get("updatedInput", {}).get("command", command)


def _post_success(adapter, ran: str, tool_use_id: str, repo: str) -> None:
    adapter.adapt_post_tool_use(_subagent_event(
        adapter, "PostToolUse", ran, tool_use_id, repo, duration_ms=40,
        tool_response={"stdout": "", "stderr": "", "interrupted": False, "isImage": False},
    ))


def _post_failure(adapter, ran: str, tool_use_id: str, repo: str, *, error: str, is_interrupt=False) -> None:
    adapter.adapt_post_tool_use(_subagent_event(
        adapter, "PostToolUseFailure", ran, tool_use_id, repo, duration_ms=40,
        error=error, is_interrupt=is_interrupt,
    ))


def _orchestrator_stop(adapter) -> None:
    adapter.adapt_stop({
        "hook_event_name": "Stop",
        "session_id": SESSION,
        "transcript_path": "/nonexistent/transcript.jsonl",
        "stop_hook_active": False,
    })


def _grant(db: Path, approval_id: str) -> dict:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return dict(con.execute(
            "SELECT status, next_index, failed_index, reservation_tool_use_id "
            "FROM approval_grants WHERE approval_id = ?", (approval_id,),
        ).fetchone())
    finally:
        con.close()


def _outcomes(approval_id: str) -> list[tuple[str, dict]]:
    from gaia.approvals.store import replay_for_approval

    return [
        (event["event_type"], json.loads(event["payload_json"] or "{}"))
        for event in replay_for_approval(approval_id)
        if event["event_type"] in ("EXECUTED", "FAILED")
    ]


def test_terminal_event_close_orchestrator_stop_leaves_a_running_subagent_command_alone(host):
    adapter = _adapter()
    approval_id = _approved_set(host["repo"])

    ran = _pre(adapter, COMMANDS[0], "toolu_push", host["repo"])
    assert ran is not None, "the approved first item must be allowed"

    _orchestrator_stop(adapter)
    assert _outcomes(approval_id) == [], "a Stop is not the call's terminal event"
    assert _grant(host["db"], approval_id)["status"] == "PENDING"

    _post_success(adapter, ran, "toolu_push", host["repo"])
    grant = _grant(host["db"], approval_id)
    assert (grant["status"], grant["next_index"]) == ("PENDING", 1)
    [(event_type, payload)] = _outcomes(approval_id)
    assert event_type == "EXECUTED"
    assert payload["command"] == COMMANDS[0], "EXECUTED records the sealed bytes, not the injected prefix"

    assert _pre(adapter, COMMANDS[1], "toolu_pr", host["repo"]) is not None, (
        "the second item runs under the same signature"
    )


def test_terminal_event_close_real_failure_freezes_the_set(host):
    adapter = _adapter()
    approval_id = _approved_set(host["repo"])

    ran = _pre(adapter, COMMANDS[0], "toolu_push", host["repo"])
    _post_failure(adapter, ran, "toolu_push", host["repo"], error="Exit code 1")

    grant = _grant(host["db"], approval_id)
    assert (grant["status"], grant["failed_index"]) == ("FAILED", 0)
    [(event_type, payload)] = _outcomes(approval_id)
    assert (event_type, payload["exit_code"], payload["command"]) == ("FAILED", 1, COMMANDS[0])
    assert _pre(adapter, COMMANDS[1], "toolu_pr", host["repo"]) is None


def test_terminal_event_close_declared_exit_lets_the_set_continue(host):
    adapter = _adapter()
    approval_id = _approved_set(host["repo"], expect_exit={0: [3]})

    ran = _pre(adapter, COMMANDS[0], "toolu_push", host["repo"])
    _post_failure(adapter, ran, "toolu_push", host["repo"], error="Exit code 3")

    grant = _grant(host["db"], approval_id)
    assert (grant["status"], grant["next_index"]) == ("PENDING", 1)
    [(event_type, payload)] = _outcomes(approval_id)
    assert (event_type, payload["exit_code"]) == ("EXECUTED", 3)
    assert _pre(adapter, COMMANDS[1], "toolu_pr", host["repo"]) is not None


def test_terminal_event_close_interrupt_leaves_the_call_without_result(host):
    adapter = _adapter()
    approval_id = _approved_set(host["repo"])

    ran = _pre(adapter, COMMANDS[0], "toolu_push", host["repo"])
    _post_failure(adapter, ran, "toolu_push", host["repo"], error="Interrupted by user", is_interrupt=True)

    assert _outcomes(approval_id) == []
    grant = _grant(host["db"], approval_id)
    assert (grant["status"], grant["next_index"], grant["reservation_tool_use_id"]) == (
        "PENDING", 0, "toolu_push",
    ), "a cancelled call neither advances nor freezes the set; its reservation waits to be reclaimed"


def test_terminal_event_close_single_command_failure_records_failed(host):
    from gaia.approvals import core

    adapter = _adapter()
    blocked = adapter.adapt_pre_tool_use(
        _subagent_event(adapter, "PreToolUse", COMMANDS[0], "toolu_blocked", host["repo"])
    )
    [approval_id] = set(re.findall(r"P-[0-9a-f]{32}", json.dumps(blocked.output)))
    core.record_presentation(approval_id, native_ref="toolu_q", session_id=SESSION, agent_id="gaia-orchestrator")
    assert core.decide(native_ref="toolu_q", option_key="approve", session_id=SESSION).status == "activated"

    ran = _pre(adapter, COMMANDS[0], "toolu_retry", host["repo"])
    assert ran is not None, "the approved retry must be allowed"
    _post_failure(adapter, ran, "toolu_retry", host["repo"], error="Exit code 128")

    [(event_type, payload)] = _outcomes(approval_id)
    assert (event_type, payload["exit_code"], payload["command"]) == ("FAILED", 128, COMMANDS[0])


def _load_build_plugin():
    spec = importlib.util.spec_from_file_location("build_plugin", _REPO_ROOT / "scripts" / "build-plugin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_terminal_event_close_hooks_json_registers_bash_failure_from_the_manifest():
    hooks = json.loads((_REPO_ROOT / "hooks" / "hooks.json").read_text())["hooks"]
    assert hooks.get("PostToolUseFailure") == [{
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/post_tool_use.py"}],
    }]

    build_plugin = _load_build_plugin()
    assert build_plugin.generate_hooks_json(build_plugin.load_manifest("gaia")) == {"hooks": hooks}, (
        "the pack-time regeneration must produce the tracked hooks.json"
    )
