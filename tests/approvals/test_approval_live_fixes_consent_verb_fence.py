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

BINDING = f"{APPROVAL_ID} --session-id {SESSION_ID} --call-id call-self --token self --reply once"

# Every spelling bash turns into the same argv, as the verifier listed them
# (evidence 244), plus the plain forms.
SHELL_SPELLINGS = {
    "empty-quotes": f"gaia approvals opencode-dec''ide {BINDING}",
    "empty-double-quotes": f'gaia approvals opencode-dec""ide {BINDING}',
    "backslash": f"gaia approvals opencode-dec\\ide {BINDING}",
    "partial-quoting": f"gaia approvals 'opencode'-decide {BINDING}",
    "ansi-c": f"gaia approvals $'opencode-decide' {BINDING}",
    "ansi-c-hex": f"gaia approvals $'opencode-\\x64ecide' {BINDING}",
    "line-continuation": f"gaia approvals \\\nopencode-decide {BINDING}",
    "brace-expansion": f"gaia approvals {{opencode-decide,}} {BINDING}",
    "xargs-verb": f"echo opencode-decide {BINDING} | xargs gaia approvals",
    "xargs-placeholder": f"printf %s opencode-decide | xargs -I{{}} gaia approvals {{}} {BINDING}",
    "variable-suffix": f"S=decide; gaia approvals opencode-$S {BINDING}",
    "braced-variable-suffix": f"S=decide; gaia approvals opencode-${{S}} {BINDING}",
    "bash-c-empty-quotes": f"bash -c \"gaia approvals opencode-dec''ide {BINDING}\"",
    "sh-c-variable": f"sh -c 'S=decide; gaia approvals opencode-$S {BINDING}'",
    "bash-c-after-option": f"bash -o pipefail -c 'gaia approvals opencode-\"decide\" {BINDING}'",
    "piped-into-bash": f"echo 'gaia approvals opencode-decide {BINDING}' | bash",
    "eval": f"eval gaia approvals opencode-dec''ide {BINDING}",
    "command-substitution": f"echo $(gaia approvals opencode-decide {BINDING})",
    "variable-program": f"G=gaia; $G approvals opencode-decide {BINDING}",
}

MODEL_SPELLINGS = [
    PRESENT,
    DECIDE,
    f"python3 {GAIA_CLI} approvals opencode-decide {APPROVAL_ID} --session-id s --call-id c --token t --reply once",
    f"cd /tmp && {DECIDE}",
    f"bash -c '{PRESENT}'",
    "python3 -c \"import subprocess; subprocess.run(['gaia', 'approvals', 'opencode-decide', 'P-x'])\"",
    "python3 -c \"import subprocess; subprocess.run(['gaia', 'approvals', 'opencode-' + 'decide', 'P-x'])\"",
    *SHELL_SPELLINGS.values(),
]

PLAIN_READS = [
    f"grep -rn opencode-decide {GAIA_CLI.parent}",
    f'grep -rn "approvals opencode-decide" {GAIA_CLI.parent}',
    f"rg 'gaia approvals opencode-present' {GAIA_CLI.parent}",
    "git log -S opencode-decide --oneline",
    f"cat {GAIA_CLI.parent}/cli/approvals.py",
    f"gaia approvals show {APPROVAL_ID} --json",
    f"gaia approvals question {APPROVAL_ID}",
    "gaia memory search 'approvals opencode-decide'",
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
    from modules.security.host_consent_verb_guard import REJECTION_MESSAGE

    verdict = _claude_code_verdict(command, payload)

    assert verdict.allowed is False, verdict.reason
    if payload is CLAUDE_CODE_SUBAGENT:
        # The orchestrator's own lane refuses first with its own words; a
        # subagent reaches this guard, whose refusal carries nothing to sign.
        assert verdict.reason == REJECTION_MESSAGE, verdict.reason
    assert verdict.block_response is None, "a categorical refusal carries no approval to sign"


@pytest.mark.parametrize("command", PLAIN_READS)
def test_approval_live_fixes_claude_code_leaves_reads_of_the_verbs_alone(db_env, command):
    verdict = _claude_code_verdict(command, CLAUDE_CODE_SUBAGENT)

    assert verdict.allowed is True, verdict.reason


def _nested_at_the_descent_bound(body):
    from modules.security.shell_substitution import _MAX_NESTING_DEPTH

    return "echo $(" * _MAX_NESTING_DEPTH + body + ")" * _MAX_NESTING_DEPTH


def test_approval_live_fixes_the_fence_reads_as_deep_as_the_validator(db_env):
    """At the validator's own descent bound the fence judges the words: a read is free, the verb is not."""
    from modules.security.host_consent_verb_guard import REJECTION_MESSAGE

    read = _claude_code_verdict(_nested_at_the_descent_bound("pwd"), CLAUDE_CODE_SUBAGENT)
    hidden = _claude_code_verdict(
        _nested_at_the_descent_bound(f"gaia approvals opencode-dec''ide {BINDING}"), CLAUDE_CODE_SUBAGENT,
    )

    assert read.allowed is True, read.reason
    assert hidden.allowed is False
    assert hidden.reason == REJECTION_MESSAGE, hidden.reason


@pytest.mark.parametrize(
    ("session_id", "command"),
    [(ROOT_SESSION_ID, PRESENT), (ROOT_SESSION_ID, DECIDE), (SESSION_ID, PRESENT), (SESSION_ID, DECIDE)]
    + [(SESSION_ID, spelling) for spelling in SHELL_SPELLINGS.values()],
    ids=["orchestrator-present", "orchestrator-decide", "specialist-present", "specialist-decide"]
    + [f"specialist-{name}" for name in SHELL_SPELLINGS],
)
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
