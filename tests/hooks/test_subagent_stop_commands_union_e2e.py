"""End-to-end wiring of the commands_executed union through the real
production entry point, ``ClaudeCodeAdapter.adapt_subagent_stop``.

``tests/hooks/modules/agents/test_commands_executed_union.py`` exercises
``merge_commands_executed`` / ``extract_commands_executed`` directly, as
pure functions. This file proves the SAME union reaches ``commands_executed``
through the real hook, against a real, isolated SQLite database -- mirroring
tests/hooks/test_subagent_stop_nonce_preservation.py's fixtures, which pinned
the sibling case (nonce preservation) reading from the SAME
``_authoritative_envelope`` local this union now also reads.

No gate function or writer is stubbed; only
``modules.memory.episode_writer.write`` is patched, to observe the
``commands_executed`` argument the hook actually computed and passed to it --
the exact seam the fix under test writes its answer into (the same list also
reaches ``modules.audit.workflow_recorder.record``, from the identical local
variable).
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
    stamp_harness_agent_id,
)
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

WORKSPACE = "me"
AGENT_ID = valid_agent_id("commands-union-e2e")
HARNESS_AGENT_ID = valid_agent_id("commands-union-e2e-harness")
SESSION_ID = "sess-commands-union-e2e"

_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)


def _evidence(commands_run):
    evidence = {k: [] for k in _EVIDENCE_KEYS}
    evidence["commands_run"] = commands_run
    return evidence


def _complete_envelope(commands_run):
    return {
        "agent_status": {
            "agent_state": "COMPLETE",
            "agent_id": AGENT_ID,
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": {
            **_evidence(commands_run),
            "verification": {"method": "test", "result": "pass", "details": "green"},
        },
        "consolidation_report": None,
        "approval_request": None,
    }


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


def _subagent_stop_event(adapter: ClaudeCodeAdapter, *, agent_output: str):
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
    return adapter.parse_event(json.dumps(payload))


def _run_and_capture_commands_executed(monkeypatch, adapter, event):
    """Run the real hook and return the ``commands_executed`` argument
    ``adapt_subagent_stop`` actually computed and handed to episode writing --
    the exact seam the fix under test writes its answer into."""
    captured: dict = {}

    def _fake_write(metrics, anomalies=None, commands_executed=None, **_kw):
        captured["commands_executed"] = commands_executed
        return "ep-test"

    import modules.memory.episode_writer as episode_writer_module
    monkeypatch.setattr(episode_writer_module, "write", _fake_write)

    adapter.adapt_subagent_stop(event)
    return captured.get("commands_executed")


def _fence(envelope: dict) -> str:
    return "Report.\n\n```agent_contract_handoff\n" + json.dumps(envelope) + "\n```\n"


# ---------------------------------------------------------------------------
# Direction 1 (measured: 9/94 turns): the row was checkpointed with commands,
# the final message carries no fence at all.
# ---------------------------------------------------------------------------

def test_fence_missing_row_commands_registered(default_db, monkeypatch):
    row_commands = ["git status", "pytest tests/foo -q -> 3 passed"]
    contract_id = _birth_row(default_db, contract_suffix="fence-missing")
    stamp_harness_agent_id(contract_id, HARNESS_AGENT_ID, db_path=default_db)
    _finalize_row(default_db, contract_id, _complete_envelope(row_commands))

    adapter = ClaudeCodeAdapter()
    event = _subagent_stop_event(adapter, agent_output="")  # no fence whatsoever

    commands_executed = _run_and_capture_commands_executed(monkeypatch, adapter, event)

    assert commands_executed == row_commands, (
        "the row's checkpointed commands were dropped -- commands_executed "
        "read only the (absent) fence"
    )


# ---------------------------------------------------------------------------
# Direction 2 (measured: 13/94 turns): the row was never mirrored, the final
# message's fence carries the full sequence.
# ---------------------------------------------------------------------------

def test_row_empty_fence_commands_not_lost(default_db, monkeypatch):
    fence_commands = ["terraform plan -chdir=/abs/path -> no changes"]
    contract_id = _birth_row(default_db, contract_suffix="row-empty")
    stamp_harness_agent_id(contract_id, HARNESS_AGENT_ID, db_path=default_db)
    # Never finalized/mirrored: the row carries no evidence at all, so the
    # gate falls to GATE_SOURCE_ROW_UNFINALIZED / _authoritative_envelope=None
    # depending on row state -- either way, no row commands exist to union.

    adapter = ClaudeCodeAdapter()
    event = _subagent_stop_event(
        adapter, agent_output=_fence(_complete_envelope(fence_commands)),
    )

    commands_executed = _run_and_capture_commands_executed(monkeypatch, adapter, event)

    assert commands_executed == fence_commands, (
        "the fence's commands were dropped when the row carried none"
    )


# ---------------------------------------------------------------------------
# Normal case: the fence is the same envelope the row was finalized with --
# no duplication.
# ---------------------------------------------------------------------------

def test_identical_row_and_fence_do_not_duplicate(default_db, monkeypatch):
    commands = ["kubectl get hr -n qxo -> all reconciled"]
    envelope = _complete_envelope(commands)
    contract_id = _birth_row(default_db, contract_suffix="identical")
    stamp_harness_agent_id(contract_id, HARNESS_AGENT_ID, db_path=default_db)
    _finalize_row(default_db, contract_id, envelope)

    adapter = ClaudeCodeAdapter()
    event = _subagent_stop_event(adapter, agent_output=_fence(envelope))

    commands_executed = _run_and_capture_commands_executed(monkeypatch, adapter, event)

    assert commands_executed == commands
    assert len(commands_executed) == 1
