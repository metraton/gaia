#!/usr/bin/env python3
"""Minimum coverage for the contract-gate rejection defect event.

Locks the three properties the write path must hold:

  (a) a rejecting verdict lands exactly one ``harness_events`` row of type
      ``agent.contract_rejected`` at severity ``error`` -- strictly above
      ``info``, which is what makes it visible to the triage reader;
  (b) the write touches ``harness_events`` ONLY: no ``episodes`` row is created,
      directly or transitively, so nothing fabricates a turn that never ran;
  (c) it is non-blocking and observation-only -- an accepting verdict writes
      nothing, and a write that fails leaves the verdict (and therefore the
      exit 2 that forces the subagent to repair) exactly as it was.

The DB these tests touch is the per-test isolated substrate installed by the
autouse ``GAIA_DATA_DIR`` fixture in tests/conftest.py, not ``~/.gaia``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = _REPO_ROOT / "hooks"
for _p in (str(_HOOKS_DIR), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import (  # noqa: E402
    CONTRACT_REJECTED_EVENT,
    evaluate_contract_gate,
)
from gaia.paths import db_path  # noqa: E402
from gaia.store import writer as _store_writer  # noqa: E402
from gaia.store.reader import read_defects  # noqa: E402
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)


def _valid_envelope() -> dict:
    return {
        "agent_status": {
            "agent_state": "COMPLETE",
            "agent_id": valid_agent_id("a1234abcd"),
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": dict(
            {k: [] for k in _EVIDENCE_KEYS},
            verification={"method": "test", "result": "pass", "details": "green"},
        ),
        "consolidation_report": None,
        "approval_request": None,
    }


def _rows(event_type: str = CONTRACT_REJECTED_EVENT) -> list:
    con = _store_writer._connect(db_path())
    try:
        return con.execute(
            "SELECT type, severity, agent, result, payload FROM harness_events "
            "WHERE type = ?",
            (event_type,),
        ).fetchall()
    finally:
        con.close()


def _episode_count() -> int:
    con = _store_writer._connect(db_path())
    try:
        return con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    finally:
        con.close()


class TestRejectionLandsAnErrorEvent:
    def test_rejection_writes_one_error_row_visible_to_triage(self):
        """An unparseable contract rejects and lands one error-severity row."""
        verdict = evaluate_contract_gate(None, agent_type="gaia-system")
        assert verdict.rejected is True

        rows = _rows()
        assert len(rows) == 1, f"expected one {CONTRACT_REJECTED_EVENT} row"
        assert rows[0]["severity"] == "error", (
            "severity must be strictly above info: the triage reader selects the "
            "orchestrator channel by severity threshold, not by event type"
        )
        assert rows[0]["agent"] == "gaia-system"

        defects = read_defects(origin="orchestrator", db_path=db_path())
        assert [d["type"] for d in defects] == [CONTRACT_REJECTED_EVENT]
        assert defects[0]["origin"] == "orchestrator"

    def test_write_creates_no_episode_row(self):
        """R6: the channel takes no episode_id, so no parent turn is fabricated."""
        before = _episode_count()
        evaluate_contract_gate(None, agent_type="gaia-system")
        assert _episode_count() == before

    def test_accepted_verdict_writes_nothing(self):
        verdict = evaluate_contract_gate(_valid_envelope(), agent_type="gaia-system")
        assert verdict.rejected is False
        assert _rows() == []


class TestNonBlocking:
    def test_write_failure_does_not_alter_the_verdict(self, monkeypatch):
        """Telemetry failure must not change the rejection the gate returns."""
        import modules.events.event_writer as event_writer

        def _boom(*_args, **_kwargs):
            raise RuntimeError("substrate unavailable")

        monkeypatch.setattr(event_writer.EventWriter, "write_event", _boom)

        verdict = evaluate_contract_gate(None, agent_type="gaia-system")
        assert verdict.rejected is True
        assert "[CONTRACT REJECTED]" in verdict.rejection_reason
        assert _rows() == []
