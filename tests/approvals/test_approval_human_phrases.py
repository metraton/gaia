"""No signature reaches the user without its requester's human phrases (plan 76, task 11, PD10).

request-set and request-file-write refuse a request missing its title, question,
what each item does and its impact, or its rollback sentence (D38), naming the
phrase that is missing. A
reactive block (Bash, protected write) still seals its request under the
event's identity, but denies naming the exact command or path and the request
line to complete with phrases. The core owns one presentable check, and a
phrased request by the same requester for the same command replaces the
phraseless reactive one instead of reusing it.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import shlex
import sqlite3
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from gaia.store import writer

COMMAND = "git push origin feat/phrases"
#: An existing directory: request-set refuses a --cwd that does not exist (D24).
REPO = str(Path(__file__).resolve().parent)
SESSION = "ses-phrases"
AGENT = "gaia-system"

PHRASES = {
    "what": "Publicar la rama de las frases en el remoto.",
    "question": "¿Publico la rama?",
    "does": "git push: sube la rama al remoto.",
    "impact": "La rama queda visible para todos.",
    "rollback": "Borrar la rama remota.",
}


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


def _set_args(**overrides):
    values = {
        "command": [COMMAND], "cwd": [REPO], "expect_exit": None,
        "what": PHRASES["what"], "question": PHRASES["question"],
        "does": [PHRASES["does"]], "impact": [PHRASES["impact"]],
        "rationale": None, "verification": None, "rollback": PHRASES["rollback"],
        "agent_id": AGENT, "session_id": SESSION, "json": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _file_args(path, **overrides):
    values = {
        "path": path, "what": "Ajustar la configuración protegida.",
        "question": "¿Edito la configuración?", "does": "Cambia un valor del archivo.",
        "impact": "Afecta a la próxima sesión.", "rationale": None,
        "verification": None, "rollback": "Restaurar el archivo desde git.",
        "agent_id": AGENT, "session_id": SESSION, "json": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _run(cmd, args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cmd(args)
    return code, json.loads(out.getvalue().strip().splitlines()[-1])


def _reactive_bash(session_id=SESSION, agent_type=AGENT):
    from modules.tools.bash_validator import BashValidator

    result = BashValidator().validate(
        COMMAND, is_subagent=True, session_id=session_id, agent_type=agent_type,
        hook_payload={"session_id": session_id, "agent_id": "a1b2c3", "agent_type": agent_type,
                      "cwd": REPO, "tool_use_id": "toolu_phrases"},
    )
    assert not result.allowed
    reason = result.block_response["hookSpecificOutput"]["permissionDecisionReason"]
    return re.search(r"approval_id:\s*(P-[\w-]+)", reason).group(1), reason


def _request_line_in(reason, verb):
    """The tokens of the ``gaia approvals <verb>`` line a denial asks its requester to run."""
    prefix = f"gaia approvals {verb}"
    line = next(text.strip() for text in reason.splitlines() if text.strip().startswith(prefix))
    return shlex.split(line)


# --------------------------------------------------------------------------- #
# Asking: request-set and request-file-write demand every phrase
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "overrides, named",
    [
        ({"what": None}, "--what"),
        ({"question": None}, "--question"),
        ({"does": None}, "--does"),
        ({"impact": None}, "--impact"),
        ({"rollback": None}, "--rollback"),
    ],
)
def test_approval_human_phrases_request_set_names_the_missing_phrase(db, overrides, named):
    from bin.cli.approvals import cmd_request_set

    code, out = _run(cmd_request_set, _set_args(**overrides))
    assert code == 1
    assert named in out["error"]
    assert _rows(db, "SELECT id FROM approvals") == []


def test_approval_human_phrases_request_set_title_does_not_fall_back_to_rationale(db):
    from bin.cli.approvals import cmd_request_set

    code, out = _run(cmd_request_set, _set_args(what=None, rationale="push the branch"))
    assert code == 1 and "--what" in out["error"]


@pytest.mark.parametrize(
    "overrides, named",
    [
        ({"what": None}, "--what"),
        ({"question": None}, "--question"),
        ({"does": None}, "--does"),
        ({"impact": None}, "--impact"),
        ({"rollback": None}, "--rollback"),
    ],
)
def test_approval_human_phrases_request_file_write_names_the_missing_phrase(db, tmp_path, overrides, named):
    from bin.cli.approvals import cmd_request_file_write

    path = str(tmp_path / ".claude" / "settings.json")
    code, out = _run(cmd_request_file_write, _file_args(path, **overrides))
    assert code == 1
    assert named in out["error"]
    assert _rows(db, "SELECT id FROM approvals") == []


def test_approval_human_phrases_phrased_requests_are_sealed_presentable(db, tmp_path):
    from gaia.approvals import core, store
    from bin.cli.approvals import cmd_request_file_write, cmd_request_set

    code, planned = _run(cmd_request_set, _set_args())
    assert code == 0
    code, written = _run(cmd_request_file_write, _file_args(str(tmp_path / ".claude" / "settings.json")))
    assert code == 0
    for approval_id in (planned["approval_id"], written["approval_id"]):
        payload = json.loads(store.get_by_id(approval_id)["payload_json"])
        core.check_presentable(payload)
        assert payload["items"][0]["does"] and payload["items"][0]["impact"]


# --------------------------------------------------------------------------- #
# Presenting: one core check
# --------------------------------------------------------------------------- #

def test_approval_human_phrases_presentable_check_rejects_a_request_without_phrases():
    from gaia.approvals import core
    from modules.tools.bash_validator import _build_sealed_payload

    reactive = _build_sealed_payload(COMMAND, "push", "MUTATIVE", agent_type=AGENT, cwd=REPO, session_id=SESSION)
    with pytest.raises(core.NotPresentableError) as refused:
        core.check_presentable(reactive)
    assert "--question" in str(refused.value) and "--does" in str(refused.value)

    phrased = core.seal_request(
        "command_set",
        [{"command": COMMAND, "cwd": REPO, "does": PHRASES["does"], "impact": PHRASES["impact"]}],
        what=PHRASES["what"], question=PHRASES["question"], session_id=SESSION, agent_id=AGENT,
    )
    core.check_presentable(phrased)
    without_impact = {**phrased, "items": [{**phrased["items"][0], "impact": None}]}
    with pytest.raises(core.NotPresentableError, match="--impact"):
        core.check_presentable(without_impact)


# --------------------------------------------------------------------------- #
# Reactive blocks: sealed, denied with the exact target and the line to complete
# --------------------------------------------------------------------------- #

def test_approval_human_phrases_reactive_bash_denial_names_command_and_request_line(db):
    from gaia.approvals import store

    approval_id, reason = _reactive_bash()
    payload = json.loads(store.get_by_id(approval_id)["payload_json"])
    assert payload["requested_by"] == {"session_id": SESSION, "agent_id": AGENT}
    assert f"Command: {COMMAND}" in reason
    tokens = _request_line_in(reason, "request-set")
    assert tokens[tokens.index("--command") + 1] == COMMAND
    assert tokens[tokens.index("--cwd") + 1] == REPO
    for flag in ("--what", "--question", "--does", "--impact", "--rollback"):
        assert flag in tokens
    assert "Report APPROVAL_REQUEST with this approval_id" not in reason


def test_approval_human_phrases_reactive_protected_write_denial_names_path_and_request_line(db, tmp_path):
    from adapters.claude_code import ClaudeCodeAdapter

    target = str(tmp_path / ".claude" / "settings.json")
    response = ClaudeCodeAdapter()._adapt_write_edit(
        "Write", {"file_path": target}, session_id=SESSION, is_subagent=True,
        agent_id="a123", agent_type=AGENT,
    )
    output = response.output["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    reason = output["permissionDecisionReason"]
    assert f"File: {target}" in reason
    tokens = _request_line_in(reason, "request-file-write")
    assert tokens[tokens.index("--path") + 1] == target
    for flag in ("--what", "--question", "--does", "--impact", "--rollback"):
        assert flag in tokens
    assert len(_rows(db, "SELECT id FROM approvals WHERE status='pending'")) == 1


# --------------------------------------------------------------------------- #
# Replacement: the phrased request retires the phraseless reactive one
# --------------------------------------------------------------------------- #

def _retirement(db_path, approval_id):
    status = _rows(db_path, "SELECT status FROM approvals WHERE id=?", approval_id)[0]["status"]
    events = _rows(
        db_path, "SELECT event_type, metadata_json FROM approval_events WHERE approval_id=? ORDER BY id",
        approval_id,
    )
    return status, events[-1]


def test_approval_human_phrases_request_set_replaces_the_reactive_request(db):
    from bin.cli.approvals import cmd_request_set

    reactive, _ = _reactive_bash()
    foreign, _ = _reactive_bash(agent_type="developer")
    code, out = _run(cmd_request_set, _set_args())
    assert code == 0
    assert out["approval_id"] != reactive

    status, last = _retirement(db, reactive)
    assert status != "rejected" and last["event_type"] != "REJECTED"
    metadata = json.loads(last["metadata_json"])
    assert metadata["reason"] == "reemplazada"
    assert metadata["replaced_by"] == out["approval_id"]
    assert _rows(db, "SELECT status FROM approvals WHERE id=?", foreign)[0]["status"] == "pending"

    retried, reason = _reactive_bash()
    assert retried == out["approval_id"]
    assert "gaia approvals request-set" not in reason


def test_approval_human_phrases_request_file_write_replaces_the_reactive_request(db, tmp_path):
    from gaia.approvals import core
    from bin.cli.approvals import cmd_request_file_write

    target = str(tmp_path / ".claude" / "settings.json")
    reactive = core.protected_write_verdict(target, session_id=SESSION, agent_id=AGENT)["approval_id"]
    code, out = _run(cmd_request_file_write, _file_args(target))
    assert code == 0
    assert out["approval_id"] != reactive

    status, last = _retirement(db, reactive)
    assert status != "rejected" and last["event_type"] != "REJECTED"
    assert json.loads(last["metadata_json"])["reason"] == "reemplazada"

    again = core.protected_write_verdict(target, session_id=SESSION, agent_id=AGENT)
    assert again["approval_id"] == out["approval_id"]
    assert again.get("request_line") is None
