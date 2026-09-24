"""The four fixes the first live test found (plan 76, task 14; brief D23 and D24, PD13).

(1) In Claude Code AskUserQuestion asks only the short question, the object
checked byte for byte by PreToolUse, whose ``allow`` reason shows the block
(D29, which replaced D23's text inside the question); no hook answers through
``systemMessage``, which the desktop app does not show; Details comes back as
its own block with the re-asked question; commands are shown exactly. (2) An item sealed in another directory runs as the
exact ``cd <sealed directory> && <sealed command>`` on both hosts, the only
compound accepted, and ``request-set`` refuses a directory that does not exist.
(3) One presentation records one SHOWN. (4) The orchestrator may withdraw with
``revoke --reason``.
"""

from __future__ import annotations

import argparse
import io
import json
import shlex
import sqlite3
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from gaia.store import writer

_ROOT = Path(__file__).resolve().parents[2]
for _path in (_ROOT, _ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

SESSION = "ses-live-fixes"
AGENT = "gaia-system"
AGENT_ID = "a0f1e2d3c4b5a6978"
ORCHESTRATOR_SESSION = "ses-live-fixes-orchestrator"
COMMAND = "git push origin feat/live"
LONG_COMMAND = (
    "python3 /home/jorge/ws/me/.project-worktrees/gaia/0ac7481a9c2e4f6b8d0a1c3e5f7b9d2e/bin/gaia"
    " dev --workspace /home/jorge/ws/me --ref bbc2f09 --host all"
)


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A scratch substrate, the agent's own directory and another directory to seal."""
    from modules.core.paths import clear_path_cache

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
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
    return {"db": data_dir / "gaia.db", "repo": str(repo), "other": str(other)}


def _request(cwd, *, command=COMMAND, question="¿Publico la rama?", what="Publicar la rama."):
    from gaia.approvals import core

    return core.request_command_set(
        [{"command": command, "cwd": cwd, "does": "Sube la rama al remoto.",
          "impact": "La rama queda publicada."}],
        what=what, question=question, session_id=SESSION, agent_id=AGENT,
        rollback="Borrar la rama remota.",
    )


def _rendered(approval_id):
    from gaia.approvals import store, surface

    return surface.render(json.loads(store.get_by_id(approval_id)["payload_json"]), approval_id)


def _events(approval_id, event_type=None):
    from gaia.approvals.store import get_history

    return [
        event for event in get_history(approval_id)
        if event_type is None or event["event_type"] == event_type
    ]


def _question_cli(*approval_ids, details=False):
    from bin.cli.approvals import cmd_question

    out = io.StringIO()
    with redirect_stdout(out):
        code = cmd_question(argparse.Namespace(
            approval_ids=list(approval_ids), details=details, json=True,
        ))
    assert code == 0, out.getvalue()
    return json.loads(out.getvalue().strip().splitlines()[-1])["questions"]


def _ask_event(event_name, questions, tool_use_id, answers=None):
    from adapters.types import HookEvent, HookEventType

    payload = {
        "hook_event_name": event_name, "tool_name": "AskUserQuestion",
        "session_id": ORCHESTRATOR_SESSION, "tool_use_id": tool_use_id,
        "tool_input": {"questions": questions},
    }
    if answers is not None:
        payload["tool_response"] = {"questions": questions, "answers": answers}
    kind = HookEventType.PRE_TOOL_USE if event_name == "PreToolUse" else HookEventType.POST_TOOL_USE
    return HookEvent(event_type=kind, session_id=ORCHESTRATOR_SESSION, payload=payload)


def _ask_pre(questions, tool_use_id):
    from adapters.claude_code import ClaudeCodeAdapter

    return ClaudeCodeAdapter().adapt_pre_tool_use(
        _ask_event("PreToolUse", questions, tool_use_id)
    ).output


def _ask_post(questions, answers, tool_use_id):
    from adapters.claude_code import ClaudeCodeAdapter

    return ClaudeCodeAdapter().adapt_post_tool_use(
        _ask_event("PostToolUse", questions, tool_use_id, answers)
    ).output


def _denied(output) -> bool:
    return output.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


# --------------------------------------------------------------------------- #
# (1) Claude Code: the signature inside the question, exact commands
# --------------------------------------------------------------------------- #

def test_approval_live_fixes_question_asks_only_the_short_question_and_the_hook_shows_the_block(host):
    approval_id = _request(host["repo"])
    rendered = _rendered(approval_id)

    [question] = _question_cli(approval_id)
    specific = _ask_pre([question], "toolu_block")["hookSpecificOutput"]

    assert question["question"] == "¿Publico la rama?"
    assert specific["permissionDecision"] == "allow"
    assert specific["permissionDecisionReason"] == rendered.block
    assert rendered.block.startswith("```\nSolicitud de aprobación · gaia-system\n")


def test_approval_live_fixes_pre_accepts_only_the_printed_object_byte_for_byte(host):
    approval_id = _request(host["repo"])
    rendered = _rendered(approval_id)
    [question] = _question_cli(approval_id)

    text_inside = {**question, "question": rendered.text + "\n\n¿Publico la rama?"}
    one_byte_off = {**question, "question": question["question"].replace("¿", "", 1)}
    for impostor in (text_inside, one_byte_off):
        assert _denied(_ask_pre([impostor], "toolu_impostor")), impostor["question"]
    assert _events(approval_id, "SHOWN") == []

    output = _ask_pre([question], "toolu_exact")
    assert not _denied(output)
    assert len(_events(approval_id, "SHOWN")) == 1


def test_approval_live_fixes_no_hook_response_uses_system_message(host):
    first, second = _request(host["repo"]), _request(
        host["repo"], command="git push origin feat/otra", question="¿Publico la otra rama?",
    )
    questions = _question_cli(first, second)

    outputs = [
        _ask_pre([{**questions[0], "question": "¿Publico la rama?"}], "toolu_denied"),
        _ask_pre(questions, "toolu_batch"),
        _ask_post(questions, {
            questions[0]["question"]: "Details", questions[1]["question"]: "Approve",
        }, "toolu_batch"),
    ]

    for output in outputs:
        assert "systemMessage" not in json.dumps(output), output


def test_approval_live_fixes_details_block_comes_back_with_the_reasked_question(host):
    approval_id = _request(host["repo"])
    rendered = _rendered(approval_id)
    [question] = _question_cli(approval_id)
    _ask_pre([question], "toolu_first")

    output = _ask_post([question], {question["question"]: "Details"}, "toolu_first")

    context = output["hookSpecificOutput"]["additionalContext"]
    assert f"gaia approvals question --details {approval_id}" in context
    [again] = _question_cli(approval_id, details=True)
    assert again == {**question, "header": "Detalles"}
    specific = _ask_pre([again], "toolu_details")["hookSpecificOutput"]
    assert specific["permissionDecision"] == "allow"
    assert specific["permissionDecisionReason"] == rendered.details_block
    _ask_post([again], {again["question"]: "Approve"}, "toolu_details")
    assert [event["event_type"] for event in _events(approval_id)].count("APPROVED") == 1


def test_approval_live_fixes_renderer_shows_every_command_exactly(host):
    approval_id = _request(host["repo"], command=LONG_COMMAND)

    rendered = _rendered(approval_id)

    assert "..." not in rendered.text
    for token in shlex.split(LONG_COMMAND):
        assert token in rendered.text, token


def test_approval_live_fixes_question_is_the_short_question_within_its_limit(host):
    from gaia.approvals import surface

    approval_id = _request(host["repo"], question="x" * 60, what="y" * 120)
    rendered = _rendered(approval_id)

    assert rendered.question["question"] == "x" * 60
    surface.check_question(rendered.question)
    with pytest.raises(surface.SurfaceLimitError, match="60"):
        _request(host["repo"], question="x" * 61)


# --------------------------------------------------------------------------- #
# (2) An item sealed in another directory runs as 'cd <dir> && <command>'
# --------------------------------------------------------------------------- #

def _claude():
    from adapters.claude_code import ClaudeCodeAdapter

    return ClaudeCodeAdapter()


def _bash_event(adapter, event_name, command, tool_use_id, cwd, **fields):
    payload = {
        "session_id": SESSION, "transcript_path": "/nonexistent/transcript.jsonl",
        "cwd": cwd, "permission_mode": "acceptEdits", "hook_event_name": event_name,
        "agent_id": AGENT_ID, "agent_type": AGENT, "tool_name": "Bash",
        "tool_input": {"command": command}, "tool_use_id": tool_use_id, **fields,
    }
    return adapter.parse_event(json.dumps(payload))


def _bash_pre(adapter, command, tool_use_id, cwd):
    """Run PreToolUse; return the hook output and whether the call may run."""
    output = adapter.adapt_pre_tool_use(
        _bash_event(adapter, "PreToolUse", command, tool_use_id, cwd)
    ).output
    decision = output.get("hookSpecificOutput", {}) if isinstance(output, dict) else {}
    allowed = isinstance(output, dict) and decision.get("permissionDecision", "allow") == "allow"
    return output, allowed


def _approve(approval_id):
    from gaia.approvals import core

    core.record_presentation(
        approval_id, native_ref="toolu_question", session_id=ORCHESTRATOR_SESSION,
        agent_id="gaia-orchestrator",
    )
    assert core.decide(
        native_ref="toolu_question", option_key="approve", session_id=ORCHESTRATOR_SESSION,
    ).status == "activated"


def _cd_form(directory, command=COMMAND):
    return f"cd {shlex.quote(directory)} && {command}"


def test_approval_live_fixes_claude_runs_an_item_sealed_elsewhere_as_the_cd_form(host):
    adapter = _claude()
    approval_id = _request(host["other"])
    invocation = _cd_form(host["other"])

    output, allowed = _bash_pre(adapter, invocation, "toolu_before", host["repo"])
    assert not allowed and approval_id in json.dumps(output), output

    _approve(approval_id)
    _, allowed = _bash_pre(adapter, invocation, "toolu_run", host["repo"])
    assert allowed
    adapter.adapt_post_tool_use(_bash_event(
        adapter, "PostToolUse", invocation, "toolu_run", host["repo"], duration_ms=40,
        tool_response={"stdout": "", "stderr": "", "interrupted": False, "isImage": False},
    ))

    [executed] = _events(approval_id, "EXECUTED")
    assert json.loads(executed["payload_json"])["command"] == COMMAND


CLOUD_COMMAND = "kubectl apply -f deploy.yaml"


def _variants(other: str, command: str) -> dict:
    """Spellings that name the sealed directory and command but are not the exact sealed form."""
    parent, name = str(Path(other).parent), Path(other).name
    return {
        "another command": _cd_form(other, "git push origin feat/otra"),
        "semicolon": f"cd {shlex.quote(other)}; {command}",
        "longer chain": _cd_form(other, command) + " && true",
        "double quotes": f'cd "{other}" && {command}',
        "trailing slash": f"cd {shlex.quote(other + '/')} && {command}",
        "doubled space after cd": f"cd  {shlex.quote(other)} && {command}",
        "doubled space before the command": f"cd {shlex.quote(other)} &&  {command}",
        "cd --": f"cd -- {shlex.quote(other)} && {command}",
        "relative path": f"cd {shlex.quote(name)} && {command}",
        "command substitution": f"cd $(printf %s {shlex.quote(other)}) && {command}",
        "dot-dot": f"cd {shlex.quote(f'{parent}/{name}/../{name}')} && {command}",
    }


def _grant_untouched(db, approval_id):
    con = sqlite3.connect(db)
    try:
        row = con.execute(
            "SELECT next_index, reservation_tool_use_id FROM approval_grants WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
    finally:
        con.close()
    return row == (0, None)


def test_approval_live_fixes_claude_runs_a_cloud_command_sealed_elsewhere_as_the_cd_form(host):
    """The chain rule for cloud commands yields only to the exact sealed form."""
    adapter = _claude()
    approval_id = _request(host["other"], command=CLOUD_COMMAND)
    invocation = _cd_form(host["other"], CLOUD_COMMAND)

    output, allowed = _bash_pre(adapter, invocation, "toolu_cloud_before", host["repo"])
    assert not allowed and approval_id in json.dumps(output), output

    _approve(approval_id)
    _, allowed = _bash_pre(adapter, invocation, "toolu_cloud_run", host["repo"])
    assert allowed
    adapter.adapt_post_tool_use(_bash_event(
        adapter, "PostToolUse", invocation, "toolu_cloud_run", host["repo"], duration_ms=40,
        tool_response={"stdout": "", "stderr": "", "interrupted": False, "isImage": False},
    ))
    [executed] = _events(approval_id, "EXECUTED")
    assert json.loads(executed["payload_json"])["command"] == CLOUD_COMMAND

    _, allowed = _bash_pre(adapter, _cd_form(host["other"], CLOUD_COMMAND) + " | tee x", "toolu_pipe", host["repo"])
    assert not allowed


def test_approval_live_fixes_sealed_form_keeps_every_single_command_check(host):
    """The sealed command is checked as if run alone in its folder: a permanently blocked one stays blocked."""
    from modules.tools.bash_validator import BashValidator

    blocked = "kubectl delete namespace kube-system"
    payload = core_seal_directly(host["other"], blocked)
    result = BashValidator().validate(
        _cd_form(host["other"], blocked), is_subagent=True, session_id=SESSION, agent_type=AGENT,
        hook_payload={"cwd": host["repo"], "tool_use_id": "toolu_blocked", "session_id": SESSION},
    )
    assert payload and not result.allowed


def core_seal_directly(cwd, command):
    """Persist a phrased pending request without the request-set T3 check, as a stale or forged row would be."""
    from gaia.approvals import core, store

    payload = core.seal_request(
        "command_set", [{"command": command, "cwd": cwd, "does": "Borra.", "impact": "Se pierde."}],
        what="Borrar.", question="¿Borro?", session_id=SESSION, agent_id=AGENT,
    )
    return store.insert_requested(payload, agent_id=AGENT, session_id=SESSION)


@pytest.mark.parametrize("variant", sorted(_variants("/x/sealed dir", COMMAND)) + [
    "another directory", "no cd", "another requester",
])
def test_approval_live_fixes_claude_matches_only_the_sealed_directory_and_command(host, variant):
    from gaia.approvals import core

    adapter = _claude()
    approval_id = _request(host["other"])
    _approve(approval_id)
    invocation = {
        **_variants(host["other"], COMMAND),
        "another directory": _cd_form(host["repo"]),
        "no cd": COMMAND,
        "another requester": _cd_form(host["other"]),
    }[variant]
    if variant == "another requester":
        assert core.sealed_elsewhere(invocation, session_id=SESSION, agent_id="developer") is None
        event = _bash_event(
            adapter, "PreToolUse", invocation, "toolu_other_agent", host["repo"], agent_type="developer",
        )
        output = adapter.adapt_pre_tool_use(event).output
        allowed = output.get("hookSpecificOutput", {}).get("permissionDecision", "allow") == "allow"
    else:
        assert variant in ("another directory", "no cd") or core.sealed_elsewhere(
            invocation, session_id=SESSION, agent_id=AGENT) is None
        _, allowed = _bash_pre(adapter, invocation, f"toolu_{variant}", host["repo"])

    assert not allowed
    assert _grant_untouched(host["db"], approval_id)


@pytest.fixture()
def driven_env(tmp_path, monkeypatch, bootstrapped_db_template):
    from modules.core.paths import clear_path_cache
    from tests.integration.test_opencode_consent_retry_e2e import _isolated_env

    env, db_path = _isolated_env(tmp_path / "state", bootstrapped_db_template)
    monkeypatch.chdir(env["WORKSPACE"])
    for key in ("GAIA_DB", "GAIA_DATA_DIR", "GAIA_OPENCODE_ATTESTATION_DIR"):
        monkeypatch.setenv(key, env[key])
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    clear_path_cache()
    return env, db_path


def _request_set_cli(env, cwd, command):
    from tests.integration import test_opencode_consent_retry_e2e as e2e

    argv = [
        sys.executable, str(_ROOT / "bin" / "gaia"), "approvals", "request-set",
        "--command", command, "--cwd", cwd,
        "--does", "Publica la rama.", "--impact", "Queda visible.",
        "--what", "Publicar la rama.", "--question", "¿Publico la rama?",
        "--rollback", "Borrar la rama remota.",
        "--agent-id", e2e.AGENT_ID, "--session-id", e2e.SESSION_ID, "--json",
    ]
    return subprocess.run(
        argv, cwd=env["WORKSPACE"], env=env, capture_output=True, text=True, timeout=180,
    )


def test_approval_live_fixes_opencode_runs_an_item_sealed_elsewhere_as_the_cd_form(driven_env):
    from tests.integration import test_opencode_consent_retry_e2e as e2e

    env, db_path = driven_env
    other = Path(env["WORKSPACE"]).parent / "sealed-elsewhere"
    other.mkdir()
    requested = _request_set_cli(env, str(other), e2e.FIRST_COMMAND)
    assert requested.returncode == 0, requested.stdout + requested.stderr
    approval_id = json.loads(requested.stdout.strip().splitlines()[-1])["approval_id"]
    invocation = _cd_form(str(other), e2e.FIRST_COMMAND)

    driven = e2e._drive(env, [
        e2e._before("blocked", invocation),
        {"kind": "control-decision", "label": "approve", "answer": "approve",
         "requestID": "question-elsewhere"},
        e2e._before("retry", invocation, call_id=e2e.RETRY_CALL_ID),
        {"kind": "after", "label": "settle", "sessionID": e2e.SESSION_ID,
         "callID": e2e.RETRY_CALL_ID, "tool": "bash", "command": invocation,
         "metadata": {"exitCode": 0}},
    ])

    assert driven["presentations"][0]["approvalID"] == approval_id, driven["presentations"]
    assert e2e._step(driven, "retry")["allowed"] is True, driven["steps"]
    con = sqlite3.connect(db_path)
    try:
        events = con.execute(
            "SELECT event_type, payload_json FROM approval_events WHERE approval_id = ? ORDER BY id",
            (approval_id,),
        ).fetchall()
    finally:
        con.close()
    executed = [json.loads(payload)["command"] for kind, payload in events if kind == "EXECUTED"]
    assert executed == [e2e.FIRST_COMMAND]
    assert [kind for kind, _ in events].count("SHOWN") == 1, events


def test_approval_live_fixes_opencode_runs_a_cloud_command_sealed_elsewhere_and_no_variant(driven_env):
    """OpenCode receives the directory as workdir and the command from the plugin: same verdicts as the core."""
    from tests.integration import test_opencode_consent_retry_e2e as e2e

    env, db_path = driven_env
    other = Path(env["WORKSPACE"]).parent / "sealed cloud"
    other.mkdir()
    requested = _request_set_cli(env, str(other), CLOUD_COMMAND)
    assert requested.returncode == 0, requested.stdout + requested.stderr
    approval_id = json.loads(requested.stdout.strip().splitlines()[-1])["approval_id"]
    invocation = _cd_form(str(other), CLOUD_COMMAND)
    variants = _variants(str(other), CLOUD_COMMAND)

    driven = e2e._drive(env, [
        e2e._before("blocked", invocation),
        {"kind": "control-decision", "label": "approve", "answer": "approve",
         "requestID": "question-cloud"},
        *[
            e2e._before(f"variant {name}", text, call_id=f"call-variant-{index}")
            for index, (name, text) in enumerate(sorted(variants.items()))
        ],
        e2e._before("retry", invocation, call_id=e2e.RETRY_CALL_ID),
        {"kind": "after", "label": "settle", "sessionID": e2e.SESSION_ID,
         "callID": e2e.RETRY_CALL_ID, "tool": "bash", "command": invocation,
         "metadata": {"exitCode": 0}},
    ])

    assert driven["presentations"][0]["approvalID"] == approval_id, driven["presentations"]
    refused_variants = [name for name in variants if e2e._step(driven, f"variant {name}")["allowed"] is False]
    assert sorted(refused_variants) == sorted(variants), driven["steps"]
    assert e2e._step(driven, "retry")["allowed"] is True, driven["steps"]
    con = sqlite3.connect(db_path)
    try:
        executed = [
            json.loads(payload)["command"]
            for (payload,) in con.execute(
                "SELECT payload_json FROM approval_events WHERE approval_id = ? "
                "AND event_type = 'EXECUTED' ORDER BY id", (approval_id,),
            )
        ]
    finally:
        con.close()
    assert executed == [CLOUD_COMMAND]


def test_approval_live_fixes_request_set_refuses_a_directory_that_does_not_exist(host):
    from bin.cli.approvals import cmd_request_set

    args = argparse.Namespace(
        command=[COMMAND], cwd=[str(Path(host["other"]) / "missing")], expect_exit=None,
        what="Publicar la rama.", question="¿Publico la rama?",
        does=["Sube la rama al remoto."], impact=["La rama queda publicada."],
        rationale=None, verification=None, rollback="Borrar la rama remota.",
        agent_id=AGENT, session_id=SESSION, json=True,
    )
    out = io.StringIO()
    with redirect_stdout(out):
        code = cmd_request_set(args)

    assert code == 1
    assert "is not an existing directory" in out.getvalue(), out.getvalue()
    con = sqlite3.connect(host["db"])
    try:
        assert con.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# (3) One presentation, one SHOWN
# --------------------------------------------------------------------------- #

def test_approval_live_fixes_one_presentation_records_one_shown(host):
    approval_id = _request(host["repo"])
    [question] = _question_cli(approval_id)

    _ask_pre([question], "toolu_once")
    _ask_post([question], {question["question"]: "Approve"}, "toolu_once")

    kinds = [event["event_type"] for event in _events(approval_id)]
    assert kinds.count("SHOWN") == 1, kinds
    assert kinds.count("APPROVED") == 1, kinds


# --------------------------------------------------------------------------- #
# (4) The orchestrator withdraws with 'revoke --reason'
# --------------------------------------------------------------------------- #

APPROVAL_ID = "P-" + "b" * 32


@pytest.mark.parametrize("candidate", [
    ("approvals", "revoke", APPROVAL_ID, "--reason", "la prueba terminó"),
    ("approvals", "revoke", APPROVAL_ID, "--reason=la prueba terminó", "--yes"),
])
def test_approval_live_fixes_guard_admits_revoke_with_a_reason(candidate):
    from modules.security.gaia_cli_only_guard import _validate_orchestrator_write

    assert _validate_orchestrator_write(candidate, ("approvals", "revoke")) is None


@pytest.mark.parametrize("candidate, phrase", [
    (("approvals", "reject", "--all"), ("approvals", "reject")),
    (("approvals", "reject", "--all", "--reason", "barrido"), ("approvals", "reject")),
    (("approvals", "revoke", APPROVAL_ID, "P-" + "c" * 32), ("approvals", "revoke")),
])
def test_approval_live_fixes_guard_still_denies_the_sweep(candidate, phrase):
    from modules.security.gaia_cli_only_guard import _validate_orchestrator_write

    assert _validate_orchestrator_write(candidate, phrase) is not None


def test_approval_live_fixes_revoke_records_its_reason(host):
    from bin.cli.approvals import cmd_revoke

    approval_id = _request(host["repo"])
    out = io.StringIO()
    with redirect_stdout(out):
        code = cmd_revoke(argparse.Namespace(
            approval_id=approval_id, yes=True, reason="la prueba terminó", json=False,
            agent_id=None, session_id=None,
        ))

    assert code == 0, out.getvalue()
    [revoked] = _events(approval_id, "REVOKED")
    assert json.loads(revoked["metadata_json"])["reason"] == "la prueba terminó"
