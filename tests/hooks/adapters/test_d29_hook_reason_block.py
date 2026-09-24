"""Claude Code shows each signature's block through the PreToolUse decision reason (plan 76, task 16, D29).

The orchestrator asks AskUserQuestion with the object ``gaia approvals
question`` prints, unchanged. PreToolUse recognises it as Gaia's, records
SHOWN per signature, and answers ``allow`` whose ``permissionDecisionReason``
is the block of every signature in the call (the Details block on a Details
re-ask). The hooks reference documents that reason as shown to the user for
``allow``, and that ``allow`` without ``updatedInput`` does not answer
AskUserQuestion: no ``updatedInput`` is returned, so the question opens. A
question Gaia did not print is denied without SHOWN. Nothing reads the
transcript or compares printed text.
"""

from __future__ import annotations

import argparse
import io
import json
import sqlite3
from contextlib import redirect_stdout

import pytest

from gaia.store import writer

SESSION = "ses-d29-orchestrator"


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


def _request(n: int) -> str:
    from gaia.approvals import core

    return core.request_command_set(
        [{"command": f"git push origin feat/d29-{n}", "cwd": "/tmp/d29-repo",
          "does": f"git push: sube la rama {n} al remoto.",
          "impact": "La rama queda visible para todo el equipo."}],
        what=f"Publicar la rama {n} en el remoto.",
        question=f"¿Publico la rama {n}?",
        session_id="ses-d29-requester", agent_id="developer",
        requested_from="/tmp/d29-repo",
    )


def _question_cli(*approval_ids, details=False):
    from bin.cli.approvals import cmd_question

    out = io.StringIO()
    with redirect_stdout(out):
        code = cmd_question(argparse.Namespace(
            approval_ids=list(approval_ids), json=True, details=details))
    assert code == 0
    return json.loads(out.getvalue().strip().splitlines()[-1])["questions"]


def _pre(questions, tool_use_id, **extra):
    from adapters.claude_code import ClaudeCodeAdapter
    from adapters.types import HookEvent, HookEventType

    payload = {
        "hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion",
        "session_id": SESSION, "tool_use_id": tool_use_id,
        "tool_input": {"questions": questions}, **extra,
    }
    return ClaudeCodeAdapter().adapt_pre_tool_use(
        HookEvent(event_type=HookEventType.PRE_TOOL_USE, session_id=SESSION, payload=payload)
    ).output


def _presented(tool_use_id):
    from gaia.approvals import core

    return core.presented(tool_use_id)


def test_d29_hook_reason_block_one_signature_allows_with_its_block(db):
    from gaia.approvals import core

    approval_id = _request(1)
    questions = _question_cli(approval_id)

    specific = _pre(questions, "toolu_d29_one")["hookSpecificOutput"]

    assert questions[0]["question"] == "¿Publico la rama 1?"
    assert specific["permissionDecision"] == "allow"
    assert specific["permissionDecisionReason"] == core.question_batch([approval_id])[0].block
    assert "updatedInput" not in specific
    assert _presented("toolu_d29_one") == [(0, approval_id)]


def test_d29_hook_reason_block_four_signatures_each_bring_their_block(db):
    from gaia.approvals import core

    ids = [_request(n) for n in range(1, 5)]

    specific = _pre(_question_cli(*ids), "toolu_d29_four")["hookSpecificOutput"]

    blocks = [s.block for s in core.question_batch(ids)]
    assert specific["permissionDecision"] == "allow"
    assert specific["permissionDecisionReason"] == "\n\n".join(blocks)
    assert "updatedInput" not in specific
    assert _presented("toolu_d29_four") == list(enumerate(ids))


def test_d29_hook_reason_block_details_re_ask_brings_the_details_block(db):
    from gaia.approvals import core

    approval_id = _request(1)

    specific = _pre(_question_cli(approval_id, details=True), "toolu_d29_det")["hookSpecificOutput"]

    rendered = core.question_batch([approval_id])[0]
    assert specific["permissionDecisionReason"] == rendered.details_block
    assert "git push origin feat/d29-1" not in specific["permissionDecisionReason"]


def test_d29_hook_reason_block_a_question_gaia_did_not_print_is_denied_without_shown(db):
    approval_id = _request(1)
    typed = dict(_question_cli(approval_id)[0], question="¿Publico la rama 1 ya?")

    specific = _pre([typed], "toolu_d29_typed")["hookSpecificOutput"]

    assert specific["permissionDecision"] == "deny"
    assert _presented("toolu_d29_typed") == []


def test_d29_hook_reason_block_never_reads_the_transcript(db, tmp_path):
    approval_id = _request(1)
    questions = _question_cli(approval_id)
    missing = tmp_path / "no-such-transcript.jsonl"

    with_path = _pre(questions, "toolu_d29_tx", transcript_path=str(missing))

    assert with_path["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert not missing.exists()
