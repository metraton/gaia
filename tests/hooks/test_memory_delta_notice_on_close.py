"""A turn's proposed project facts are not announced on the SubagentStop close.

What the user feels: the close message on their display stays about their work.
The facts a specialist proposed reach the orchestrator through the dispatch
result (see test_memory_delta_notice.py); SubagentStop's ``systemMessage`` goes
to the user's display and its ``additionalContext`` would resume the closing
subagent, so it carries neither. Runs against a temporary GAIA_DATA_DIR.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT / "hooks"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from gaia.store.writer import stamp_harness_agent_id  # noqa: E402
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

SESSION_ID = "sess-memory-delta-notice"
HARNESS_AGENT_ID = valid_agent_id("memory-delta-notice-harness")
_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)
PROPOSAL = {
    "operation": "create",
    "create": {
        "slug": "atom_alpha_admin_url",
        "type": "atom",
        "description": "Fact: alpha admin lives at https://admin.alpha.test.",
        "body": "Measured 2026-10-20 from the ingress manifest.",
    },
}


@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.setenv("GAIA_WORKSPACE", "me")


def _cli(argv):
    env = dict(os.environ, PYTHONPATH=str(_REPO_ROOT))
    return subprocess.run([sys.executable, *argv], capture_output=True, text=True, env=env)


def _close_turn(db_path: Path, memory_delta):
    identity = ClaudeCodeAdapter._maybe_birth_dispatched_row(
        {"prompt": "find the admin url"}, "developer", SESSION_ID,
    )
    assert identity is not None
    stamp_harness_agent_id(identity["contract_id"], HARNESS_AGENT_ID, db_path=db_path)
    envelope = {
        "agent_status": {
            "agent_id": identity["agent_id"], "agent_state": "NEEDS_VERIFICATION",
            "pending_steps": [], "next_action": "done",
        },
        "user_facing_summary": "Work recorded for the test.",
        "evidence_report": dict(
            {k: [] for k in _EVIDENCE_KEYS},
            verification={"method": "test", "result": "pass", "details": "ok"},
        ),
        "memory_delta": memory_delta,
    }
    contract_cli = str(_REPO_ROOT / "bin" / "cli" / "contract.py")
    filled = _cli([contract_cli, "fill", "--draft-id", identity["contract_id"],
                   "--json", json.dumps(envelope)])
    assert filled.returncode == 0, filled.stderr
    finalized = _cli([contract_cli, "finalize", "--draft-id", identity["contract_id"],
                      "--session-id", SESSION_ID, "--json"])
    assert finalized.returncode == 0, finalized.stderr

    adapter = ClaudeCodeAdapter()
    event = adapter.parse_event(json.dumps({
        "hook_event_name": "SubagentStop",
        "session_id": SESSION_ID,
        "agent_type": "developer",
        "agent_id": HARNESS_AGENT_ID,
        "agent_transcript_path": "",
        "last_assistant_message": f"NEEDS_VERIFICATION\ncontract_id: {identity['contract_id']}",
        "stop_reason": "end_turn",
        "cwd": "/tmp",
    }))
    return adapter.adapt_subagent_stop(event)


@pytest.mark.parametrize(
    "memory_delta", [{"version": 1, "proposals": [PROPOSAL, PROPOSAL]}, None],
)
def test_the_close_message_never_announces_proposed_facts(tmp_path, memory_delta):
    response = _close_turn(tmp_path / "gaia-data" / "gaia.db", memory_delta)

    assert "project fact" not in (response.output.get("systemMessage") or ""), response.output
    assert "additionalContext" not in json.dumps(response.output.get("hookSpecificOutput") or {})
