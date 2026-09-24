"""D39: in OpenCode only the orchestrator presents a pending signature.

The orchestrator runs ``gaia approvals question <id>``, which in OpenCode prints
a question carrying only the approval id, and calls the question tool with it.
The real plugin (``opencode/plugin.ts`` under bun, real bridge, real
``opencode-present`` / ``opencode-decide``) replaces those arguments with the
renderer's questions, one per command (D37), records SHOWN, and binds the
answer to that approval; the grant stays bound to the requesting session and
agent (D6), and the same specialist executes it.

WHAT IS NOT PROVEN: no OpenCode host runs here, so the question call and the
host's question events are issued by the driver, not observed from OpenCode.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout

import pytest

from tests.integration.test_opencode_consent_retry_e2e import (  # noqa: F401 (db_env is a fixture)
    AGENT_ID,
    FIRST_COMMAND,
    GAIA_CLI,
    RETRY_CALL_ID,
    ROOT_SESSION_ID,
    SESSION_ID,
    _approval_status,
    _before,
    _drive,
    _grant,
    _request_set,
    _step,
    _tool_exchanges,
    db_env,
)

ASK_CALL_ID = "call-orchestrator-ask"


def _placeholder(env, approval_id, *, details=False):
    """What `gaia approvals question` prints in an OpenCode shell."""
    argv = [sys.executable, str(GAIA_CLI), "approvals", "question", approval_id]
    if details:
        argv.insert(4, "--details")
    result = subprocess.run(
        argv, cwd=env["WORKSPACE"], env={**env, "GAIA_HOST_SESSION_ID": ROOT_SESSION_ID},
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def _ask(label, args, *, answer=None, session_id=ROOT_SESSION_ID, call_id=ASK_CALL_ID):
    return {
        "kind": "orchestrator-question", "label": label, "sessionID": session_id,
        "callID": call_id, "args": args, "answer": answer,
    }


def _rendered(approval_id):
    from gaia.approvals import store, surface

    return surface.render(json.loads(store.get_by_id(approval_id)["payload_json"]), approval_id)


def _shown(approval_id):
    from gaia.approvals.store import get_history

    return [
        (event["session_id"], json.loads(event["metadata_json"]))
        for event in get_history(approval_id) if event["event_type"] == "SHOWN"
    ]


def test_approval_live_fixes_opencode_orchestrator_presents_and_the_specialist_executes(db_env):
    env, db_path = db_env
    approval_id = _request_set(env)

    driven = _drive(env, [
        _ask("ask", _placeholder(env, approval_id), answer="approve"),
        _before("retry", FIRST_COMMAND, call_id=RETRY_CALL_ID),
    ])

    asked = _step(driven, "ask")
    assert asked["allowed"] is True, driven
    assert [q["question"] for q in asked["questions"]] == [q["question"] for q in _rendered(approval_id).questions]
    assert FIRST_COMMAND in asked["question"]["question"]
    assert [option["label"] for option in asked["question"]["options"]] == ["Approve", "Reject", "Details"]
    # The model only opened the question: no control prompt went to any session.
    assert driven["controlPrompts"] == []
    [(shown_session, shown)] = _shown(approval_id)
    assert shown_session == SESSION_ID
    assert shown["call_id"] == ASK_CALL_ID
    assert shown["presenter_session_id"] == ROOT_SESSION_ID
    assert _approval_status(db_path, approval_id) == "approved"
    grant = _grant(db_path, approval_id)
    assert (grant["session_id"], grant["agent_id"]) == (SESSION_ID, AGENT_ID)
    assert f"task_id {SESSION_ID}" in asked["output"] and "execution" in asked["output"]
    assert _step(driven, "retry")["allowed"] is True, driven
    retry = next(x for x in _tool_exchanges(driven) if x["sent"].get("callID") == RETRY_CALL_ID)
    assert retry["sent"]["consentRetry"]["approval_id"] == approval_id


def test_approval_live_fixes_opencode_orchestrator_reject_grants_nothing(db_env):
    env, db_path = db_env
    approval_id = _request_set(env)

    driven = _drive(env, [_ask("ask", _placeholder(env, approval_id), answer="reject")])

    assert _step(driven, "ask")["allowed"] is True, driven
    assert _approval_status(db_path, approval_id) == "rejected"
    assert _grant(db_path, approval_id) is None


def test_approval_live_fixes_opencode_orchestrator_details_is_asked_again_with_the_details(db_env):
    env, db_path = db_env
    approval_id = _request_set(env)

    driven = _drive(env, [
        _ask("ask", _placeholder(env, approval_id), answer="details"),
        _ask("details", _placeholder(env, approval_id, details=True), call_id="call-details"),
    ])

    assert _approval_status(db_path, approval_id) == "pending"
    assert f"gaia approvals question --details {approval_id}" in _step(driven, "ask")["output"]
    assert [q["question"] for q in _step(driven, "details")["questions"]] == [
        q["question"] for q in _rendered(approval_id).details_questions
    ]
    assert [event for event in driven["hostEvents"] if event["type"] == "message"] == []


@pytest.mark.parametrize("who", ["foreign requester", "specialist session"])
def test_approval_live_fixes_opencode_presentation_outside_the_orchestrator_of_the_requester_is_refused(
    db_env, who,
):
    env, _ = db_env
    if who == "foreign requester":
        foreign = subprocess.run(
            [
                sys.executable, str(GAIA_CLI), "approvals", "request-set", "--command", FIRST_COMMAND,
                "--what", "Publicar la rama.", "--question", "¿Publico la rama?",
                "--does", "Sube la rama.", "--impact", "Queda visible.",
                "--rollback", "Borrar la rama remota.",
                "--agent-id", AGENT_ID, "--session-id", "ses-not-a-child", "--json",
            ],
            cwd=env["WORKSPACE"], env=env, capture_output=True, text=True, timeout=120,
        )
        assert foreign.returncode == 0, foreign.stdout + foreign.stderr
        approval_id = json.loads(foreign.stdout.strip().splitlines()[-1])["approval_id"]
        step = _ask("ask", _placeholder(env, approval_id))
    else:
        approval_id = _request_set(env)
        step = _ask("ask", _placeholder(env, approval_id), session_id=SESSION_ID)

    driven = _drive(env, [step])

    assert _step(driven, "ask")["allowed"] is False, driven
    assert _shown(approval_id) == []


def test_approval_live_fixes_question_cli_in_opencode_prints_one_id_placeholder(db_env, monkeypatch):
    env, _ = db_env
    approval_id = _request_set(env)
    from bin.cli.approvals import cmd_question

    monkeypatch.setenv("GAIA_HOST_SESSION_ID", ROOT_SESSION_ID)
    printed = {}
    for details in (False, True):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cmd_question(argparse.Namespace(approval_ids=[approval_id], details=details, json=True))
        assert code == 0, out.getvalue()
        [question] = json.loads(out.getvalue())["questions"]
        printed[details] = question["question"]
    assert printed == {False: approval_id, True: f"{approval_id} details"}

    # D39: one call asks several signatures, one placeholder each, in order.
    other_id = _request_set(env, commands=("docker push registry/app:2",), session_id="ses-other-specialist")
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cmd_question(argparse.Namespace(approval_ids=[approval_id, other_id], details=False, json=True))
    assert code == 0, out.getvalue() + err.getvalue()
    assert [q["question"] for q in json.loads(out.getvalue())["questions"]] == [approval_id, other_id]


def test_approval_live_fixes_question_cli_in_opencode_refuses_two_signatures_of_one_session(
    db_env, monkeypatch,
):
    """The CLI refuses what the plugin's presentForOrchestrator refuses, before the question opens."""
    env, _ = db_env
    first = _request_set(env)
    second = _request_set(env, commands=("docker push registry/app:2",))
    from bin.cli.approvals import cmd_question

    monkeypatch.setenv("GAIA_HOST_SESSION_ID", ROOT_SESSION_ID)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cmd_question(argparse.Namespace(approval_ids=[first, second], details=False, json=True))

    assert code == 1
    refusal = out.getvalue() + err.getvalue()
    assert "separate calls" in refusal and SESSION_ID in refusal, refusal
    assert '"questions"' not in refusal, "a refused batch must print no question input"

    # Claude Code keeps asking them together: there each approval activates on its own answer.
    monkeypatch.delenv("GAIA_HOST_SESSION_ID")
    with redirect_stdout(io.StringIO()) as claude_out:
        assert cmd_question(argparse.Namespace(approval_ids=[first, second], details=False, json=True)) == 0
    assert json.loads(claude_out.getvalue())["questions"]
