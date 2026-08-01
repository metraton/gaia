"""
Dispatch-side adoption seam: the born row carries an identity an agent can adopt.

The born-at-dispatch row used to be minted under a SYNTHETIC key --
``dispatch.{session}.{agent}.{key}`` for ``contract_id``, and the agent NAME for
``agent_id``. Both are unusable by the contract substrate: ``_agent_of`` splits a
draft id on its first dot and would recover the literal ``"dispatch"``, and
``AGENT_ID_PATTERN_TEXT`` rejects a name like ``gaia-system`` outright. The row
existed, but nothing could converge onto it.

These tests pin the seam the rest of the plan consumes:

  * the identity minted at dispatch is REAL -- ``agent_id`` satisfies the
    validator, ``contract_id`` has the draft-id shape ``_agent_of`` understands;
  * the nascent row is BORN under exactly that identity;
  * that SAME ``contract_id`` reaches the subagent through the context the hook
    bridge injects at SubagentStart;
  * two concurrent dispatches never hand one identity to two agents, nor swap
    them -- the binding corruption the row exists to prevent.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = str(_REPO_ROOT / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from gaia.contract.drafts import _agent_of, mint_agent_id
from gaia.contract.validator import AGENT_ID_PATTERN_TEXT
from modules.agents.dispatch_identity import (
    IDENTITY_BLOCK_HEADING,
    mint_dispatch_identity,
    render_identity_block,
)

_AGENT_ID_RE = re.compile(AGENT_ID_PATTERN_TEXT)

CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"

WORKSPACE = "me"
PLAN_ID = 47
TASK_PENDING = 196


@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    """Point Gaia's data substrate (DB + drafts) at a throwaway directory."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    monkeypatch.setenv("GAIA_WORKSPACE", WORKSPACE)
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    yield


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "gaia-data" / "gaia.db"


def _seed_plan_task(db_path: Path) -> None:
    """Seed briefs -> plans -> tasks so a task_execution binding resolves."""
    from gaia.store.reader import _connect

    con = _connect(db_path)
    try:
        con.execute(
            "INSERT INTO workspaces (name) VALUES (?) ON CONFLICT DO NOTHING",
            (WORKSPACE,),
        )
        con.execute(
            "INSERT INTO briefs (id, workspace, name, status) VALUES (?, ?, ?, ?)",
            (1, WORKSPACE, "contrato-adoptado", "in-progress"),
        )
        con.execute(
            "INSERT INTO plans (id, brief_id, status) VALUES (?, ?, ?)",
            (PLAN_ID, 1, "active"),
        )
        con.execute(
            "INSERT INTO tasks (id, plan_id, order_num, goal, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (TASK_PENDING, PLAN_ID, 1, "adoption seam", "pending"),
        )
        con.commit()
    finally:
        con.close()


def _row_for(db_path: Path, contract_id: str):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT contract_id, agent_id, agent_state, plan_task_id "
            "FROM agent_contract_handoffs WHERE contract_id = ?",
            (contract_id,),
        ).fetchone()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# The identity itself: real, adoptable, unique
# ---------------------------------------------------------------------------

def test_minted_agent_id_satisfies_the_contract_validator():
    identity = mint_dispatch_identity()
    assert _AGENT_ID_RE.match(identity["agent_id"]), (
        f"agent_id {identity['agent_id']!r} must match {AGENT_ID_PATTERN_TEXT} "
        "or no contract carrying it can ever validate"
    )


def test_minted_contract_id_has_the_draft_id_shape():
    """``_agent_of`` must recover the SAME, valid agent handle from the id."""
    identity = mint_dispatch_identity()
    recovered = _agent_of(identity["contract_id"])
    assert recovered == identity["agent_id"]
    assert _AGENT_ID_RE.match(recovered)


def test_the_legacy_synthetic_key_is_what_this_replaces():
    """Anchors WHY the format changed: the old key fails both checks."""
    legacy = "dispatch.sess-1.gaia-system.196"
    assert _agent_of(legacy) == "dispatch"
    assert not _AGENT_ID_RE.match(_agent_of(legacy))
    assert not _AGENT_ID_RE.match("gaia-system")


def test_identity_is_minted_not_derived_so_it_never_repeats():
    """Two dispatches of the same agent + task must NOT share an identity.

    A derived key (session + agent + task) would give both the same id and both
    agents would converge onto one row. Uniqueness is the property AC-1 rests on.
    """
    minted = [mint_dispatch_identity()["contract_id"] for _ in range(200)]
    assert len(set(minted)) == len(minted)


def test_cli_and_dispatch_mint_the_same_handle_shape():
    """The CLI's own mint and the dispatch mint share one SSOT."""
    assert _AGENT_ID_RE.match(mint_agent_id())


def test_identity_block_carries_both_halves_verbatim():
    block = render_identity_block("a0123456789abcdef", "a0123456789abcdef.beef")
    assert IDENTITY_BLOCK_HEADING in block
    assert "a0123456789abcdef.beef" in block
    assert "--draft-id a0123456789abcdef.beef" in block
    assert "--agent-id a0123456789abcdef" in block


def test_identity_block_is_absent_rather_than_malformed():
    assert render_identity_block("", "a0123456789abcdef.beef") is None
    assert render_identity_block("a0123456789abcdef", "") is None


# ---------------------------------------------------------------------------
# The row is BORN under that identity
# ---------------------------------------------------------------------------

def test_dispatch_births_row_under_the_minted_identity(db_path):
    from adapters.claude_code import ClaudeCodeAdapter

    _seed_plan_task(db_path)
    identity = ClaudeCodeAdapter._maybe_birth_dispatched_row(
        {"prompt": f"Ejecuta plan_id={PLAN_ID} task_id={TASK_PENDING}"},
        "gaia-system",
        "sess-birth",
    )

    assert identity is not None, "a resolvable binding must birth a row"
    row = _row_for(db_path, identity["contract_id"])
    assert row is not None, "the row must exist under the MINTED contract_id"
    assert row["agent_id"] == identity["agent_id"]
    assert _AGENT_ID_RE.match(row["agent_id"]), (
        "the row's agent_id column must hold a real handle, not the agent NAME"
    )
    assert row["agent_state"] == "DISPATCHED"
    assert row["plan_task_id"] == TASK_PENDING


def test_unresolvable_binding_degrades_to_an_unbound_row(db_path):
    """Plan 49 task 1 (D1, gate 499): an unresolved task_id= no longer yields
    no row at all -- it DEGRADES. The row still births (plan_task_id NULL in
    the column, the rejection recorded inside the envelope) and the identity
    still comes back, so it still reaches the subagent -- see
    tests/hooks/test_dispatch_binding_rejected_event.py for the full
    degrade-vs-drop coverage. What this file still pins: the row born this
    way carries NO resolvable plan_task_id, so it remains UNBOUND for the
    blind-verification gate, same governance posture as "no row at all"."""
    from adapters.claude_code import ClaudeCodeAdapter
    from gaia.store.reader import _connect

    _seed_plan_task(db_path)
    identity = ClaudeCodeAdapter._maybe_birth_dispatched_row(
        {"prompt": f"Ejecuta plan_id={PLAN_ID} task_id=999999"},
        "gaia-system",
        "sess-unresolvable",
    )
    assert identity is not None

    con = _connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT plan_task_id FROM agent_contract_handoffs WHERE contract_id = ?",
            (identity["contract_id"],),
        ).fetchone()
    finally:
        con.close()
    assert row["plan_task_id"] is None, "unresolved -- stays UNBOUND for the blind-verification gate"


# ---------------------------------------------------------------------------
# The identity REACHES the subagent
# ---------------------------------------------------------------------------

def _dispatch(adapter, session_id, agent_type, description, prompt):
    """Run the real PreToolUse:Task path and return the born contract_id."""
    born = {}
    original = adapter._maybe_birth_dispatched_row

    def _capture(parameters, agent_name, sid):
        result = original(parameters, agent_name, sid)
        if result:
            born.update(result)
        return result

    adapter._maybe_birth_dispatched_row = _capture
    try:
        adapter._adapt_task(
            "Task",
            {
                "subagent_type": agent_type,
                "description": description,
                "prompt": prompt,
            },
            [agent_type],
            Path(_HOOKS_DIR),
            session_id,
        )
    finally:
        adapter._maybe_birth_dispatched_row = original
    return born


def test_injected_subagent_context_carries_the_born_contract_id(
    db_path, tmp_path, monkeypatch,
):
    from adapters.claude_code import ClaudeCodeAdapter

    monkeypatch.setattr(
        ClaudeCodeAdapter, "CONTEXT_CACHE_DIR", tmp_path / "ctx-cache",
    )
    _seed_plan_task(db_path)
    adapter = ClaudeCodeAdapter()

    born = _dispatch(
        adapter, "sess-inject", "gaia-system", "adopt the seam",
        f"Ejecuta plan_id={PLAN_ID} task_id={TASK_PENDING}",
    )
    assert born, "the dispatch must have birthed a row to inject"

    result = adapter.adapt_subagent_start({
        "session_id": "sess-inject",
        "agent_type": "gaia-system",
        "task_description": "adopt the seam",
    })

    assert result.context_injected
    assert born["contract_id"] in result.additional_context, (
        "the subagent's INPUT must name the contract_id its row was born under"
    )
    assert born["agent_id"] in result.additional_context


def test_concurrent_same_type_dispatches_do_not_cross_identities(
    db_path, tmp_path, monkeypatch,
):
    """R1: two live dispatches of ONE agent type must not swap injected ids.

    Both rows are born before either subagent starts -- the interleaving that
    made a recency-only cache read hand agent A the identity minted for B.
    """
    from adapters.claude_code import ClaudeCodeAdapter

    monkeypatch.setattr(
        ClaudeCodeAdapter, "CONTEXT_CACHE_DIR", tmp_path / "ctx-cache",
    )
    _seed_plan_task(db_path)
    adapter = ClaudeCodeAdapter()

    first = _dispatch(
        adapter, "sess-conc", "gaia-system", "first increment",
        f"Ejecuta plan_id={PLAN_ID} task_id={TASK_PENDING}",
    )
    second = _dispatch(
        adapter, "sess-conc", "gaia-system", "second increment",
        f"Ejecuta plan_id={PLAN_ID} task_id={TASK_PENDING}",
    )
    assert first["contract_id"] != second["contract_id"]

    ctx_first = adapter.adapt_subagent_start({
        "session_id": "sess-conc",
        "agent_type": "gaia-system",
        "task_description": "first increment",
    }).additional_context
    ctx_second = adapter.adapt_subagent_start({
        "session_id": "sess-conc",
        "agent_type": "gaia-system",
        "task_description": "second increment",
    }).additional_context

    assert first["contract_id"] in ctx_first
    assert second["contract_id"] not in ctx_first
    assert second["contract_id"] in ctx_second
    assert first["contract_id"] not in ctx_second


def test_dispatches_of_different_agent_types_do_not_cross_identities(
    db_path, tmp_path, monkeypatch,
):
    """The cache read must key on agent_type, not on recency alone."""
    from adapters.claude_code import ClaudeCodeAdapter

    monkeypatch.setattr(
        ClaudeCodeAdapter, "CONTEXT_CACHE_DIR", tmp_path / "ctx-cache",
    )
    _seed_plan_task(db_path)
    adapter = ClaudeCodeAdapter()

    sysagent = _dispatch(
        adapter, "sess-types", "gaia-system", "meta work",
        f"Ejecuta plan_id={PLAN_ID} task_id={TASK_PENDING}",
    )
    devagent = _dispatch(
        adapter, "sess-types", "developer", "app work",
        f"Ejecuta plan_id={PLAN_ID} task_id={TASK_PENDING}",
    )

    ctx_sys = adapter.adapt_subagent_start({
        "session_id": "sess-types",
        "agent_type": "gaia-system",
        "task_description": "meta work",
    }).additional_context

    assert sysagent["contract_id"] in ctx_sys
    assert devagent["contract_id"] not in ctx_sys


# ---------------------------------------------------------------------------
# END TO END: the adopted identity collapses dispatch and finalize to ONE row
#
# The tests above pin the two halves separately (a real identity is minted; it
# reaches the subagent). These run the whole arc -- birth, adoption through the
# REAL cli, finalize, then the SubagentStop persister -- and assert the property
# the arc exists for: one row per dispatch, its binding intact, no orphan added.
# ---------------------------------------------------------------------------

_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)


def _cli(args: list) -> subprocess.CompletedProcess:
    """Drive the real contract CLI against the test's isolated substrate.

    ``os.environ`` already carries the GAIA_DATA_DIR the autouse fixture set, so
    the subprocess resolves the SAME db and drafts directory as the in-process
    writer calls around it.
    """
    return subprocess.run(
        [sys.executable, str(CONTRACT_CLI), *args],
        capture_output=True, text=True, env=dict(os.environ), timeout=30,
    )


def _seed_parent_handoff(db_path: Path) -> int:
    """A real ``agent_contract_handoffs`` row a dispatch can bind a parent to."""
    from gaia.store.writer import finalize_agent_contract_handoff

    outcome = finalize_agent_contract_handoff(
        contract_id="a00000000000000ff.parent-seed",
        agent_id="a00000000000000ff",
        workspace=WORKSPACE,
        agent_state="COMPLETE",
        raw_handoff_json=json.dumps({"seed": True}),
        db_path=db_path,
    )
    return outcome["handoff_id"]


def _all_rows(db_path: Path) -> list:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(
            "SELECT id, contract_id, agent_id, agent_state, session_id, "
            "plan_task_id, plan_id, parent_handoff_id, kind, cut_reason, "
            "raw_handoff_json "
            "FROM agent_contract_handoffs ORDER BY id"
        )]
    finally:
        con.close()


def _rows_for(db_path: Path, contract_id: str) -> list:
    return [r for r in _all_rows(db_path) if r["contract_id"] == contract_id]


def _adopt(identity: dict) -> None:
    """Run the adoption the injected block instructs, through the real CLI."""
    init = _cli([
        "init",
        "--agent-id", identity["agent_id"],
        "--draft-id", identity["contract_id"],
        "--json",
    ])
    assert init.returncode == 0, init.stderr
    assert json.loads(init.stdout)["draft_id"] == identity["contract_id"], (
        "adoption means the draft is created AT the born id, not beside it"
    )
    patch = json.dumps({"evidence_report": {k: [] for k in _EVIDENCE_KEYS}})
    assert _cli([
        "fill", "--draft-id", identity["contract_id"], "--json", patch,
    ]).returncode == 0


def _finalize(identity: dict, session_id: str, state: str = "NEEDS_VERIFICATION",
              plan_task_id: "int | None" = None) -> subprocess.CompletedProcess:
    assert _cli([
        "set", "--draft-id", identity["contract_id"],
        "agent_status.next_action", "hand off to the verifier",
    ]).returncode == 0
    assert _cli([
        "set", "--draft-id", identity["contract_id"],
        "agent_status.agent_state", state,
    ]).returncode == 0
    args = ["finalize", "--draft-id", identity["contract_id"],
            "--session-id", session_id, "--json"]
    if plan_task_id is not None:
        args[-1:] = ["--plan-task-id", str(plan_task_id), "--json"]
    return _cli(args)


def _task_info(db_path: Path, agent: str = "gaia-system") -> dict:
    """The SubagentStop view of a turn: the agent NAME plus a harness agent_id
    that belongs to neither the draft nor the born row's identity space."""
    return {
        "agent": agent,
        "agent_id": "a00000000000000aa",
        "workspace": WORKSPACE,
        "db_path": str(db_path),
    }


def _finalized_envelope(identity: dict) -> dict:
    return {
        "agent_status": {
            "agent_state": "NEEDS_VERIFICATION",
            "agent_id": identity["agent_id"],
            "pending_steps": [],
            "next_action": "hand off to the verifier",
        },
        "evidence_report": {k: [] for k in _EVIDENCE_KEYS},
        "consolidation_report": None,
        "approval_request": None,
    }


def _producer_prompt(parent_handoff_id: int) -> str:
    """A producer dispatch that names BOTH binding coordinates.

    ``plan_task_id`` is what a task_execution dispatch always carries;
    ``parent_handoff_id`` is extracted whenever the prompt names it (it is
    REQUIRED only for a verifier turn, optional and existence-checked otherwise),
    so a producer dispatched as a continuation of a known handoff carries both.
    """
    return (
        f"Ejecuta plan_id={PLAN_ID} task_id={TASK_PENDING} "
        f"parent_handoff_id={parent_handoff_id}"
    )


def test_adopted_finalize_converges_the_born_row_and_adds_none(db_path):
    """One dispatch -> exactly ONE row, binding intact, nothing orphaned."""
    from adapters.claude_code import ClaudeCodeAdapter
    from modules.agents.handoff_persister import persist_handoff

    _seed_plan_task(db_path)
    parent_id = _seed_parent_handoff(db_path)
    before = len(_all_rows(db_path))

    identity = ClaudeCodeAdapter._maybe_birth_dispatched_row(
        {"prompt": _producer_prompt(parent_id)}, "gaia-system", "sess-e2e",
    )
    assert identity is not None
    assert len(_all_rows(db_path)) == before + 1, "birth adds exactly one row"

    _adopt(identity)
    fin = _finalize(identity, "sess-e2e", plan_task_id=TASK_PENDING)
    assert fin.returncode == 0, f"{fin.stdout} {fin.stderr}"
    assert json.loads(fin.stdout)["draft_id"] == identity["contract_id"]

    persist_handoff(
        parsed_contract=_finalized_envelope(identity),
        agent_output="done",
        task_info=_task_info(db_path),
        session_id="sess-e2e",
        plan_task_id=TASK_PENDING,
    )

    rows = _rows_for(db_path, identity["contract_id"])
    assert len(rows) == 1, "the born row and the finalized row are ONE row"
    row = rows[0]
    assert row["agent_state"] == "NEEDS_VERIFICATION"
    assert row["agent_id"] == identity["agent_id"], (
        "the closure must not restamp the row with the agent NAME"
    )
    assert row["plan_task_id"] == TASK_PENDING
    assert row["parent_handoff_id"] == parent_id
    assert row["plan_id"] == PLAN_ID
    assert row["kind"] == "task_execution"

    envelope = json.loads(row["raw_handoff_json"])
    assert not envelope.get("degraded"), "a turn that finalized is not degraded"
    assert not envelope.get("reaped"), "a converged row must not be reaped"

    assert len(_all_rows(db_path)) == before + 1, (
        "the whole arc added exactly the born row -- no second, unbound row"
    )


def test_adopted_turn_still_reads_as_plan_task_bound_at_subagent_stop(db_path):
    """The gate that forbids a bound producer's self-COMPLETE must still fire.

    An adopted turn CONVERGES its born row at finalize, so the row is no longer
    'DISPATCHED' when SubagentStop resolves the binding. A DISPATCHED-only lookup
    reports None -- i.e. UNBOUND -- and the blind-verification gate silently stops
    applying to exactly the turns it exists for.
    """
    from adapters.claude_code import ClaudeCodeAdapter

    _seed_plan_task(db_path)
    parent_id = _seed_parent_handoff(db_path)

    identity = ClaudeCodeAdapter._maybe_birth_dispatched_row(
        {"prompt": _producer_prompt(parent_id)}, "gaia-system", "sess-bound",
    )
    _adopt(identity)
    assert _finalize(
        identity, "sess-bound", plan_task_id=TASK_PENDING,
    ).returncode == 0

    resolved = ClaudeCodeAdapter._resolve_dispatch_row(
        session_id="sess-bound",
        agent_type="gaia-system",
        task_info=_task_info(db_path),
        parsed_contract=_finalized_envelope(identity),
        db_path=db_path,
    )
    assert resolved is not None, "an adopted turn must not read as unbound"
    assert resolved["plan_task_id"] == TASK_PENDING
    assert resolved["contract_id"] == identity["contract_id"]


def test_bound_producer_cannot_self_complete_under_the_adopted_id(db_path):
    """The CLI anti-leak recovers the binding BY the adopted contract_id."""
    from adapters.claude_code import ClaudeCodeAdapter

    _seed_plan_task(db_path)
    parent_id = _seed_parent_handoff(db_path)
    identity = ClaudeCodeAdapter._maybe_birth_dispatched_row(
        {"prompt": _producer_prompt(parent_id)}, "gaia-system", "sess-leak",
    )
    _adopt(identity)
    assert _cli([
        "fill", "--draft-id", identity["contract_id"], "--json",
        json.dumps({"evidence_report": {
            "verification": {"method": "pytest", "result": "pass", "details": "x"},
        }}),
    ]).returncode == 0
    assert _cli([
        "set", "--draft-id", identity["contract_id"],
        "agent_status.next_action", "done",
    ]).returncode == 0
    assert _cli([
        "set", "--draft-id", identity["contract_id"],
        "agent_status.agent_state", "COMPLETE",
    ]).returncode == 0

    fin = _cli(["finalize", "--draft-id", identity["contract_id"], "--json"])
    assert fin.returncode == 1, "a plan-task-bound producer may not self-COMPLETE"
    payload = json.loads(fin.stdout)
    assert payload["reason"] == "blind_verification_required"
    assert payload["plan_task_id"] == TASK_PENDING


def test_concurrent_same_type_dispatches_converge_their_own_rows(db_path):
    """Two live dispatches of ONE agent type: two rows, neither touching the other.

    The concurrency case the identity was minted to survive. Both rows are born
    before either turn finalizes, so every closure path runs while the sibling
    row is still open -- the interleaving where a recency-keyed or name-keyed
    closure reaps a row that belongs to an agent that is still working.
    """
    from adapters.claude_code import ClaudeCodeAdapter
    from modules.agents.handoff_persister import persist_handoff

    _seed_plan_task(db_path)
    parent_id = _seed_parent_handoff(db_path)
    before = len(_all_rows(db_path))

    first = ClaudeCodeAdapter._maybe_birth_dispatched_row(
        {"prompt": _producer_prompt(parent_id)}, "gaia-system", "sess-conc-e2e",
    )
    second = ClaudeCodeAdapter._maybe_birth_dispatched_row(
        {"prompt": _producer_prompt(parent_id)}, "gaia-system", "sess-conc-e2e",
    )
    assert first["contract_id"] != second["contract_id"]

    _adopt(first)
    _adopt(second)

    # The FIRST turn ends while the second is still running.
    assert _finalize(
        first, "sess-conc-e2e", plan_task_id=TASK_PENDING,
    ).returncode == 0
    persist_handoff(
        parsed_contract=_finalized_envelope(first),
        agent_output="first done",
        task_info=_task_info(db_path),
        session_id="sess-conc-e2e",
        plan_task_id=TASK_PENDING,
    )

    still_open = _rows_for(db_path, second["contract_id"])[0]
    assert still_open["agent_state"] == "DISPATCHED", (
        "the sibling's row must survive the first turn's closure untouched"
    )
    assert not json.loads(still_open["raw_handoff_json"]).get("reaped")

    # Now the second turn ends.
    assert _finalize(
        second, "sess-conc-e2e", plan_task_id=TASK_PENDING,
    ).returncode == 0
    persist_handoff(
        parsed_contract=_finalized_envelope(second),
        agent_output="second done",
        task_info=_task_info(db_path),
        session_id="sess-conc-e2e",
        plan_task_id=TASK_PENDING,
    )

    for identity in (first, second):
        rows = _rows_for(db_path, identity["contract_id"])
        assert len(rows) == 1
        assert rows[0]["agent_state"] == "NEEDS_VERIFICATION"
        assert rows[0]["agent_id"] == identity["agent_id"]
        assert rows[0]["plan_task_id"] == TASK_PENDING
        assert rows[0]["parent_handoff_id"] == parent_id
        assert not json.loads(rows[0]["raw_handoff_json"]).get("degraded")

    assert len(_all_rows(db_path)) == before + 2, (
        "two dispatches, two rows -- no orphan and no duplicate"
    )


def test_same_type_same_description_still_crosses_injected_identities(
    db_path, tmp_path, monkeypatch,
):
    """R1 RESIDUAL, pinned as it actually stands -- NOT closed.

    Correlation at SubagentStart runs agent_type + task_description, then
    agent_type, then recency. Two concurrent dispatches of the SAME type with the
    SAME description are identical under every tier, so the newest cache entry
    wins for whichever subagent starts first: the first agent is handed the
    SECOND dispatch's identity. Closing this needs a correlation token in
    SubagentStart's own payload; no heuristic at this seam can recover it.

    What IS closed by the minting: the two rows are distinct and each keeps its
    own binding, so the failure is bounded to which agent adopts which row --
    never two agents converging onto ONE row.
    """
    from adapters.claude_code import ClaudeCodeAdapter

    monkeypatch.setattr(
        ClaudeCodeAdapter, "CONTEXT_CACHE_DIR", tmp_path / "ctx-cache-residual",
    )
    _seed_plan_task(db_path)
    adapter = ClaudeCodeAdapter()

    same_description = "same increment"
    first = _dispatch(
        adapter, "sess-residual", "gaia-system", same_description,
        f"Ejecuta plan_id={PLAN_ID} task_id={TASK_PENDING}",
    )
    second = _dispatch(
        adapter, "sess-residual", "gaia-system", same_description,
        f"Ejecuta plan_id={PLAN_ID} task_id={TASK_PENDING}",
    )
    assert first["contract_id"] != second["contract_id"], (
        "birth-side uniqueness holds even when the dispatches are indistinguishable"
    )

    ctx = adapter.adapt_subagent_start({
        "session_id": "sess-residual",
        "agent_type": "gaia-system",
        "task_description": same_description,
    }).additional_context

    assert second["contract_id"] in ctx
    assert first["contract_id"] not in ctx


def test_unadopted_turn_born_row_is_closed_through_the_birth_envelope_name(db_path):
    """A turn that mints its OWN id shares no identifier with its born row.

    Before the identity was minted at dispatch, the row carried the agent NAME in
    ``agent_id`` and the closure found it there. Now the name lives in the birth
    envelope instead, and that is the lane that keeps an unadopting turn's row
    from being stranded in 'DISPATCHED' forever.
    """
    from adapters.claude_code import ClaudeCodeAdapter
    from gaia.contract.drafts import mint_draft_id, save_draft
    from gaia.store.writer import finalize_agent_contract_handoff
    from modules.agents.handoff_persister import persist_handoff

    _seed_plan_task(db_path)
    born = ClaudeCodeAdapter._maybe_birth_dispatched_row(
        {"prompt": f"Ejecuta plan_id={PLAN_ID} task_id={TASK_PENDING}"},
        "gaia-system", "sess-unadopted",
    )

    own_agent_id = mint_agent_id()
    own_draft_id = mint_draft_id(own_agent_id)
    own_envelope = {
        "agent_status": {
            "agent_state": "NEEDS_VERIFICATION", "agent_id": own_agent_id,
            "pending_steps": [], "next_action": "verify",
        },
        "evidence_report": {k: [] for k in _EVIDENCE_KEYS},
        "consolidation_report": None, "approval_request": None,
    }
    save_draft(own_draft_id, own_envelope)
    finalize_agent_contract_handoff(
        contract_id=own_draft_id,
        agent_id=own_agent_id,
        workspace=WORKSPACE,
        agent_state="NEEDS_VERIFICATION",
        raw_handoff_json=json.dumps(own_envelope),
        session_id="sess-unadopted",
        db_path=db_path,
    )

    persist_handoff(
        parsed_contract=own_envelope,
        agent_output="finished under its own id",
        task_info=_task_info(db_path),
        session_id="sess-unadopted",
        plan_task_id=TASK_PENDING,
    )

    row = _rows_for(db_path, born["contract_id"])[0]
    assert row["agent_state"] != "DISPATCHED", "the born row must be closed"
    assert row["agent_id"] == born["agent_id"], (
        "closing must preserve the identity the row was born under"
    )
    envelope = json.loads(row["raw_handoff_json"])
    assert envelope.get("superseded_by_contract_id") == own_draft_id
    assert not envelope.get("reaped"), (
        "the turn DID record its own contract row -- superseded, never reaped"
    )
    assert not envelope.get("degraded")


def test_name_lane_declines_when_two_dispatches_share_the_name(db_path):
    """Ambiguity is declared, never guessed: a live sibling is not closed.

    With two unadopted born rows under one name in one session, the name lane
    cannot tell which belongs to the ending turn. Closing the most recent would
    reap a row whose agent is still running, so it closes NEITHER and both stay
    'DISPATCHED' for a closure path that can distinguish them.
    """
    from adapters.claude_code import ClaudeCodeAdapter
    from gaia.store.writer import find_dispatched_row_by_agent_name
    from modules.agents.handoff_persister import persist_handoff

    _seed_plan_task(db_path)
    prompt = f"Ejecuta plan_id={PLAN_ID} task_id={TASK_PENDING}"
    first = ClaudeCodeAdapter._maybe_birth_dispatched_row(
        {"prompt": prompt}, "gaia-system", "sess-ambiguous",
    )
    second = ClaudeCodeAdapter._maybe_birth_dispatched_row(
        {"prompt": prompt}, "gaia-system", "sess-ambiguous",
    )

    assert find_dispatched_row_by_agent_name(
        "sess-ambiguous", "gaia-system", db_path=db_path,
    ) is None

    persist_handoff(
        parsed_contract=None,
        agent_output="cut with no fence and no draft",
        task_info=_task_info(db_path),
        session_id="sess-ambiguous",
        plan_task_id=TASK_PENDING,
    )

    for identity in (first, second):
        row = _rows_for(db_path, identity["contract_id"])[0]
        assert row["agent_state"] == "DISPATCHED", (
            "neither sibling may be closed on an ambiguous name match"
        )


def _verifier_prompt(parent_handoff_id: int) -> str:
    """A verifier dispatch: it names the producer handoff it verifies.

    The prompt still mentions the task, because the orchestrator's template
    does -- extraction is what must discard it for a verifier turn.
    """
    return (
        f"Verifica la TASK (task_id={TASK_PENDING}) del plan_id={PLAN_ID}, "
        f"parent_handoff_id={parent_handoff_id}"
    )


def _complete_envelope(identity: dict) -> dict:
    return {
        "agent_status": {
            "agent_state": "COMPLETE",
            "agent_id": identity["agent_id"],
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": {
            **{k: [] for k in _EVIDENCE_KEYS},
            "verification": {
                "method": "test", "result": "pass", "details": "suite green",
            },
        },
        "consolidation_report": None,
        "approval_request": None,
    }


def test_adopted_verifier_class_row_is_born_adopted_and_finalized_as_one_row(
    db_path,
):
    """The verifier class through the SAME arc the producer class already pins.

    The verifier binding is the mirror image of the producer's: it binds by
    ``parent_handoff_id`` and deliberately carries NO ``plan_task_id``, because
    a bound turn may not self-COMPLETE -- binding the verifier by task would
    re-arm the blind-verification gate against the one turn that exists to
    clear it. So this class is the only one that both adopts a born row AND
    finalizes it straight to COMPLETE, and it must still converge on ONE row.
    """
    from adapters.claude_code import ClaudeCodeAdapter
    from gaia.state import CUT_REASON_NEVER_FINALIZED

    _seed_plan_task(db_path)
    parent_id = _seed_parent_handoff(db_path)
    before = len(_all_rows(db_path))

    identity = ClaudeCodeAdapter._maybe_birth_dispatched_row(
        {"prompt": _verifier_prompt(parent_id)}, "gaia-verifier", "sess-verifier",
    )
    assert identity is not None, (
        "a verifier dispatch naming a resolvable parent must be born"
    )

    born = _rows_for(db_path, identity["contract_id"])
    assert len(born) == 1, "birth adds exactly one row"
    assert born[0]["agent_state"] == "DISPATCHED"
    assert born[0]["kind"] == "verifier"
    assert born[0]["parent_handoff_id"] == parent_id
    assert born[0]["plan_task_id"] is None, (
        "the verifier binds by parent, never by task -- see the gate deadlock"
    )
    assert born[0]["cut_reason"] == CUT_REASON_NEVER_FINALIZED, (
        "the cut mark is stamped AT BIRTH; finalize is what clears it"
    )

    _adopt(identity)
    assert _cli([
        "fill", "--draft-id", identity["contract_id"], "--json",
        json.dumps({"evidence_report": {
            "verification": {
                "method": "test", "result": "pass", "details": "suite green",
            },
        }}),
    ]).returncode == 0
    assert _cli([
        "set", "--draft-id", identity["contract_id"],
        "agent_status.next_action", "done",
    ]).returncode == 0
    assert _cli([
        "set", "--draft-id", identity["contract_id"],
        "agent_status.agent_state", "COMPLETE",
    ]).returncode == 0

    fin = _cli([
        "finalize", "--draft-id", identity["contract_id"],
        "--session-id", "sess-verifier", "--json",
    ])
    assert fin.returncode == 0, (
        f"an UNBOUND verifier turn may self-COMPLETE: {fin.stdout} {fin.stderr}"
    )

    rows = _rows_for(db_path, identity["contract_id"])
    assert len(rows) == 1, "the born row and the finalized row are ONE row"
    row = rows[0]
    assert row["agent_state"] == "COMPLETE"
    assert row["agent_id"] == identity["agent_id"]
    assert row["parent_handoff_id"] == parent_id
    assert row["plan_task_id"] is None
    assert row["kind"] == "verifier"
    assert row["cut_reason"] is None, "a clean close never carries the cut mark"
    assert len(_all_rows(db_path)) == before + 1, (
        "the whole verifier arc added exactly the born row"
    )


def test_subagent_stop_gate_rejects_a_bound_complete_from_the_resolved_row(
    db_path,
):
    """The blind-verification rejection, driven end-to-end from a born row.

    The sibling gate tests hand ``plan_task_id`` to ``evaluate_contract_gate``
    as a literal, which proves the gate's logic but not the seam SubagentStop
    actually runs: resolve the dispatch row first, then read the binding off
    it. This drives both halves in order, and pins the negative case on the
    verifier class -- the row whose NULL binding is what lets it promote.
    """
    from adapters.claude_code import ClaudeCodeAdapter, evaluate_contract_gate

    _seed_plan_task(db_path)
    parent_id = _seed_parent_handoff(db_path)

    producer = ClaudeCodeAdapter._maybe_birth_dispatched_row(
        {"prompt": _producer_prompt(parent_id)}, "gaia-system", "sess-gate-e2e",
    )
    _adopt(producer)
    assert _finalize(
        producer, "sess-gate-e2e", plan_task_id=TASK_PENDING,
    ).returncode == 0

    resolved = ClaudeCodeAdapter._resolve_dispatch_row(
        session_id="sess-gate-e2e",
        agent_type="gaia-system",
        task_info=_task_info(db_path),
        parsed_contract=_finalized_envelope(producer),
        db_path=db_path,
    )
    assert resolved is not None and resolved["plan_task_id"] == TASK_PENDING

    gate = evaluate_contract_gate(
        _complete_envelope(producer),
        agent_type="gaia-system",
        plan_task_id=resolved["plan_task_id"],
        ramp_enabled=True,
        db_path=str(db_path),
    )
    assert gate.rejected, (
        "a plan-task-bound producer's COMPLETE must be rejected at SubagentStop"
    )
    assert any(
        a["code"] == "BLIND_VERIFICATION_REQUIRED" for a in gate.anomalies
    ), gate.anomalies

    verifier = ClaudeCodeAdapter._maybe_birth_dispatched_row(
        {"prompt": _verifier_prompt(parent_id)}, "gaia-verifier", "sess-gate-e2e",
    )
    _adopt(verifier)
    verifier_resolved = ClaudeCodeAdapter._resolve_dispatch_row(
        session_id="sess-gate-e2e",
        agent_type="gaia-verifier",
        task_info=_task_info(db_path, agent="gaia-verifier"),
        parsed_contract=_complete_envelope(verifier),
        db_path=db_path,
    )
    assert verifier_resolved is not None
    assert verifier_resolved["plan_task_id"] is None

    verifier_gate = evaluate_contract_gate(
        _complete_envelope(verifier),
        agent_type="gaia-verifier",
        plan_task_id=verifier_resolved["plan_task_id"],
        ramp_enabled=True,
        db_path=str(db_path),
    )
    assert not verifier_gate.rejected, (
        f"an unbound verifier's COMPLETE is the promotion, not a violation: "
        f"{verifier_gate.rejection_reason}"
    )


def test_free_kind_row_adopted_and_finalized_to_complete_with_no_blind_verification(
    db_path,
):
    """Gate 492 point (d), end to end: a genuinely FREE dispatch (no binding
    token at all -- no task_id=, no plan_id=, no parent_handoff_id=) births a
    kind=investigation row with plan_task_id NULL (plan 49 task 1, S1). The
    turn adopts that row through the real CLI, finalizes straight to COMPLETE,
    and the SubagentStop gate -- driven from the RESOLVED row via
    ``_resolve_dispatch_row``, the same seam SubagentStop actually runs, not a
    hand-fed literal -- neither rejects the turn nor raises
    BLIND_VERIFICATION_REQUIRED.

    Before this test the property was inferred only from unchanged gate code
    (``_blind_verification_required`` is a pure function of ``plan_task_id``)
    plus the already-green adoption suites above, never exercised end to end
    for the free-kind class specifically -- this closes that gap.
    """
    from adapters.claude_code import ClaudeCodeAdapter, evaluate_contract_gate

    identity = ClaudeCodeAdapter._maybe_birth_dispatched_row(
        {"prompt": "investigate why the build is flaky"},
        "gaia-system", "sess-free-e2e",
    )
    assert identity is not None, "a genuinely free dispatch must still birth its row"

    born = _rows_for(db_path, identity["contract_id"])
    assert len(born) == 1, "birth adds exactly one row"
    assert born[0]["agent_state"] == "DISPATCHED"
    assert born[0]["kind"] == "investigation"
    assert born[0]["plan_task_id"] is None, (
        "a free turn never carries a plan_task_id -- that is what keeps the "
        "blind-verification gate disarmed for it"
    )

    _adopt(identity)
    assert _cli([
        "fill", "--draft-id", identity["contract_id"], "--json",
        json.dumps({"evidence_report": {
            "verification": {
                "method": "test", "result": "pass", "details": "suite green",
            },
        }}),
    ]).returncode == 0
    assert _cli([
        "set", "--draft-id", identity["contract_id"],
        "agent_status.next_action", "done",
    ]).returncode == 0
    assert _cli([
        "set", "--draft-id", identity["contract_id"],
        "agent_status.agent_state", "COMPLETE",
    ]).returncode == 0

    fin = _cli([
        "finalize", "--draft-id", identity["contract_id"],
        "--session-id", "sess-free-e2e", "--json",
    ])
    assert fin.returncode == 0, (
        f"a free-kind (unbound) turn may self-COMPLETE: {fin.stdout} {fin.stderr}"
    )

    rows = _rows_for(db_path, identity["contract_id"])
    assert len(rows) == 1, "the born row and the finalized row are ONE row"
    row = rows[0]
    assert row["agent_state"] == "COMPLETE"
    assert row["kind"] == "investigation"
    assert row["plan_task_id"] is None

    resolved = ClaudeCodeAdapter._resolve_dispatch_row(
        session_id="sess-free-e2e",
        agent_type="gaia-system",
        task_info=_task_info(db_path),
        parsed_contract=_complete_envelope(identity),
        db_path=db_path,
    )
    assert resolved is not None, "an adopted free turn must not read as rowless"
    assert resolved["plan_task_id"] is None

    gate = evaluate_contract_gate(
        _complete_envelope(identity),
        agent_type="gaia-system",
        plan_task_id=resolved["plan_task_id"],
        ramp_enabled=True,
        db_path=str(db_path),
    )
    assert not gate.rejected, (
        f"a free-kind turn's COMPLETE must not be rejected: {gate.rejection_reason}"
    )
    assert not any(
        a["code"] == "BLIND_VERIFICATION_REQUIRED" for a in gate.anomalies
    ), gate.anomalies
