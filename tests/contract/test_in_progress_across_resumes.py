#!/usr/bin/env python3
"""AC-19 -- M4 IN_PROGRESS across resumes.

Brief: contract-as-managed-data-agent-contract-handoff-agnostico-por-cli, T17.

AC-19 (verbatim): "plan_status permanece IN_PROGRESS a traves de N mensajes y
solo finaliza en estado terminal; el gate de SubagentStop NO rechaza un
IN_PROGRESS legitimo entre mensajes."

Three guarantees, each pinned below:

1. THE ON GATE DOES NOT REJECT A LEGITIMATE IN_PROGRESS. The full-verdict core
   requires ``verification.result == pass`` only for COMPLETE; a shape-valid
   IN_PROGRESS envelope is accepted, N times over.

2. THE DRAFT STAYS IN_PROGRESS ACROSS N RESUMES. The resume substrate (T6)
   reads are non-consuming: the same draft is recoverable after N resumes and
   its plan_status stays IN_PROGRESS until a terminal finalize flips it.

3. THE STATE MACHINE HOLDS IN_PROGRESS ACROSS N RESUMES. A legitimate resume
   (``is_resume=True``) does NOT accumulate toward the anti-parking retry cap,
   so N > _MAX_IN_PROGRESS_RETRIES consecutive IN_PROGRESS resumes stay legal;
   the ordinary within-turn retry path (is_resume=False) is unchanged.
"""

import sys
from pathlib import Path

import pytest

PKG_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = PKG_ROOT / "hooks"
for _p in (str(HOOKS_DIR), str(PKG_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import evaluate_contract_gate  # noqa: E402
from gaia.contract import drafts as drafts_mod  # noqa: E402
from modules.agents.state_tracker import (  # noqa: E402
    _MAX_IN_PROGRESS_RETRIES,
    _STATE_FILE,
    track_transition,
)


_EVIDENCE_KEYS = (
    "patterns_checked",
    "files_checked",
    "commands_run",
    "key_outputs",
    "verbatim_outputs",
    "cross_layer_impacts",
    "open_gaps",
)


def _evidence():
    return {k: [] for k in _EVIDENCE_KEYS}


def _in_progress_envelope(agent_id="a1b2c3", next_action="continue"):
    return {
        "agent_status": {
            "plan_status": "IN_PROGRESS",
            "agent_id": agent_id,
            "pending_steps": ["step-1"],
            "next_action": next_action,
        },
        "evidence_report": _evidence(),
        "consolidation_report": None,
        "approval_request": None,
    }


def _complete_envelope(agent_id="a1b2c3"):
    env = _in_progress_envelope(agent_id, next_action="done")
    env["agent_status"]["plan_status"] = "COMPLETE"
    env["evidence_report"]["verification"] = {
        "method": "test",
        "result": "pass",
        "details": "verified",
    }
    return env


@pytest.fixture(autouse=True)
def clean_state_file():
    if _STATE_FILE.exists():
        _STATE_FILE.unlink()
    yield
    if _STATE_FILE.exists():
        _STATE_FILE.unlink()


@pytest.fixture()
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    return tmp_path


class TestGateAcceptsInProgress:
    def test_on_gate_does_not_reject_in_progress(self):
        v = evaluate_contract_gate(_in_progress_envelope(), ramp_enabled=True)
        assert v.rejected is False
        assert v.anomalies == ()

    def test_on_gate_accepts_in_progress_across_n_resumes(self):
        # The same legitimate IN_PROGRESS envelope, evaluated N times (as N
        # resumes would), is never rejected by the full-verdict gate.
        for i in range(6):
            v = evaluate_contract_gate(
                _in_progress_envelope(next_action=f"continue step {i}"),
                ramp_enabled=True,
            )
            assert v.rejected is False, f"resume {i} was wrongly rejected"

    def test_off_gate_also_accepts_in_progress(self):
        v = evaluate_contract_gate(_in_progress_envelope(), ramp_enabled=False)
        assert v.rejected is False


class TestDraftHoldsInProgressAcrossResumes:
    def test_non_consuming_resume_keeps_in_progress(self, isolated_data_dir):
        agent_id = "a1b2c3"
        draft_id = drafts_mod.mint_draft_id(agent_id)
        drafts_mod.save_draft(draft_id, _in_progress_envelope(agent_id))

        # N non-consuming resumes: each recovers the SAME draft, still
        # IN_PROGRESS. Nothing finalizes it.
        for _ in range(5):
            resolved = drafts_mod.resolve_draft_id(agent_id=agent_id)
            assert resolved == draft_id
            loaded = drafts_mod.load_draft(draft_id)
            assert loaded is not None
            assert loaded["agent_status"]["plan_status"] == "IN_PROGRESS"

        # Only a terminal write flips it -- and only then.
        drafts_mod.save_draft(draft_id, _complete_envelope(agent_id))
        loaded = drafts_mod.load_draft(draft_id)
        assert loaded["agent_status"]["plan_status"] == "COMPLETE"


class TestStateMachineHoldsAcrossResumes:
    def test_resume_does_not_trip_retry_cap(self):
        # A resume is not a retry: N > _MAX_IN_PROGRESS_RETRIES consecutive
        # IN_PROGRESS resumes stay legal and never exceed the baseline count.
        n = _MAX_IN_PROGRESS_RETRIES + 4
        first = track_transition("a12345", "IN_PROGRESS", is_resume=True)
        assert first.valid is True
        for i in range(n):
            r = track_transition("a12345", "IN_PROGRESS", is_resume=True)
            assert r.valid is True, f"resume {i} wrongly rejected: {r.error}"
            assert r.error == ""
            assert r.in_progress_count == 1

    def test_resume_then_terminal_finalize(self):
        for _ in range(_MAX_IN_PROGRESS_RETRIES + 3):
            assert track_transition("a12345", "IN_PROGRESS", is_resume=True).valid
        # Only a terminal state finalizes -- and it is legal from IN_PROGRESS.
        r = track_transition("a12345", "COMPLETE")
        assert r.valid is True
        assert r.previous_state == "IN_PROGRESS"

    def test_default_retry_cap_unchanged_when_not_resume(self):
        # The anti-parking cap still governs the ordinary retry path.
        track_transition("a99999", "IN_PROGRESS")
        track_transition("a99999", "IN_PROGRESS")  # count = 2
        r = track_transition("a99999", "IN_PROGRESS")  # count = 3 > cap
        assert r.valid is False
        assert "retry limit exceeded" in r.error.lower()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
