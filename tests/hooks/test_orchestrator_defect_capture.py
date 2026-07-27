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
``TestRealSubstrateCutRows``, which deliberately reads the REAL substrate at
``~/.gaia/gaia.db`` -- read-only, via ``read_defects`` -- to check read-back
fidelity on rows this repo did not write. See the comment block above that
class for its precondition and why the precondition is measured outside the
reader.
"""

from __future__ import annotations

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


# ---------------------------------------------------------------------------
# Read-back fidelity against the machine's own accumulated substrate
# ---------------------------------------------------------------------------
#
# The property here is read-back fidelity on rows this commit did not write:
# agent.cut rows accumulated over time by whatever task_result_observer was
# installed when each was recorded must STILL surface through read_defects as
# origin=orchestrator / severity=warning today. The fixture-seeded test above
# cannot cover that -- it writes and reads within one commit.
#
# The precondition is measured through a channel INDEPENDENT of the code under
# test: a raw COUNT over harness_events, never read_defects. That independence
# is the entire design, and it is what stops this from becoming an inert
# sentinel that can only skip or pass:
#
#   raw count == 0 -> nothing of this type is on disk, so there is nothing to
#                     read back and the precondition is genuinely unmet: skip
#                     with the reason named. No reader regression can fabricate
#                     this state, because the count never goes through the
#                     reader.
#   raw count > 0  -> cuts ARE on disk, and every one of them must come back as
#                     an orchestrator-origin warning: assert. A reader that
#                     surfaces fewer -- a moved severity floor, a broken origin
#                     mapping, a renamed column -- FAILS here.
#
# Do NOT collapse the guard into `if not read_defects(...): skip`. Measuring the
# precondition with the code under test makes the exact regression this test
# exists to catch indistinguishable from having nothing to check, and the
# sentinel silently stops biting. TestTheRealSubstrateSentinelStillBites pins
# both directions so that collapse cannot land unnoticed.
#
# Equally deliberate: a substrate holding thousands of OTHER event types but no
# agent.cut row skips rather than fails. A machine that never suffered a harness
# cut is healthy, not defective -- requiring one to exist is the "this machine's
# history is an invariant of the suite" coupling this test was rewritten to
# remove.
_REAL_SUBSTRATE = Path.home() / ".gaia" / "gaia.db"

# Read-back cap, and therefore also the ceiling on the expected count: a
# substrate holding more cuts than this stays comparable instead of failing on
# truncation alone.
_READ_BACK_LIMIT = 10_000


def _count_recorded_cuts(substrate: Path) -> int:
    """Raw ``agent.cut`` row count, read-only and bypassing ``read_defects``."""
    con = sqlite3.connect(f"file:{substrate}?mode=ro", uri=True)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM harness_events WHERE type = ?",
            (AGENT_CUT_EVENT,),
        ).fetchone()[0]
    finally:
        con.close()


def _assert_recorded_cuts_read_back_as_orchestrator_warnings(substrate: Path) -> None:
    """Skip when no cut is recorded; assert read-back fidelity when one is."""
    if not substrate.exists():
        pytest.skip(f"precondition unmet: no substrate at {substrate} on this machine")

    try:
        recorded = _count_recorded_cuts(substrate)
    except sqlite3.OperationalError as exc:
        pytest.skip(
            f"precondition unmet: substrate at {substrate} has no queryable "
            f"harness_events table ({exc})"
        )

    if recorded == 0:
        pytest.skip(
            f"precondition unmet: substrate at {substrate} has recorded no "
            f"{AGENT_CUT_EVENT} row (raw harness_events count is 0), so there "
            "is no accumulated row to read back"
        )

    rows = read_defects(
        origin="orchestrator",
        type=AGENT_CUT_EVENT,
        workspace=None,
        limit=_READ_BACK_LIMIT,
        db_path=substrate,
    )

    assert len(rows) == min(recorded, _READ_BACK_LIMIT), (
        f"{recorded} {AGENT_CUT_EVENT} row(s) are on disk in {substrate} but "
        f"read_defects surfaced {len(rows)} -- recorded cuts stopped reading "
        "back as orchestrator-origin defects"
    )
    assert all(row["origin"] == "orchestrator" for row in rows)
    assert all(row["severity"] == "warning" for row in rows)


class TestRealSubstrateCutRows:
    """Read-only against the real substrate -- no write touches this path."""

    def test_recorded_cut_rows_read_back_as_orchestrator_origin_and_warning(self):
        _assert_recorded_cuts_read_back_as_orchestrator_warnings(_REAL_SUBSTRATE)


class TestTheRealSubstrateSentinelStillBites:
    """The guard above must tell 'nothing recorded' apart from 'recorded but no
    longer surfaced', rather than skipping its way out of both. These pin each
    branch on a synthetic substrate, so the distinction stays enforced on any
    machine -- including one whose real substrate always skips."""

    def test_recorded_cuts_that_stop_surfacing_as_defects_fail(self, tmp_path):
        substrate = tmp_path / "populated.db"
        write_harness_event(
            event_type=CONTRACT_REJECTED_EVENT, source="hook", agent="gaia-system",
            result="an unrelated defect the reader still surfaces",
            severity="error", workspace=WORKSPACE, db_path=substrate,
        )
        # Cuts on disk, graded below the reader's defect floor -- the shape a
        # writer/reader drift produces, and the failure this test owns.
        for _ in range(2):
            write_harness_event(
                event_type=AGENT_CUT_EVENT, source="hook", agent="gaia-system",
                result="a cut that no longer grades as a defect",
                severity="info", workspace=WORKSPACE, db_path=substrate,
            )

        assert _count_recorded_cuts(substrate) == 2
        assert read_defects(origin="orchestrator", db_path=substrate), (
            "fixture drifted: the substrate must be populated and readable, so "
            "the failure below is about cuts specifically, not a dead reader"
        )

        with pytest.raises(AssertionError, match="stopped reading back"):
            _assert_recorded_cuts_read_back_as_orchestrator_warnings(substrate)

    def test_a_substrate_with_events_but_no_recorded_cut_skips_with_the_reason(
        self, tmp_path,
    ):
        substrate = tmp_path / "no_cuts.db"
        write_harness_event(
            event_type="agent.dispatch", source="hook", agent="gaia-system",
            result="ordinary telemetry", severity="info",
            workspace=WORKSPACE, db_path=substrate,
        )

        assert _count_recorded_cuts(substrate) == 0
        with pytest.raises(pytest.skip.Exception,
                           match="raw harness_events count is 0"):
            _assert_recorded_cuts_read_back_as_orchestrator_warnings(substrate)

    def test_an_absent_substrate_skips_with_the_reason(self, tmp_path):
        with pytest.raises(pytest.skip.Exception, match="no substrate at"):
            _assert_recorded_cuts_read_back_as_orchestrator_warnings(
                tmp_path / "never-created.db"
            )

    def test_well_formed_recorded_cuts_pass(self, tmp_path):
        """The counterpart to the failing branch: the guard is not simply
        strict, it passes on a substrate whose cuts read back correctly."""
        substrate = tmp_path / "healthy.db"
        write_harness_event(
            event_type=AGENT_CUT_EVENT, source="hook", agent="gaia-system",
            result="a cut recorded exactly as the observer writes it",
            severity="warning", workspace=WORKSPACE, db_path=substrate,
        )

        _assert_recorded_cuts_read_back_as_orchestrator_warnings(substrate)


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
