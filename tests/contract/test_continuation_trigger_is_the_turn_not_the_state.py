"""The continuation trigger is a CLOSED TURN, and a link is born clean.

Two measured defects are locked here, plus the property their fix puts most at
risk.

DEFECT 1 -- THE TRIGGER WAS DRAWN ON THE WRONG AXIS. A link used to be minted
only when the addressed ROW was terminal, and terminal meant COMPLETE alone. A
turn that closed declaring ``NEEDS_VERIFICATION`` therefore left its row
writable and its draft live: hand the SAME agent a new assignment and its
evidence MERGED into the row an independent verifier was about to read, and its
close REPLACED the producer's verdict -- every call exiting 0, with no notice.
The frontier is not a state, it is WHOSE turn ended: an agent that already
declared a close and writes again is a NEW turn, whatever it declared.

DEFECT 2 -- THE LINK WAS BORN POPULATED WITH SOMETHING FALSE. It inherited the
parent's dispatch columns verbatim -- the assignment, the description, the
prompt/tool-use correlation, the project -- so a link whose content described
renaming a module carried "audit the pipeline" as its recorded assignment. A
blank field is visible; a field filled with the previous turn's value is not.

DEFECT 3 -- CLEANING THE BIRTH ALSO DROPPED WHAT CONSTRAINS THE AGENT. The two
rules point opposite ways: what DESCRIBES the assignment must not be inherited
(it would lie), and what RESTRICTS the agent must not be lost (it would turn a
resumption into an escape hatch). Sorting those apart is the criterion the
mint now follows; both halves are asserted here.

WHAT MUST NOT BREAK -- ``finalize_agent_contract_handoff`` converges any row
that is not already COMPLETE, and the trigger widening runs straight at that,
since a producer closing NEEDS_VERIFICATION now ends its turn while its row
stays writable. The convergence is asserted here explicitly, including with a
continuation already open. (What that writer-level property is FOR is a
separate question, answered where the two frontiers are defined -- gaia/state:
it protects the one verdict that must never regress from a later writer that
knows less, not a cross-agent signature the CLI seam refuses outright.)

Every test drives the REAL writers and the REAL CLI against an isolated
``GAIA_DATA_DIR`` -- no mock of the store.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT / "hooks"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gaia.store.writer import (  # noqa: E402
    finalize_agent_contract_handoff,
    insert_dispatched_handoff,
    stamp_harness_agent_id,
)
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"

WORKSPACE = "me"
PRODUCER_AGENT_ID = valid_agent_id("closed-turn-producer")
VERIFIER_AGENT_ID = valid_agent_id("closed-turn-verifier")
AGENT_NAME = "gaia-system"
SESSION_ID = "sess-closed-turn"
HARNESS_ID = valid_agent_id("closed-turn-harness")

PLAN_ID = 91
TASK_ID = 910

_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_dispatch(monkeypatch):
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    yield


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "gaia.db"


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "gaia_data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    for var in ("GAIA_DB", "GAIA_DB_PATH", "CLAUDE_SESSION_ID", "GAIA_DISPATCH_AGENT"):
        monkeypatch.delenv(var, raising=False)
    return dict(os.environ)


def _cli_db(env: dict) -> Path:
    path = Path(env["GAIA_DATA_DIR"]) / "gaia.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _run(args: list, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CONTRACT_CLI), *args],
        capture_output=True, text=True, env=env, timeout=30,
    )


def _rows(db_path: Path, **where) -> list:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        clause = " AND ".join(f"{col} = ?" for col in where)
        sql = "SELECT * FROM agent_contract_handoffs"
        if clause:
            sql += f" WHERE {clause}"
        return con.execute(sql + " ORDER BY id ASC", tuple(where.values())).fetchall()
    finally:
        con.close()


def _envelope(state: str, note: str, agent_id: str = PRODUCER_AGENT_ID) -> dict:
    envelope = {
        "agent_status": {
            "agent_state": state,
            "agent_id": agent_id,
            "pending_steps": [] if state == "COMPLETE" else ["awaiting verification"],
            "next_action": "done" if state == "COMPLETE" else "hand to a verifier",
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


def _seed_plan_binding(db_path: Path) -> None:
    """Materialize briefs -> plans -> tasks so a plan_task_id satisfies the FKs."""
    finalize_agent_contract_handoff(
        contract_id=f"{PRODUCER_AGENT_ID}.schema-seed",
        agent_id=PRODUCER_AGENT_ID, workspace=WORKSPACE, agent_state="COMPLETE",
        raw_handoff_json=json.dumps(_envelope("COMPLETE", "seed")),
        db_path=db_path,
    )
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO briefs (id, workspace, name, status) VALUES (?, ?, ?, ?)",
            (1, WORKSPACE, "continuation-trigger", "in-progress"),
        )
        con.execute("INSERT INTO plans (id, brief_id, status) VALUES (?, ?, ?)",
                    (PLAN_ID, 1, "active"))
        con.execute(
            "INSERT INTO tasks (id, plan_id, order_num, goal, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (TASK_ID, PLAN_ID, 3, "produce the increment", "pending"),
        )
        con.commit()
    finally:
        con.close()


def _producer_awaiting_verification(
    db_path: Path,
    contract_id: str,
    *,
    plan_task_id: "int | None" = None,
    note: str = "producer increment",
) -> dict:
    """A turn that ran, produced, and CLOSED declaring NEEDS_VERIFICATION."""
    insert_dispatched_handoff(
        contract_id=contract_id,
        agent_id=PRODUCER_AGENT_ID,
        workspace=WORKSPACE,
        session_id=SESSION_ID,
        kind="task_execution",
        agent_name=AGENT_NAME,
        plan_task_id=plan_task_id,
        plan_id=PLAN_ID if plan_task_id else None,
        dispatch_prompt_id="prompt-producer",
        dispatch_tool_use_id="toolu-producer",
        dispatch_description="audit the release pipeline",
        dispatch_prompt="Audit the release pipeline end to end.",
        dispatch_project="gaia (/home/jorge/ws/me/gaia)",
        db_path=db_path,
    )
    stamp_harness_agent_id(contract_id, HARNESS_ID, db_path=db_path)
    envelope = _envelope("NEEDS_VERIFICATION", note)
    finalize_agent_contract_handoff(
        contract_id=contract_id,
        agent_id=PRODUCER_AGENT_ID,
        workspace=WORKSPACE,
        agent_state="NEEDS_VERIFICATION",
        raw_handoff_json=json.dumps(envelope),
        session_id=SESSION_ID,
        db_path=db_path,
    )
    return envelope


# ---------------------------------------------------------------------------
# DEFECT 1 -- the measured regression, end to end through the real CLI
# ---------------------------------------------------------------------------

def test_new_assignment_after_a_needs_verification_close_does_not_touch_the_producer_row(cli_env):
    """The measured case, in full: close NEEDS_VERIFICATION, then a new assignment.

    Against the pre-change code this runs to completion and fails on the defect
    itself: ``NEEDS_VERIFICATION`` was not terminal, so no link was minted, the
    new assignment's evidence was mirrored INTO the producer's row, and the new
    turn's close REPLACED the producer's verdict with COMPLETE.
    """
    db_path = _cli_db(cli_env)
    producer_id = f"{PRODUCER_AGENT_ID}.awaiting-verification"
    producer_envelope = _producer_awaiting_verification(db_path, producer_id)
    before = dict(_rows(db_path, contract_id=producer_id)[0])

    # The same agent is handed a NEW assignment and writes exactly as always.
    addc = _run(
        ["add", "--draft-id", producer_id, "--json",
         "evidence_report.key_outputs", "renamed the module (new assignment)"],
        cli_env,
    )
    assert addc.returncode == 0, addc.stderr
    payload = json.loads(addc.stdout)

    assert "continuation" in payload, (
        "a write from an agent that already declared a close is a NEW turn and "
        "must land in a NEW contract -- whatever state it closed in"
    )
    link_id = payload["draft_id"]
    assert link_id != producer_id
    assert payload["continuation"]["continues_contract_id"] == producer_id

    # The load-bearing assertion: the producer's record is untouched.
    after = dict(_rows(db_path, contract_id=producer_id)[0])
    assert after == before, (
        "the row an independent verifier is about to read must be byte-identical"
    )
    assert json.loads(after["raw_handoff_json"]) == producer_envelope

    # ... and the new turn's close must not replace the producer's verdict.
    assert _run(
        ["fill", "--draft-id", producer_id, "--json", json.dumps({
            "agent_status": {"agent_state": "COMPLETE", "next_action": "done"},
            "evidence_report": {
                "verification": {
                    "method": "test", "result": "pass", "details": "new assignment",
                },
            },
        })],
        cli_env,
    ).returncode == 0
    fin = _run(["finalize", "--draft-id", producer_id, "--json"], cli_env)
    assert fin.returncode == 0, fin.stderr
    assert json.loads(fin.stdout)["draft_id"] == link_id

    producer_row = _rows(db_path, contract_id=producer_id)[0]
    assert producer_row["agent_state"] == "NEEDS_VERIFICATION", (
        "the new assignment's verdict must never overwrite the producer's"
    )
    link_row = _rows(db_path, contract_id=link_id)[0]
    assert link_row["agent_state"] == "COMPLETE"
    link_envelope = json.loads(link_row["raw_handoff_json"])
    assert link_envelope["evidence_report"]["key_outputs"] == [
        "renamed the module (new assignment)"
    ]


@pytest.mark.parametrize(
    "closing_state",
    ["COMPLETE", "BLOCKED", "NEEDS_INPUT", "NEEDS_VERIFICATION", "APPROVAL_REQUEST"],
)
def test_every_declared_close_is_a_closed_turn(db, closing_state):
    """The trigger is the turn ending, not which of the six states named it."""
    from gaia.store.writer import open_contract_continuation

    contract_id = f"{PRODUCER_AGENT_ID}.closed-{closing_state.lower()}"
    insert_dispatched_handoff(
        contract_id=contract_id, agent_id=PRODUCER_AGENT_ID, workspace=WORKSPACE,
        session_id=SESSION_ID, db_path=db,
    )
    finalize_agent_contract_handoff(
        contract_id=contract_id, agent_id=PRODUCER_AGENT_ID, workspace=WORKSPACE,
        agent_state=closing_state,
        raw_handoff_json=json.dumps(_envelope(closing_state, "closed")),
        db_path=db,
    )

    outcome = open_contract_continuation(
        contract_id, f"{PRODUCER_AGENT_ID}.link-{closing_state.lower()}",
        raw_handoff_json="{}", db_path=db,
    )
    assert outcome["status"] == "opened", (
        f"a turn that closed declaring {closing_state} has ended; its next write "
        f"belongs to a new contract"
    )


def test_a_turn_still_running_is_written_in_place(db):
    """DISPATCHED and the reaped IN_PROGRESS are turns nobody declared closed."""
    from gaia.store.writer import open_contract_continuation

    dispatched = f"{PRODUCER_AGENT_ID}.still-dispatched"
    insert_dispatched_handoff(
        contract_id=dispatched, agent_id=PRODUCER_AGENT_ID, workspace=WORKSPACE,
        session_id=SESSION_ID, db_path=db,
    )
    assert open_contract_continuation(
        dispatched, f"{PRODUCER_AGENT_ID}.premature-a", raw_handoff_json="{}",
        db_path=db,
    ) == {"status": "skipped", "reason": "not_closed"}

    # IN_PROGRESS is what the backstop reaps an orphan to -- a turn that was CUT,
    # never one that declared a close. It stays recoverable in place.
    reaped = f"{PRODUCER_AGENT_ID}.reaped-in-progress"
    insert_dispatched_handoff(
        contract_id=reaped, agent_id=PRODUCER_AGENT_ID, workspace=WORKSPACE,
        session_id=SESSION_ID, db_path=db,
    )
    finalize_agent_contract_handoff(
        contract_id=reaped, agent_id=PRODUCER_AGENT_ID, workspace=WORKSPACE,
        agent_state="IN_PROGRESS",
        raw_handoff_json=json.dumps(_envelope("IN_PROGRESS", "reaped")),
        cut_reason="reaped", db_path=db,
    )
    assert open_contract_continuation(
        reaped, f"{PRODUCER_AGENT_ID}.premature-b", raw_handoff_json="{}", db_path=db,
    ) == {"status": "skipped", "reason": "not_closed"}
    assert _rows(db, contract_id=f"{PRODUCER_AGENT_ID}.premature-b") == []


# ---------------------------------------------------------------------------
# WHAT MUST NOT BREAK -- the independent verifier still converges the producer
# ---------------------------------------------------------------------------

def test_an_independent_verifier_converges_the_producers_row_in_place(db):
    """The producer's row is writable ON PURPOSE, and stays so.

    Widening the continuation trigger to NEEDS_VERIFICATION is the change that
    puts this at risk: the row is deliberately left convergeable so a DIFFERENT
    agent can record the truer verdict for the SAME contract_id. Freezing it
    would strand every produced increment at NEEDS_VERIFICATION forever.
    """
    _seed_plan_binding(db)
    producer_id = f"{PRODUCER_AGENT_ID}.verifier-converges"
    _producer_awaiting_verification(db, producer_id, plan_task_id=TASK_ID)
    born_row_id = _rows(db, contract_id=producer_id)[0]["id"]

    verdict = _envelope("COMPLETE", "verifier re-observed the gates", VERIFIER_AGENT_ID)
    outcome = finalize_agent_contract_handoff(
        contract_id=producer_id,
        agent_id=VERIFIER_AGENT_ID,
        workspace=WORKSPACE,
        agent_state="COMPLETE",
        raw_handoff_json=json.dumps(verdict),
        db_path=db,
    )

    assert outcome["handoff_id"] == born_row_id, "converged in place, not duplicated"
    rows = _rows(db, contract_id=producer_id)
    assert len(rows) == 1
    assert rows[0]["agent_state"] == "COMPLETE", (
        "the independent verifier's verdict must land on the producer's own row"
    )
    assert json.loads(rows[0]["raw_handoff_json"]) == verdict
    assert rows[0]["plan_task_id"] == TASK_ID, "the binding survives the convergence"
    assert _rows(db, continues_handoff_id=born_row_id) == [], (
        "convergence by a verifier is not a resumption and mints no link"
    )


def test_the_verifier_still_converges_after_the_producer_opened_a_continuation(db):
    """The hardest ordering: the producer moved on, the verifier arrives later.

    A link existing must not make the producer's row unreachable -- the verdict
    the verifier owes is owed to the row that proposed it, not to whatever the
    agent did next.
    """
    from gaia.store.writer import open_contract_continuation

    producer_id = f"{PRODUCER_AGENT_ID}.late-verifier"
    _producer_awaiting_verification(db, producer_id)
    producer_row_id = _rows(db, contract_id=producer_id)[0]["id"]

    link_id = f"{PRODUCER_AGENT_ID}.late-verifier-link"
    assert open_contract_continuation(
        producer_id, link_id,
        raw_handoff_json=json.dumps({"continues_contract_id": producer_id}),
        db_path=db,
    )["status"] == "opened"

    verdict = _envelope("COMPLETE", "late but independent", VERIFIER_AGENT_ID)
    finalize_agent_contract_handoff(
        contract_id=producer_id, agent_id=VERIFIER_AGENT_ID, workspace=WORKSPACE,
        agent_state="COMPLETE", raw_handoff_json=json.dumps(verdict), db_path=db,
    )

    assert _rows(db, contract_id=producer_id)[0]["agent_state"] == "COMPLETE"
    assert json.loads(
        _rows(db, contract_id=producer_id)[0]["raw_handoff_json"]
    ) == verdict
    link = _rows(db, contract_id=link_id)[0]
    assert link["agent_state"] == "DISPATCHED", (
        "the verifier's verdict belongs to the producer's row alone"
    )
    assert link["continues_handoff_id"] == producer_row_id


# ---------------------------------------------------------------------------
# DEFECT 2 -- the link is born clean
# ---------------------------------------------------------------------------

def test_the_link_inherits_identity_and_nothing_that_describes_the_old_assignment(db):
    """A field that cannot be filled legitimately is EMPTY, never the parent's."""
    from gaia.store.writer import open_contract_continuation

    _seed_plan_binding(db)
    producer_id = f"{PRODUCER_AGENT_ID}.clean-birth"
    _producer_awaiting_verification(db, producer_id, plan_task_id=TASK_ID)
    parent = dict(_rows(db, contract_id=producer_id)[0])
    assert parent["plan_task_id"] == TASK_ID and parent["dispatch_prompt"], (
        "precondition: the parent really does carry a dispatch and a binding"
    )

    link_id = f"{PRODUCER_AGENT_ID}.clean-birth-link"
    open_contract_continuation(
        producer_id, link_id,
        raw_handoff_json=json.dumps({"continues_contract_id": producer_id}),
        db_path=db,
    )
    link = dict(_rows(db, contract_id=link_id)[0])

    for column in (
        "dispatch_prompt",          # the assignment
        "dispatch_description",     # its description
        "dispatch_prompt_id",       # the prompt correlation key
        "dispatch_tool_use_id",     # the Task call correlation key
        "dispatch_project",         # the project it ran from
        "brief_id",
        "context_anchors",
        "kernel_sections",
    ):
        assert link[column] is None, (
            f"{column} describes the turn that ENDED. A new turn cannot fill it "
            f"legitimately, so it stays empty -- populated with the previous "
            f"turn's value it reads as true and is not"
        )

    # What a link legitimately carries: who it is, where it runs, and its origin.
    for column in ("agent_id", "workspace", "session_id", "harness_agent_id"):
        assert link[column] == parent[column], (
            f"{column} identifies the agent and its run, which the resumption "
            f"does not change"
        )

    # And what it carries for the opposite reason: a CONSTRAINT does not expire
    # with the turn it was stamped on. Losing it would make the resumption a way
    # around the rule -- see the escape-hatch test above.
    assert link["plan_task_id"] == parent["plan_task_id"] == TASK_ID, (
        "plan_task_id does not describe the old assignment, it restricts the "
        "agent -- and the same agent is still working the same plan task"
    )
    for column in ("kind", "plan_id", "parent_handoff_id"):
        assert link[column] == parent[column], (
            f"{column} is read by is_born_at_dispatch_row, whose only consumer "
            f"REFUSES the name lane of the SubagentStop closure when it answers "
            f"yes -- dropping it made a resumed turn close a concurrent "
            f"sibling's live dispatch (see "
            f"test_resumed_turn_does_not_reap_a_sibling.py)"
        )
    assert link["continues_handoff_id"] == parent["id"]
    assert link["agent_state"] == "DISPATCHED"
    assert link["cut_reason"] == "never_finalized"
    assert link["claimed_at"] is not None


def test_cli_born_link_keeps_the_constraint_and_drops_the_assignment(cli_env):
    """End to end through the real CLI: the two rules, on one link.

    The assignment columns stay empty because a new turn cannot fill them
    truthfully; the plan-task binding travels because the agent has not stopped
    being subject to it.
    """
    db_path = _cli_db(cli_env)
    _seed_plan_binding(db_path)
    producer_id = f"{PRODUCER_AGENT_ID}.cli-clean-birth"
    _producer_awaiting_verification(db_path, producer_id, plan_task_id=TASK_ID)

    setc = _run(
        ["set", "--draft-id", producer_id, "--json",
         "agent_status.next_action", "working the new assignment"],
        cli_env,
    )
    assert setc.returncode == 0, setc.stderr
    link_id = json.loads(setc.stdout)["draft_id"]
    assert link_id != producer_id

    link = _rows(db_path, contract_id=link_id)[0]
    assert link["dispatch_prompt"] is None
    assert link["dispatch_description"] is None
    assert link["plan_task_id"] == TASK_ID
    assert _rows(db_path, contract_id=producer_id)[0]["plan_task_id"] == TASK_ID


# ---------------------------------------------------------------------------
# DEFECT 3 -- a resumption is not an escape hatch from the turn's constraints
# ---------------------------------------------------------------------------

def test_a_plan_bound_turn_cannot_self_sign_through_its_continuation(cli_env):
    """The regression: the link must not be a way around blind verification.

    The producer is bound to a plan task, so it may not sign its own COMPLETE
    (bin/cli/contract.py's blind-verification seam, the CLI twin of
    hooks/adapters/claude_code.py::_blind_verification_required). Before this
    fix the constraint was dropped at the mint -- the link was born with
    ``plan_task_id`` NULL and the binding reader did a flat SELECT that never
    walked the chain -- so the SAME agent, on the SAME plan task, self-signed
    COMPLETE simply by writing once more after its close.
    """
    db_path = _cli_db(cli_env)
    _seed_plan_binding(db_path)
    producer_id = f"{PRODUCER_AGENT_ID}.no-self-sign"
    _producer_awaiting_verification(db_path, producer_id, plan_task_id=TASK_ID)

    # The producer is resumed and writes again: a link is minted.
    setc = _run(
        ["set", "--draft-id", producer_id, "--json",
         "agent_status.next_action", "kept working after the close"],
        cli_env,
    )
    assert setc.returncode == 0, setc.stderr
    link_id = json.loads(setc.stdout)["draft_id"]
    assert link_id != producer_id

    # It then declares the very COMPLETE the plan binding forbids it to declare.
    assert _run(
        ["fill", "--draft-id", producer_id, "--json", json.dumps({
            "agent_status": {"agent_state": "COMPLETE", "next_action": "done"},
            "evidence_report": {
                "verification": {
                    "method": "test", "result": "pass", "details": "self-signed",
                },
            },
        })],
        cli_env,
    ).returncode == 0

    fin = _run(["finalize", "--draft-id", producer_id, "--json"], cli_env)
    assert fin.returncode == 1, (
        "a turn bound to a plan task may not self-COMPLETE, and resuming is "
        "not a way out of that binding -- the link is the same agent, still on "
        f"the same task. stdout={fin.stdout!r} stderr={fin.stderr!r}"
    )
    payload = json.loads(fin.stdout)
    assert payload["reason"] == "blind_verification_required"
    assert payload["plan_task_id"] == TASK_ID

    link_row = _rows(db_path, contract_id=link_id)[0]
    assert link_row["agent_state"] != "COMPLETE", (
        "the refused COMPLETE must not have landed on the link either"
    )


def test_the_binding_reader_recovers_a_constraint_from_anywhere_in_the_chain(db):
    """The reader the two finalize seams share walks the chain, not one row.

    The mint carries the constraint forward, so a link resolves it from its own
    column; a link minted WITHOUT it (an older build, a mint that raced) still
    resolves it from the turn it continues. Both are the same answer, and the
    reader must give it either way -- a constraint recoverable only when a
    single writer got it right is not a constraint.
    """
    from gaia.store.writer import (
        dispatched_binding_plan_task_id_by_contract,
        open_contract_continuation,
    )

    _seed_plan_binding(db)
    producer_id = f"{PRODUCER_AGENT_ID}.chain-reader"
    _producer_awaiting_verification(db, producer_id, plan_task_id=TASK_ID)

    first = f"{PRODUCER_AGENT_ID}.chain-reader-link-1"
    open_contract_continuation(
        producer_id, first, raw_handoff_json="{}", db_path=db,
    )
    assert dispatched_binding_plan_task_id_by_contract(first, db_path=db) == TASK_ID

    # Second link, minted by hand with the binding column left NULL -- the shape
    # a pre-fix build produced. The chain still answers.
    finalize_agent_contract_handoff(
        contract_id=first, agent_id=PRODUCER_AGENT_ID, workspace=WORKSPACE,
        agent_state="BLOCKED",
        raw_handoff_json=json.dumps(_envelope("BLOCKED", "still stuck")),
        db_path=db,
    )
    second = f"{PRODUCER_AGENT_ID}.chain-reader-link-2"
    open_contract_continuation(first, second, raw_handoff_json="{}", db_path=db)
    con = sqlite3.connect(str(db))
    try:
        con.execute(
            "UPDATE agent_contract_handoffs SET plan_task_id = NULL "
            "WHERE contract_id = ?",
            (second,),
        )
        con.commit()
    finally:
        con.close()

    assert _rows(db, contract_id=second)[0]["plan_task_id"] is None
    assert dispatched_binding_plan_task_id_by_contract(second, db_path=db) == TASK_ID, (
        "the constraint is a property of the chain: a link whose own column is "
        "empty still resolves it from the turn it continues"
    )

    # An unbound chain stays unbound -- the walk must not invent a binding.
    unbound_id = f"{PRODUCER_AGENT_ID}.chain-reader-unbound"
    _producer_awaiting_verification(db, unbound_id)
    unbound_link = f"{PRODUCER_AGENT_ID}.chain-reader-unbound-link"
    open_contract_continuation(
        unbound_id, unbound_link, raw_handoff_json="{}", db_path=db,
    )
    assert dispatched_binding_plan_task_id_by_contract(
        unbound_link, db_path=db
    ) is None


def test_the_row_the_stop_gate_judges_carries_the_binding(db):
    """SubagentStop resolves by harness id, which collapses to the chain's tip.

    So the row the gate reads IS the link, and the binding the gate keys on has
    to be on it. This asserts the gate's INPUT at the same seam the hook uses.
    """
    import sys as _sys

    _sys.path.insert(0, str(_REPO_ROOT / "hooks"))
    from modules.agents.handoff_persister import dispatch_row_by_harness_id
    from gaia.store.writer import open_contract_continuation

    _seed_plan_binding(db)
    producer_id = f"{PRODUCER_AGENT_ID}.stop-gate-input"
    _producer_awaiting_verification(db, producer_id, plan_task_id=TASK_ID)
    link_id = f"{PRODUCER_AGENT_ID}.stop-gate-input-link"
    open_contract_continuation(producer_id, link_id, raw_handoff_json="{}", db_path=db)

    resolved = dispatch_row_by_harness_id(
        {"agent_id": HARNESS_ID}, session_id=SESSION_ID, db_path=db,
    )
    assert resolved is not None
    assert resolved["contract_id"] == link_id, "the tip is what the gate judges"
    assert resolved["plan_task_id"] == TASK_ID, (
        "the gate keys blind verification on this value; a NULL here is the "
        "self-signature the plan binding exists to refuse"
    )


# ---------------------------------------------------------------------------
# The two frontiers no longer disagree, and neither is undocumented
# ---------------------------------------------------------------------------

def test_the_closed_turn_frontier_is_every_state_but_in_progress():
    from gaia.state import (
        CLOSED_TURN_PLAN_STATUSES,
        TERMINAL_PLAN_STATUSES,
        VALID_PLAN_STATUSES,
    )

    assert set(CLOSED_TURN_PLAN_STATUSES) == set(VALID_PLAN_STATUSES) - {"IN_PROGRESS"}
    assert set(TERMINAL_PLAN_STATUSES) < set(CLOSED_TURN_PLAN_STATUSES), (
        "the verdict-overwrite frontier is a STRICT subset of the closed-turn "
        "one: a verdict that may never be overwritten was certainly declared at "
        "a close, and the gap between them is load-bearing -- collapsing it "
        "either freezes every closed row or lets a closed turn keep writing"
    )
    assert set(CLOSED_TURN_PLAN_STATUSES) - set(TERMINAL_PLAN_STATUSES) == {
        "APPROVAL_REQUEST", "BLOCKED", "NEEDS_INPUT", "NEEDS_VERIFICATION",
    }, "the states that end a turn without freezing its row, named explicitly"


def test_a_write_that_reached_no_row_says_so(cli_env):
    """A mirror that did not land is reported, never reduced to exit 0.

    ``no_row`` is the one benign case -- a draft with no dispatch behind it never
    had a row -- so it is carried in ``--json`` for a machine reader and kept off
    stderr, which would otherwise cry wolf on every write of such a draft.
    """
    _cli_db(cli_env)
    init = _run(["init", "--agent-id", PRODUCER_AGENT_ID, "--json"], cli_env)
    draft_id = json.loads(init.stdout)["draft_id"]

    setc = _run(
        ["set", "--draft-id", draft_id, "--json", "agent_status.next_action", "probe"],
        cli_env,
    )
    assert setc.returncode == 0, setc.stderr
    payload = json.loads(setc.stdout)
    assert payload["mirrored"] is False
    assert payload["mirror_skipped_reason"] == "no_row", (
        "a caller must be able to learn WHY its evidence reached no row"
    )
    assert "MIRROR SKIPPED" not in setc.stderr


def test_a_mirror_blocked_by_a_closed_turn_is_announced_loudly():
    """Every non-benign skip produces a stderr line naming the reason."""
    sys.path.insert(0, str(_REPO_ROOT / "bin" / "cli"))
    import contract as contract_cli

    assert contract_cli._mirror_warning({"status": "applied"}) is None
    assert contract_cli._mirror_warning({"status": "skipped", "reason": "no_row"}) is None

    warning = contract_cli._mirror_warning({"status": "skipped", "reason": "closed"})
    assert warning is not None and "MIRROR SKIPPED: closed" in warning

    raised = contract_cli._mirror_warning(
        {"status": "skipped", "reason": "error", "detail": "db is locked"}
    )
    assert raised is not None and "db is locked" in raised


def test_draft_spentness_uses_the_same_frontier_as_the_continuation_trigger(cli_env):
    """The two disagreeing frontiers are reconciled onto one definition.

    Asserted through ``spent_draft_ids`` against real rows, one per state, and
    not by comparing the module's frozenset to the tuple it is built from --
    that comparison is true however the predicate behaves.
    """
    from gaia.contract.drafts import spent_draft_ids
    from gaia.state import CLOSED_TURN_PLAN_STATUSES, VALID_PLAN_STATUSES

    db_path = _cli_db(cli_env)
    ids = {}
    for state in VALID_PLAN_STATUSES:
        contract_id = f"{PRODUCER_AGENT_ID}.spent-{state.lower()}"
        ids[state] = contract_id
        insert_dispatched_handoff(
            contract_id=contract_id, agent_id=PRODUCER_AGENT_ID,
            workspace=WORKSPACE, session_id=SESSION_ID, db_path=db_path,
        )
        finalize_agent_contract_handoff(
            contract_id=contract_id, agent_id=PRODUCER_AGENT_ID,
            workspace=WORKSPACE, agent_state=state,
            raw_handoff_json=json.dumps(_envelope(state, "spentness probe")),
            db_path=db_path,
        )

    # A turn still running: born, never closed.
    dispatched_id = f"{PRODUCER_AGENT_ID}.spent-dispatched"
    ids["DISPATCHED"] = dispatched_id
    insert_dispatched_handoff(
        contract_id=dispatched_id, agent_id=PRODUCER_AGENT_ID,
        workspace=WORKSPACE, session_id=SESSION_ID, db_path=db_path,
    )

    spent = spent_draft_ids(candidates=set(ids.values()))

    assert spent == {ids[state] for state in CLOSED_TURN_PLAN_STATUSES}, (
        "a draft is spent exactly when its turn declared a close -- the same "
        "moment the next write is diverted into a new contract"
    )
    assert ids["IN_PROGRESS"] not in spent, (
        "the state the backstop reaps a CUT turn to is what a resume reads"
    )
    assert ids["DISPATCHED"] not in spent
