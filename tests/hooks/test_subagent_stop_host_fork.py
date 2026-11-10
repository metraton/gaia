"""A host fork Gaia never dispatched ends with no contract gate and no agent-turn record."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_HOOKS_DIR), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from modules.core.paths import clear_path_cache  # noqa: E402

SESSION_ID = "sess-host-fork"
FORK_AGENT_ID = "afdb6fe7db3dfdcbd"
SUGGESTION = "dale, y asigna el 844 al tenant demo"


@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    monkeypatch.delenv("GAIA_CONTRACT_FULL_VERDICT_GATE", raising=False)
    monkeypatch.setattr(
        ClaudeCodeAdapter, "_get_gaia_agent_names",
        lambda self: {"gaia-orchestrator", "gaia-system"},
    )
    clear_path_cache()
    yield
    clear_path_cache()


def _stop(agent_type: str, agent_id: str = FORK_AGENT_ID):
    adapter = ClaudeCodeAdapter()
    payload = {
        "hook_event_name": "SubagentStop",
        "session_id": SESSION_ID,
        "agent_id": agent_id,
        "agent_type": agent_type,
        "agent_transcript_path": "",
        "last_assistant_message": SUGGESTION,
        "stop_reason": "end_turn",
        "cwd": "/tmp",
    }
    return adapter.adapt_subagent_stop(adapter.parse_event(json.dumps(payload)))


def _count(tmp_path, sql: str) -> int:
    db = tmp_path / "gaia_data" / "gaia.db"
    if not db.exists():
        return 0
    with sqlite3.connect(db) as conn:
        try:
            return conn.execute(sql).fetchone()[0]
        except sqlite3.OperationalError:
            return 0


def _events(tmp_path, event_type: str) -> int:
    return _count(tmp_path, f"SELECT count(*) FROM harness_events WHERE type = '{event_type}'")


def _rejected_turn_files() -> list:
    from gaia.paths import rejected_turns_dir

    directory = rejected_turns_dir()
    return list(directory.glob("*.txt")) if directory.exists() else []


@pytest.mark.parametrize("agent_type", ["gaia-orchestrator", "gaia:gaia-orchestrator"])
def test_an_undispatched_fork_of_the_main_thread_is_never_gated_or_counted(tmp_path, agent_type):
    responses = [_stop(agent_type) for _ in range(3)]

    assert [r.exit_code for r in responses] == [0, 0, 0]
    assert all(r.output.get("host_fork") is True for r in responses)
    assert _events(tmp_path, "agent.contract_rejected") == 0
    assert _events(tmp_path, "agent.contract_circuit_open") == 0
    assert _events(tmp_path, "agent.complete") == 0
    assert _count(tmp_path, "SELECT count(*) FROM episodes") == 0
    assert _count(tmp_path, "SELECT count(*) FROM agent_contract_handoffs") == 0
    assert _rejected_turn_files() == []
    assert _events(tmp_path, "agent.host_fork") == 3


def test_a_dispatched_turn_of_the_same_type_that_never_closed_is_still_rejected(tmp_path):
    from gaia.store.writer import insert_dispatched_handoff, stamp_harness_agent_id
    from tests.fixtures.agent_ids import valid_agent_id

    db = tmp_path / "gaia_data" / "gaia.db"
    claimed, unclaimed = valid_agent_id("fork-claimed"), valid_agent_id("fork-unclaimed")
    insert_dispatched_handoff(
        f"{claimed}.never-closed", claimed, "me", session_id=SESSION_ID,
        agent_name="gaia-orchestrator", db_path=db,
    )
    stamp_harness_agent_id(f"{claimed}.never-closed", "a1111111111111111", db_path=db)

    by_harness_id = _stop("gaia-orchestrator", agent_id="a1111111111111111")

    assert by_harness_id.exit_code == 2
    assert not by_harness_id.output.get("host_fork")

    insert_dispatched_handoff(
        f"{unclaimed}.never-claimed", unclaimed, "me", session_id=SESSION_ID,
        agent_name="gaia-orchestrator", db_path=db,
    )

    by_birth_name = _stop("gaia-orchestrator", agent_id="a2222222222222222")

    assert by_birth_name.exit_code == 2
    assert not by_birth_name.output.get("host_fork")
