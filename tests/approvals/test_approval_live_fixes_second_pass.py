"""The second live-test pass of plan 76 task 14, pinned against what each component produces.

(1) OpenCode: a request made with ``gaia approvals request-set`` or
``request-file-write`` opens no question in the requester's session, since only
the orchestrator opens one (D39); a signature-shaped question Gaia did not open
is refused with what to do instead.
(2) A command sealed outside the requester's shell folder shows that folder in
Details, never in the question (D35, D37).
(3) A long command stays exact on one line (D37 replaced the wrapping).
(4) A refused sensitive read is not called irreversible.
(5) The installed CLI takes ``revoke --reason`` and records it.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from gaia.store import writer

_ROOT = Path(__file__).resolve().parents[2]
for _path in (_ROOT, _ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

GAIA_CLI = _ROOT / "bin" / "gaia"
DRIVER = _ROOT / "tests" / "opencode" / "presentation_driver.ts"
SESSION = "ses-live-fixes-second"
# The role presentation_driver.ts dispatches: only the requesting agent presents.
AGENT = "gaia-system"
COMMAND = "git push origin feat/live"
SHORT_QUESTION = "¿Publico la rama?"


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A scratch substrate, the requester's shell folder and another folder to seal."""
    from modules.core.paths import clear_path_cache

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_HOST_SESSION_ID", raising=False)
    con = sqlite3.connect(data_dir / "gaia.db")
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()
    clear_path_cache()
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    monkeypatch.setattr("modules.core.state.find_claude_dir", lambda: claude_dir)
    repo = tmp_path / "repo"
    other = tmp_path / "sealed dir"
    repo.mkdir()
    other.mkdir()
    monkeypatch.chdir(repo)
    return {"db": data_dir / "gaia.db", "repo": str(repo), "other": str(other)}


def _request_set(command=COMMAND, cwd=None):
    """Seal through the CLI handler, from the requester's shell folder (the process cwd)."""
    from bin.cli.approvals import cmd_request_set

    args = argparse.Namespace(
        command=[command], cwd=[cwd] if cwd else None, expect_exit=None,
        what="Publicar la rama.", question=SHORT_QUESTION,
        does=["Sube la rama al remoto."], impact=["La rama queda publicada."],
        rationale=None, verification=None, rollback="Borrar la rama remota.",
        agent_id=AGENT, session_id=SESSION, json=True,
    )
    out = io.StringIO()
    with redirect_stdout(out):
        code = cmd_request_set(args)
    assert code == 0, out.getvalue()
    return json.loads(out.getvalue().strip().splitlines()[-1])["approval_id"]


def _row(approval_id):
    from gaia.approvals import store

    return store.get_by_id(approval_id)


def _rendered(approval_id):
    from gaia.approvals import surface

    return surface.render(json.loads(_row(approval_id)["payload_json"]), approval_id)


# --------------------------------------------------------------------------- #
# (1) OpenCode: the request opens no question; the orchestrator asks (D39)
# --------------------------------------------------------------------------- #

@pytest.fixture()
def cli_env(tmp_path, monkeypatch, bootstrapped_db_template):
    """A real bootstrapped database reachable by the plugin's own gaia subprocesses."""
    from tests.conftest import copy_bootstrapped_db

    db_path = tmp_path / "second-pass.db"
    copy_bootstrapped_db(bootstrapped_db_template, db_path)
    monkeypatch.setenv("GAIA_DB", str(db_path))
    env = os.environ.copy()
    env["GAIA_DB"] = str(db_path)
    env.pop("GAIA_HOST_SESSION_ID", None)
    return env


def _cli(env, *argv):
    return subprocess.run(
        [sys.executable, str(GAIA_CLI), *argv],
        env=env, capture_output=True, text=True, timeout=120,
    )


def _request_line(kind, target):
    phrases = [
        "--what", "Publicar la rama.", "--question", SHORT_QUESTION,
        "--does", "Sube la rama al remoto.", "--impact", "La rama queda publicada.",
        "--rollback", "Borrar la rama remota.",
    ]
    if kind == "request-set":
        return ["approvals", "request-set", "--command", target, *phrases]
    return ["approvals", "request-file-write", "--path", target, *phrases]


@pytest.mark.parametrize("kind", ["request-set", "request-file-write"])
def test_approval_live_fixes_opencode_request_opens_no_question_in_the_requester_session(
    cli_env, tmp_path, kind,
):
    target = COMMAND if kind == "request-set" else str(tmp_path / "protected.json")
    argv = _request_line(kind, target)
    sealed = _cli(cli_env, *argv, "--agent-id", AGENT, "--session-id", SESSION)
    assert sealed.returncode == 0, sealed.stdout + sealed.stderr
    call_id = f"call-{kind}"
    scenario = {
        "sessionID": SESSION, "callID": call_id, "approvalID": "unused",
        "args": {"command": "gaia " + " ".join(argv)},
        "requestOutput": sealed.stdout,
    }
    driven = subprocess.run(
        ["bun", str(DRIVER), json.dumps(scenario)],
        env=cli_env, capture_output=True, text=True, timeout=180,
    )
    assert driven.returncode == 0, driven.stdout + driven.stderr
    delivered = json.loads(driven.stdout.strip().splitlines()[-1])

    assert delivered.get("error") is None, delivered
    assert delivered["controlPrompts"] == [], delivered
    from gaia.approvals.store import get_history

    approval_id = next(
        token for token in sealed.stdout.split() if token.startswith("P-")
    )
    assert [event for event in get_history(approval_id) if event["event_type"] == "SHOWN"] == []


def _opencode_pre_question(questions):
    from adapters.opencode import OpenCodeAdapter

    adapter = OpenCodeAdapter()
    return adapter.adapt_pre_tool_use(adapter.parse_event(json.dumps({
        "event": "tool.execute.before", "sessionID": "ses-orchestrator", "callID": "call-q",
        "agent": "gaia-orchestrator", "tool": "AskUserQuestion",
        "args": {"questions": questions},
    }))).output


# The question the orchestrator typed on the live test (prt_0d16042ba001iH2n5iGwDx15q5).
LIVE_LOOKALIKE = {
    "question": (
        "Solicitud de aprobación · gaia-system  \nRestrict access to a harmless local test probe."
        "\n\nComandos (1)  \n1. `chmod 600 /tmp/probe`\n\nApprove this reversible permission test?"
    ),
    "header": "Approval",
    "options": [
        {"label": "Approve", "description": "Approve"},
        {"label": "Reject", "description": "Reject"},
        {"label": "Details", "description": "Details"},
    ],
    "multiple": False,
}


@pytest.mark.parametrize("variant", ["live copy", "text only", "header only"])
def test_approval_live_fixes_opencode_refuses_a_signature_question_it_did_not_open(variant):
    question = dict(LIVE_LOOKALIKE)
    if variant == "text only":
        question["options"] = [{"label": "Sí", "description": "Sigue"}, {"label": "No", "description": "Para"}]
    if variant == "header only":
        question = {
            "question": "¿Sigo con el cambio?", "header": "Approval",
            "options": [{"label": "Sí", "description": "Sigue"}], "multiple": False,
        }

    output = _opencode_pre_question([question])

    assert output.get("action") == "deny", output
    assert "Gaia did not open this question" in output.get("reason", ""), output
    assert "gaia approvals question" in output["reason"], output


def test_approval_live_fixes_opencode_leaves_an_ordinary_question_to_the_policy():
    output = _opencode_pre_question([{
        "question": "¿Qué rama uso?", "header": "Rama",
        "options": [{"label": "main", "description": "La principal"}], "multiple": False,
    }])

    assert "Gaia did not open this question" not in str(output.get("reason", "")), output


# --------------------------------------------------------------------------- #
# (2) The folder, only when a command runs outside the requester's shell folder
# --------------------------------------------------------------------------- #

def test_approval_live_fixes_details_names_the_folder_of_a_command_run_elsewhere(host):
    elsewhere = _rendered(_request_set(cwd=host["other"]))

    assert elsewhere.details.endswith(f"[ CWD: {host['other']} ]"), elsewhere.details
    assert host["other"] not in elsewhere.text


def test_approval_live_fixes_signature_omits_the_folder_the_requester_runs_in(host):
    here = _rendered(_request_set())

    assert host["repo"] not in here.text + here.details, here.details


# --------------------------------------------------------------------------- #
# (3) A long command stays exact on one line (D37; the host wraps it)
# --------------------------------------------------------------------------- #

# The command of the Claude Code live test (P-8a94e99b...), whose Gaia-made
# breaks and the host's own wrap together broke it apart; one line keeps it whole.
# The folder keeps the live one's length off Gaia's scratch, where cp is not T3.
LIVE_SCRATCH = "/home/jorge/ws/me/pruebas/a1fc9164405433383.b2455ec1105e"
LIVE_CP = (
    "cp --verbose --no-dereference --preserve=mode,timestamps "
    f"{LIVE_SCRATCH}/movido.txt {LIVE_SCRATCH}/copia-de-prueba.txt"
)


@pytest.mark.parametrize("command", [
    LIVE_CP,
    f"cp -S .anterior {LIVE_SCRATCH}/origen {LIVE_SCRATCH}/destino",
    "mv muestra.txt movido.txt",
])
def test_approval_live_fixes_long_command_stays_exact_on_one_line(host, command):
    assert _rendered(_request_set(command=command)).text.splitlines() == [
        f"[ GAIA-SECURITY ] [ AGENT-REQUEST ] [ {AGENT} ] [ COMMAND ] [ {command} ]"
    ]


# --------------------------------------------------------------------------- #
# (4) A refused sensitive read is categorical, not irreversible
# --------------------------------------------------------------------------- #

def _claude_bash_refusal(command, cwd):
    from adapters.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter()
    event = adapter.parse_event(json.dumps({
        "session_id": SESSION, "transcript_path": "/nonexistent/transcript.jsonl",
        "cwd": cwd, "permission_mode": "acceptEdits", "hook_event_name": "PreToolUse",
        "agent_id": "a0f1e2d3c4b5a6978", "agent_type": AGENT, "tool_name": "Bash",
        "tool_input": {"command": command}, "tool_use_id": "toolu_sensitive",
    }))
    response = adapter.adapt_pre_tool_use(event)
    if isinstance(response.output, str):
        # A categorical refusal is the hook's stderr text under exit code 2.
        assert response.exit_code == 2, response
        return response.output
    decision = response.output.get("hookSpecificOutput", {})
    assert decision.get("permissionDecision") == "deny", response.output
    return decision.get("permissionDecisionReason", "")


def test_approval_live_fixes_sensitive_read_refusal_is_not_called_irreversible(host):
    reason = _claude_bash_refusal(f"cat {Path.home()}/.ssh/id_ed25519", host["repo"])

    assert "[BLOCKED]" in reason, reason
    assert "irreversible" not in reason, reason


def test_approval_live_fixes_blocked_command_is_still_called_irreversible(host):
    reason = _claude_bash_refusal("dd if=/dev/zero of=/dev/sda", host["repo"])

    assert "(irreversible operation)" in reason, reason


# --------------------------------------------------------------------------- #
# (5) The installed CLI: revoke --reason
# --------------------------------------------------------------------------- #

def test_approval_live_fixes_installed_cli_revokes_with_a_reason(host):
    from gaia.approvals.store import get_history

    approval_id = _request_set()

    revoked = _cli(os.environ.copy(), "approvals", "revoke", approval_id, "--reason", "la prueba terminó", "--yes")

    assert revoked.returncode == 0, revoked.stdout + revoked.stderr
    [event] = [event for event in get_history(approval_id) if event["event_type"] == "REVOKED"]
    assert json.loads(event["metadata_json"])["reason"] == "la prueba terminó"
