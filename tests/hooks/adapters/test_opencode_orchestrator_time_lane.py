"""In OpenCode, an attested orchestrator reads the time with `gaia now` and is pointed there from `date`."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parents[3] / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

from adapters.opencode import OpenCodeAdapter  # noqa: E402
from modules.orchestrator.delegate_mode import SessionRole, classify_session_role  # noqa: E402
from tests.hooks.adapters.test_opencode_attestation_provenance import (  # noqa: E402,F401
    _REPO,
    _emitted,
    _policy_payload,
    drive,
    ledger,
    path_without_host_gaia,
)


def _trusted_cli() -> str:
    manifest = json.loads((_REPO / "package.json").read_text(encoding="utf-8"))
    return str((_REPO / manifest["bin"]["gaia"]).resolve())


def _now_turns(drive):
    now = shlex.join([_trusted_cli(), "now"])
    return drive(
        {
            "steps": [
                {"kind": "message", "sessionID": "ses-root", "agent": "gaia-orchestrator"},
                {"kind": "before", "sessionID": "ses-root", "callID": "call-1",
                 "tool": "bash", "args": {"command": now}},
                {"kind": "before", "sessionID": "ses-root", "callID": "call-2",
                 "tool": "task", "args": {"subagent_type": "gaia-system", "prompt": "go"}},
                {"kind": "after-task", "sessionID": "ses-root", "callID": "call-2",
                 "args": {"subagent_type": "gaia-system", "prompt": "go"},
                 "childSessionID": "ses-child"},
                {"kind": "before", "sessionID": "ses-child", "callID": "call-3",
                 "tool": "bash", "args": {"command": now}},
            ],
        }
    )


def _verdict(emitted, command=None):
    if command is not None:
        emitted = dict(emitted, args={"command": command}, originalArgs={"command": command})
    adapter = OpenCodeAdapter()
    return adapter.adapt_pre_tool_use(adapter.parse_event(json.dumps(emitted))).output


def _root_bash(requests):
    return next(
        r for r in requests
        if r.get("event") == "tool.execute.before" and r.get("sessionID") == "ses-root"
        and str((r.get("args") or {}).get("command", "")).endswith(" now")
    )


def test_the_attested_orchestrator_runs_gaia_now_and_is_pointed_there_from_date(drive):
    requests = _now_turns(drive)
    root = _root_bash(requests)
    assert classify_session_role(_policy_payload(root)) is SessionRole.ORCHESTRATOR

    assert _verdict(root).get("action") != "deny"

    date = _verdict(root, "date")
    assert date["action"] == "deny"
    assert "gaia now" in date["reason"] and "--in" not in date["reason"]

    forge = _verdict(root, "gh pr checks 42")
    assert forge["action"] == "deny"
    assert "PR/CI state is a specialist's evidence" in forge["reason"]


def test_a_specialist_runs_gaia_now(drive):
    """Judged by the guard on the policy payload: the driver emits no message.part.updated,
    so the adapter's child-session binding backstop would deny any child tool call."""
    from modules.security import gaia_cli_only_guard as guard

    child = _emitted(_now_turns(drive), "tool.execute.before", "ses-child")
    payload = _policy_payload(child)
    assert classify_session_role(payload) is not SessionRole.ORCHESTRATOR

    allowed, reason = guard.check(child["args"]["command"], payload)
    assert allowed, reason
