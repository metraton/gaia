"""A main-session T3 in OpenCode reaches a signature the orchestrator can present and use, end to end.

The OpenCode twin of tests/hooks/test_orchestrator_t3_signature_path.py. Every
host event runs through the real plugin under bun and the real policy bridge;
the request is sealed by the real CLI with the environment the plugin's
``shell.env`` gives the orchestrator's shell (``GAIA_HOST_SESSION_ID``, no
``GAIA_DISPATCH_AGENT``).
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys

import pytest

from tests.hooks.adapters.test_opencode_attestation_provenance import bare_gaia_on_path
from tests.integration import test_opencode_consent_retry_e2e as e2e

T3 = "gaia scan"
PHRASES = {
    "--what": "Rescan the workspace",
    "--question": "Rescan it now?",
    "--does": "Rescans the workspace projects.",
    "--impact": "Rewrites the project context rows.",
    "--rollback": "Run the scan again after fixing the source.",
    "--verification": "gaia context show",
    "--shared-state": "Yes: the project context rows.",
}


@pytest.fixture()
def db_env(tmp_path, monkeypatch, bootstrapped_db_template, bun):
    from modules.core.paths import clear_path_cache

    bare_gaia_on_path(tmp_path, monkeypatch)
    env, db_path = e2e._isolated_env(tmp_path / "state", bootstrapped_db_template)
    monkeypatch.chdir(env["WORKSPACE"])
    for key in ("GAIA_DB", "GAIA_DATA_DIR", "GAIA_OPENCODE_ATTESTATION_DIR"):
        monkeypatch.setenv(key, env[key])
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    clear_path_cache()
    return env, db_path


def _orchestrator(label, command, call_id):
    return {"kind": "before", "label": label, "sessionID": e2e.ROOT_SESSION_ID,
            "callID": call_id, "tool": "bash", "command": command}


def _phrased(request_line: str) -> list[str]:
    """The request line the deny printed, with each ``<...>`` placeholder replaced by a phrase."""
    words = shlex.split(request_line)
    for index, word in enumerate(words):
        if word.startswith("<"):
            words[index] = PHRASES[words[index - 1]]
    return words


def _orchestrator_gaia(env, args: list[str]) -> subprocess.CompletedProcess:
    shell = {key: value for key, value in env.items() if key != "GAIA_DISPATCH_AGENT"}
    shell["GAIA_HOST_SESSION_ID"] = e2e.ROOT_SESSION_ID
    return subprocess.run([sys.executable, str(e2e.GAIA_CLI), *args], cwd=env["WORKSPACE"], env=shell,
                          capture_output=True, text=True, timeout=180)


def test_a_main_session_t3_is_signed_through_the_orchestrator_question(db_env):
    env, db_path = db_env

    denied = e2e._step(e2e._drive(env, [_orchestrator("blocked", T3, "call-orch-t3")]), "blocked")
    assert denied["allowed"] is False, denied
    assert re.search(r"approval_id: P-[0-9a-f]+", denied["error"]), denied
    request_line = next(line.strip() for line in denied["error"].splitlines()
                        if line.strip().startswith("gaia approvals request-set"))
    assert "--agent-id gaia-orchestrator" in request_line

    words = _phrased(request_line)
    requested = _orchestrator_gaia(env, words[1:] + ["--json"])
    assert requested.returncode == 0, requested.stdout + requested.stderr
    approval_id = json.loads(requested.stdout.strip().splitlines()[-1])["approval_id"]

    driven = e2e._drive(env, [
        _orchestrator("request", shlex.join(words), "call-orch-request"),
        {"kind": "control-decision", "label": "approve", "approvalIDs": [approval_id], "answer": "approve"},
        _orchestrator("retry", T3, "call-orch-retry"),
        {"kind": "after", "label": "ran", "sessionID": e2e.ROOT_SESSION_ID, "callID": "call-orch-retry",
         "tool": "bash", "command": T3, "metadata": {"exitCode": 0}},
        _orchestrator("again", T3, "call-orch-again"),
    ])
    assert e2e._step(driven, "request")["allowed"] is True, e2e._step(driven, "request")
    assert e2e._step(driven, "approve")["allowed"] is True, e2e._step(driven, "approve")
    assert e2e._step(driven, "retry")["allowed"] is True, e2e._step(driven, "retry")
    assert e2e._step(driven, "again")["allowed"] is False, "the signature runs the command once"
    assert e2e._approval_status(db_path, approval_id) == "approved"
