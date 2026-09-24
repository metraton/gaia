"""The second live-test pass of plan 76 task 14, pinned against what each component produces.

(1) OpenCode: a request made with ``gaia approvals request-set`` or
``request-file-write`` opens Gaia's own question in the requester's session with
no attempt of the sealed command; a signature-shaped question Gaia did not open
is refused with what to do instead; ``gaia approvals question`` refuses to run
in an OpenCode shell. (What OpenCode shows for a signature is D26's, in
``test_approval_live_fixes_opencode_block.py``.)
(2) A command sealed outside the requester's shell folder shows that folder.
(3) A long command wraps at token boundaries with the continuation indent.
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
        rationale=None, verification=None, rollback=None,
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


def _command_block(text):
    """The lines under ``Comandos (N)``: every command line the user reads."""
    lines = text.split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith("Comandos ("))
    return lines[start + 1:]


# --------------------------------------------------------------------------- #
# (1) OpenCode: Gaia's own question, with no model step after the request
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
    ]
    if kind == "request-set":
        return ["approvals", "request-set", "--command", target, *phrases]
    return ["approvals", "request-file-write", "--path", target, *phrases]


@pytest.mark.parametrize("kind", ["request-set", "request-file-write"])
def test_approval_live_fixes_opencode_request_opens_gaia_question_without_an_attempt(
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
    assert [prompt["path"]["id"] for prompt in delivered["controlPrompts"]] == [SESSION], delivered
    from gaia.approvals.store import get_history

    approval_id = next(
        token for token in sealed.stdout.split() if token.startswith("P-")
    )
    # Queued, not yet shown: SHOWN waits for the block the question call posts.
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

def test_approval_live_fixes_signature_names_the_folder_of_a_command_run_elsewhere(host):
    elsewhere = _rendered(_request_set(cwd=host["other"]))

    assert any(host["other"] in line for line in _command_block(elsewhere.text)), elsewhere.text
    assert host["other"] in elsewhere.question["question"]


def test_approval_live_fixes_signature_omits_the_folder_the_requester_runs_in(host):
    here = _rendered(_request_set())

    assert host["repo"] not in here.text, here.text


# --------------------------------------------------------------------------- #
# (3) A long command keeps its continuation indent
# --------------------------------------------------------------------------- #

LONG_TARGETS = [
    f"/home/jorge/ws/me/.project-worktrees/gaia/e811c2894a09cdf63697b03f7b1f5702/tests/fixture-{n}.json"
    for n in range(3)
]
LONG_COMMAND = "chmod 600 " + " ".join(LONG_TARGETS)


def test_approval_live_fixes_long_command_wraps_with_its_continuation_indent(host):
    block = _command_block(_rendered(_request_set(command=LONG_COMMAND)).text)
    first, *rest = block

    assert first.startswith("  1  chmod 600")
    assert rest, block
    for line in block:
        overlong = [token for token in line.split() if len(token) > 80 - 9 - 2]
        assert len(line) <= 80 or len(overlong) == 1, line
    assert all(line.startswith(" " * 9) and not line.startswith(" " * 10) for line in rest), block
    assert all(line.endswith(" \\") for line in block[:-1]), block
    tokens = [token for line in block for token in line.removesuffix(" \\").split()]
    assert tokens == ["1", *LONG_COMMAND.split(" ")]


# The command of the Claude Code live test (P-8a94e99b...): its two paths shared
# one line past the host's width, and the host wrapped the second with no indent.
# The folder keeps the live one's length off Gaia's scratch, where cp is not T3.
LIVE_SCRATCH = "/home/jorge/ws/me/pruebas/a1fc9164405433383.b2455ec1105e"
LIVE_CP = (
    "cp --verbose --no-dereference --preserve=mode,timestamps "
    f"{LIVE_SCRATCH}/movido.txt {LIVE_SCRATCH}/copia-de-prueba.txt"
)


def _layout(command):
    """Each command line as read, without its indent or trailing continuation."""
    block = _command_block(_rendered(_request_set(command=command)).text)
    return [line.strip().removesuffix(" \\") for line in block]


def test_approval_live_fixes_wrapped_command_gives_each_positional_its_own_line(host):
    assert _layout(LIVE_CP) == [
        "1  cp",
        "--verbose",
        "--no-dereference",
        "--preserve=mode,timestamps",
        f"{LIVE_SCRATCH}/movido.txt",
        f"{LIVE_SCRATCH}/copia-de-prueba.txt",
    ]


def test_approval_live_fixes_wrapped_flag_keeps_one_value_and_short_command_one_line(host):
    wrapped = f"cp -S .anterior {LIVE_SCRATCH}/origen {LIVE_SCRATCH}/destino"

    assert _layout(wrapped) == [
        "1  cp",
        "-S .anterior",
        f"{LIVE_SCRATCH}/origen",
        f"{LIVE_SCRATCH}/destino",
    ]
    assert _layout("mv muestra.txt movido.txt") == ["1  mv muestra.txt movido.txt"]


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
