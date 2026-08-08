"""A resumed turn must not close the live dispatch of a concurrent sibling.

THE MEASURED DEFECT. The SubagentStop closure has a last-resort lane that finds
this turn's born row by the dispatched agent's NAME
(``find_dispatched_row_by_agent_name``). A name is shared by every dispatch of
that agent, so the lane is guarded: it is skipped when the turn's OWN row was
born at dispatch, because then the turn adopted its dispatch identity and there
is no separate scaffold left to close. The guard reads three columns --
``kind``, ``plan_id``, ``parent_handoff_id`` (``is_born_at_dispatch_row``).

A resumption's link used to be minted with all three NULL, so the guard answered
"not born at dispatch" for a turn that WAS, the lane switched back on, and the
only DISPATCHED row carrying that agent's name was a CONCURRENT SIBLING'S live
dispatch -- closed, and stamped as superseded by the resuming turn's link.

The path that reaches it is the ordinary one: the resumed turn finalizes its
link too, so by SubagentStop neither its born row nor its link is DISPATCHED any
more, ``find_orphaned_dispatched_handoff`` finds nothing, and the name lane is
the last thing standing.

WHAT MUST NOT BREAK is the case the lane exists for -- a turn that never adopted
its minted identity, whose born row is genuinely still open. The last test
asserts that lane still closes that row.
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

from gaia.store import writer as _writer  # noqa: E402
from gaia.store.writer import (  # noqa: E402
    finalize_agent_contract_handoff,
    insert_dispatched_handoff,
    is_born_at_dispatch_row,
    open_contract_continuation,
)
from modules.agents.handoff_persister import (  # noqa: E402
    close_born_dispatch_row,
    dispatch_identity_candidates,
)
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

WORKSPACE = "me"
AGENT_NAME = "gaia-system"
SESSION_ID = "sess-siblings"

RESUMED_AGENT_ID = valid_agent_id("resumed-turn")
SIBLING_AGENT_ID = valid_agent_id("sibling-turn")

PLAN_ID = 77
RESUMED_TASK_ID = 771
SIBLING_TASK_ID = 772

_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)


@pytest.fixture(autouse=True)
def _clean_dispatch(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    yield


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "gaia.db"


def _envelope(state: str, agent_id: str, note: str) -> dict:
    envelope = {
        "agent_status": {
            "agent_state": state,
            "agent_id": agent_id,
            "pending_steps": [] if state == "COMPLETE" else ["more to do"],
            "next_action": "done" if state == "COMPLETE" else "continue",
        },
        "evidence_report": {k: [] for k in _EVIDENCE_KEYS},
        "consolidation_report": None,
        "approval_request": None,
    }
    envelope["evidence_report"]["key_outputs"] = [note]
    envelope["evidence_report"]["verification"] = {
        "method": "test", "result": "pass", "details": note,
    }
    return envelope


def _row(db_path: Path, contract_id: str) -> sqlite3.Row:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT * FROM agent_contract_handoffs WHERE contract_id = ?",
            (contract_id,),
        ).fetchone()
    finally:
        con.close()


def _seed_plan_binding(db_path: Path) -> None:
    """briefs -> plans -> tasks, so a plan_task_id satisfies the FKs."""
    finalize_agent_contract_handoff(
        contract_id=f"{RESUMED_AGENT_ID}.schema-seed",
        agent_id=RESUMED_AGENT_ID, workspace=WORKSPACE, agent_state="COMPLETE",
        raw_handoff_json=json.dumps(
            _envelope("COMPLETE", RESUMED_AGENT_ID, "seed")
        ),
        db_path=db_path,
    )
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO briefs (id, workspace, name, status) VALUES (?, ?, ?, ?)",
            (1, WORKSPACE, "sibling-reaping", "in-progress"),
        )
        con.execute("INSERT INTO plans (id, brief_id, status) VALUES (?, ?, ?)",
                    (PLAN_ID, 1, "active"))
        for task_id, order_num in ((RESUMED_TASK_ID, 1), (SIBLING_TASK_ID, 2)):
            con.execute(
                "INSERT INTO tasks (id, plan_id, order_num, goal, status) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, PLAN_ID, order_num, f"task {order_num}", "pending"),
            )
        con.commit()
    finally:
        con.close()


def _born(
    db_path: Path,
    contract_id: str,
    agent_id: str,
    *,
    kind: str,
    plan_task_id: "int | None" = None,
    plan_id: "int | None" = None,
    prompt_id: str = "prompt-x",
) -> None:
    insert_dispatched_handoff(
        contract_id=contract_id,
        agent_id=agent_id,
        workspace=WORKSPACE,
        session_id=SESSION_ID,
        agent_name=AGENT_NAME,
        kind=kind,
        plan_task_id=plan_task_id,
        plan_id=plan_id,
        dispatch_prompt_id=prompt_id,
        dispatch_description=f"description for {contract_id}",
        dispatch_prompt=f"goal for {contract_id}",
        db_path=db_path,
    )


def _resume(db_path: Path, born_contract_id: str, link_contract_id: str) -> None:
    """The turn closes, writes again (minting the link), and closes again."""
    finalize_agent_contract_handoff(
        contract_id=born_contract_id, agent_id=RESUMED_AGENT_ID,
        workspace=WORKSPACE, agent_state="NEEDS_VERIFICATION",
        raw_handoff_json=json.dumps(
            _envelope("NEEDS_VERIFICATION", RESUMED_AGENT_ID, "first close")
        ),
        session_id=SESSION_ID, db_path=db_path,
    )
    outcome = open_contract_continuation(
        born_contract_id, link_contract_id,
        raw_handoff_json=json.dumps({
            "continues_contract_id": born_contract_id,
            "born_at_dispatch": True,
            _writer.BIRTH_AGENT_NAME_KEY: AGENT_NAME,
        }),
        db_path=db_path,
    )
    assert outcome["status"] == "opened" and outcome["created"], outcome
    finalize_agent_contract_handoff(
        contract_id=link_contract_id, agent_id=RESUMED_AGENT_ID,
        workspace=WORKSPACE, agent_state="COMPLETE",
        raw_handoff_json=json.dumps(
            _envelope("COMPLETE", RESUMED_AGENT_ID, "second close")
        ),
        session_id=SESSION_ID, db_path=db_path,
    )


def _subagent_stop_closure(db_path: Path, link_contract_id: str):
    """Exactly what ``persist_handoff`` step 5 calls for the resumed turn."""
    return close_born_dispatch_row(
        _writer,
        session_id=SESSION_ID,
        identity_candidates=dispatch_identity_candidates(
            RESUMED_AGENT_ID, {"agent": AGENT_NAME, "agent_id": None},
        ),
        workspace=WORKSPACE,
        contract_pointer=link_contract_id,
        turn_recorded_own_contract=True,
        db_path=db_path,
        skip_contract_id=link_contract_id,
        agent_name=AGENT_NAME,
    )


# ---------------------------------------------------------------------------
# The regression
# ---------------------------------------------------------------------------

def test_a_resumed_turn_does_not_close_a_concurrent_siblings_dispatch(db):
    """Two dispatches of the same agent; one resumes, the other is still running."""
    _seed_plan_binding(db)

    sibling_id = f"{SIBLING_AGENT_ID}.still-running"
    _born(db, sibling_id, SIBLING_AGENT_ID, kind="task_execution",
          plan_task_id=SIBLING_TASK_ID, plan_id=PLAN_ID, prompt_id="prompt-sibling")

    born_id = f"{RESUMED_AGENT_ID}.first-turn"
    link_id = f"{RESUMED_AGENT_ID}.the-link"
    _born(db, born_id, RESUMED_AGENT_ID, kind="task_execution",
          plan_task_id=RESUMED_TASK_ID, plan_id=PLAN_ID, prompt_id="prompt-resumed")
    _resume(db, born_id, link_id)

    before = dict(_row(db, sibling_id))
    assert before["agent_state"] == "DISPATCHED", "precondition: the sibling is live"

    outcome = _subagent_stop_closure(db, link_id)

    after = dict(_row(db, sibling_id))
    assert after == before, (
        "the resuming turn adopted its dispatch identity, so it has no scaffold "
        "left to close -- the sibling's live dispatch is not its to reap"
    )
    assert after["agent_state"] == "DISPATCHED"
    assert "superseded_by_contract_id" not in json.loads(after["raw_handoff_json"])
    assert outcome is None, (
        "nothing was left to close for this turn; a returned outcome means some "
        f"row was written: {outcome}"
    )


def test_a_free_dispatch_carries_the_same_protection_through_kind_alone(db):
    """No plan binding at all: ``kind`` is the only column left to carry it."""
    sibling_id = f"{SIBLING_AGENT_ID}.free-still-running"
    _born(db, sibling_id, SIBLING_AGENT_ID, kind="investigation",
          prompt_id="prompt-sibling-free")

    born_id = f"{RESUMED_AGENT_ID}.free-first-turn"
    link_id = f"{RESUMED_AGENT_ID}.free-link"
    _born(db, born_id, RESUMED_AGENT_ID, kind="investigation",
          prompt_id="prompt-resumed-free")
    _resume(db, born_id, link_id)

    before = dict(_row(db, sibling_id))
    assert _subagent_stop_closure(db, link_id) is None
    assert dict(_row(db, sibling_id)) == before


def test_the_link_of_an_adopted_chain_reads_as_born_at_dispatch(db):
    """The predicate itself: a link descends from a dispatch and says so."""
    born_id = f"{RESUMED_AGENT_ID}.predicate"
    link_id = f"{RESUMED_AGENT_ID}.predicate-link"
    _born(db, born_id, RESUMED_AGENT_ID, kind="investigation")
    _resume(db, born_id, link_id)

    assert is_born_at_dispatch_row(born_id, db_path=db) is True
    assert is_born_at_dispatch_row(link_id, db_path=db) is True, (
        "a resumption of a dispatched turn is still that dispatched turn"
    )


def test_a_link_of_a_self_minted_chain_is_not_born_at_dispatch(db):
    """The other direction: no dispatch behind the chain, no inherited answer."""
    own_id = f"{RESUMED_AGENT_ID}.self-minted"
    finalize_agent_contract_handoff(
        contract_id=own_id, agent_id=RESUMED_AGENT_ID, workspace=WORKSPACE,
        agent_state="NEEDS_VERIFICATION",
        raw_handoff_json=json.dumps(
            _envelope("NEEDS_VERIFICATION", RESUMED_AGENT_ID, "no dispatch")
        ),
        session_id=SESSION_ID, db_path=db,
    )
    link_id = f"{RESUMED_AGENT_ID}.self-minted-link"
    assert open_contract_continuation(
        own_id, link_id, raw_handoff_json=json.dumps({"continues": own_id}),
        db_path=db,
    )["status"] == "opened"

    assert is_born_at_dispatch_row(own_id, db_path=db) is False
    assert is_born_at_dispatch_row(link_id, db_path=db) is False


def test_a_link_carrying_the_binding_still_stays_out_of_the_claim_pool(db):
    """The columns it now inherits are not what kept it out, and still are not.

    ``claim_dispatch_row``'s pool is DISPATCHED + unclaimed, narrowed by a
    correlation key. A link is stamped claimed at mint and carries neither
    correlation key, so it is excluded twice over -- and the binding columns it
    inherits are read only as a signature between rival candidates.
    """
    from gaia.store.writer import claim_dispatch_row

    _seed_plan_binding(db)
    born_id = f"{RESUMED_AGENT_ID}.claimable"
    link_id = f"{RESUMED_AGENT_ID}.claimable-link"
    _born(db, born_id, RESUMED_AGENT_ID, kind="task_execution",
          plan_task_id=RESUMED_TASK_ID, plan_id=PLAN_ID, prompt_id="prompt-claim")
    finalize_agent_contract_handoff(
        contract_id=born_id, agent_id=RESUMED_AGENT_ID, workspace=WORKSPACE,
        agent_state="NEEDS_VERIFICATION",
        raw_handoff_json=json.dumps(
            _envelope("NEEDS_VERIFICATION", RESUMED_AGENT_ID, "closed")
        ),
        session_id=SESSION_ID, db_path=db,
    )
    open_contract_continuation(
        born_id, link_id,
        raw_handoff_json=json.dumps({
            "continues_contract_id": born_id,
            "born_at_dispatch": True,
            _writer.BIRTH_AGENT_NAME_KEY: AGENT_NAME,
        }),
        db_path=db,
    )
    link = dict(_row(db, link_id))
    assert link["kind"] == "task_execution" and link["plan_id"] == PLAN_ID
    assert link["agent_state"] == "DISPATCHED" and link["claimed_at"] is not None

    assert claim_dispatch_row(
        agent_name=AGENT_NAME, dispatch_prompt_id="prompt-claim", db_path=db,
    ) is None, "a link is never a candidate for a later SubagentStart"
    assert dict(_row(db, link_id)) == link


# ---------------------------------------------------------------------------
# What must not break: the lane the guard switches off still works
# ---------------------------------------------------------------------------

def test_a_turn_that_never_adopted_still_gets_its_own_born_row_closed(db):
    """The name lane's real job -- unchanged by the guard."""
    born_id = f"{RESUMED_AGENT_ID}.never-adopted"
    _born(db, born_id, RESUMED_AGENT_ID, kind="investigation")

    # The turn minted an identity of its own, so its contract row shares no
    # coordinate with its born row except the dispatched NAME.
    own_id = "hook-backstop.a1111111111111111.sess-siblings"
    outcome = close_born_dispatch_row(
        _writer,
        session_id=SESSION_ID,
        identity_candidates=dispatch_identity_candidates(
            None, {"agent": AGENT_NAME, "agent_id": None},
        ),
        workspace=WORKSPACE,
        contract_pointer=own_id,
        turn_recorded_own_contract=True,
        db_path=db,
        skip_contract_id=own_id,
        agent_name=AGENT_NAME,
    )

    assert outcome is not None, "the born row was this turn's own and must close"
    closed = dict(_row(db, born_id))
    assert closed["agent_state"] != "DISPATCHED"
    assert json.loads(closed["raw_handoff_json"])["superseded_by_contract_id"] == own_id
