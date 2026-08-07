"""
Turn -> row identity resolution when the final message carries NO fence.

The contract's source of truth moved from the fenced block in an agent's last
message to its ``agent_contract_handoffs`` row. Retiring the fence is only safe
once a turn without one still resolves to ITS row -- the one born with its
dispatch -- and to no other. These tests pin the three failure modes that made
that unsafe, all measured live:

  (a) ``resolve_minted_agent_id`` fell back to the HARNESS agent id, which has
      the same ``a``+hex shape as a minted one. Being non-empty it satisfied
      every ``if not minted_agent_id`` guard downstream, so the M4
      reconstruction globbed a draft that could not exist, returned None with no
      log, and a complete ``update_contracts`` proposal was dropped in silence.

  (b) That wrong value then POISONED the dispatch-row lookup: the backstop
      capture writes the harness id into a residue row's ``agent_id`` column, so
      the adopted lane's ``WHERE agent_id = ?`` matched it and, ordering by
      recency, returned the residue row instead of the turn's own cleanly-closed
      one.

  (c) is the CLI closure path, pinned in tests/cli/test_contract_reconcile.py.

Every test runs against a real sqlite substrate -- no mocks, no monkeypatched
resolvers -- and the harness agent id is deliberately DIFFERENT from the minted
one throughout. Equating them is what masked this hole once already: with one
value standing in for two spaces, every lane appears to work.
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
_HOOKS_DIR = str(_REPO_ROOT / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"

WORKSPACE = "me"

# The two identifier spaces, kept visibly apart. Both satisfy
# AGENT_ID_PATTERN_TEXT, which is exactly why nothing failed loudly when they
# were confused -- the shape cannot tell them apart, only the row can.
HARNESS_AGENT_ID = "a00000000000000ff"

_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)

# A COMPLETE envelope must carry a passing verification block before the
# terminal state is set -- the validator's build-order rule.
_VERIFICATION = {
    "method": "real sqlite substrate",
    "result": "pass",
    "details": "row resolved by the coordinate under test",
}


@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    monkeypatch.setenv("GAIA_WORKSPACE", WORKSPACE)
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    yield


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "gaia-data" / "gaia.db"


def _cli(argv, env_extra=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_ROOT)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(CONTRACT_CLI), *argv],
        capture_output=True, text=True, env=env,
    )


def _task_info(db_path: Path, agent: str = "gaia-system") -> dict:
    """The SubagentStop view of a turn: the agent NAME plus the harness's own
    per-run agent id, which belongs to neither the draft nor the row identity
    space."""
    return {
        "agent": agent,
        "agent_id": HARNESS_AGENT_ID,
        "workspace": WORKSPACE,
        "db_path": str(db_path),
    }


def _adopt_and_finalize(identity: dict, session_id: str, state: str = "COMPLETE"):
    """Drive the real CLI through a fenceless turn's whole life: adopt the born
    draft, fill it, and close it -- exactly what a turn does today."""
    assert _cli([
        "fill", "--draft-id", identity["contract_id"],
        "--json", json.dumps({
            "agent_status": {
                "agent_id": identity["agent_id"],
                "agent_state": state,
                "pending_steps": [],
                "next_action": "done",
            },
            "evidence_report": dict(
                {k: [] for k in _EVIDENCE_KEYS}, verification=_VERIFICATION,
            ),
        }),
    ]).returncode == 0
    return _cli([
        "finalize", "--draft-id", identity["contract_id"],
        "--session-id", session_id, "--json",
    ])


def _rows(db_path: Path):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(
            "SELECT * FROM agent_contract_handoffs ORDER BY id"
        )]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# (a) the last resort admits failure instead of returning a wrong identifier
# ---------------------------------------------------------------------------

def test_unresolvable_identity_returns_none_not_the_harness_id(db_path, caplog):
    """No fence, no mint report, no row: the resolver must say so.

    The old behaviour returned ``task_info['agent_id']`` -- non-empty, so every
    ``if not minted_agent_id`` guard downstream passed and carried the wrong
    value into a draft glob that could never match.
    """
    from modules.agents.handoff_persister import resolve_minted_agent_id

    task_info = _task_info(db_path)
    with caplog.at_level("WARNING"):
        resolved = resolve_minted_agent_id(None, task_info, session_id="sess-a")

    assert resolved is None, (
        "an unresolvable identity must be None -- returning the harness id "
        f"({HARNESS_AGENT_ID}) turns a detectable failure into a silent one"
    )
    assert resolved != HARNESS_AGENT_ID
    assert any(
        "Minted agent id UNRESOLVED" in rec.message for rec in caplog.records
    ), "an unresolvable identity must leave a trace, not return quietly"


def test_bridge_recovers_the_minted_id_from_the_row_that_holds_both(db_path):
    """The row knows both identities; the resolver now crosses between them."""
    from adapters.claude_code import ClaudeCodeAdapter
    from gaia.store.writer import stamp_harness_agent_id
    from modules.agents.handoff_persister import resolve_minted_agent_id

    identity = ClaudeCodeAdapter._maybe_birth_dispatched_row(
        {"prompt": "arregla la resolucion de identidad"}, "gaia-system", "sess-bridge",
    )
    assert identity is not None
    assert identity["agent_id"] != HARNESS_AGENT_ID, (
        "the test is meaningless unless the two id spaces hold DIFFERENT values"
    )
    stamp_harness_agent_id(
        identity["contract_id"], HARNESS_AGENT_ID, db_path=db_path,
    )

    resolved = resolve_minted_agent_id(
        None, _task_info(db_path), session_id="sess-bridge",
    )
    assert resolved == identity["agent_id"], (
        "the harness_agent_id -> row -> agent_id bridge is the only path to the "
        "minted id for a turn that never ran `gaia contract init`"
    )


def test_bridge_refuses_a_row_whose_agent_id_is_not_a_minted_handle(db_path):
    """A legacy row carries the agent NAME in agent_id. Returning that would
    recreate the same space confusion one lane lower."""
    from gaia.store.writer import insert_dispatched_handoff, stamp_harness_agent_id
    from modules.agents.handoff_persister import resolve_minted_agent_id

    insert_dispatched_handoff(
        contract_id="dispatch.sess-legacy.gaia-system.1",
        agent_id="gaia-system",
        workspace=WORKSPACE,
        session_id="sess-legacy",
        db_path=db_path,
    )
    stamp_harness_agent_id(
        "dispatch.sess-legacy.gaia-system.1", HARNESS_AGENT_ID, db_path=db_path,
    )

    assert resolve_minted_agent_id(
        None, _task_info(db_path), session_id="sess-legacy",
    ) is None


def test_reconstruction_recovers_a_fenceless_turns_update_contracts(db_path, caplog):
    """The measured loss (handoff row 11304), end to end.

    A turn adopts its born draft, records an ``update_contracts`` proposal in it,
    finalizes cleanly, and emits NO fence. The reconstruction must rebuild that
    envelope so ``process_update_contracts`` still sees the proposal -- under the
    old resolver it globbed a draft under the harness id, found none, and
    returned None without writing a single log line.
    """
    from adapters.claude_code import ClaudeCodeAdapter
    from gaia.store.writer import stamp_harness_agent_id

    adapter = ClaudeCodeAdapter()
    identity = ClaudeCodeAdapter._maybe_birth_dispatched_row(
        {"prompt": "propone una actualizacion de contexto"}, "gaia-system", "sess-m4",
    )
    assert identity is not None
    stamp_harness_agent_id(
        identity["contract_id"], HARNESS_AGENT_ID, db_path=db_path,
    )

    assert _cli([
        "fill", "--draft-id", identity["contract_id"],
        "--json", json.dumps({
            "agent_status": {
                "agent_id": identity["agent_id"],
                "agent_state": "COMPLETE",
                "pending_steps": [],
                "next_action": "done",
            },
            "evidence_report": dict(
                {k: [] for k in _EVIDENCE_KEYS}, verification=_VERIFICATION,
            ),
            "update_contracts": [
                {"section": "architecture", "payload": {"note": "la propuesta"}},
            ],
        }),
    ]).returncode == 0
    assert _cli([
        "finalize", "--draft-id", identity["contract_id"],
        "--session-id", "sess-m4", "--json",
    ]).returncode == 0

    with caplog.at_level("INFO"):
        recon = adapter._reconstruct_contract_from_finalized_draft(
            task_info=_task_info(db_path),
            parsed_contract=None,
            session_id="sess-m4",
        )

    assert recon is not None, (
        "a fenceless turn that finalized cleanly must have its envelope rebuilt"
    )
    assert recon["reconstructed_from_finalized_draft"] == identity["contract_id"]
    assert recon["update_contracts"] == [
        {"section": "architecture", "payload": {"note": "la propuesta"}},
    ], "the proposal the turn carried must survive reconstruction"


def test_reconstruction_logs_when_it_cannot_find_a_draft(db_path, caplog):
    """A miss must be diagnosable. It used to return None four different ways
    without a single log line, which is why the defect lived unseen."""
    from adapters.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter()
    with caplog.at_level("WARNING"):
        recon = adapter._reconstruct_contract_from_finalized_draft(
            task_info=_task_info(db_path),
            parsed_contract=None,
            session_id="sess-miss",
        )

    assert recon is None
    assert any("M4 reconstruction" in rec.message for rec in caplog.records), (
        "every reconstruction miss must leave a trace"
    )


# ---------------------------------------------------------------------------
# (b) a fenceless turn binds to ITS row, never to a residue row
# ---------------------------------------------------------------------------

def test_fenceless_turn_binds_to_its_own_row_not_the_residue_row(db_path):
    """The live failure, reproduced from the real hook paths.

    The turn adopts, closes clean, emits no fence. Then the SubagentStop
    backstop runs for the SAME turn. The resolution must land on the turn's own
    cleanly-closed row -- not on any residue row that shares the harness id in
    its ``agent_id`` column.
    """
    from adapters.claude_code import ClaudeCodeAdapter
    from gaia.store.writer import stamp_harness_agent_id
    from modules.agents.handoff_persister import persist_handoff

    identity = ClaudeCodeAdapter._maybe_birth_dispatched_row(
        {"prompt": "cierra sin fence"}, "gaia-system", "sess-b",
    )
    assert identity is not None
    stamp_harness_agent_id(
        identity["contract_id"], HARNESS_AGENT_ID, db_path=db_path,
    )
    fin = _adopt_and_finalize(identity, "sess-b")
    assert fin.returncode == 0, f"{fin.stdout} {fin.stderr}"

    # The stop hook fires for the same turn, with NO parsed contract at all.
    persist_handoff(
        parsed_contract=None,
        agent_output="",
        task_info=_task_info(db_path),
        session_id="sess-b",
    )

    resolved = ClaudeCodeAdapter._resolve_dispatch_row(
        session_id="sess-b",
        agent_type="gaia-system",
        task_info=_task_info(db_path),
        parsed_contract=None,
        db_path=db_path,
    )
    assert resolved is not None, "a fenceless turn must not read as unbound"
    assert resolved["contract_id"] == identity["contract_id"], (
        f"resolution landed on {resolved['contract_id']!r} instead of the "
        f"turn's own row {identity['contract_id']!r}"
    )
    assert resolved["agent_state"] == "COMPLETE"
    assert not resolved.get("cut_reason"), (
        "the turn's own row closed clean; a cut row is a residue row"
    )


def test_the_backstop_no_longer_manufactures_a_residue_row(db_path):
    """The accumulation stops at its source.

    Eight cut rows in eighteen minutes, six of them residue, each duplicating a
    turn that had already closed clean. They exist because the backstop could
    not find the turn's draft (it globbed the harness id) and so synthesized a
    `hook-backstop.*` id and wrote a second row. With the bridge in place it
    resolves the real draft, sees the terminal row, and stays passive.
    """
    from adapters.claude_code import ClaudeCodeAdapter
    from gaia.store.writer import stamp_harness_agent_id
    from modules.agents.handoff_persister import persist_handoff

    identity = ClaudeCodeAdapter._maybe_birth_dispatched_row(
        {"prompt": "una vuelta sana"}, "gaia-system", "sess-clean",
    )
    stamp_harness_agent_id(
        identity["contract_id"], HARNESS_AGENT_ID, db_path=db_path,
    )
    assert _adopt_and_finalize(identity, "sess-clean").returncode == 0
    assert len(_rows(db_path)) == 1

    persist_handoff(
        parsed_contract=None,
        agent_output="",
        task_info=_task_info(db_path),
        session_id="sess-clean",
    )

    rows = _rows(db_path)
    assert len(rows) == 1, (
        "a healthy fenceless turn must leave ONE row; the extra rows are: "
        + ", ".join(r["contract_id"] for r in rows[1:])
    )
    assert rows[0]["cut_reason"] is None
    assert rows[0]["agent_state"] == "COMPLETE"


def test_harness_lane_declines_an_ambiguous_join(db_path, caplog):
    """Two rows under one harness id is not a recency contest.

    The measured failure was precisely a most-recent-wins tiebreak returning the
    wrong row. When the exact coordinate is not exact, the lane declines and
    says so rather than guessing.
    """
    from adapters.claude_code import ClaudeCodeAdapter
    from gaia.store.writer import insert_dispatched_handoff, stamp_harness_agent_id

    for n in (1, 2):
        cid = f"a{'b' * 16}.tok{n}"
        insert_dispatched_handoff(
            contract_id=cid,
            agent_id=f"a{'b' * 16}",
            workspace=WORKSPACE,
            session_id="sess-ambig",
            db_path=db_path,
        )
        stamp_harness_agent_id(cid, HARNESS_AGENT_ID, db_path=db_path)

    with caplog.at_level("WARNING"):
        ClaudeCodeAdapter._resolve_dispatch_row(
            session_id="sess-ambig",
            agent_type="gaia-system",
            task_info=_task_info(db_path),
            parsed_contract=None,
            db_path=db_path,
        )

    assert any(
        "refusing to guess" in rec.message for rec in caplog.records
    ), "an ambiguous harness join must be declined out loud, not resolved by recency"


def test_a_full_resolution_miss_is_logged(db_path, caplog):
    """No row for this turn at all. Unbound is a legitimate outcome -- being
    unbound SILENTLY is what let the defects above live."""
    from adapters.claude_code import ClaudeCodeAdapter

    with caplog.at_level("WARNING"):
        resolved = ClaudeCodeAdapter._resolve_dispatch_row(
            session_id="sess-nothing",
            agent_type="gaia-system",
            task_info=_task_info(db_path),
            parsed_contract=None,
            db_path=db_path,
        )

    assert resolved is None
    assert any(
        "NO lane resolved a row" in rec.message for rec in caplog.records
    )
