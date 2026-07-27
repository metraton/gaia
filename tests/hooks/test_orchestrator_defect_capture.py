#!/usr/bin/env python3
"""Both orchestrator-origin failure channels land as triage-visible defects.

A subagent turn reports its own failures through ``episode_anomalies`` (the
``origin=subagent`` channel). Two failures are never reported that way because
the subagent never gets to speak: a dispatch the harness cuts mid-turn
(``agent.cut``, written by ``task_result_observer``) and a contract the
SubagentStop gate rejects outright (``agent.contract_rejected``, written by
``evaluate_contract_gate``). Both are recorded by the ORCHESTRATOR side into
``harness_events`` and must reach the reader (``read_defects``) tagged
``origin=orchestrator`` -- distinguishable from a subagent-reported row, not
merely present somewhere in the table.

Every seeded row here goes through an isolated, per-test substrate (the
autouse ``GAIA_DATA_DIR`` fixture in ``tests/conftest.py``), never the
developer's own ``~/.gaia``. The one exception is
``TestRealProductionCutRows``, which deliberately reads the REAL substrate at
``~/.gaia/gaia.db`` -- read-only, via ``read_defects`` -- to confirm the
harness-cut case is not merely reproducible in a fixture but already landing
in production today.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = _REPO_ROOT / "hooks"
for _p in (str(_HOOKS_DIR), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import (  # noqa: E402
    CONTRACT_REJECTED_EVENT,
    evaluate_contract_gate,
)
from modules.agents.task_result_observer import (  # noqa: E402
    AGENT_CUT_EVENT,
    observe_task_result,
)
from gaia.paths import db_path  # noqa: E402
from gaia.store.reader import read_defects  # noqa: E402
from gaia.store.writer import (  # noqa: E402
    insert_episode,
    insert_episode_anomaly,
    write_harness_event,
)

WORKSPACE = "orchestrator-defect-ws"

# A completed Task/Agent result carrying no fence -- the minimal shape
# observe_task_result needs to recognize the harness-cut signature. The
# exhaustive shape coverage (background dispatch, visible failure, flat
# result...) already lives in tests/hooks/test_task_cut_detection.py; this
# module only needs one real instance of the signature to prove where it
# lands, not to re-verify how it is detected.
_CUT_TASK_INPUT = {"subagent_type": "gaia-system"}
_CUT_TASK_RESPONSE = {
    "status": "completed",
    "content": [{"type": "text", "text": "Now I'll finalize the contract..."}],
}


def _seed_harness_cut() -> None:
    """Drive the real production path that writes an agent.cut defect."""
    cut = observe_task_result(
        {
            "tool_name": "Agent",
            "session_id": "s-orchestrator-defect",
            "tool_input": _CUT_TASK_INPUT,
            "tool_response": _CUT_TASK_RESPONSE,
        }
    )
    assert cut is not None, "fixture drifted: the response no longer reads as a cut"


def _seed_contract_rejection() -> None:
    """Drive the real production path that writes a contract_rejected defect."""
    verdict = evaluate_contract_gate(None, agent_type="gaia-system")
    assert verdict.rejected is True, "fixture drifted: an unparseable contract must reject"


def _seed_subagent_defect(defect_type: str = "agent_reported_defect",
                          severity: str = "critical") -> None:
    """A subagent-reported defect, via the channel a subagent actually uses."""
    episode_id = "ep-orchestrator-defect-test"
    insert_episode(
        WORKSPACE, episode_id,
        {"timestamp": "2026-07-26T09:00:00+00:00", "agent": "developer"},
        db_path=db_path(),
    )
    insert_episode_anomaly(
        WORKSPACE, episode_id,
        {
            "timestamp": "2026-07-26T09:00:01+00:00",
            "type": defect_type,
            "severity": severity,
            "message": "seeded subagent-origin defect",
        },
        db_path=db_path(),
    )


class TestHarnessCutLandsAsOrchestratorDefect:
    def test_seeded_cut_is_visible_via_read_defects_as_orchestrator_origin(self):
        _seed_harness_cut()

        rows = read_defects(origin="orchestrator", db_path=db_path())

        assert len(rows) == 1, f"expected exactly one {AGENT_CUT_EVENT} row"
        assert rows[0]["type"] == AGENT_CUT_EVENT
        assert rows[0]["origin"] == "orchestrator"
        assert rows[0]["severity"] == "warning"


class TestRealProductionCutRows:
    """Read-only: confirms the cut case is not just reproducible, it already
    happened. No write of any kind touches this path."""

    def test_the_live_agent_cut_rows_are_orchestrator_origin_and_warning(self):
        real_db = Path.home() / ".gaia" / "gaia.db"
        if not real_db.exists():
            pytest.skip(f"no real substrate at {real_db} on this machine")

        rows = read_defects(
            origin="orchestrator",
            type=AGENT_CUT_EVENT,
            workspace=None,
            limit=10_000,
            db_path=real_db,
        )

        assert len(rows) > 0, (
            f"expected at least one real {AGENT_CUT_EVENT} row already recorded "
            "in production"
        )
        assert all(row["origin"] == "orchestrator" for row in rows)
        assert all(row["severity"] == "warning" for row in rows)


class TestContractRejectionLandsAsOrchestratorDefect:
    def test_seeded_rejection_is_visible_via_read_defects_as_orchestrator_origin(self):
        _seed_contract_rejection()

        rows = read_defects(origin="orchestrator", db_path=db_path())

        assert len(rows) == 1, f"expected exactly one {CONTRACT_REJECTED_EVENT} row"
        assert rows[0]["type"] == CONTRACT_REJECTED_EVENT
        assert rows[0]["origin"] == "orchestrator"
        assert rows[0]["severity"] == "error"


class TestOriginSeparatesOrchestratorFromSubagent:
    """The property that matters is not that both land -- it's that origin
    tells them apart from a defect a subagent itself reported."""

    def test_orchestrator_and_subagent_rows_coexist_but_stay_distinguishable(self):
        _seed_harness_cut()
        _seed_contract_rejection()
        _seed_subagent_defect()

        everything = read_defects(origin="all", db_path=db_path())
        assert {row["origin"] for row in everything} == {"orchestrator", "subagent"}
        assert {row["type"] for row in everything} == {
            AGENT_CUT_EVENT, CONTRACT_REJECTED_EVENT, "agent_reported_defect",
        }

        orchestrator_only = read_defects(origin="orchestrator", db_path=db_path())
        assert {row["type"] for row in orchestrator_only} == {
            AGENT_CUT_EVENT, CONTRACT_REJECTED_EVENT,
        }
        assert all(row["origin"] == "orchestrator" for row in orchestrator_only)

        subagent_only = read_defects(origin="subagent", db_path=db_path())
        assert {row["type"] for row in subagent_only} == {"agent_reported_defect"}
        assert all(row["origin"] == "subagent" for row in subagent_only)


class TestSeverityGatesOrchestratorInclusion:
    """An orchestrator-origin event graded info/debug is ordinary telemetry,
    not a defect -- the same rule the two seeded defect types rely on to be
    visible at all (warning / error, both strictly above the floor)."""

    @pytest.mark.parametrize("severity", ["info", "debug"])
    def test_an_orchestrator_event_at_or_below_info_is_not_a_defect(self, severity):
        write_harness_event(
            event_type="agent.dispatch",
            source="hook",
            agent="gaia-system",
            result="ordinary telemetry, not a defect",
            severity=severity,
            workspace=WORKSPACE,
            db_path=db_path(),
        )

        rows = read_defects(origin="orchestrator", db_path=db_path())

        assert rows == [], (
            f"a severity={severity!r} orchestrator event must never be read as "
            "a defect"
        )

    def test_the_same_event_type_at_warning_is_included(self):
        """Pins that the exclusion above is about severity, not event type."""
        write_harness_event(
            event_type="agent.dispatch",
            source="hook",
            agent="gaia-system",
            result="now grades as abnormal",
            severity="warning",
            workspace=WORKSPACE,
            db_path=db_path(),
        )

        rows = read_defects(origin="orchestrator", db_path=db_path())

        assert len(rows) == 1
        assert rows[0]["type"] == "agent.dispatch"
        assert rows[0]["origin"] == "orchestrator"
