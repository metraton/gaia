"""``user_facing_summary`` is relayed from the row the gate treated as
authoritative -- never from the fence, and never for a turn that did not close.

``parse_user_facing_summary`` existed twice, was exported, and was covered by
tests, but had NO production caller: the field a subagent wrote for the user
was parsed by nothing and reached nobody. This wires it into
``adapt_subagent_stop``, off the same ``_authoritative_envelope`` the contract
gate and nonce preservation already read, and delivers it on ``systemMessage``
-- the one SubagentStop stdout channel that reaches the orchestrator without
resuming the subagent whose turn is being closed.

These tests drive the REAL production entry point,
``ClaudeCodeAdapter.adapt_subagent_stop``, exactly as Claude Code drives it (a
JSON SubagentStop payload through ``parse_event``) against a real isolated
SQLite substrate. Nothing is stubbed; the assertions read the hook's own
returned output. Fixtures mirror
tests/hooks/test_subagent_stop_nonce_preservation.py, which exercises the same
local for a different purpose.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = str(Path(__file__).resolve().parents[2] / "hooks")
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
for _p in (_HOOKS_DIR, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from gaia.store.writer import (  # noqa: E402
    finalize_agent_contract_handoff,
    insert_dispatched_handoff,
    mirror_partial_contract_handoff,
    stamp_harness_agent_id,
)
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

WORKSPACE = "me"
AGENT_ID = valid_agent_id("ufs-relay")
HARNESS_AGENT_ID = valid_agent_id("ufs-relay-harness")
SESSION_ID = "sess-ufs-relay"

SUMMARY = (
    "Cablee el parser del resumen y consolide la copia duplicada; "
    "reinicia Claude Code para levantar el build nuevo."
)

_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)


def _envelope(*, summary: str | None) -> dict:
    envelope = {
        "agent_status": {
            "agent_state": "COMPLETE",
            "agent_id": AGENT_ID,
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": {
            **{k: [] for k in _EVIDENCE_KEYS},
            "verification": {
                "type": "command",
                "command": "pytest -q tests/hooks",
                "method": "ran the touched slice",
                "result": "pass",
                "details": "green",
            },
        },
        "consolidation_report": None,
        "approval_request": None,
    }
    if summary is not None:
        envelope["user_facing_summary"] = summary
    return envelope


@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    monkeypatch.setenv("GAIA_WORKSPACE", WORKSPACE)
    monkeypatch.delenv("GAIA_CONTRACT_FULL_VERDICT_GATE", raising=False)
    yield


@pytest.fixture()
def default_db(tmp_path) -> Path:
    return tmp_path / "gaia_data" / "gaia.db"


def _birth_row(db_path: Path, *, contract_suffix: str) -> str:
    contract_id = f"{AGENT_ID}.{contract_suffix}"
    insert_dispatched_handoff(
        contract_id=contract_id,
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        session_id=SESSION_ID,
        db_path=db_path,
    )
    stamp_harness_agent_id(contract_id, HARNESS_AGENT_ID, db_path=db_path)
    return contract_id


def _finalize_row(db_path: Path, contract_id: str, envelope: dict) -> None:
    finalize_agent_contract_handoff(
        contract_id=contract_id,
        agent_id=envelope["agent_status"]["agent_id"],
        workspace=WORKSPACE,
        agent_state=envelope["agent_status"]["agent_state"],
        raw_handoff_json=json.dumps(envelope),
        session_id=SESSION_ID,
        db_path=db_path,
    )


def _run(agent_output: str = "") -> dict:
    adapter = ClaudeCodeAdapter()
    payload = {
        "hook_event_name": "SubagentStop",
        "session_id": SESSION_ID,
        "agent_type": "gaia-system",
        "agent_id": HARNESS_AGENT_ID,
        "agent_transcript_path": "",
        "last_assistant_message": agent_output,
        "stop_reason": "end_turn",
        "cwd": "/tmp",
    }
    response = adapter.adapt_subagent_stop(adapter.parse_event(json.dumps(payload)))
    return response.output


def _fenced(envelope: dict) -> str:
    return (
        "Listo.\n\n```agent_contract_handoff\n"
        + json.dumps(envelope)
        + "\n```\n"
    )


# ---------------------------------------------------------------------------
# THE CENTRAL CASE: the summary lives on a cleanly finalized row and the final
# message carries no fence at all -- the shape every turn takes once the fence
# is retired as a delivery channel. Fails on the pre-wiring code, which parsed
# the field nowhere and returned no such key.
# ---------------------------------------------------------------------------

def test_summary_on_finalized_row_is_relayed_with_no_fence(default_db):
    contract_id = _birth_row(default_db, contract_suffix="central")
    _finalize_row(default_db, contract_id, _envelope(summary=SUMMARY))

    output = _run(agent_output="")

    assert output.get("user_facing_summary") == SUMMARY
    assert SUMMARY in output.get("systemMessage", "")


# ---------------------------------------------------------------------------
# Advisory in both directions: a turn that writes no summary closes exactly as
# it did before -- no key, no message. If this fails, the relay has started
# manufacturing user-facing text for turns that wrote none.
# ---------------------------------------------------------------------------

def test_row_without_summary_relays_nothing(default_db):
    contract_id = _birth_row(default_db, contract_suffix="absent")
    _finalize_row(default_db, contract_id, _envelope(summary=None))

    output = _run(agent_output="")

    assert "user_facing_summary" not in output
    assert "systemMessage" not in output


def test_blank_summary_relays_nothing(default_db):
    contract_id = _birth_row(default_db, contract_suffix="blank")
    _finalize_row(default_db, contract_id, _envelope(summary="   "))

    output = _run(agent_output="")

    assert "user_facing_summary" not in output


# ---------------------------------------------------------------------------
# The row is the source, not the text. A summary that exists ONLY in a fence,
# with no reachable row, is not relayed -- the same inversion the gate and
# nonce preservation already made.
# ---------------------------------------------------------------------------

def test_fence_only_summary_is_not_relayed(default_db):
    output = _run(agent_output=_fenced(_envelope(summary=SUMMARY)))

    assert "user_facing_summary" not in output


# ---------------------------------------------------------------------------
# A turn the gate REJECTS is being sent back for repair, not closed. Relaying
# its summary would hand the user a report of work that did not close.
# ---------------------------------------------------------------------------

def test_rejected_turn_does_not_relay_its_summary(default_db):
    contract_id = _birth_row(default_db, contract_suffix="rejected")
    mirror_partial_contract_handoff(
        contract_id,
        json.dumps(_envelope(summary=SUMMARY)),
        db_path=default_db,
    )

    output = _run(agent_output="")

    assert output.get("contract_rejected") is True
    assert "user_facing_summary" not in output


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
