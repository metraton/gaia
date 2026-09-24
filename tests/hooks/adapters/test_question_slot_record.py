"""The hook resolves a signature by the slot Gaia handed it out in (plan 76, task 19, D31).

``gaia approvals question`` records which pending signatures it handed out and
at which slot. Two pending signatures with the same short question render the
same question object, so the AskUserQuestion PreToolUse hook reads that record,
never the transcript, to know which one each slot asks. A question no hand-out
and no single pending signature accounts for is denied without SHOWN.
"""

from __future__ import annotations

import argparse
import io
import json
import sqlite3
from contextlib import redirect_stdout

import pytest

from gaia.store import writer

SESSION = "ses-d31-orchestrator"
SAME = "¿Publico la rama?"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_HOST_SESSION_ID", raising=False)
    con = sqlite3.connect(tmp_path / "gaia.db")
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()
    return tmp_path / "gaia.db"


def _request(n: int, question: str) -> str:
    from gaia.approvals import core

    return core.request_command_set(
        [{"command": f"git push origin feat/d31-{n}", "cwd": "/tmp/d31-repo",
          "does": f"git push: sube la rama {n} al remoto.",
          "impact": "La rama queda visible para todo el equipo."}],
        what=f"Publicar la rama {n} en el remoto.",
        question=question,
        session_id=f"ses-d31-requester-{n}", agent_id="developer",
        requested_from="/tmp/d31-repo",
    )


def _question_cli(*approval_ids, details=False):
    from bin.cli.approvals import cmd_question

    out = io.StringIO()
    with redirect_stdout(out):
        code = cmd_question(argparse.Namespace(
            approval_ids=list(approval_ids), json=True, details=details))
    assert code == 0
    return json.loads(out.getvalue().strip().splitlines()[-1])["questions"]


def _pre(questions, tool_use_id):
    from adapters.claude_code import ClaudeCodeAdapter
    from adapters.types import HookEvent, HookEventType

    payload = {
        "hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion",
        "session_id": SESSION, "tool_use_id": tool_use_id,
        "tool_input": {"questions": questions},
    }
    return ClaudeCodeAdapter().adapt_pre_tool_use(
        HookEvent(event_type=HookEventType.PRE_TOOL_USE, session_id=SESSION, payload=payload)
    ).output["hookSpecificOutput"]


def test_question_slot_record_two_pending_with_the_same_question_resolve_to_the_one_handed_out(db):
    from gaia.approvals import core, store

    first, second = _request(1, SAME), _request(2, SAME)

    asked_second = _question_cli(second)
    shown_second = _pre(asked_second, "toolu_d31_second")
    asked_first = _question_cli(first)
    shown_first = _pre(asked_first, "toolu_d31_first")

    assert asked_first == asked_second
    assert shown_second["permissionDecision"] == "allow"
    assert shown_second["permissionDecisionReason"] == core.question_batch([second])[0].block
    assert core.presented("toolu_d31_second") == [(0, second)]
    assert shown_first["permissionDecisionReason"] == core.question_batch([first])[0].block
    assert core.presented("toolu_d31_first") == [(0, first)]

    decided = core.decide(native_ref="toolu_d31_first", session_id=SESSION, option_key="reject")
    assert (decided.status, decided.approval_id) == ("rejected", first)
    assert store.get_by_id(second)["status"] == "pending"


def test_question_slot_record_four_signature_batch_and_details_stay_bound_per_slot(db):
    from gaia.approvals import core

    rival = _request(1, SAME)
    ids = [_request(2, SAME)] + [_request(n, f"¿Publico la rama {n}?") for n in range(3, 6)]

    shown = _pre(_question_cli(*ids), "toolu_d31_four")
    details = _pre(_question_cli(*ids, details=True), "toolu_d31_four_details")

    rendered = core.question_batch(ids)
    assert shown["permissionDecision"] == "allow"
    assert shown["permissionDecisionReason"] == "\n\n".join(s.block for s in rendered)
    assert core.presented("toolu_d31_four") == list(enumerate(ids))
    assert details["permissionDecisionReason"] == "\n\n".join(s.details_block for s in rendered)
    assert core.presented("toolu_d31_four_details") == list(enumerate(ids))
    assert rival not in dict(core.presented("toolu_d31_four")).values()


def test_question_slot_record_a_question_gaia_did_not_hand_out_is_denied_without_shown(db):
    from gaia.approvals import core

    _request(1, SAME)
    handed = _question_cli(_request(2, SAME))
    typed = [dict(handed[0], question="¿Publico la rama ya?")]
    lone_pair = [_request(3, "¿Subo la rama?"), _request(4, "¿Subo la rama?")]
    never_handed = [core.question_batch([lone_pair[0]])[0].question]

    typed_verdict = _pre(typed, "toolu_d31_typed")
    never_verdict = _pre(never_handed, "toolu_d31_never")

    assert typed_verdict["permissionDecision"] == "deny"
    assert core.presented("toolu_d31_typed") == []
    assert never_verdict["permissionDecision"] == "deny"
    assert core.presented("toolu_d31_never") == []
