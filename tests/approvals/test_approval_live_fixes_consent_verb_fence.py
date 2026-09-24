"""Only the OpenCode plugin records a presentation or applies a decision.

``gaia approvals opencode-present`` records SHOWN and ``opencode-decide``
applies the user's reply, and the CLI can check no more than the requester's
session and agent: the call id and the token are whatever the caller passes,
and the plugin's spawn carries no secret a shell could not copy. So the
requester could present its own request to itself and approve it. The plugin
runs both verbs through its own process spawn, which no tool hook sees; every
model-issued shell call is refused, in both hosts and for every role.

WHAT IS NOT PROVEN: no live OpenCode or Claude Code host runs here. A spelling
the guard does not read (a verb assembled at run time, a script file that runs
it) is not refused by it; reaching the verbs that way is the elusion the
security-tiers skill forbids.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from tests.integration.test_opencode_consent_retry_e2e import (  # noqa: F401 (db_env is a fixture)
    AGENT_ID,
    GAIA_CLI,
    ROOT_SESSION_ID,
    SESSION_ID,
    _approval_status,
    _drive,
    _request_set,
    _step,
    db_env,
)

APPROVAL_ID = "P-0123456789abcdef0123456789abcdef"
PRESENT = (
    f"gaia approvals opencode-present {APPROVAL_ID} --session-id {SESSION_ID} "
    f"--agent-id {AGENT_ID} --call-id call-self --token self --json"
)
DECIDE = (
    f"gaia approvals opencode-decide {APPROVAL_ID} --session-id {SESSION_ID} "
    "--call-id call-self --token self --reply once --json"
)

MODEL_SPELLINGS = [
    PRESENT,
    DECIDE,
    f"python3 {GAIA_CLI} approvals opencode-decide {APPROVAL_ID} --session-id s --call-id c --token t --reply once",
    f"cd /tmp && {DECIDE}",
    f"bash -c '{PRESENT}'",
    "python3 -c \"import subprocess; subprocess.run(['gaia', 'approvals', 'opencode-decide', 'P-x'])\"",
]

CLAUDE_CODE_SUBAGENT = {
    "agent_id": "a1b2c3d4e5f60718", "agent_type": "developer",
    "session_id": "claude-session", "tool_name": "Bash",
}
CLAUDE_CODE_ORCHESTRATOR = {"session_id": "claude-session", "tool_name": "Bash"}


def _claude_code_verdict(command, payload):
    from modules.tools.bash_validator import BashValidator

    return BashValidator().validate(
        command,
        is_subagent=bool(payload.get("agent_id")),
        session_id=payload["session_id"],
        agent_type=payload.get("agent_type", ""),
        hook_payload={**payload, "tool_input": {"command": command}},
    )


def _run_cli(env, *args):
    result = subprocess.run(
        [sys.executable, str(GAIA_CLI), "approvals", *args, "--json"],
        cwd=env["WORKSPACE"], env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_approval_live_fixes_the_cli_alone_lets_a_requester_approve_itself(db_env):
    """Why the fence lives in the hook: the CLI cannot tell the plugin from its requester."""
    env, db_path = db_env
    approval_id = _request_set(env)
    binding = ["--session-id", SESSION_ID, "--call-id", "call-self", "--token", "chosen-by-the-caller"]

    _run_cli(env, "opencode-present", approval_id, *binding, "--agent-id", AGENT_ID)
    decided = _run_cli(env, "opencode-decide", approval_id, *binding, "--reply", "once")

    assert decided["status"] == "approved"
    assert _approval_status(db_path, approval_id) == "approved"


@pytest.mark.parametrize("command", MODEL_SPELLINGS)
@pytest.mark.parametrize(
    "payload", [CLAUDE_CODE_SUBAGENT, CLAUDE_CODE_ORCHESTRATOR], ids=["subagent", "orchestrator"],
)
def test_approval_live_fixes_claude_code_refuses_the_host_consent_verbs(db_env, command, payload):
    verdict = _claude_code_verdict(command, payload)

    assert verdict.allowed is False, verdict.reason
    assert verdict.block_response is None, "a categorical refusal carries no approval to sign"


@pytest.mark.parametrize("command", [
    f"grep -rn opencode-decide {GAIA_CLI.parent}",
    f"gaia approvals show {APPROVAL_ID} --json",
    f"gaia approvals question {APPROVAL_ID}",
])
def test_approval_live_fixes_claude_code_leaves_reads_of_the_verbs_alone(db_env, command):
    assert _claude_code_verdict(command, CLAUDE_CODE_SUBAGENT).allowed is True


@pytest.mark.parametrize("session_id", [SESSION_ID, ROOT_SESSION_ID], ids=["specialist", "orchestrator"])
@pytest.mark.parametrize("command", [PRESENT, DECIDE])
def test_approval_live_fixes_opencode_refuses_the_host_consent_verbs(db_env, session_id, command):
    env, db_path = db_env
    approval_id = _request_set(env)
    command = command.replace(APPROVAL_ID, approval_id)

    driven = _drive(env, [{
        "kind": "before", "label": "self", "sessionID": session_id,
        "callID": "call-self", "tool": "bash", "command": command,
    }])

    assert _step(driven, "self")["allowed"] is False, driven
    assert _approval_status(db_path, approval_id) == "pending"
