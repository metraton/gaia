#!/usr/bin/env python3
"""AC-10 -- M4 ramp/rollback: flag OFF reverts to the 3-case gate WITHOUT
losing already-written drafts (safe rollback).

Brief: contract-as-managed-data-agent-contract-handoff-agnostico-por-cli, T17.

AC-10 (verbatim): "con el ramp flag apagado, el comportamiento vuelve al gate
de 3 casos SIN perder drafts ya escritos."

The gate (``evaluate_contract_gate``) is a PURE verdict function: it reads the
envelope and returns a verdict, and NEVER writes, reads, or deletes a draft.
Drafts are written independently by ``finalize`` (CLI) and the SubagentStop
backstop. Flipping the ramp flag therefore cannot lose a draft -- these tests
prove exactly that:

1. Toggling ON -> OFF restores byte-identical 3-case behavior (an envelope the
   full-verdict gate rejects is accepted again by the 3-case gate).
2. A previously-written on-disk draft survives the full ON->OFF toggle,
   untouched (same bytes, same content), including across a rejecting gate
   evaluation.
3. Rollback is one env var: with the env var unset the gate is full-verdict (the
   new default); an explicit falsy token restores the 3-case verdict.
"""

import json
import sys
from pathlib import Path

import pytest

PKG_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = PKG_ROOT / "hooks"
for _p in (str(HOOKS_DIR), str(PKG_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import (  # noqa: E402
    GATE_MODE_FULL_VERDICT,
    GATE_MODE_THREE_CASE,
    GATE_RAMP_ENV_VAR,
    evaluate_contract_gate,
    full_verdict_gate_enabled,
)
from gaia.contract import drafts as drafts_mod  # noqa: E402


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


def _malformed_agent_id_envelope():
    """agent_status present, plan_status valid, but agent_id does not match
    ^a[0-9a-f]{5,}$ -- the 3-case gate lets this through, the full-verdict gate
    rejects it (AGENT_ID_FORMAT)."""
    return {
        "agent_status": {
            "plan_status": "IN_PROGRESS",
            "agent_id": "BADID",
            "pending_steps": [],
            "next_action": "continue",
        },
        "evidence_report": _evidence(),
        "consolidation_report": None,
        "approval_request": None,
    }


@pytest.fixture()
def isolated_data_dir(tmp_path, monkeypatch):
    """Point Gaia's data substrate at a temp dir so drafts land in isolation."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    return tmp_path


class TestRampToggleRestoresThreeCase:
    def test_on_then_off_reverts_verdict(self):
        env = _malformed_agent_id_envelope()
        # ON: full-verdict rejects the malformed agent_id.
        on = evaluate_contract_gate(env, ramp_enabled=True)
        assert on.mode == GATE_MODE_FULL_VERDICT
        assert on.rejected is True
        # OFF: byte-identical 3-case behavior returns -- the SAME envelope
        # is accepted again (rollback is a true no-op restore).
        off = evaluate_contract_gate(env, ramp_enabled=False)
        assert off.mode == GATE_MODE_THREE_CASE
        assert off.rejected is False
        assert off.anomalies == ()

    def test_env_var_unset_is_full_verdict(self, monkeypatch):
        # Default flipped ON: unset env -> full-verdict (the new default).
        monkeypatch.delenv(GATE_RAMP_ENV_VAR, raising=False)
        assert full_verdict_gate_enabled() is True
        v = evaluate_contract_gate(_malformed_agent_id_envelope(), ramp_enabled=None)
        assert v.mode == GATE_MODE_FULL_VERDICT
        assert v.rejected is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "off"])
    def test_env_var_explicit_falsy_is_three_case(self, monkeypatch, val):
        # The one-env-var rollback path (T17): explicit falsy -> 3-case.
        monkeypatch.setenv(GATE_RAMP_ENV_VAR, val)
        v = evaluate_contract_gate(_malformed_agent_id_envelope(), ramp_enabled=None)
        assert v.mode == GATE_MODE_THREE_CASE
        assert v.rejected is False


class TestRollbackPreservesDrafts:
    def test_draft_survives_on_off_toggle_untouched(self, isolated_data_dir):
        # A draft written before the toggle (as finalize/backstop would).
        agent_id = "a1b2c3"
        draft_id = drafts_mod.mint_draft_id(agent_id)
        original = {
            "agent_status": {
                "plan_status": "IN_PROGRESS",
                "agent_id": agent_id,
                "pending_steps": ["step-1"],
                "next_action": "continue the increment",
            },
            "evidence_report": _evidence(),
            "consolidation_report": None,
            "approval_request": None,
        }
        drafts_mod.save_draft(draft_id, original)
        path = drafts_mod.draft_path(draft_id)
        assert path.is_file()
        bytes_before = path.read_bytes()

        # Toggle the gate ON (a rejecting evaluation) then OFF. The gate is a
        # pure verdict function; neither evaluation may touch the draft.
        rejected = evaluate_contract_gate(
            _malformed_agent_id_envelope(), ramp_enabled=True
        )
        assert rejected.rejected is True
        accepted = evaluate_contract_gate(
            _malformed_agent_id_envelope(), ramp_enabled=False
        )
        assert accepted.rejected is False

        # The draft is byte-for-byte identical and still loadable, IN_PROGRESS.
        assert path.is_file()
        assert path.read_bytes() == bytes_before
        loaded = drafts_mod.load_draft(draft_id)
        assert loaded == original
        assert loaded["agent_status"]["plan_status"] == "IN_PROGRESS"

    def test_gate_never_creates_or_removes_drafts(self, isolated_data_dir):
        # No drafts to start.
        assert drafts_mod.list_draft_ids() == []
        # Evaluate the gate in both modes on several envelopes.
        for ramp in (True, False, True, False):
            evaluate_contract_gate(_malformed_agent_id_envelope(), ramp_enabled=ramp)
        # The pure verdict function created nothing on disk.
        assert drafts_mod.list_draft_ids() == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
