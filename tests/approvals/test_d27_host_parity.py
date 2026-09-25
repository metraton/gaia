"""Both hosts ask the same question bytes for one signature (plan 76, task 16; D37 replaces the D27/D29 block).

Claude Code's orchestrator asks what ``gaia approvals question`` prints;
OpenCode's plugin writes into the orchestrator's question call the questions of
``gaia approvals opencode-present`` (D39). Both come from one renderer, so each
command's question and its Details re-ask must be identical, header and
options included.
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
PUSH = "git push origin feature/demo-login"


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
        [{"command": PUSH, "cwd": "/tmp/parity/demo-repo",
          "does": "git push: sube la rama de prueba al remoto.",
          "impact": "La rama queda visible para todo el equipo."}],
        what="Publicar la rama de prueba.",
        question="¿Publico la rama?",
        session_id="ses-parity-requester", agent_id="developer",
        requested_from="/tmp/parity", rollback="Borrar la rama remota.",
    )


def _claude_code_questions(approval_id, details):
    from bin.cli.approvals import cmd_question

    out = io.StringIO()
    with redirect_stdout(out):
        cmd_question(argparse.Namespace(approval_ids=[approval_id], json=True, details=details))
    return json.loads(out.getvalue().strip().splitlines()[-1])["questions"]


def _opencode_signature(approval_id):
    from bin.cli.approvals import _opencode_presentation
    from gaia.approvals import store

    presentation = _opencode_presentation(store.get_by_id(approval_id), SESSION, "call-parity")
    assert "presentation_error" not in presentation, presentation
    return presentation["signature"]


def _shown(questions):
    return [{key: q[key] for key in ("question", "header", "options")} for q in questions]


def test_d27_host_parity_block_bytes_match(approval_id):
    """The signature question each host asks is the same bytes (D37)."""
    opencode = _opencode_signature(approval_id)["questions"]

    assert _shown(_claude_code_questions(approval_id, details=False)) == opencode
    assert opencode[0]["question"] == f"[ GAIA-SECURITY ] [ AGENT-REQUEST ] [ developer ] [ COMMAND ] [ {PUSH} ]"


def test_d27_host_parity_details_bytes_match(approval_id):
    opencode = _opencode_signature(approval_id)["details_questions"]

    assert _shown(_claude_code_questions(approval_id, details=True)) == opencode
    assert opencode[0]["question"].startswith("[ GAIA-SECURITY ] [ DETAILS ] [ developer ]")


def test_d27_host_parity_opencode_question_carries_only_the_short_question(approval_id):
    """OpenCode asks one question per command with the three fixed options, nothing posted outside it."""
    signature = _opencode_signature(approval_id)

    assert set(signature) == {"questions", "details_questions"}
    [question] = signature["questions"]
    assert question["header"] == "Firma 1/1"
    assert [o["label"] for o in question["options"]] == ["Approve", "Reject", "Details"]
