"""Dispatch-row closure + contract attribution, keyed the way PRODUCTION keys.

These are the clauses the pre-existing reaper suite could not catch, because it
births its fixture rows under the AGENT'S MINTED DRAFT ID
(``insert_dispatched_handoff(contract_id=draft_id, agent_id=AGENT_ID)``) while
the live dispatch births them under a SYNTHETIC dispatch key with the agent's
NAME (``dispatch.{session}.{agent-name}.{task}``, ``agent_id='developer'``).
Fixturing the two identity spaces as one made every clause pass while nothing
converged in production -- zero rows were ever reaped, and every born row stayed
in 'DISPATCHED'. So every born row here is stamped exactly as the hook adapter
stamps it, and the agent's own contract lands on a DIFFERENT contract_id.

Clauses:
  1. a NORMAL turn (agent finalized, no crash) leaves NO 'DISPATCHED' row: the
     dispatch row is closed as SUPERSEDED, pointing at the contract row.
  2. a turn that never finalized is REAPED to a degraded, non-COMPLETE verdict.
  3. closure adds NO row -- the turn still has exactly the two rows the dispatch
     and the finalize created, and the COMPLETE is counted exactly once.
  4. the flags are separated: a superseded dispatch row is NOT marked degraded
     (only a genuinely unfinalized one is).
  5. attribution: a contract finalized through the CLI with --session-id /
     --plan-task-id is findable by that coordinate pair.
  6. a flagless finalize never CLEARS an attribution already on the row.

Fresh DB; the writer materializes the real schema. Drafts live under an isolated
GAIA_DATA_DIR. The dispatch id is cleared so the write-guard allows the hook path.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = str(_REPO_ROOT / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from gaia.contract.drafts import mint_draft_id, save_draft
from gaia.store.writer import (
    agent_contract_handoff_state,
    finalize_agent_contract_handoff,
    insert_dispatched_handoff,
)
from modules.agents.handoff_persister import persist_handoff

WORKSPACE = "me"

# The two identity spaces, kept deliberately distinct in every fixture below.
MINTED_AGENT_ID = "a1234abcd"      # what the agent mints for its own draft
DISPATCH_AGENT_NAME = "developer"  # what the DISPATCH stamps on the born row

PLAN_ID = 34
TASK_ID = 42


@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    yield


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "gaia.db"


def _envelope(state: str = "COMPLETE") -> dict:
    return {
        "agent_status": {
            "agent_state": state,
            "agent_id": MINTED_AGENT_ID,
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": {
            "patterns_checked": [], "files_checked": [], "commands_run": [],
            "key_outputs": [], "verbatim_outputs": [], "cross_layer_impacts": [],
            "open_gaps": [],
            "verification": {"method": "test", "checks": ["closure"],
                             "result": "pass", "details": "ok"},
        },
        "consolidation_report": None,
        "approval_request": None,
    }


def _task_info(db_path: Path) -> dict:
    """The SubagentStop task_info shape: harness agent NAME under 'agent'."""
    return {
        "agent_id": MINTED_AGENT_ID,
        "agent": DISPATCH_AGENT_NAME,
        "workspace": WORKSPACE,
        "db_path": str(db_path),
    }


def _seed_binding_targets(db_path: Path) -> None:
    """Materialize the schema + seed the born-at-dispatch binding FK targets."""
    finalize_agent_contract_handoff(
        contract_id="seed.parent", agent_id=MINTED_AGENT_ID, workspace=WORKSPACE,
        agent_state="COMPLETE", raw_handoff_json=json.dumps(_envelope("COMPLETE")),
        db_path=db_path,
    )
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO briefs (id, workspace, name, status) VALUES (?, ?, ?, ?)",
            (1, WORKSPACE, "contract-traceability", "in-progress"),
        )
        con.execute("INSERT INTO plans (id, brief_id, status) VALUES (?, ?, ?)",
                    (PLAN_ID, 1, "active"))
        con.execute(
            "INSERT INTO tasks (id, plan_id, order_num, goal, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (TASK_ID, PLAN_ID, 5, "closure", "pending"),
        )
        con.commit()
    finally:
        con.close()


def _birth_as_production_does(db_path: Path, session_id: str) -> dict:
    """Birth the nascent row EXACTLY as hooks/adapters/claude_code.py does.

    The synthetic dispatch key and the agent NAME in agent_id are the whole
    point: any fixture that births under the minted draft id instead makes the
    two key spaces coincide and cannot reproduce the defect.
    """
    contract_id = f"dispatch.{session_id}.{DISPATCH_AGENT_NAME}.{TASK_ID}"
    born = insert_dispatched_handoff(
        contract_id=contract_id,
        agent_id=DISPATCH_AGENT_NAME,
        workspace=WORKSPACE,
        plan_task_id=TASK_ID,
        plan_id=PLAN_ID,
        kind="task_execution",
        session_id=session_id,
        db_path=db_path,
    )
    born["contract_id"] = contract_id
    return born


def _row(db_path: Path, contract_id: str):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT id, contract_id, agent_id, session_id, plan_task_id, plan_id, "
            "kind, agent_state, raw_handoff_json FROM agent_contract_handoffs "
            "WHERE contract_id = ?",
            (contract_id,),
        ).fetchone()
    finally:
        con.close()


def _session_rows(db_path: Path, session_id: str):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT id, contract_id, agent_state, plan_task_id, raw_handoff_json "
            "FROM agent_contract_handoffs WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    finally:
        con.close()


def _dispatched_count(db_path: Path, session_id: str) -> int:
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(
            "SELECT count(*) FROM agent_contract_handoffs "
            "WHERE agent_state = 'DISPATCHED' AND session_id = ?",
            (session_id,),
        ).fetchone()[0]
    finally:
        con.close()


def _flags(raw_handoff_json: str) -> dict:
    return json.loads(raw_handoff_json)


def _load_contract_cli():
    """Import bin/cli/contract.py by path (it is a CLI plugin, not a package)."""
    spec = importlib.util.spec_from_file_location(
        "gaia_contract_cli", str(_REPO_ROOT / "bin" / "cli" / "contract.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Clause 1 + 3 + 4 -- a NORMAL turn leaves no DISPATCHED row, adds no row,
# counts its COMPLETE once, and its dispatch row is superseded (not degraded)
# ---------------------------------------------------------------------------

def test_normal_finalize_leaves_no_dispatched_row(db):
    session_id = "sess-normal"
    _seed_binding_targets(db)
    born = _birth_as_production_does(db, session_id)

    # The agent builds and finalizes its OWN draft -- a different contract_id,
    # in a different key space. This is the healthy path, no crash involved.
    draft_id = mint_draft_id(MINTED_AGENT_ID)
    envelope = _envelope("COMPLETE")
    save_draft(draft_id, envelope)
    finalize_agent_contract_handoff(
        contract_id=draft_id, agent_id=MINTED_AGENT_ID, workspace=WORKSPACE,
        agent_state="COMPLETE", raw_handoff_json=json.dumps(envelope),
        session_id=session_id, db_path=db,
    )
    assert agent_contract_handoff_state(born["contract_id"], db_path=db) == "DISPATCHED"

    # SubagentStop fires.
    persist_handoff(
        parsed_contract=envelope, agent_output="",
        task_info=_task_info(db), session_id=session_id,
    )

    # Clause 1: nothing is left in 'DISPATCHED' for this turn.
    assert _dispatched_count(db, session_id) == 0, (
        "a normal finalize must leave no DISPATCHED row -- the born row is "
        "closed by the stop hook, not by finalize (two key spaces)"
    )

    born_row = _row(db, born["contract_id"])
    assert born_row["id"] == born["handoff_id"], "same physical row, converged"
    born_flags = _flags(born_row["raw_handoff_json"])

    # Clause 1: the pointer makes the chain walkable across the key spaces.
    assert born_flags.get("superseded_by_contract_id") == draft_id
    # Clause 4: separated flags -- provenance pointer WITHOUT a quality verdict.
    assert "degraded" not in born_flags, (
        "a superseded dispatch row must not be marked degraded: nothing "
        "degraded, and it would drown the population that flag identifies"
    )
    assert "reaped" not in born_flags

    # Clause 3: the COMPLETE is counted exactly ONCE, on the contract row.
    rows = _session_rows(db, session_id)
    assert len(rows) == 2, "closure adds no row: dispatch scaffold + contract"
    completes = [r for r in rows if r["agent_state"] == "COMPLETE"]
    assert len(completes) == 1
    assert completes[0]["contract_id"] == draft_id
    assert not _flags(completes[0]["raw_handoff_json"]).get("degraded")


# ---------------------------------------------------------------------------
# Clause 2 -- a turn that never finalized is reaped with a degraded verdict
# ---------------------------------------------------------------------------

def test_turn_without_finalize_is_reaped_degraded(db):
    session_id = "sess-crashed"
    _seed_binding_targets(db)
    born = _birth_as_production_does(db, session_id)

    # No draft, no fence, no finalize -- the turn was cut off.
    persist_handoff(
        parsed_contract=None, agent_output="truncated mid-sentence",
        task_info=_task_info(db), session_id=session_id,
    )

    assert _dispatched_count(db, session_id) == 0, (
        "an unfinalized turn's born row must be reaped, not left orphaned"
    )
    born_row = _row(db, born["contract_id"])
    assert born_row["id"] == born["handoff_id"]
    assert born_row["agent_state"] != "COMPLETE", (
        "a turn that never finalized never truly completed"
    )
    born_flags = _flags(born_row["raw_handoff_json"])
    assert born_flags.get("degraded") is True
    assert born_flags.get("reaped") is True
    # The binding survives the reap, so the orphan stays attributable.
    assert born_row["plan_task_id"] == TASK_ID
    assert born_row["plan_id"] == PLAN_ID
    assert born_row["kind"] == "task_execution"


# ---------------------------------------------------------------------------
# Clause 5 -- attribution: findable by (session_id, plan_task_id) after a CLI
# finalize that was GIVEN those coordinates as explicit flags
# ---------------------------------------------------------------------------

def test_cli_finalized_contract_findable_by_session_and_plan_task(db, monkeypatch):
    # The CLI resolves its own DB path through gaia.paths, so point the whole
    # substrate at one tmp dir instead of passing db_path (which the CLI, being
    # harness-agnostic, deliberately has no flag for).
    data_dir = db.parent / "cli_data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    from gaia.paths import db_path as _resolve_db_path
    cli_db = _resolve_db_path()
    cli_db.parent.mkdir(parents=True, exist_ok=True)
    _seed_binding_targets(cli_db)

    cli = _load_contract_cli()
    session_id = "sess-attributed"
    draft_id = mint_draft_id(MINTED_AGENT_ID)
    save_draft(draft_id, _envelope("NEEDS_VERIFICATION"))

    parser = cli._build_standalone_parser()
    args = parser.parse_args([
        "finalize",
        "--draft-id", draft_id,
        "--workspace", WORKSPACE,
        "--session-id", session_id,
        "--plan-task-id", str(TASK_ID),
        "--json",
    ])
    assert args.func(args) == 0

    con = sqlite3.connect(str(cli_db))
    con.row_factory = sqlite3.Row
    try:
        found = con.execute(
            "SELECT contract_id, agent_state FROM agent_contract_handoffs "
            "WHERE session_id = ? AND plan_task_id = ?",
            (session_id, TASK_ID),
        ).fetchall()
    finally:
        con.close()

    assert len(found) == 1, (
        "a finalized contract must be findable by (session_id, plan_task_id) -- "
        "without the explicit flags both columns land NULL and the turn is "
        "unattributable by query"
    )
    assert found[0]["contract_id"] == draft_id


# ---------------------------------------------------------------------------
# Clause 6 -- a flagless finalize records nothing and CLEARS nothing
# ---------------------------------------------------------------------------

def test_flagless_finalize_never_clears_existing_attribution(db):
    session_id = "sess-coalesce"
    _seed_binding_targets(db)
    born = _birth_as_production_does(db, session_id)

    # Converge the born row WITHOUT carrying either coordinate -- exactly what a
    # CLI finalize that was given no flags does.
    finalize_agent_contract_handoff(
        contract_id=born["contract_id"], agent_id=MINTED_AGENT_ID,
        workspace=WORKSPACE, agent_state="IN_PROGRESS",
        raw_handoff_json=json.dumps({"flagless": True}), db_path=db,
    )

    row = _row(db, born["contract_id"])
    assert row["plan_task_id"] == TASK_ID, (
        "a finalize that carries no plan_task_id must not NULL the binding the "
        "dispatch stamped"
    )
    assert row["session_id"] == session_id, (
        "likewise for session_id -- absent means 'record nothing', not 'clear it'"
    )
