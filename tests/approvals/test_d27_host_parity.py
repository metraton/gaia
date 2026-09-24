"""Both hosts show the same block and Details bytes for one signature (plan 76, task 16, D26, D29).

Claude Code shows the block through the PreToolUse decision reason; OpenCode's
plugin posts the ``block`` / ``details_block`` of its presentation before a
question that carries only the short question. Both come from one renderer, so
the bytes must be identical.
"""

from __future__ import annotations

import argparse
import io
import json
import sqlite3
from contextlib import redirect_stdout

import pytest

from gaia.store import writer

SESSION = "ses-parity"


@pytest.fixture
def approval_id(tmp_path, monkeypatch):
    from gaia.approvals import core

    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_HOST_SESSION_ID", raising=False)
    con = sqlite3.connect(tmp_path / "gaia.db")
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()
    return core.request_command_set(
        [{"command": "git push origin feature/demo-login", "cwd": "/tmp/parity/demo-repo",
          "does": "git push: sube la rama de prueba al remoto.",
          "impact": "La rama queda visible para todo el equipo."}],
        what="Publicar la rama de prueba.",
        question="¿Publico la rama?",
        session_id="ses-parity-requester", agent_id="developer",
        requested_from="/tmp/parity",
    )


def _claude_code_reason(approval_id, details):
    from adapters.claude_code import ClaudeCodeAdapter
    from adapters.types import HookEvent, HookEventType
    from bin.cli.approvals import cmd_question

    out = io.StringIO()
    with redirect_stdout(out):
        cmd_question(argparse.Namespace(approval_ids=[approval_id], json=True, details=details))
    questions = json.loads(out.getvalue().strip().splitlines()[-1])["questions"]
    payload = {
        "hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion",
        "session_id": SESSION, "tool_use_id": f"toolu_parity_{details}",
        "tool_input": {"questions": questions},
    }
    output = ClaudeCodeAdapter().adapt_pre_tool_use(
        HookEvent(event_type=HookEventType.PRE_TOOL_USE, session_id=SESSION, payload=payload)
    ).output
    return output["hookSpecificOutput"]["permissionDecisionReason"]


def _opencode_signature(approval_id):
    from bin.cli.approvals import _opencode_presentation
    from gaia.approvals import store

    presentation = _opencode_presentation(store.get_by_id(approval_id), SESSION, "call-parity")
    assert "presentation_error" not in presentation, presentation
    return presentation["signature"]


def test_d27_host_parity_block_bytes_match(approval_id):
    signature = _opencode_signature(approval_id)

    assert _claude_code_reason(approval_id, details=False) == signature["block"]
    assert signature["block"].startswith("```\nSolicitud de aprobación · developer\n")


def test_d27_host_parity_details_bytes_match(approval_id):
    assert _claude_code_reason(approval_id, details=True) == _opencode_signature(approval_id)["details_block"]


def test_d27_host_parity_opencode_question_carries_only_the_short_question(approval_id):
    signature = _opencode_signature(approval_id)

    assert signature["question"] == "¿Publico la rama?"
    assert [o["label"] for o in signature["options"]] == ["Approve", "Reject", "Details"]
