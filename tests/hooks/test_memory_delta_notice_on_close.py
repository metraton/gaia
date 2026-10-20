"""A specialist's proposed project facts reach the orchestrator when the turn closes.

What the user feels: a fact a specialist found (a tool URL, a login method) is
not left sitting in a contract nobody opens -- the close names how many were
proposed and which contract holds them, so the orchestrator can curate them; a
turn that proposed nothing adds no line. Runs against a temporary GAIA_DATA_DIR.
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
from modules.agents.proposal_notices import append_notice, memory_delta_notice  # noqa: E402
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
    return identity["contract_id"], adapter.adapt_subagent_stop(event)


def test_a_turn_with_proposed_facts_names_the_count_and_the_contract(tmp_path):
    contract_id, response = _close_turn(
        tmp_path / "gaia-data" / "gaia.db",
        {"version": 1, "proposals": [PROPOSAL, PROPOSAL]},
    )

    message = response.output.get("systemMessage") or ""
    assert f"2 project facts proposed — contract {contract_id}" in message, response.output


@pytest.mark.parametrize("memory_delta", [None, {"version": 1, "proposals": []}])
def test_a_turn_with_no_proposed_facts_adds_no_line(tmp_path, memory_delta):
    _, response = _close_turn(tmp_path / "gaia-data" / "gaia.db", memory_delta)

    assert "project fact" not in (response.output.get("systemMessage") or ""), response.output
    assert "memory_delta_proposed" not in response.output


def test_the_notice_names_the_project_by_its_name_only():
    contract = {"memory_delta": {"version": 1, "proposals": [PROPOSAL]}}

    notice = memory_delta_notice(
        contract, contract_id="c1.abc", dispatch_project="alpha (/work/alpha)",
    )

    assert notice == "1 project fact proposed for alpha — contract c1.abc"
    assert append_notice("", notice) == notice
    assert append_notice("earlier", notice) == f"earlier\n{notice}"
