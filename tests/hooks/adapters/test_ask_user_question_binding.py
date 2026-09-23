"""Claude Code signs on the question Gaia builds, each answer bound to its own signature (plan 76, task 4).

PD4 and PD9: the orchestrator asks AskUserQuestion with the exact object
``gaia approvals question`` prints for 1 to 4 pending signatures. PreToolUse
compares that object with the batch Gaia renders, records SHOWN per signature
under the call's tool_use_id and position, and answers with the renderer's text
as ``systemMessage`` (documented as a message shown to the user); a question
that differs, or a signature without its requester's phrases, is denied with a
reason. PostToolUse maps each chosen label of the SAME tool_use_id to its own
signature: Approve activates with no ID in the label, Reject rejects, Details
shows the renderer's Details and asks for that signature again, and free text,
a dismissal or no selection decide nothing.

The PostToolUse shape comes from host-produced results in
``fixtures/ask_user_question_post_real.json``.
"""

from __future__ import annotations

import argparse
import io
import json
import sqlite3
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from gaia.store import writer

REPO = "/tmp/ask-binding-repo"
REQUESTER_SESSION = "ses-requester"
REQUESTER = "gaia-system"
ORCHESTRATOR_SESSION = "ses-orchestrator"
_ROOT = Path(__file__).resolve().parents[3]
REAL = json.loads(
    (Path(__file__).parent / "fixtures" / "ask_user_question_post_real.json").read_text()
)["results"]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DB", raising=False)
    con = sqlite3.connect(tmp_path / "gaia.db")
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()
    return tmp_path / "gaia.db"


def _rows(db_path, sql, *args):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def _request(n: int) -> str:
    """Persist one phrased pending request whose question is unique to ``n``."""
    from gaia.approvals import core

    return core.request_command_set(
        [{"command": f"git push origin feat/binding-{n}", "cwd": REPO,
          "does": f"git push: sube la rama {n} al remoto.",
          "impact": "La rama queda visible para todo el equipo."}],
        what=f"Publicar la rama {n} en el remoto.",
        question=f"¿Publico la rama {n}?",
        session_id=REQUESTER_SESSION, agent_id=REQUESTER,
    )


def _phraseless() -> str:
    """Persist a pending request sealed without a question or item phrases."""
    from gaia.approvals import core, store

    payload = core.seal_request(
        "command_set", [{"command": "git push origin feat/sin-frases", "cwd": REPO}],
        what="Publicar la rama sin frases.",
        session_id=REQUESTER_SESSION, agent_id=REQUESTER,
    )
    return store.insert_requested(payload, agent_id=REQUESTER, session_id=REQUESTER_SESSION)


def _question_cli(*approval_ids):
    from bin.cli.approvals import cmd_question

    out = io.StringIO()
    with redirect_stdout(out):
        code = cmd_question(argparse.Namespace(approval_ids=list(approval_ids), json=True))
    return code, json.loads(out.getvalue().strip().splitlines()[-1])


def _surfaces(*approval_ids):
    from gaia.approvals import core

    return core.question_batch(list(approval_ids))


def _pre(questions, tool_use_id):
    from adapters.claude_code import ClaudeCodeAdapter
    from adapters.types import HookEvent, HookEventType

    payload = {
        "hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion",
        "session_id": ORCHESTRATOR_SESSION, "tool_use_id": tool_use_id,
        "tool_input": {"questions": questions},
    }
    return ClaudeCodeAdapter().adapt_pre_tool_use(
        HookEvent(event_type=HookEventType.PRE_TOOL_USE, session_id=ORCHESTRATOR_SESSION,
                  payload=payload)
    ).output


def _post(questions, answers, tool_use_id):
    """Deliver the PostToolUse event in the host's shape (tool_response = questions + answers)."""
    from adapters.claude_code import ClaudeCodeAdapter
    from adapters.types import HookEvent, HookEventType

    payload = {
        "hook_event_name": "PostToolUse", "tool_name": "AskUserQuestion",
        "session_id": ORCHESTRATOR_SESSION, "tool_use_id": tool_use_id,
        "tool_input": {"questions": questions},
        "tool_response": {"questions": questions, "answers": answers},
    }
    return ClaudeCodeAdapter().adapt_post_tool_use(
        HookEvent(event_type=HookEventType.POST_TOOL_USE, session_id=ORCHESTRATOR_SESSION,
                  payload=payload)
    ).output


def _status(approval_id):
    from gaia.approvals.store import get_by_id

    return get_by_id(approval_id)["status"]


def _shown(db_path, approval_id):
    return [
        json.loads(row["metadata_json"])
        for row in _rows(db_path, "SELECT metadata_json FROM approval_events "
                                  "WHERE approval_id = ? AND event_type = 'SHOWN'", approval_id)
    ]


def _deny_reason(output):
    specific = output["hookSpecificOutput"]
    assert specific["permissionDecision"] == "deny"
    return specific["permissionDecisionReason"]


# --------------------------------------------------------------------------- #
# PreToolUse: shown only when it is the batch Gaia produced
# --------------------------------------------------------------------------- #

def test_question_verb_prints_only_the_ask_user_question_input(db):
    first = _request(1)

    code, out = _question_cli(first)

    assert code == 0
    assert out == {"questions": [_surfaces(first)[0].question]}


def test_pre_one_signature_shows_the_renderer_text_and_records_shown(db):
    first = _request(1)
    _, asked = _question_cli(first)

    output = _pre(asked["questions"], "toolu_one")

    assert output == {"systemMessage": _surfaces(first)[0].text}
    assert _shown(db, first) == [{"native_ref": "toolu_one", "position": 0}]
    ordinary = REAL[0]["tool_response"]["questions"]
    assert _pre(ordinary, REAL[0]["tool_use_id"]) == {}


def test_pre_four_signatures_show_each_text_in_order(db):
    ids = [_request(n) for n in range(1, 5)]
    _, asked = _question_cli(*ids)

    output = _pre(asked["questions"], "toolu_four")

    texts = [surface.text for surface in _surfaces(*ids)]
    assert output["systemMessage"] == "\n\n".join(texts)
    assert "hookSpecificOutput" not in output
    for position, approval_id in enumerate(ids):
        assert _shown(db, approval_id) == [{"native_ref": "toolu_four", "position": position}]


def test_pre_question_differing_from_the_batch_is_denied_with_a_reason(db):
    first = _request(1)
    _, asked = _question_cli(first)
    asked["questions"][0]["options"][0]["label"] = f"Approve [{first}]"

    reason = _deny_reason(_pre(asked["questions"], "toolu_changed"))

    assert "question 1" in reason and "gaia approvals question" in reason
    assert _shown(db, first) == []


def test_pre_signature_without_phrases_is_not_shown_and_is_denied(db):
    from gaia.approvals import surface
    from gaia.approvals.store import get_by_id

    bare = _phraseless()
    code, refused = _question_cli(bare)
    assert code == 1 and "--question" in refused["error"]
    forged = surface.render(json.loads(get_by_id(bare)["payload_json"]), bare).question

    reason = _deny_reason(_pre([forged], "toolu_bare"))

    assert "--question" in reason and "--does for item 1" in reason
    assert _shown(db, bare) == []


# --------------------------------------------------------------------------- #
# PostToolUse: each answer decides only its own signature
# --------------------------------------------------------------------------- #

def test_post_each_answer_decides_only_its_own_signature_by_position(db):
    for result in REAL:
        response = result["tool_response"]
        assert set(response) == {"questions", "answers"}
        assert set(response["answers"]) <= {q["question"] for q in response["questions"]}
    ids = [_request(n) for n in range(1, 5)]
    _, asked = _question_cli(*ids)
    questions = asked["questions"]
    _pre(questions, "toolu_batch")

    _post(questions, {questions[0]["question"]: "Reject",
                      questions[2]["question"]: "Approve"}, "toolu_batch")

    assert [_status(a) for a in ids] == ["rejected", "pending", "approved", "pending"]


def test_post_approve_label_carries_no_id_and_activates_a_bound_grant(db):
    first = _request(1)
    _, asked = _question_cli(first)
    questions = asked["questions"]
    assert all(first not in option["label"] for option in questions[0]["options"])
    _pre(questions, "toolu_approve")

    _post(questions, {questions[0]["question"]: "Approve"}, "toolu_approve")

    assert _status(first) == "approved"
    grant = _rows(db, "SELECT * FROM approval_grants WHERE approval_id = ?", first)
    assert len(grant) == 1
    assert (grant[0]["session_id"], grant[0]["agent_id"]) == (REQUESTER_SESSION, REQUESTER)


def test_post_answer_is_bound_by_tool_use_id_not_by_question_text(db):
    first = _request(1)
    _, asked = _question_cli(first)
    questions = asked["questions"]
    _pre(questions, "toolu_shown")

    _post(questions, {questions[0]["question"]: "Approve"}, "toolu_never_shown")

    assert _status(first) == "pending"


def test_post_label_with_an_approval_id_never_activates(db):
    first = _request(1)
    question = "Proceed?"

    _post([{"question": question, "header": "Legacy",
            "options": [{"label": f"Approve -- git push [{first}]"}, {"label": "Reject"}]}],
          {question: f"Approve -- git push [{first}]"}, "toolu_legacy")

    assert _status(first) == "pending"


def test_post_details_shows_the_renderer_details_and_asks_that_signature_again(db):
    first, second = _request(1), _request(2)
    _, asked = _question_cli(first, second)
    questions = asked["questions"]
    _pre(questions, "toolu_details")

    output = _post(questions, {questions[1]["question"]: "Details"}, "toolu_details")

    assert output["systemMessage"] == _surfaces(first, second)[1].details
    context = output["hookSpecificOutput"]["additionalContext"]
    assert f"gaia approvals question {second}" in context and first not in context
    assert [_status(first), _status(second)] == ["pending", "pending"]


@pytest.mark.parametrize(
    "answer",
    [
        REAL[1]["tool_response"]["answers"]["6 — ¿Cómo pauso un plan o una tarea?"],
        REAL[2]["tool_response"]["answers"]["¿Reinstalo Gaia?"],
        None,
    ],
    ids=["free-text", "dismissed", "no-selection"],
)
def test_post_free_text_dismissal_or_no_selection_decide_nothing(db, answer):
    first = _request(1)
    _, asked = _question_cli(first)
    questions = asked["questions"]
    _pre(questions, "toolu_nothing")

    answers = {} if answer is None else {questions[0]["question"]: answer}
    output = _post(questions, answers, "toolu_nothing")

    assert _status(first) == "pending"
    assert "systemMessage" not in output


# --------------------------------------------------------------------------- #
# Wiring: the host calls PreToolUse for AskUserQuestion
# --------------------------------------------------------------------------- #

def test_pre_tool_use_is_registered_for_ask_user_question():
    manifest = json.loads((_ROOT / "build" / "gaia.manifest.json").read_text())
    hooks = json.loads((_ROOT / "hooks" / "hooks.json").read_text())

    assert {"matcher": "AskUserQuestion"} in manifest["hooks"]["matchers"]["PreToolUse"]
    assert "AskUserQuestion" in [entry["matcher"] for entry in hooks["hooks"]["PreToolUse"]]
