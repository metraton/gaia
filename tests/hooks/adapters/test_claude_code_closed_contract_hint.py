"""A resumed Claude Code agent whose contract already closed is told so at SubagentStart.

Runs against a real SQLite file and the real writers; only the resume-mapping
cache file (which session resumed which agent) is stubbed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _p in (str(_REPO_ROOT / "hooks"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from gaia.store.writer import (  # noqa: E402
    finalize_agent_contract_handoff,
    insert_dispatched_handoff,
)
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

AGENT_ID = valid_agent_id("closed-hint")
_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    data_dir = tmp_path / "gaia_data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.setenv("GAIA_DB", str(data_dir / "gaia.db"))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    return data_dir / "gaia.db"


def _born(db: Path, contract_id: str) -> None:
    insert_dispatched_handoff(
        contract_id=contract_id, agent_id=AGENT_ID, workspace="me",
        session_id="sess-hint", kind="task_execution", agent_name="gaia-system",
        dispatch_prompt_id=f"prompt-{contract_id}", db_path=db,
    )


def _close(db: Path, contract_id: str) -> None:
    envelope = {
        "agent_status": {
            "agent_state": "COMPLETE", "agent_id": AGENT_ID,
            "pending_steps": [], "next_action": "done",
        },
        "evidence_report": {key: [] for key in _EVIDENCE_KEYS},
        "consolidation_report": None,
        "approval_request": None,
    }
    envelope["evidence_report"]["verification"] = {
        "method": "test", "result": "pass", "details": "closed",
    }
    finalize_agent_contract_handoff(
        contract_id=contract_id, agent_id=AGENT_ID, workspace="me",
        agent_state="COMPLETE", raw_handoff_json=json.dumps(envelope),
        session_id="sess-hint", db_path=db,
    )


def _resumed_hint(monkeypatch) -> str | None:
    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(adapter, "_read_resume_mapping", lambda session_id: AGENT_ID)
    return adapter._build_resume_draft_context("sess-hint")


def test_resumed_agent_on_a_closed_contract_is_told_its_id_and_that_set_continues_it(db, monkeypatch):
    closed_id = f"{AGENT_ID}.closed"
    _born(db, closed_id)
    _close(db, closed_id)

    hint = _resumed_hint(monkeypatch)

    assert hint is not None
    assert closed_id in hint
    assert f"gaia contract set --draft-id {closed_id}" in hint
    assert "continuation" in hint


def test_agent_without_any_contract_row_gets_no_hint(db, monkeypatch):
    assert _resumed_hint(monkeypatch) is None
