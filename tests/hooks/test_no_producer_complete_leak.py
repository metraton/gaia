#!/usr/bin/env python3
"""ANTI-LEAK closure across the THREE persistence paths (plan 34 task 8).

Brief: contrato-binding-y-verificacion-por-task-id (plan_id=34, task order_num=8).

The invariant this suite locks: NO producer (a plan-task-bound turn) may persist
a self-COMPLETE through ANY of the three paths a terminal verdict can reach the
``agent_contract_handoffs`` row, WHILE a legitimate verifier turn is still free
to promote the increment to COMPLETE. The three paths:

  (a) the SubagentStop FENCE GATE
      (hooks/adapters/claude_code.py::evaluate_contract_gate) -- keyed on
      plan_task_id since task 7; a bound COMPLETE is forced to
      NEEDS_VERIFICATION.
  (b) the ``gaia contract finalize`` CLI (bin/cli/contract.py::cmd_finalize) --
      the role-blind hole that leaked the 31 COMPLETEs. Task 8 makes it
      binding-aware: a bound COMPLETE is REFUSED at the CLI seam too.
  (c) the DEGRADED BACKSTOP / reaper
      (hooks/modules/agents/handoff_persister.py::persist_handoff) -- an
      orphaned DISPATCHED row is reaped to a NON-COMPLETE verdict, never a false
      COMPLETE.

Plus the DEADLOCK-avoidance fix: ``extract_dispatch_binding`` must NOT stamp a
plan_task_id onto a VERIFIER turn (it binds by parent_handoff_id), so the
plan_task_id-keyed gate treats the verifier as unbound and lets it self-COMPLETE
(promote). Without this the verifier would be sent back for verification of
itself, forever.

And the PRESERVATION case: a verifier / unbound turn reaching COMPLETE is NOT
blocked by any of the three closures.
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
_HOOKS_DIR = _REPO_ROOT / "hooks"
for _p in (str(_HOOKS_DIR), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import (  # noqa: E402
    GATE_MODE_FULL_VERDICT,
    GATE_MODE_THREE_CASE,
    evaluate_contract_gate,
)
from gaia.contract.drafts import mint_draft_id, save_draft  # noqa: E402
from gaia.store.writer import (  # noqa: E402
    agent_contract_handoff_state,
    finalize_agent_contract_handoff,
    insert_dispatched_handoff,
)
from modules.agents.dispatch_binding import extract_dispatch_binding  # noqa: E402
from modules.agents.handoff_persister import persist_handoff  # noqa: E402

CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"

WORKSPACE = "me"
AGENT_ID = "a1234abcd"
PLAN_ID = 34
TASK_ID = 42

_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)


def _evidence():
    ev = {k: [] for k in _EVIDENCE_KEYS}
    return ev


def _complete_envelope(agent_id: str = AGENT_ID) -> dict:
    env = {
        "agent_status": {
            "agent_state": "COMPLETE",
            "agent_id": agent_id,
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": _evidence(),
        "consolidation_report": None,
        "approval_request": None,
    }
    env["evidence_report"]["verification"] = {
        "method": "test", "result": "pass", "details": "suite green",
    }
    return env


# ===========================================================================
# 0. DEADLOCK-avoidance: extract_dispatch_binding drops plan_task_id for a
#    VERIFIER turn (it binds by parent_handoff_id, not a plan_task_id of its own)
# ===========================================================================

class TestVerifierBindingDropsPlanTaskId:
    def test_verifier_turn_has_no_plan_task_id(self):
        """A verifier dispatch prompt MENTIONS the task_id it verifies, but the
        verifier turn must NOT carry it as its own binding -- otherwise the
        plan_task_id-keyed gate would force the verifier's own COMPLETE to
        NEEDS_VERIFICATION (a deadlock: the verifier could never promote)."""
        binding = extract_dispatch_binding({
            "prompt": "Verifica la TASK 8 (order_num=8, task_id=45) del plan_id=34",
            "subagent_type": "gaia-verifier",
        })
        assert binding["turn_role"] == "verifier"
        assert binding["kind"] == "verifier"
        assert binding["plan_task_id"] is None, (
            "a verifier turn must not be stamped with a plan_task_id"
        )
        # The plan_id is still extracted (context), only plan_task_id is dropped.
        assert binding["plan_id"] == 34

    def test_producer_turn_keeps_plan_task_id(self):
        """A non-verifier (producer) dispatch DOES keep its plan_task_id -- that
        is exactly what makes the gate treat it as a bound producer turn."""
        binding = extract_dispatch_binding({
            "prompt": "Ejecuta la TASK 8 (order_num=8, task_id=45) del plan_id=34",
            "subagent_type": "gaia-system",
        })
        assert binding["turn_role"] is None
        assert binding["kind"] == "task_execution"
        assert binding["plan_task_id"] == 45


# ===========================================================================
# (a) PATH A -- the SubagentStop fence gate
# ===========================================================================

class TestPathAFenceGate:
    @pytest.mark.parametrize("ramp", [True, False])
    def test_bound_producer_complete_rejected(self, ramp):
        gate = evaluate_contract_gate(
            _complete_envelope(), agent_type="gaia-system",
            plan_task_id=TASK_ID, ramp_enabled=ramp,
        )
        assert gate.rejected is True, (
            "a plan-task-bound producer COMPLETE must be blocked by the fence gate"
        )
        assert f"plan_task_id={TASK_ID}" in gate.rejection_reason

    @pytest.mark.parametrize("ramp", [True, False])
    def test_verifier_unbound_complete_promotes(self, ramp):
        """The verifier turn carries NO plan_task_id (it binds by
        parent_handoff_id), so the gate treats it as unbound and lets it
        self-COMPLETE -- the increment is promoted, no deadlock."""
        gate = evaluate_contract_gate(
            _complete_envelope(agent_id="a9f00d1"), agent_type="gaia-verifier",
            plan_task_id=None, ramp_enabled=ramp,
        )
        assert gate.rejected is False, (
            "a verifier / unbound COMPLETE must NOT be blocked by the anti-leak"
        )

    def test_full_verdict_mode_carries_blind_anomaly(self):
        gate = evaluate_contract_gate(
            _complete_envelope(), agent_type="gaia-system",
            plan_task_id=TASK_ID, ramp_enabled=True,
        )
        assert gate.mode == GATE_MODE_FULL_VERDICT
        assert any(
            a["code"] == "BLIND_VERIFICATION_REQUIRED" for a in gate.anomalies
        )

    def test_three_case_mode_is_not_a_bypass(self):
        gate = evaluate_contract_gate(
            _complete_envelope(), agent_type="gaia-system",
            plan_task_id=TASK_ID, ramp_enabled=False,
        )
        assert gate.mode == GATE_MODE_THREE_CASE
        assert gate.rejected is True


# ===========================================================================
# (b) PATH B -- the `gaia contract finalize` CLI (the role-blind leak, closed)
# ===========================================================================

@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "gaia_data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DB_PATH", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    return dict(os.environ)


def _db_path(env: dict) -> Path:
    return Path(env["GAIA_DATA_DIR"]) / "gaia.db"


def _run_cli(args: list, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CONTRACT_CLI), *args],
        capture_output=True, text=True, env=env, timeout=30,
    )


def _seed_binding_targets(db_path: Path) -> int:
    """Materialize schema + seed briefs->plans->tasks so a plan_task_id binding
    satisfies the runtime FKs. Returns a real parent handoff id."""
    parent = finalize_agent_contract_handoff(
        contract_id="a1234abcd.parent-seed", agent_id=AGENT_ID, workspace=WORKSPACE,
        agent_state="COMPLETE",
        raw_handoff_json=json.dumps(_complete_envelope()), db_path=db_path,
    )
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO briefs (id, workspace, name, status) VALUES (?, ?, ?, ?)",
            (1, WORKSPACE, "contrato-binding-y-verificacion-por-task-id", "in-progress"),
        )
        con.execute("INSERT INTO plans (id, brief_id, status) VALUES (?, ?, ?)",
                    (PLAN_ID, 1, "active"))
        con.execute(
            "INSERT INTO tasks (id, plan_id, order_num, goal, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (TASK_ID, PLAN_ID, 8, "anti-leak", "pending"),
        )
        con.commit()
    finally:
        con.close()
    return parent["handoff_id"]


def _build_complete_draft_at(env: dict, draft_id: str) -> None:
    """Fill the on-disk draft <draft_id> to a genuinely-valid COMPLETE envelope
    via the real CLI verbs (validate-on-write throughout)."""
    patch = json.dumps({
        "evidence_report": {
            "verification": {"method": "pytest", "result": "pass", "details": "x"},
        },
    })
    assert _run_cli(["fill", "--draft-id", draft_id, "--json", patch], env).returncode == 0
    assert _run_cli(["set", "--draft-id", draft_id, "agent_status.next_action", "done"], env).returncode == 0
    assert _run_cli(["set", "--draft-id", draft_id, "agent_status.agent_state", "COMPLETE"], env).returncode == 0


def _row_state(db_path: Path, contract_id: str) -> "str | None":
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT agent_state FROM agent_contract_handoffs WHERE contract_id = ?",
            (contract_id,),
        ).fetchone()
        return row["agent_state"] if row is not None else None
    finally:
        con.close()


def test_path_b_bound_producer_finalize_refused_and_no_complete_persisted(cli_env):
    db = _db_path(cli_env)
    _seed_binding_targets(db)

    # init a real draft through the CLI, then BIRTH the born-at-dispatch row
    # under that same contract_id carrying the plan_task_id (a producer turn).
    init = _run_cli(["init", "--agent-id", AGENT_ID, "--json"], cli_env)
    assert init.returncode == 0, init.stderr
    draft_id = json.loads(init.stdout)["draft_id"]

    insert_dispatched_handoff(
        contract_id=draft_id, agent_id=AGENT_ID, workspace=WORKSPACE,
        plan_task_id=TASK_ID, plan_id=PLAN_ID, kind="task_execution",
        session_id="sess-b", db_path=db,
    )
    assert agent_contract_handoff_state(draft_id, db_path=db) == "DISPATCHED"

    _build_complete_draft_at(cli_env, draft_id)

    fin = _run_cli(["finalize", "--draft-id", draft_id, "--json"], cli_env)
    assert fin.returncode == 1, (
        "a plan-task-bound producer COMPLETE must be REFUSED by the CLI finalize"
    )
    payload = json.loads(fin.stdout)
    assert payload["status"] == "rejected"
    assert payload["reason"] == "blind_verification_required"
    assert payload["plan_task_id"] == TASK_ID

    # The load-bearing invariant: no COMPLETE was persisted -- the born row
    # is still DISPATCHED (never converged to COMPLETE by the leak).
    assert _row_state(db, draft_id) == "DISPATCHED"
    assert _row_state(db, draft_id) != "COMPLETE"


def test_path_b_verifier_unbound_finalize_promotes_to_complete(cli_env):
    """PRESERVATION at the CLI seam: a turn whose born row carries NO
    plan_task_id (a verifier bound by parent_handoff_id, or any unbound turn)
    finalizes COMPLETE normally -- the anti-leak does not block promotion."""
    db = _db_path(cli_env)
    parent_id = _seed_binding_targets(db)

    init = _run_cli(["init", "--agent-id", "a9f00d1", "--json"], cli_env)
    assert init.returncode == 0, init.stderr
    draft_id = json.loads(init.stdout)["draft_id"]

    # Verifier turn: bound by parent_handoff_id, plan_task_id is None.
    insert_dispatched_handoff(
        contract_id=draft_id, agent_id="a9f00d1", workspace=WORKSPACE,
        plan_task_id=None, parent_handoff_id=parent_id, kind="verifier",
        session_id="sess-verif", db_path=db,
    )
    assert agent_contract_handoff_state(draft_id, db_path=db) == "DISPATCHED"

    _build_complete_draft_at(cli_env, draft_id)

    fin = _run_cli(["finalize", "--draft-id", draft_id, "--json"], cli_env)
    assert fin.returncode == 0, (
        f"an unbound / verifier COMPLETE must finalize normally: {fin.stdout} {fin.stderr}"
    )
    payload = json.loads(fin.stdout)
    assert payload["status"] == "finalized"
    assert _row_state(db, draft_id) == "COMPLETE"


def test_path_b_unbound_no_born_row_finalize_still_completes(cli_env):
    """A plain turn with NO born-at-dispatch row at all (the legacy /
    investigation / memory path) still self-COMPLETEs -- the CLI guard only
    fires when a binding is actually present."""
    db = _db_path(cli_env)
    init = _run_cli(["init", "--agent-id", AGENT_ID, "--json"], cli_env)
    assert init.returncode == 0, init.stderr
    draft_id = json.loads(init.stdout)["draft_id"]

    _build_complete_draft_at(cli_env, draft_id)
    fin = _run_cli(["finalize", "--draft-id", draft_id, "--json"], cli_env)
    assert fin.returncode == 0, f"unbound COMPLETE must finalize: {fin.stderr}"
    assert _row_state(db, draft_id) == "COMPLETE"


# ===========================================================================
# (c) PATH C -- the degraded backstop / reaper
# ===========================================================================

@pytest.fixture()
def reaper_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    yield


def _task_info(db_path: Path) -> dict:
    return {"agent_id": AGENT_ID, "agent": "gaia-system",
            "workspace": WORKSPACE, "db_path": str(db_path)}


def test_path_c_bound_producer_orphan_reaped_never_false_complete(reaper_env, tmp_path):
    db = tmp_path / "gaia.db"
    _seed_binding_targets(db)

    draft_id = mint_draft_id(AGENT_ID)
    envelope = _complete_envelope()  # the producer BUILT a COMPLETE contract ...
    save_draft(draft_id, envelope)

    # ... a bound row was born at dispatch, then the producer crashed before its
    # own verified finalize (row still DISPATCHED, carrying the plan_task_id).
    born = insert_dispatched_handoff(
        contract_id=draft_id, agent_id=AGENT_ID, workspace=WORKSPACE,
        plan_task_id=TASK_ID, plan_id=PLAN_ID, kind="task_execution",
        session_id="sess-c", db_path=db,
    )
    assert agent_contract_handoff_state(draft_id, db_path=db) == "DISPATCHED"

    # The backstop fires -> reap the orphan to a NON-COMPLETE verdict.
    persist_handoff(
        parsed_contract=envelope,
        agent_output="crashed after building COMPLETE",
        task_info=_task_info(db),
        session_id="sess-c",
    )

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, agent_state, raw_handoff_json FROM agent_contract_handoffs "
            "WHERE contract_id = ?",
            (draft_id,),
        ).fetchall()
    finally:
        con.close()

    assert len(rows) == 1, "the orphan is reaped in place, not duplicated"
    assert rows[0]["id"] == born["handoff_id"]
    # The load-bearing invariant: NEVER a false COMPLETE for a bound producer.
    assert rows[0]["agent_state"] != "COMPLETE"
    assert rows[0]["agent_state"] == "IN_PROGRESS"
    flags = json.loads(rows[0]["raw_handoff_json"])
    assert flags.get("degraded") is True
    assert flags.get("reaped") is True
