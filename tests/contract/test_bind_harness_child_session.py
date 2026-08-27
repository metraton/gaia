"""plan 65 T10 -- unambiguous binding via the parent's own message.part.updated.

``bind_harness_child_session`` is the SECOND join OpenCode needs, after T9's
Task-dispatch claim (``claim_dispatch_row``, layer 0, exact callID): it stamps
the CHILD's own harness session id onto the row its Task call was born under,
read from the PARENT's ``message.part.updated`` event -- never from
``session.created`` on the child's own session, which carries no callID and
so cannot resolve a row without guessing.

Named cases (gate 1012, task 538):
  (a) two identical concurrent dispatches (same agent, same description) each
      bind to their own row via the callID reported on the parent's event;
  (b) a session-created-shaped coordinate (no callID) never participates in
      the bind -- the function has exactly one correlation key;
  (c) a bind attempt whose callID cannot resolve against callID-born rows
      declines (None/skip) instead of misbinding a sibling -- Claude Code's
      own FIFO layer inside ``claim_dispatch_row`` (exercised, unmodified, by
      ``tests/contract/test_claim_dispatch_row.py::test_identical_siblings_claim_fifo_oldest_first``)
      is untouched by this function, which never runs it;
  (d) claimed_at and harness_agent_id land on the correct row.

Runs against a fresh DB; the writer's own ``_connect`` materializes the real
schema from ``gaia/store/schema.sql``.
"""

from __future__ import annotations

import sqlite3

import pytest

from gaia.store.writer import (
    bind_harness_child_session,
    claim_dispatch_row,
    insert_dispatched_handoff,
    is_harness_session_bound,
)
from tests.fixtures.agent_ids import valid_agent_id

WORKSPACE = "me"


@pytest.fixture(autouse=True)
def _clean_dispatch(monkeypatch):
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    yield


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "gaia.db"


def _birth(db, token: str, **overrides):
    """Birth (and, like T9's real dispatch, immediately claim) one row --
    the state a message.part.updated bind always finds it in."""
    agent_id = valid_agent_id(f"a{token}")
    fields = {
        "agent_name": "gaia-system",
        "kind": "investigation",
        "dispatch_prompt_id": "prompt-1",
        "dispatch_description": "concurrent identical dispatch",
        "dispatch_prompt": "do the identical thing",
        "dispatch_tool_use_id": f"call-{token}",
    }
    fields.update(overrides)
    result = insert_dispatched_handoff(
        f"{agent_id}.{token}cafe", agent_id, WORKSPACE,
        session_id=None, db_path=db, **fields,
    )
    assert result["created"] is True
    contract_id = result["contract_id"]
    # Claimed immediately, exactly like T9's real dispatch -- with every
    # correlation key this birth carries, so a row born with no callID
    # (dispatch_tool_use_id=None) still self-claims via FIFO, sequentially,
    # one unclaimed candidate at a time.
    claimed = claim_dispatch_row(
        agent_name="gaia-system",
        dispatch_prompt_id=fields["dispatch_prompt_id"],
        dispatch_description=fields["dispatch_description"],
        dispatch_tool_use_id=fields["dispatch_tool_use_id"],
        db_path=db,
    )
    assert claimed is not None and claimed["contract_id"] == contract_id
    return contract_id


def _row(db, contract_id):
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT * FROM agent_contract_handoffs WHERE contract_id = ?",
            (contract_id,),
        ).fetchone()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# (a) concurrent identical dispatches bind via the parent's own callID
# ---------------------------------------------------------------------------

def test_concurrent_identical_dispatches_bind_to_their_own_row_via_parent_callid(db):
    """Two dispatches of the SAME agent with the SAME description -- an
    identical-looking pair by every other signal -- each land on their own
    row because each carries a DISTINCT callID from the parent's own event."""
    first = _birth(db, "aaa111")
    second = _birth(db, "bbb222")

    bound_first = bind_harness_child_session(
        dispatch_tool_use_id="call-aaa111", harness_agent_id="child-session-A",
        db_path=db,
    )
    bound_second = bind_harness_child_session(
        dispatch_tool_use_id="call-bbb222", harness_agent_id="child-session-B",
        db_path=db,
    )

    assert bound_first == {
        "status": "applied", "handoff_id": _row(db, first)["id"],
        "contract_id": first,
    }
    assert bound_second == {
        "status": "applied", "handoff_id": _row(db, second)["id"],
        "contract_id": second,
    }
    assert _row(db, first)["harness_agent_id"] == "child-session-A"
    assert _row(db, second)["harness_agent_id"] == "child-session-B"


# ---------------------------------------------------------------------------
# (b) session.created never participates in the bind
# ---------------------------------------------------------------------------

def test_session_created_style_coordinate_never_participates_in_the_bind(db):
    """The function's only correlation key is the exact callID
    (``dispatch_tool_use_id``) the PARENT's ``message.part.updated`` names.
    A session.created-shaped signal on the CHILD -- a bare session id with no
    callID at all -- resolves nothing: there is no second argument this
    function reads as a fallback coordinate."""
    contract_id = _birth(db, "ccc333")

    # session.created on the child names only its own session id -- there is
    # no callID to hand this function, so the call it would have to make
    # (dispatch_tool_use_id=None) is the exact "unusable" shape.
    declined = bind_harness_child_session(
        dispatch_tool_use_id=None, harness_agent_id="child-session-from-created",
        db_path=db,
    )

    assert declined == {"status": "skipped", "reason": "no_dispatch_tool_use_id"}
    assert _row(db, contract_id)["harness_agent_id"] is None


# ---------------------------------------------------------------------------
# (c) unusable callID declines instead of misbinding a sibling; Claude Code's
#     FIFO layer (inside claim_dispatch_row) is a separate, untouched code
#     path -- not exercised by this function at all.
# ---------------------------------------------------------------------------

def test_bind_declines_when_callid_is_unusable_against_callid_born_rows(db):
    one = _birth(db, "ddd444")
    two = _birth(db, "eee555")

    declined = bind_harness_child_session(
        dispatch_tool_use_id="call-never-born", harness_agent_id="child-session-X",
        db_path=db,
    )

    assert declined == {"status": "skipped", "reason": "no_row_for_tool_use_id"}
    assert _row(db, one)["harness_agent_id"] is None
    assert _row(db, two)["harness_agent_id"] is None


def test_bind_never_invokes_claim_dispatch_rows_fifo_ladder(db):
    """This function has no correlation ladder of its own: it is a plain
    WHERE dispatch_tool_use_id = ? lookup, never the (a)/(b)/(c) ladder
    ``claim_dispatch_row`` runs for a host with no callID (Claude Code's
    FIFO, enmienda E1). Two rows sharing no callID at all -- the exact shape
    that ladder's FIFO layer resolves -- must NOT be resolved by this
    function; it declines instead of guessing FIFO order."""
    one = _birth(db, "fff666", dispatch_tool_use_id=None)
    two = _birth(db, "ggg777", dispatch_tool_use_id=None)

    declined = bind_harness_child_session(
        dispatch_tool_use_id=None, harness_agent_id="child-session-Y",
        db_path=db,
    )

    assert declined == {"status": "skipped", "reason": "no_dispatch_tool_use_id"}
    assert _row(db, one)["harness_agent_id"] is None
    assert _row(db, two)["harness_agent_id"] is None


# ---------------------------------------------------------------------------
# (d) claimed_at and harness_agent_id land on the correct row
# ---------------------------------------------------------------------------

def test_bind_stamps_claimed_at_and_harness_agent_id_on_the_correct_row(db):
    contract_id = _birth(db, "hhh888")
    row_before = _row(db, contract_id)
    assert row_before["claimed_at"] is not None  # T9 already claimed at birth
    assert row_before["harness_agent_id"] is None

    result = bind_harness_child_session(
        dispatch_tool_use_id="call-hhh888", harness_agent_id="child-session-correct",
        db_path=db,
    )

    assert result["status"] == "applied"
    assert result["contract_id"] == contract_id
    row_after = _row(db, contract_id)
    assert row_after["claimed_at"] == row_before["claimed_at"]  # untouched, already set
    assert row_after["harness_agent_id"] == "child-session-correct"
    assert is_harness_session_bound("child-session-correct", db_path=db) is True
    assert is_harness_session_bound("some-other-session", db_path=db) is False


def test_bind_claims_a_still_unclaimed_row_defensively(db):
    """A row somehow still unclaimed when the bind arrives (T9's own claim
    lost a race or never ran) gets BOTH claimed_at and harness_agent_id from
    this one call -- the bind must not depend on T9 having already run."""
    agent_id = valid_agent_id("aiii999")
    result = insert_dispatched_handoff(
        f"{agent_id}.iii999cafe", agent_id, WORKSPACE,
        session_id=None, db_path=db,
        agent_name="gaia-system", kind="investigation",
        dispatch_tool_use_id="call-iii999",
    )
    contract_id = result["contract_id"]
    assert _row(db, contract_id)["claimed_at"] is None

    bound = bind_harness_child_session(
        dispatch_tool_use_id="call-iii999", harness_agent_id="child-session-late",
        db_path=db,
    )

    assert bound["status"] == "applied"
    row = _row(db, contract_id)
    assert row["claimed_at"] is not None
    assert row["harness_agent_id"] == "child-session-late"
