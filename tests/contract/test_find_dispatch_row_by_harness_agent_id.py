"""plan 65 T11 -- the host-neutral harness_agent_id -> row join
``resolve_close`` reads to find the bound row a session lifecycle event names.

Host-neutral counterpart of ``hooks.modules.agents.handoff_persister.
dispatch_row_by_harness_id`` (Claude-Code-shaped ``task_info``): OpenCode's
session lifecycle events carry the harness_agent_id directly as their own
``sessionID``, so this function is keyed on it with no intermediate shape.

Named cases:
  (a) a single bound row resolves by its exact harness_agent_id;
  (b) no row at all -> None;
  (c) two UNRELATED rows sharing a harness_agent_id decline (None), never a
      recency guess;
  (d) a continuation chain sharing one harness_agent_id is NOT an ambiguity
      -- collapses to its live link.

Runs against a fresh DB; the writer's own ``_connect`` materializes the real
schema from ``gaia/store/schema.sql``.
"""

from __future__ import annotations

from gaia.store.writer import (
    bind_harness_child_session,
    claim_dispatch_row,
    find_dispatch_row_by_harness_agent_id,
    finalize_agent_contract_handoff,
    insert_dispatched_handoff,
    open_contract_continuation,
)
from tests.fixtures.agent_ids import valid_agent_id

WORKSPACE = "me"


def _birth_and_bind(db, token: str, harness_agent_id: str) -> str:
    agent_id = valid_agent_id(f"a{token}")
    result = insert_dispatched_handoff(
        f"{agent_id}.{token}cafe", agent_id, WORKSPACE,
        session_id=None, db_path=db,
        agent_name="gaia-system", kind="investigation",
        dispatch_tool_use_id=f"call-{token}",
    )
    contract_id = result["contract_id"]
    claim_dispatch_row(dispatch_tool_use_id=f"call-{token}", db_path=db)
    bind_harness_child_session(
        dispatch_tool_use_id=f"call-{token}", harness_agent_id=harness_agent_id,
        db_path=db,
    )
    return contract_id


def test_a_single_bound_row_resolves_by_its_exact_harness_agent_id(tmp_path):
    db = tmp_path / "gaia.db"
    contract_id = _birth_and_bind(db, "aaa111", "child-session-A")

    row = find_dispatch_row_by_harness_agent_id("child-session-A", db_path=db)

    assert row is not None
    assert row["contract_id"] == contract_id


def test_no_row_at_all_resolves_to_none(tmp_path):
    db = tmp_path / "gaia.db"
    assert find_dispatch_row_by_harness_agent_id("nobody-home", db_path=db) is None
    assert find_dispatch_row_by_harness_agent_id(None, db_path=db) is None


def test_two_unrelated_rows_sharing_a_harness_id_decline_rather_than_guess(tmp_path):
    db = tmp_path / "gaia.db"
    _birth_and_bind(db, "bbb222", "shared-session")
    _birth_and_bind(db, "ccc333", "shared-session")

    assert find_dispatch_row_by_harness_agent_id("shared-session", db_path=db) is None


def test_a_continuation_chain_is_not_an_ambiguity_and_collapses_to_its_live_link(tmp_path):
    db = tmp_path / "gaia.db"
    parent_contract_id = _birth_and_bind(db, "ddd444", "resumed-session")
    parent_row = find_dispatch_row_by_harness_agent_id("resumed-session", db_path=db)
    assert parent_row is not None

    finalize_agent_contract_handoff(
        contract_id=parent_contract_id,
        agent_id=parent_row["agent_id"],
        workspace=WORKSPACE,
        agent_state="NEEDS_VERIFICATION",
        raw_handoff_json="{}",
        db_path=db,
    )
    continuation = open_contract_continuation(
        parent_contract_id, "aeee555f00d.resumecafe",
        raw_handoff_json="{}", db_path=db,
    )
    assert continuation["status"] in {"opened", "already_open"}
    child_contract_id = continuation["contract_id"]
    finalize_agent_contract_handoff(
        contract_id=child_contract_id,
        agent_id=parent_row["agent_id"],
        workspace=WORKSPACE,
        agent_state="IN_PROGRESS",
        raw_handoff_json="{}",
        session_id="resumed-session",
        db_path=db,
    )
    from gaia.store.writer import stamp_harness_agent_id

    stamp_harness_agent_id(child_contract_id, "resumed-session", db_path=db)

    resolved = find_dispatch_row_by_harness_agent_id("resumed-session", db_path=db)

    assert resolved is not None
    assert resolved["contract_id"] == child_contract_id
