"""A signed set runs whole in Claude Code, and a host denial never spends a signature.

Every request is made through ``gaia approvals request-set`` as a subprocess
and every call runs through the Claude Code adapter's real PreToolUse and
PostToolUse/PostToolUseFailure entry points against a scratch database. A
call the host's permission layer refused reaches Gaia as a PostToolUseFailure
carrying the call's ``tool_use_id`` and the host's refusal text in ``error``
(measured on Claude Code, rc.1 2026-09-23); it never ran.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys

import pytest

from tests.hooks.test_terminal_event_close import (  # noqa: F401 - the fixture is used by name
    AGENT_TYPE,
    SESSION,
    _adapter,
    _grant,
    _outcomes,
    _post_failure,
    _post_success,
    _pre,
    _REPO_ROOT,
    host,
)

PUSH = "git push origin feat/x"
COMMIT = "git commit --allow-empty -m 'chore: record the step'"
OPEN_PR = "gh pr create --base main --fill"
MERGE_PR = "gh pr merge --squash"


def _denied(ran: str) -> str:
    """The text Claude Code puts in ``error`` when its permission layer refuses the call."""
    return f"Permission to use Bash with command {ran} has been denied."


def _request_set(data_dir, steps: list[tuple[str, str]]) -> subprocess.CompletedProcess:
    """Run ``gaia approvals request-set`` for ``(command, cwd)`` steps, as the requester would."""
    argv = [sys.executable, str(_REPO_ROOT / "bin" / "gaia"), "approvals", "request-set"]
    for command, cwd in steps:
        argv += ["--command", command, "--cwd", cwd,
                 "--does", "Un paso de la entrega.", "--impact", "Cambia el remoto o el repo local."]
    argv += ["--what", "Publicar la rama y abrir el PR", "--question", "¿Publico la rama?",
             "--rollback", "Borrar la rama remota y cerrar el PR.",
             "--verification", "gh pr view", "--shared-state", "Sí: la rama remota y el PR.",
             "--session-id", SESSION, "--agent-id", AGENT_TYPE, "--json"]
    env = {key: value for key, value in os.environ.items()
           if key not in ("GAIA_DB", "GAIA_DISPATCH_AGENT") and not key.startswith("CLAUDE")}
    env["GAIA_DATA_DIR"] = str(data_dir)
    return subprocess.run(argv, cwd=steps[0][1], env=env, capture_output=True, text=True, timeout=180)


def _approve(approval_id: str) -> None:
    from gaia.approvals import core

    core.record_presentation(
        approval_id, native_ref="toolu_question", session_id=SESSION, agent_id="gaia-orchestrator",
    )
    decision = core.decide(native_ref="toolu_question", option_key="approve", session_id=SESSION)
    assert decision.status == "activated", decision


def _pending_count(db) -> int:
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT COUNT(*) FROM approvals WHERE status = 'pending'").fetchone()[0]
    finally:
        con.close()


def _requested(host, steps) -> str:
    result = _request_set(host["db"].parent, steps)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])["approval_id"]


def test_signed_set_runs_whole_with_a_local_commit_and_a_sealed_folder(host, tmp_path):
    """N signed items, a local commit between them and one item sealed in another folder all run on one signature."""
    other = tmp_path / "other"
    other.mkdir()
    repo = host["repo"]
    approval_id = _requested(host, [
        (PUSH, repo), (COMMIT, repo), (OPEN_PR, str(other)), (MERGE_PR, str(other)),
    ])
    _approve(approval_id)
    adapter = _adapter()

    calls = [PUSH, COMMIT, f"cd {other} && {OPEN_PR}", f"cd {other} && {MERGE_PR}"]
    for position, command in enumerate(calls):
        tool_use_id = f"toolu_step_{position}"
        ran = _pre(adapter, command, tool_use_id, repo)
        assert ran is not None, f"step {position + 1} ({command}) must run under the one signature"
        _post_success(adapter, ran, tool_use_id, repo)

    assert _grant(host["db"], approval_id)["status"] == "CONSUMED"
    outcomes = _outcomes(approval_id)
    assert [event for event, _ in outcomes] == ["EXECUTED"] * 3
    assert [payload["command"] for _, payload in outcomes] == [PUSH, OPEN_PR, MERGE_PR]
    assert _pending_count(host["db"]) == 0, "no second signature was asked"


def test_signed_set_request_shows_unsigned_steps_in_position_without_reserving_them(host):
    repo = host["repo"]
    result = _request_set(host["db"].parent, [(PUSH, repo), (COMMIT, repo), (OPEN_PR, repo)])
    assert result.returncode == 0, result.stdout + result.stderr
    approval_id = json.loads(result.stdout.strip().splitlines()[-1])["approval_id"]

    from gaia.approvals import core

    [shown] = core.question_batch([approval_id])
    lines = shown.text.splitlines()
    assert [line.endswith(f"[ {command} ]") for line, command in zip(lines, (PUSH, COMMIT, OPEN_PR))] == [True] * 3
    assert len(shown.questions) == 2, "only the signed steps are asked"

    _approve(approval_id)
    adapter = _adapter()
    assert _pre(adapter, OPEN_PR, "toolu_skip", repo) is None, "a signed step still runs only in order"
    ran = _pre(adapter, PUSH, "toolu_push", repo)
    _post_success(adapter, ran, "toolu_push", repo)
    assert _grant(host["db"], approval_id)["next_index"] == 1, "the unsigned commit holds no index"


def test_signed_set_request_with_no_signed_step_is_refused(host):
    result = _request_set(host["db"].parent, [(COMMIT, host["repo"])])
    assert result.returncode != 0
    assert _pending_count(host["db"]) == 0


def test_signed_set_item_denied_by_the_host_is_not_consumed(host):
    repo = host["repo"]
    approval_id = _requested(host, [(PUSH, repo), (OPEN_PR, repo)])
    _approve(approval_id)
    adapter = _adapter()

    ran = _pre(adapter, PUSH, "toolu_denied", repo)
    assert ran is not None
    _post_failure(adapter, ran, "toolu_denied", repo, error=_denied(ran))

    grant = _grant(host["db"], approval_id)
    assert (grant["status"], grant["next_index"], grant["reservation_tool_use_id"]) == ("PENDING", 0, None)
    assert _outcomes(approval_id) == [], "a call the host refused neither executed nor failed"

    retried = _pre(adapter, PUSH, "toolu_retry", repo)
    assert retried is not None, "the retry runs under the same signature"
    _post_success(adapter, retried, "toolu_retry", repo)
    assert [event for event, _ in _outcomes(approval_id)] == ["EXECUTED"]
    assert _pending_count(host["db"]) == 0, "no second signature was minted"


def test_signed_single_command_denied_by_the_host_is_not_consumed(host):
    from gaia.approvals import core, store
    from modules.tools.bash_validator import _build_sealed_payload

    repo = host["repo"]
    payload = _build_sealed_payload(
        PUSH, "push", "MUTATIVE", agent_type=AGENT_TYPE, cwd=repo, session_id=SESSION,
    )
    approval_id = store.insert_requested(payload, agent_id=AGENT_TYPE, session_id=SESSION)
    core.record_presentation(approval_id, native_ref="toolu_q1", session_id=SESSION, agent_id="gaia-orchestrator")
    assert core.decide(native_ref="toolu_q1", option_key="approve", session_id=SESSION).status == "activated"
    adapter = _adapter()

    ran = _pre(adapter, PUSH, "toolu_denied", repo)
    assert ran is not None
    _post_failure(adapter, ran, "toolu_denied", repo, error=_denied(ran))
    assert _grant(host["db"], approval_id)["status"] == "PENDING"

    assert _pre(adapter, PUSH, "toolu_retry", repo) is not None, "the retry spends the same signature"
    assert _grant(host["db"], approval_id)["status"] == "CONSUMED"
    assert _pending_count(host["db"]) == 0


def test_signed_set_dispatch_identity_still_reaches_the_command(host):
    """The DB guards fail open without GAIA_DISPATCH_AGENT: the command the host runs must export it."""
    ran = _pre(_adapter(), "printenv GAIA_DISPATCH_AGENT", "toolu_identity", host["repo"])
    env = {key: value for key, value in os.environ.items() if key != "GAIA_DISPATCH_AGENT"}
    shell = subprocess.run(["bash", "-c", ran], env=env, capture_output=True, text=True, timeout=30)
    assert shell.stdout.strip() == AGENT_TYPE


def test_signed_set_an_approve_that_activates_nothing_is_recorded(host):
    from gaia.approvals import core, store

    repo = host["repo"]
    approval_id = _requested(host, [(PUSH, repo)])
    [shown] = core.hand_out_question([approval_id], session_id=SESSION, agent_id="gaia-orchestrator")
    core.record_presentation(approval_id, native_ref="toolu_ask", session_id=SESSION, agent_id="gaia-orchestrator")
    store.revoke(approval_id, SESSION, agent_id=AGENT_TYPE)

    question = shown.questions[0]
    event = _adapter().parse_event(json.dumps({
        "session_id": SESSION, "transcript_path": "/nonexistent/transcript.jsonl", "cwd": repo,
        "hook_event_name": "PostToolUse", "tool_name": "AskUserQuestion", "tool_use_id": "toolu_ask",
        "tool_input": {"questions": [question]},
        "tool_response": {"questions": [question], "answers": {question["question"]: "Approve"}},
    }))
    _adapter().adapt_post_tool_use(event)

    con = sqlite3.connect(host["db"])
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in con.execute(
            "SELECT * FROM harness_events WHERE type = 'consent.decision.not_activated'"
        )]
    finally:
        con.close()
    assert any(approval_id in json.dumps(row) for row in rows), rows
