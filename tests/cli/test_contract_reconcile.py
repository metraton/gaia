"""
A hook-written residue row has a closure path.

The SubagentStop backstop keys the row it captures
``hook-backstop.{agent_id}.{session_id}``. Every CLI door that could close a row
splits a contract id on its FIRST dot to recover the agent handle, and that
split yields the literal ``hook-backstop`` -- which ``AGENT_ID_PATTERN_TEXT``
(``^a[0-9a-f]{16,}$``) forbids. ``_maybe_adopt_draft`` refuses the prefix, so no
draft can be materialized; and if one were, ``cmd_finalize``'s coherence check
would demand ``agent_status.agent_id == "hook-backstop"`` while the validator
forbids exactly that value. No value satisfies both, so the row was unclosable
by construction.

Measured cost: eight cut rows in eighteen minutes, six of them residue, each one
duplicating a turn that had already closed clean on another row. They accumulate
without limit inside ``gaia contract list --cut`` -- the signal the orchestrator
reads to find degraded work -- until that signal is almost entirely false
positives.

These tests build the residue row through the REAL backstop path (no fixture
row hand-inserted to match the assertion), demonstrate that ``finalize`` still
cannot close it, and demonstrate that ``reconcile`` can -- without touching
``agent_state``, and while refusing a cut row an agent authored itself.
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
SESSION = "sess-residue"

# A minted-shape handle that owns NO draft on disk -- the fence-only turn the
# backstop's synthetic-id branch exists for.
ORPHAN_MINTED_ID = "a1111111111111111"

_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)


@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    monkeypatch.setenv("GAIA_WORKSPACE", WORKSPACE)
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    yield


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "gaia-data" / "gaia.db"


def _cli(argv):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_ROOT)
    return subprocess.run(
        [sys.executable, str(CONTRACT_CLI), *argv],
        capture_output=True, text=True, env=env,
    )


def _fence(agent_id: str, state: str = "BLOCKED") -> dict:
    return {
        "agent_status": {
            "agent_id": agent_id,
            "agent_state": state,
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": {k: [] for k in _EVIDENCE_KEYS},
        "consolidation_report": None,
        "approval_request": None,
    }


def _row(db_path: Path, contract_id: str):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        found = con.execute(
            "SELECT * FROM agent_contract_handoffs WHERE contract_id = ?",
            (contract_id,),
        ).fetchone()
        return dict(found) if found is not None else None
    finally:
        con.close()


@pytest.fixture()
def residue_contract_id(db_path):
    """A genuine residue row, written by the real SubagentStop backstop.

    A fence-only turn: it emitted an envelope naming a minted handle, but never
    opened a draft under it, so the backstop synthesizes its own key.
    """
    from modules.agents.handoff_persister import persist_handoff

    persist_handoff(
        parsed_contract=_fence(ORPHAN_MINTED_ID),
        agent_output="no draft was ever opened",
        task_info={
            "agent": "gaia-system",
            "agent_id": "a00000000000000ff",
            "workspace": WORKSPACE,
            "db_path": str(db_path),
        },
        session_id=SESSION,
    )
    contract_id = f"hook-backstop.{ORPHAN_MINTED_ID}.{SESSION}"
    assert _row(db_path, contract_id) is not None, (
        "the backstop must have written the residue row this suite closes"
    )
    return contract_id


# ---------------------------------------------------------------------------
# The row is real, marked cut, and unclosable through the agent's own door
# ---------------------------------------------------------------------------

def test_the_residue_row_is_marked_cut_and_pollutes_the_cut_signal(
    db_path, residue_contract_id,
):
    row = _row(db_path, residue_contract_id)
    assert row["cut_reason"] == "backstop_capture"

    listed = _cli(["list", "--cut", "--json"])
    assert listed.returncode == 0, listed.stderr
    payload = json.loads(listed.stdout)
    assert residue_contract_id in [
        h["contract_id"] for h in payload["handoffs"]
    ], "the residue row is exactly what contaminates `contract list --cut`"


def test_finalize_still_cannot_close_a_residue_row(residue_contract_id):
    """The agent's clean-close door stays shut, deliberately.

    ``reconcile`` is a separate verb precisely so this door is not widened for
    every turn in order to serve rows no agent wrote. This pins that it is not.
    """
    result = _cli(["finalize", "--draft-id", residue_contract_id, "--json"])
    assert result.returncode == 1
    assert "No draft" in result.stdout or "no draft" in result.stdout.lower(), (
        f"unexpected finalize failure mode: {result.stdout} {result.stderr}"
    )


# ---------------------------------------------------------------------------
# reconcile: the closure path
# ---------------------------------------------------------------------------

def test_reconcile_closes_the_residue_row(db_path, residue_contract_id, capsys):
    from gaia.store.writer import insert_dispatched_handoff

    # The row that holds the turn's real verdict -- the pointer's target must
    # exist, so a typo cannot record a dangling link.
    insert_dispatched_handoff(
        contract_id="a2222222222222222.real",
        agent_id="a2222222222222222",
        workspace=WORKSPACE,
        session_id=SESSION,
        db_path=db_path,
    )

    before = _row(db_path, residue_contract_id)
    result = _cli([
        "reconcile",
        "--contract-id", residue_contract_id,
        "--superseded-by", "a2222222222222222.real",
        "--json",
    ])
    assert result.returncode == 0, f"{result.stdout} {result.stderr}"

    payload = json.loads(result.stdout)
    assert payload["status"] == "reconciled"
    assert payload["cut_reason_before"] == "backstop_capture"
    assert payload["cut_reason_after"] is None
    assert payload["superseded_by_contract_id"] == "a2222222222222222.real"

    after = _row(db_path, residue_contract_id)
    assert after["cut_reason"] is None, "the cut mark must be gone"
    assert after["agent_state"] == before["agent_state"], (
        "reconcile must NOT touch agent_state -- it cannot widen the gate or "
        "promote anything to COMPLETE"
    )
    envelope = json.loads(after["raw_handoff_json"])
    assert envelope["reconciled"] is True
    assert envelope["superseded_by_contract_id"] == "a2222222222222222.real"

    listed = json.loads(_cli(["list", "--cut", "--json"]).stdout)
    assert residue_contract_id not in [
        h["contract_id"] for h in listed["handoffs"]
    ], "a reconciled row must leave the --cut signal"


def test_reconcile_addresses_by_harness_id_too(db_path, residue_contract_id):
    """The same bridge `view` walks: harness_agent_id -> row."""
    from gaia.store.writer import stamp_harness_agent_id

    stamp_harness_agent_id(
        residue_contract_id, "a00000000000000ff", db_path=db_path,
    )
    result = _cli([
        "reconcile", "--harness-id", "a00000000000000ff", "--json",
    ])
    assert result.returncode == 0, f"{result.stdout} {result.stderr}"
    assert json.loads(result.stdout)["contract_id"] == residue_contract_id


def test_reconcile_refuses_a_turns_own_cut_row(db_path):
    """A genuinely cut turn must stay visible in `--cut`.

    The value of the signal is that it names work that really was degraded.
    A verb that could clear any cut mark would destroy the signal it exists to
    clean.
    """
    from gaia.store.writer import finalize_agent_contract_handoff

    finalize_agent_contract_handoff(
        contract_id="a3333333333333333.own",
        agent_id="a3333333333333333",
        workspace=WORKSPACE,
        agent_state="IN_PROGRESS",
        raw_handoff_json=json.dumps(_fence("a3333333333333333", "IN_PROGRESS")),
        session_id=SESSION,
        cut_reason="never_finalized",
        db_path=db_path,
    )

    result = _cli([
        "reconcile", "--contract-id", "a3333333333333333.own", "--json",
    ])
    assert result.returncode == 1
    assert "hook-capture marker" in result.stdout, result.stdout
    assert _row(db_path, "a3333333333333333.own")["cut_reason"] == "never_finalized"


def test_reconcile_refuses_a_row_that_is_not_cut(db_path, residue_contract_id):
    """Idempotent by refusal: a second reconcile has nothing to do and says so."""
    assert _cli([
        "reconcile", "--contract-id", residue_contract_id, "--json",
    ]).returncode == 0
    second = _cli([
        "reconcile", "--contract-id", residue_contract_id, "--json",
    ])
    assert second.returncode == 1
    assert "no cut_reason" in second.stdout, second.stdout


def test_reconcile_refuses_a_dangling_superseded_by(residue_contract_id):
    result = _cli([
        "reconcile", "--contract-id", residue_contract_id,
        "--superseded-by", "a9999999999999999.nonexistent", "--json",
    ])
    assert result.returncode == 1
    assert "no\nagent_contract_handoffs row" in result.stdout.replace(" ", "\n") or (
        "has no" in result.stdout
    ), result.stdout


def test_reconcile_has_no_default_target():
    """A bare invocation must not fall back to whatever row sorts first."""
    result = _cli(["reconcile", "--json"])
    assert result.returncode == 1
    assert "no default target" in result.stdout, result.stdout
