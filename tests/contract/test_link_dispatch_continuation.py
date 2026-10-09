"""A dispatch that resumes a harness session is chained to the row that session last held.

Runs against the real writers and a real SQLite file, no mocks: the chain is
what ``find_dispatch_row_by_harness_agent_id`` resolves a session through, so it
is asserted through that read.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT / "hooks"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gaia.store.writer import (  # noqa: E402
    finalize_agent_contract_handoff,
    find_dispatch_row_by_harness_agent_id,
    insert_dispatched_handoff,
    link_dispatch_continuation,
    stamp_harness_agent_id,
)
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

WORKSPACE = "me"
SESSION_ID = "sess-link"
HARNESS_SESSION = "ses_resumed_child"
_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)


@pytest.fixture(autouse=True)
def _clean_dispatch(monkeypatch):
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "gaia.db"


def _born(db: Path, name: str) -> tuple[str, str]:
    agent_id = valid_agent_id(f"link-{name}")
    contract_id = f"{agent_id}.{name}"
    insert_dispatched_handoff(
        contract_id=contract_id, agent_id=agent_id, workspace=WORKSPACE,
        session_id=SESSION_ID, kind="task_execution", agent_name="gaia-system",
        dispatch_prompt_id=f"prompt-{name}", db_path=db,
    )
    return contract_id, agent_id


def _close(db: Path, contract_id: str, agent_id: str) -> None:
    envelope = {
        "agent_status": {
            "agent_state": "COMPLETE", "agent_id": agent_id,
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
        contract_id=contract_id, agent_id=agent_id, workspace=WORKSPACE,
        agent_state="COMPLETE", raw_handoff_json=json.dumps(envelope),
        session_id=SESSION_ID, db_path=db,
    )


def _row(db: Path, contract_id: str) -> sqlite3.Row:
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT * FROM agent_contract_handoffs WHERE contract_id = ?", (contract_id,),
        ).fetchone()
    finally:
        con.close()


def test_resumed_session_resolves_to_the_new_linked_row(db):
    first_id, first_agent = _born(db, "first")
    stamp_harness_agent_id(first_id, HARNESS_SESSION, db_path=db)
    _close(db, first_id, first_agent)
    first = _row(db, first_id)

    second_id, _ = _born(db, "second")
    outcome = link_dispatch_continuation(
        second_id, continues_handoff_id=first["id"],
        harness_agent_id=HARNESS_SESSION, db_path=db,
    )

    assert outcome == {"status": "applied", "contract_id": second_id}
    second = _row(db, second_id)
    assert second["continues_handoff_id"] == first["id"]
    assert second["harness_agent_id"] == HARNESS_SESSION
    assert _row(db, first_id)["agent_state"] == first["agent_state"]
    resolved = find_dispatch_row_by_harness_agent_id(HARNESS_SESSION, db_path=db)
    assert resolved["contract_id"] == second_id


def test_an_already_linked_row_is_not_relinked(db):
    first_id, _ = _born(db, "first")
    second_id, _ = _born(db, "second")
    third_id, _ = _born(db, "third")
    first, third = _row(db, first_id), _row(db, third_id)
    link_dispatch_continuation(
        second_id, continues_handoff_id=first["id"],
        harness_agent_id=HARNESS_SESSION, db_path=db,
    )

    outcome = link_dispatch_continuation(
        second_id, continues_handoff_id=third["id"],
        harness_agent_id="ses_other", db_path=db,
    )

    assert outcome == {"status": "skipped", "reason": "not_linkable"}
    second = _row(db, second_id)
    assert second["continues_handoff_id"] == first["id"]
    assert second["harness_agent_id"] == HARNESS_SESSION


def test_a_closed_row_is_never_rewritten_by_a_link(db):
    first_id, _ = _born(db, "first")
    closed_id, closed_agent = _born(db, "closed")
    _close(db, closed_id, closed_agent)
    first = _row(db, first_id)

    outcome = link_dispatch_continuation(
        closed_id, continues_handoff_id=first["id"],
        harness_agent_id=HARNESS_SESSION, db_path=db,
    )

    assert outcome == {"status": "skipped", "reason": "not_linkable"}
    assert _row(db, closed_id)["continues_handoff_id"] is None
