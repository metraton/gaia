#!/usr/bin/env python3
"""
Integration tests for M4: handoff persistence (agent-contract-handoff brief).

Covers:
  T4.3-a: SubagentStop hook inserts an agent_contract_handoffs row.
  T4.3-b: Envelope with approval_request inserts an approvals row too.
  T4.3-c: DB write failure does NOT crash the hook.
  T4.3-d: trg_pcc_history trigger captures before/after payloads on UPDATE.
  M4 gate reason (CAMBIO 1/2): the full-verdict gate's rejection reason is
    grouped BY NATURE (Faltan / Inválidos) and carries the honest-failure
    signpost on a VERIFICATION_RESULT defect -- delivered verbatim to stderr
    (exit 2) through the real subagent_stop wire, with CANONICAL_REPAIR_MESSAGE
    and the raw code strings still embedded.
"""

import io
import json
import sqlite3
import sys
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Path setup (follows project conventions from other integration tests)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"

sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(REPO_ROOT))

from tests.fixtures.db_helpers import (
    bootstrap_gaia_schema,
    bootstrap_m4_schema,
    seed_workspace,
    seed_agent_perms,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path):
    """Return a bootstrapped test DB path with M4 schema."""
    db = tmp_path / "gaia_test.db"
    bootstrap_gaia_schema(db)
    bootstrap_m4_schema(db)
    seed_workspace(db, "test-ws")
    seed_agent_perms(db, "test-agent", ["*"], ["*"])
    return db


def _minimal_envelope(agent_state: str = "COMPLETE") -> dict:
    """Return a minimal agent_contract_handoff envelope (legacy form for simplicity)."""
    return {
        "agent_status": {
            "agent_state": agent_state,
            "agent_id": "a7e5123456",
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": {
            "patterns_checked": [],
            "files_checked": [],
            "commands_run": [],
            "key_outputs": [],
            "verbatim_outputs": [],
            "cross_layer_impacts": [],
            "open_gaps": [],
            "verification": {
                "method": "self-review",
                "checks": ["test check"],
                "result": "pass",
                "details": "test detail",
            },
        },
        "consolidation_report": None,
        "approval_request": None,
    }


def _envelope_with_approval(approval_id: str) -> dict:
    """Return an envelope that carries an approval_request."""
    env = _minimal_envelope()
    env["approval_request"] = {
        "approval_id": approval_id,
        "operation": "test op",
        "exact_content": "echo hello",
        "scope": "COMMAND_SET",
        "risk_level": "T3",
        "rollback": "n/a",
        "verification": "check output",
    }
    return env


# ---------------------------------------------------------------------------
# T4.3-a: hook writes handoff row
# ---------------------------------------------------------------------------

def test_hook_inserts_handoff_row(tmp_db):
    """SubagentStop hook inserts one agent_contract_handoffs row."""
    from hooks.subagent_stop import subagent_stop_hook

    envelope = _minimal_envelope("COMPLETE")
    agent_output = (
        "Final response\n\n"
        "```agent_contract_handoff\n"
        + json.dumps(envelope) + "\n"
        "```\n"
    )

    task_info = {
        "task_id": "T001",
        "agent": "test-agent",
        "agent_id": "a7e5123456",
        "workspace": "test-ws",
        "db_path": str(tmp_db),
    }

    result = subagent_stop_hook(task_info, agent_output)
    # Hook must not crash
    assert result.get("success") is not False

    con = sqlite3.connect(str(tmp_db))
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM agent_contract_handoffs").fetchall()
    con.close()

    assert len(rows) >= 1, "Expected at least one handoff row"
    row = rows[0]
    assert row["agent_id"] in ("a7e5123456", "test-agent", "unknown")
    assert row["workspace"] == "test-ws"
    assert row["agent_state"] in ("COMPLETE", "IN_PROGRESS", "APPROVAL_REQUEST", "BLOCKED", "NEEDS_INPUT")
    assert row["raw_handoff_json"]  # non-empty JSON blob


# ---------------------------------------------------------------------------
# T4.3-b: envelope with approval_request inserts approvals row
# ---------------------------------------------------------------------------

def test_hook_inserts_approval_row_when_approval_request_present(tmp_db):
    """When envelope carries approval_request, an approvals row is written."""
    from hooks.subagent_stop import subagent_stop_hook

    approval_id = "deadbeef" * 4  # 32-char hex

    # Seed an approval_grants row so the FK is satisfied
    con = sqlite3.connect(str(tmp_db))
    con.execute(
        "INSERT OR IGNORE INTO approval_grants "
        "(approval_id, agent_id, session_id, command_set_json, status) "
        "VALUES (?, ?, ?, ?, ?)",
        (approval_id, "a7e5123456", "sess-001",
         json.dumps([{"command": "echo hello", "rationale": "test"}]),
         "CONSUMED"),
    )
    con.commit()
    con.close()

    envelope = _envelope_with_approval(approval_id)
    agent_output = (
        "Final response\n\n"
        "```agent_contract_handoff\n"
        + json.dumps(envelope) + "\n"
        "```\n"
    )

    task_info = {
        "task_id": "T002",
        "agent": "test-agent",
        "agent_id": "a7e5123456",
        "workspace": "test-ws",
        "db_path": str(tmp_db),
    }

    result = subagent_stop_hook(task_info, agent_output)
    assert result.get("success") is not False

    con = sqlite3.connect(str(tmp_db))
    con.row_factory = sqlite3.Row

    handoffs = con.execute("SELECT id FROM agent_contract_handoffs").fetchall()
    assert len(handoffs) >= 1

    handoff_id = handoffs[0]["id"]
    approvals = con.execute(
        "SELECT * FROM agent_contract_handoff_approvals WHERE handoff_id = ?",
        (handoff_id,),
    ).fetchall()
    con.close()

    assert len(approvals) >= 1, "Expected at least one approvals row"
    assert approvals[0]["approval_id"] == approval_id
    assert approvals[0]["decision"] in ("APPROVED", "REJECTED", "EXPIRED", "REVOKED")


# ---------------------------------------------------------------------------
# T4.3-c: DB write failure does NOT crash the hook
# ---------------------------------------------------------------------------

def test_db_write_failure_does_not_crash_hook(tmp_db):
    """If the DB write raises, the hook continues and returns success=True."""
    from hooks.subagent_stop import subagent_stop_hook
    from gaia.store import writer

    envelope = _minimal_envelope("COMPLETE")
    agent_output = (
        "Final response\n\n"
        "```agent_contract_handoff\n"
        + json.dumps(envelope) + "\n"
        "```\n"
    )

    task_info = {
        "task_id": "T003",
        "agent": "test-agent",
        "agent_id": "a7e5123456",
        "workspace": "test-ws",
        "db_path": str(tmp_db),
    }

    with patch.object(writer, "insert_agent_contract_handoff", side_effect=RuntimeError("DB exploded")):
        result = subagent_stop_hook(task_info, agent_output)

    # Hook must NOT crash; it should still return normally
    assert "success" in result
    # Accept True (ran cleanly) or an error dict (other errors unrelated to DB)
    # Key invariant: the hook did not raise an exception
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# T4.3-d: trigger captures before/after payloads on UPDATE
# ---------------------------------------------------------------------------

def test_trg_pcc_history_fires_on_update(tmp_db):
    """trg_pcc_history inserts a history row when project_context_contracts is UPDATEd."""
    con = sqlite3.connect(str(tmp_db))
    con.execute("PRAGMA foreign_keys = ON")

    # Insert initial PCC row (workspace already seeded by fixture)
    con.execute(
        "INSERT OR IGNORE INTO project_context_contracts "
        "(contract_name, workspace, payload) "
        "VALUES (?, ?, ?)",
        ("test_contract", "test-ws", json.dumps({"version": 1})),
    )
    con.commit()

    # UPDATE the payload to fire the trigger
    con.execute(
        "UPDATE project_context_contracts SET payload = ? "
        "WHERE contract_name = ? AND workspace = ?",
        (json.dumps({"version": 2}), "test_contract", "test-ws"),
    )
    con.commit()

    history = con.execute(
        "SELECT * FROM project_context_contracts_history "
        "WHERE contract_key = ? AND workspace = ?",
        ("test_contract", "test-ws"),
    ).fetchall()
    con.close()

    assert len(history) == 1, f"Expected 1 history row, got {len(history)}"

    con2 = sqlite3.connect(str(tmp_db))
    con2.row_factory = sqlite3.Row
    h = dict(con2.execute(
        "SELECT * FROM project_context_contracts_history "
        "WHERE contract_key = ? AND workspace = ?",
        ("test_contract", "test-ws"),
    ).fetchone())
    con2.close()

    assert h["before_payload_json"] == json.dumps({"version": 1})
    assert h["after_payload_json"] == json.dumps({"version": 2})
    assert h["contract_key"] == "test_contract"
    assert h["workspace"] == "test-ws"


# ---------------------------------------------------------------------------
# M4 gate reason (CAMBIO 1/2): grouped reason + honest signpost on the wire
# ---------------------------------------------------------------------------
#
# The REAL full-verdict gate (adapters.claude_code.evaluate_contract_gate,
# default ramp ON) produces the rejection reason; that reason is then delivered
# through the REAL subagent_stop wire (_handle_subagent_stop) to stderr with
# exit 2. Only the adapter's get_adapter is stubbed to hand the real
# gate-driven HookResponse to the printer -- the gate output under test is not
# faked. No DB is touched (form-layer rejections skip the layer-2 cross-check),
# so these run without the tmp_db fixture.

from adapters.claude_code import evaluate_contract_gate  # noqa: E402
from adapters.types import HookResponse  # noqa: E402


def _rejected_envelope_missing_and_invalid() -> dict:
    """IN_PROGRESS with BOTH a MISSING_FIELD defect (next_action absent) and a
    value-shape defect (agent_id malformed) -- drives the two-group rendering."""
    env = _minimal_envelope("IN_PROGRESS")
    env["agent_status"]["agent_id"] = "BADID"
    del env["agent_status"]["next_action"]
    return env


def _rejected_envelope_failed_verification() -> dict:
    """COMPLETE whose verification.result is 'fail' -- drives VERIFICATION_RESULT
    and the honest-failure signpost."""
    env = _minimal_envelope("COMPLETE")
    env["evidence_report"]["verification"]["result"] = "fail"
    env["evidence_report"]["verification"]["details"] = "the suite did not pass"
    return env


def _reason_via_wire(monkeypatch, envelope: dict) -> str:
    """Run the REAL gate on ``envelope`` and push the resulting HookResponse
    through the REAL subagent_stop._handle_subagent_stop printer, returning the
    text delivered to stderr. Asserts the exit code is 2 (a hard rejection)."""
    import subagent_stop

    v = evaluate_contract_gate(envelope, ramp_enabled=True)
    assert v.rejected is True

    response = HookResponse(
        output={
            "success": True,
            "contract_rejected": True,
            "contract_rejection_reason": v.rejection_reason,
        },
        exit_code=2,
    )

    class _StubAdapter:
        def adapt_subagent_stop(self, event):
            return response

    monkeypatch.setattr(subagent_stop, "get_adapter", lambda: _StubAdapter())

    buf = io.StringIO()
    with redirect_stderr(buf):
        with pytest.raises(SystemExit) as exc:
            subagent_stop._handle_subagent_stop(event=None)
    assert exc.value.code == 2
    return buf.getvalue()


def test_grouped_reason_reaches_stderr_via_wire(monkeypatch):
    """CAMBIO 1: MISSING_FIELD -> 'Faltan:', value codes -> 'Inválidos:', two
    labeled lines delivered to stderr instead of one flat list."""
    stderr_text = _reason_via_wire(
        monkeypatch, _rejected_envelope_missing_and_invalid()
    )
    assert "Faltan:" in stderr_text
    assert "Inválidos:" in stderr_text

    faltan_line = next(
        l for l in stderr_text.splitlines() if l.startswith("Faltan:")
    )
    invalid_line = next(
        l for l in stderr_text.splitlines() if l.startswith("Inválidos:")
    )
    assert "MISSING_FIELD" in faltan_line
    assert "next_action" in faltan_line
    assert "AGENT_ID_FORMAT" in invalid_line
    assert "agent_id" in invalid_line
    # Groups do not bleed into each other.
    assert "MISSING_FIELD" not in invalid_line
    assert "AGENT_ID_FORMAT" not in faltan_line


def test_honest_signpost_reaches_stderr_on_verification_defect(monkeypatch):
    """CAMBIO 2: a VERIFICATION_RESULT defect appends the honest-failure
    signpost -- point at retry/block, never a nudge to fake a pass."""
    stderr_text = _reason_via_wire(
        monkeypatch, _rejected_envelope_failed_verification()
    )
    assert "VERIFICATION_RESULT" in stderr_text
    assert "NO emitas COMPLETE" in stderr_text
    assert "IN_PROGRESS" in stderr_text
    assert "BLOCKED" in stderr_text
    assert "verification.result='fail'" in stderr_text


def test_signpost_absent_without_verification_defect(monkeypatch):
    """The signpost is CONDITIONAL: a non-verification rejection does not carry
    it (no false nudge on unrelated defects)."""
    stderr_text = _reason_via_wire(
        monkeypatch, _rejected_envelope_missing_and_invalid()
    )
    assert "NO emitas COMPLETE" not in stderr_text


def test_prior_assertions_stay_green_on_the_wire(monkeypatch):
    """Byte-stability guard: CANONICAL_REPAIR_MESSAGE and the raw code strings
    (e.g. AGENT_ID_FORMAT) remain embedded verbatim after grouping/signpost."""
    from gaia.contract.validator import CANONICAL_REPAIR_MESSAGE

    stderr_text = _reason_via_wire(
        monkeypatch, _rejected_envelope_missing_and_invalid()
    )
    assert CANONICAL_REPAIR_MESSAGE in stderr_text
    assert "AGENT_ID_FORMAT" in stderr_text
