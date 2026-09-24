"""D26: in OpenCode the signature text is a message Gaia posts, not the question text.

OpenCode's question interface shows its text on one line, so the renderer's
multi-line text (header, title, commands; or the Details) is posted by the
plugin into the session as a Markdown code block with ``noReply`` right before
the question, and the question carries only the short question with Approve /
Reject / Details. Both openers are covered: the question Gaia opens in the
requesting specialist's session and the orchestrator's presentation (D25).

WHAT IS NOT PROVEN: no OpenCode host runs here; the driver records the
``session.prompt`` post and delivers ``question.asked`` itself, so whether the
host renders the block with its line breaks is not observed.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from tests.approvals.test_approval_live_fixes_orchestrator_presents import (
    _ask,
    _placeholder,
    _rendered,
    _shown,
)
from tests.integration.test_opencode_consent_retry_e2e import (  # noqa: F401 (db_env is a fixture)
    AGENT_ID,
    CALL_ID,
    FIRST_COMMAND,
    GAIA_CLI,
    ROOT_SESSION_ID,
    SECOND_COMMAND,
    SESSION_ID,
    _approval_status,
    _before,
    _drive,
    _request_set,
    _step,
    db_env,
)

SHORT_QUESTION = "¿Publico la rama y la imagen?"


def _fenced(text):
    return f"```\n{text}\n```"


def _messages_before_each_ask(driven, session_id):
    """The message posted in ``session_id`` immediately before each question.asked there."""
    events = [event for event in driven["hostEvents"] if event["sessionID"] == session_id]
    posted = []
    for index, event in enumerate(events):
        if event["type"] == "question.asked":
            assert index > 0 and events[index - 1]["type"] == "message", events
            posted.append(events[index - 1])
    return posted


def _assert_posted_by_gaia(message, text):
    assert message["text"] == text
    assert message["noReply"] is True
    assert message["partCount"] == 1
    # A synthetic part is hidden from the user in OpenCode's interface.
    assert not message.get("synthetic")


def test_approval_live_fixes_opencode_presentation_emits_the_block_and_the_short_question(db_env):
    from bin.cli.approvals import _opencode_presentation
    from gaia.approvals import store

    approval_id = _request_set(db_env[0])
    rendered = _rendered(approval_id)

    signature = _opencode_presentation(store.get_by_id(approval_id), SESSION_ID, "call-block")["signature"]

    assert signature["question"] == SHORT_QUESTION
    assert signature["block"] == _fenced(rendered.text)
    assert signature["details_block"] == _fenced(rendered.details)
    assert "\n" in rendered.text and approval_id in rendered.details


def test_approval_live_fixes_block_fence_outgrows_backticks_in_a_command(db_env):
    from gaia.approvals import store, surface

    approval_id = _request_set(db_env[0])
    payload = json.loads(store.get_by_id(approval_id)["payload_json"])
    payload["items"][0]["command"] = "git commit -m 'a ``` b'"

    rendered = surface.render(payload, approval_id)

    assert rendered.block == f"````\n{rendered.text}\n````"


def test_approval_live_fixes_specialist_question_follows_the_posted_block(db_env):
    env, db_path = db_env
    approval_id = _request_set(env)
    rendered = _rendered(approval_id)

    driven = _drive(env, [
        _before("blocked", FIRST_COMMAND),
        {"kind": "control-decision", "label": "details", "answer": "details"},
        {"kind": "control-decision", "label": "approve", "answer": "approve"},
    ])

    first, second = _messages_before_each_ask(driven, SESSION_ID)
    _assert_posted_by_gaia(first, _fenced(rendered.text))
    _assert_posted_by_gaia(second, _fenced(rendered.details))
    assert [(session, shown["call_id"]) for session, shown in _shown(approval_id)] == [(SESSION_ID, CALL_ID)]
    for label in ("details", "approve"):
        assert _step(driven, label)["question"]["question"] == SHORT_QUESTION, driven
    assert _approval_status(db_path, approval_id) == "approved"


def test_approval_live_fixes_orchestrator_question_follows_the_posted_block(db_env):
    env, db_path = db_env
    approval_id = _request_set(env)
    rendered = _rendered(approval_id)

    driven = _drive(env, [
        _ask("ask", _placeholder(env, approval_id), answer="details"),
        _ask("details", _placeholder(env, approval_id, details=True), answer="approve", call_id="call-details"),
    ])

    first, second = _messages_before_each_ask(driven, ROOT_SESSION_ID)
    _assert_posted_by_gaia(first, _fenced(rendered.text))
    _assert_posted_by_gaia(second, _fenced(rendered.details))
    for label in ("ask", "details"):
        question = _step(driven, label)["question"]["question"]
        assert question == SHORT_QUESTION, driven
        assert FIRST_COMMAND not in question and SECOND_COMMAND not in question
    assert _approval_status(db_path, approval_id) == "approved"


@pytest.mark.parametrize("opener", ["specialist", "orchestrator"])
def test_approval_live_fixes_no_question_is_asked_when_the_block_is_refused(db_env, opener):
    env, db_path = db_env
    approval_id = _request_set(env)
    if opener == "specialist":
        steps = [
            _before("blocked", FIRST_COMMAND),
            {"kind": "control-decision", "label": "ask", "answer": "approve"},
        ]
    else:
        steps = [_ask("ask", _placeholder(env, approval_id), answer="approve")]

    driven = _drive(env, steps, reject_messages=True)

    assert _step(driven, "ask")["allowed"] is False, driven
    assert [event for event in driven["hostEvents"] if event["type"] == "question.asked"] == []
    assert _approval_status(db_path, approval_id) == "pending"
    # SHOWN says the user saw the signature; a refused block showed them nothing.
    assert _shown(approval_id) == []


def test_approval_live_fixes_a_preview_renders_the_signature_and_records_nothing(db_env):
    env, _ = db_env
    approval_id = _request_set(env)

    result = subprocess.run(
        [
            sys.executable, str(GAIA_CLI), "approvals", "opencode-present", approval_id,
            "--session-id", SESSION_ID, "--agent-id", AGENT_ID,
            "--call-id", "call-preview", "--token", "preview", "--preview", "--json",
        ],
        cwd=env["WORKSPACE"], env=env, capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    previewed = json.loads(result.stdout.strip().splitlines()[-1])
    assert previewed["status"] == "previewed"
    assert previewed["signature"]["block"] == _fenced(_rendered(approval_id).text)
    assert _shown(approval_id) == []
