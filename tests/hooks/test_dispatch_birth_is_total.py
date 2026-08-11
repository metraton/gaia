"""
Birth is TOTAL: every dispatch that reaches PreToolUse:Agent gets a row.

The born row is the only channel through which a dispatched turn receives its
identity, its kernel, and -- at the SubagentStart claim -- the harness stamp
that makes the turn recoverable after a cut. A dispatch whose row is not born
loses all three at once, and nothing downstream can rebuild them: no CLI verb
writes ``harness_agent_id``, so the turn cannot even name itself afterwards.

Degrade-not-drop was introduced for the two ``plan_task_id`` rejection reasons
and left the other three rejecting. This file locks the generalization: EVERY
``DispatchBindingError`` degrades. The unresolvable coordinate is stamped NULL
(referential integrity is never weakened), the reason and the attempted value
are recorded inside the birth envelope, the anomaly event still fires -- and
the row exists.

The verifier lane is the case that motivated it: ``extract_dispatch_binding``
labels any agent whose name contains "verifier" a verifier turn, which REQUIRES
a resolvable ``parent_handoff_id=<N>`` token in the dispatch prompt. When the
orchestrator omits that token the binding is rejected, and before this change
the row was dropped -- so a whole agent type dispatched without the token never
received a contract at all.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = _REPO_ROOT / "hooks"
for _p in (str(_HOOKS_DIR), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import (  # noqa: E402
    DISPATCH_BINDING_REJECTED_EVENT,
    ClaudeCodeAdapter,
)
from gaia.paths import db_path  # noqa: E402
from gaia.store import writer as _store_writer  # noqa: E402

WORKSPACE = "me"
PLAN_ID = 41
TASK_PENDING = 71
TASK_DONE = 72
MISSING_TASK = 9999
MISSING_PARENT = 8888
SESSION = "sess-birth-is-total"


def _seed_plan_tasks() -> None:
    con = _store_writer._connect(db_path())
    try:
        _store_writer._ensure_workspace_row(con, WORKSPACE)
        con.execute(
            "INSERT INTO briefs (id, workspace, name, status) VALUES (?, ?, ?, ?)",
            (1, WORKSPACE, "birth-is-total", "in-progress"),
        )
        con.execute(
            "INSERT INTO plans (id, brief_id, status) VALUES (?, ?, ?)",
            (PLAN_ID, 1, "active"),
        )
        con.execute(
            "INSERT INTO tasks (id, plan_id, order_num, goal, status) VALUES (?,?,?,?,?)",
            (TASK_PENDING, PLAN_ID, 1, "some task", "pending"),
        )
        con.execute(
            "INSERT INTO tasks (id, plan_id, order_num, goal, status) VALUES (?,?,?,?,?)",
            (TASK_DONE, PLAN_ID, 2, "closed task", "done"),
        )
        con.commit()
    finally:
        con.close()


def _birth(prompt: str, agent_name: str = "gaia-system", hook_data=None):
    """Drive the real dispatch-side entry point, exactly as the hook calls it."""
    parameters = {"prompt": prompt, "workspace": WORKSPACE}
    return ClaudeCodeAdapter._maybe_birth_dispatched_row(
        parameters, agent_name, SESSION, hook_data=hook_data,
    )


def _fetch_row(contract_id: str) -> dict:
    con = _store_writer._connect(db_path())
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT id, contract_id, agent_id, session_id, kind, plan_task_id, "
            "parent_handoff_id, agent_state, harness_agent_id, claimed_at, "
            "dispatch_prompt_id, dispatch_description, raw_handoff_json "
            "FROM agent_contract_handoffs WHERE contract_id = ?",
            (contract_id,),
        ).fetchone()
        return dict(row) if row is not None else {}
    finally:
        con.close()


def _rejection_events() -> list:
    con = _store_writer._connect(db_path())
    try:
        return con.execute(
            "SELECT payload FROM harness_events WHERE type = ?",
            (DISPATCH_BINDING_REJECTED_EVENT,),
        ).fetchall()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# The invariant, reason by reason.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "agent_name,prompt,reason,attempted_key,attempted_value,expected_kind",
    [
        (
            "gaia-verifier",
            "verifica el trabajo del productor",
            "verifier_requires_parent_handoff_id",
            "attempted_parent_handoff_id",
            None,
            "verifier",
        ),
        (
            "gaia-verifier",
            f"verifica parent_handoff_id={MISSING_PARENT}",
            "parent_handoff_id_unresolved",
            "attempted_parent_handoff_id",
            MISSING_PARENT,
            "verifier",
        ),
        (
            "gaia-system",
            f"haz la cosa plan_id={PLAN_ID}",
            "task_execution_requires_plan_task_id",
            "attempted_plan_task_id",
            None,
            "task_execution",
        ),
        (
            "gaia-system",
            f"haz la cosa task_id={MISSING_TASK}",
            "plan_task_id_unresolved",
            "attempted_plan_task_id",
            MISSING_TASK,
            "task_execution",
        ),
        (
            "gaia-system",
            f"haz la cosa task_id={TASK_DONE}",
            "plan_task_id_not_dispatchable",
            "attempted_plan_task_id",
            TASK_DONE,
            "task_execution",
        ),
    ],
    ids=[
        "verifier_missing_parent_token",
        "verifier_dangling_parent",
        "task_execution_missing_task_token",
        "task_execution_unresolved_task",
        "task_execution_terminal_task",
    ],
)
def test_every_rejected_binding_still_births_its_row(
    agent_name, prompt, reason, attempted_key, attempted_value, expected_kind,
):
    """No rejection reason drops the row -- all five degrade."""
    _seed_plan_tasks()

    identity = _birth(prompt, agent_name=agent_name)
    assert identity is not None, (
        f"{reason}: the dispatch must still receive an identity -- a dropped "
        f"row leaves the turn with no contract and no way to recover one"
    )

    row = _fetch_row(identity["contract_id"])
    assert row, f"{reason}: no row was born for contract_id={identity['contract_id']}"
    assert row["agent_state"] == "DISPATCHED"
    assert row["kind"] == expected_kind, "the ATTEMPTED kind is preserved"
    assert row["session_id"] == SESSION

    # Referential integrity is not weakened: the coordinate that failed to
    # resolve is NULL in the column, never sealed.
    assert row["plan_task_id"] is None
    assert row["parent_handoff_id"] is None

    envelope = json.loads(row["raw_handoff_json"] or "{}")
    rejection = envelope.get("binding_rejection") or {}
    assert rejection.get("reason") == reason
    assert rejection.get(attempted_key) == attempted_value, (
        "the coordinate that failed is consultable in the birth envelope"
    )

    # Degrading never silences the anomaly channel.
    payloads = " ".join(str(r["payload"]) for r in _rejection_events())
    assert reason in payloads


def test_verifier_without_parent_token_is_claimable_and_stampable():
    """The end-to-end chain the live case broke.

    A born row is only worth as much as what SubagentStart can do with it:
    claim it by the dispatch coordinates, stamp the harness agent id onto it,
    and render the kernel. This asserts the whole chain for the exact shape
    that used to go unborn -- a gaia-verifier dispatch carrying no
    ``parent_handoff_id=`` token.
    """
    from gaia.store.writer import claim_dispatch_row, stamp_harness_agent_id

    _seed_plan_tasks()

    prompt_id = "prompt-verifier-no-parent"
    description = "Verificar el incremento"
    parameters = {
        "prompt": "verifica el trabajo del productor",
        "workspace": WORKSPACE,
        "description": description,
    }
    identity = ClaudeCodeAdapter._maybe_birth_dispatched_row(
        parameters, "gaia-verifier", SESSION,
        hook_data={"prompt_id": prompt_id, "cwd": str(_REPO_ROOT)},
    )
    assert identity is not None

    claimed = claim_dispatch_row(
        agent_name="gaia-verifier",
        dispatch_prompt_id=prompt_id,
        dispatch_description=description,
    )
    assert claimed is not None, (
        "SubagentStart must be able to claim the row it was born for"
    )
    assert claimed["contract_id"] == identity["contract_id"]

    harness_id = "aharnessverifier01"
    stamp = stamp_harness_agent_id(claimed["contract_id"], harness_id)
    assert stamp.get("status") == "applied"

    row = _fetch_row(identity["contract_id"])
    assert row["harness_agent_id"] == harness_id, (
        "without this stamp the turn is unrecoverable: no CLI verb writes "
        "harness_agent_id"
    )
    assert row["claimed_at"]


def test_a_sound_verifier_binding_is_unaffected():
    """The generalization must not swallow a binding that DOES resolve.

    A verifier dispatch naming a real producer handoff still binds to it --
    ``parent_handoff_id`` is stamped, no rejection is recorded, and the anomaly
    channel stays silent.
    """
    _seed_plan_tasks()
    parent = _store_writer.finalize_agent_contract_handoff(
        contract_id="aproducer0000000.parent",
        agent_id="aproducer0000000",
        workspace=WORKSPACE,
        agent_state="COMPLETE",
        raw_handoff_json="{}",
        db_path=db_path(),
    )["handoff_id"]

    identity = _birth(
        f"verifica parent_handoff_id={parent}", agent_name="gaia-verifier",
    )
    assert identity is not None

    row = _fetch_row(identity["contract_id"])
    assert row["parent_handoff_id"] == parent
    envelope = json.loads(row["raw_handoff_json"] or "{}")
    assert "binding_rejection" not in envelope
    assert _rejection_events() == []
