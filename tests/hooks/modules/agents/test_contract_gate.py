#!/usr/bin/env python3
"""AC-9 -- M4 full-verdict SubagentStop contract gate behind a ramp flag.

Brief: contract-as-managed-data-agent-contract-handoff-agnostico-por-cli, T16.

AC-9 (verbatim): "un envelope con agent_id malformado o sin next_action que
antes salia exit 0 ahora sale exit 2 con el mensaje de reparacion rico en
stderr; y una sola anomalia por invalidez (no dos)."

These tests pin the T16 contract:

1. RAMP FLAG DEFAULT ON -- with GAIA_CONTRACT_FULL_VERDICT_GATE unset, the gate
   is the full-verdict verdict. An explicit falsy token ({"0","false","no",
   "off"}) is the one-env-var rollback to the legacy 3-case Option B verdict.
2. OFF (explicit falsy) preserves the legacy behavior -- an envelope with a
   malformed agent_id or a missing next_action (but a present agent_status + a
   valid plan_status) passes the 3-case gate and would exit 0.
3. ON (the default) activates full-verdict -- the SAME envelopes now REJECT (the
   hook returns exit 2), driven by the SINGLE portable core
   (gaia.contract.crosscheck).
4. EXACTLY ONE anomaly per invalidity -- a single defect -> a single anomaly,
   typed off the NAMED FormErrorCode enum (not the retired token strings), and
   NOT the historical double (contract_validation_failure +
   response_contract_violation).
5. RICH repair message -> stderr -- the canonical repair block is delivered to
   stderr on exit 2 via subagent_stop._handle_subagent_stop.
6. SALVAGE-vs-VIOLATION -- a max_tokens truncation is NOT hard-rejected (the
   T11 fast-path / T9 backstop already capture a degraded row).
"""

import io
import json
import sys
from contextlib import redirect_stderr
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
PKG_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(HOOKS_DIR), str(PKG_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import (
    GATE_MODE_FULL_VERDICT,
    GATE_MODE_THREE_CASE,
    GATE_RAMP_ENV_VAR,
    STOP_REASON_TRUNCATION,
    STOP_REASON_UNKNOWN,
    STOP_REASON_VIOLATION,
    ContractGateVerdict,
    evaluate_contract_gate,
    full_verdict_gate_enabled,
)
from adapters.types import HookResponse
from gaia.contract.validator import FormErrorCode


# ---------------------------------------------------------------------------
# Envelope builders
# ---------------------------------------------------------------------------

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


def _valid_envelope():
    """A shape-valid IN_PROGRESS envelope (passes both gates)."""
    return {
        "agent_status": {
            "agent_state": "IN_PROGRESS",
            "agent_id": "a1b2c3",
            "pending_steps": [],
            "next_action": "continue the increment",
        },
        "evidence_report": _evidence(),
        "consolidation_report": None,
        "approval_request": None,
    }


def _malformed_agent_id_envelope():
    """agent_status present, plan_status valid, but agent_id does not match
    ^a[0-9a-f]{5,}$ -- the 3-case gate lets this through (exit 0), the
    full-verdict gate rejects it (AGENT_ID_FORMAT)."""
    env = _valid_envelope()
    env["agent_status"]["agent_id"] = "BADID"
    return env


def _missing_next_action_envelope():
    """agent_status present, plan_status valid, but next_action absent -- the
    3-case gate lets this through (exit 0), the full-verdict gate rejects it
    (MISSING_FIELD on agent_status.next_action)."""
    env = _valid_envelope()
    del env["agent_status"]["next_action"]
    return env


def _missing_and_invalid_envelope():
    """BOTH a MISSING_FIELD defect (next_action absent) AND a value-shape defect
    (agent_id malformed). Exercises the two-group Faltan/Inválidos rendering of
    the rejection reason (CAMBIO 1)."""
    env = _malformed_agent_id_envelope()
    del env["agent_status"]["next_action"]
    return env


def _complete_failed_verification_envelope():
    """A COMPLETE envelope whose verification.result is 'fail' -- the full-verdict
    gate rejects it (VERIFICATION_RESULT). A COMPLETE that asserts success while
    verification actually failed; drives the honest-failure signpost (CAMBIO 2)."""
    env = _valid_envelope()
    env["agent_status"]["agent_state"] = "COMPLETE"
    env["evidence_report"]["verification"] = {
        "method": "test",
        "result": "fail",
        "details": "the suite did not pass",
    }
    return env


def _declared_type_missing_field_envelope():
    """R3: an IN_PROGRESS envelope whose verification declares a deterministic
    type ('command') but omits the required 'command' field -- the full-verdict
    gate rejects it with the additive VERIFICATION_SHAPE code."""
    env = _valid_envelope()
    env["evidence_report"]["verification"] = {
        "method": "command",
        "type": "command",  # missing required 'command'
    }
    return env


# ---------------------------------------------------------------------------
# 1. Ramp flag: DEFAULT ON (cutover); explicit falsy is the rollback path
# ---------------------------------------------------------------------------

class TestRampFlagDefaultOn:
    def test_unset_env_is_on(self, monkeypatch):
        monkeypatch.delenv(GATE_RAMP_ENV_VAR, raising=False)
        assert full_verdict_gate_enabled() is True

    def test_empty_env_is_on(self, monkeypatch):
        monkeypatch.setenv(GATE_RAMP_ENV_VAR, "")
        assert full_verdict_gate_enabled() is True

    @pytest.mark.parametrize("val", ["0", "false", "FALSE", "no", "off", "Off"])
    def test_explicit_falsy_is_off(self, monkeypatch, val):
        """The one-env-var rollback path (T17): explicit falsy -> 3-case."""
        monkeypatch.setenv(GATE_RAMP_ENV_VAR, val)
        assert full_verdict_gate_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on", "On", "nope", "  "])
    def test_non_falsy_is_on(self, monkeypatch, val):
        """Default ON: anything that is not an explicit falsy token -> ON."""
        monkeypatch.setenv(GATE_RAMP_ENV_VAR, val)
        assert full_verdict_gate_enabled() is True

    def test_gate_reads_env_when_ramp_enabled_none(self, monkeypatch):
        """ramp_enabled=None -> the gate reads the env flag itself (default ON)."""
        monkeypatch.delenv(GATE_RAMP_ENV_VAR, raising=False)
        v = evaluate_contract_gate(_malformed_agent_id_envelope(), ramp_enabled=None)
        assert v.mode == GATE_MODE_FULL_VERDICT
        assert v.rejected is True  # ON -> full-verdict rejects the malformed id

    def test_drift_partial_handoff_enforced_by_default(self, monkeypatch):
        """Regression for thread_subagent_no_contract_handoff_drift.

        A handoff that PARSES (agent_status + valid plan_status) but is
        incomplete/drifted (here: missing next_action) previously exited 0 in
        the default mode -- only a critical response_contract_violation anomaly
        was raised, so recovery fell to the orchestrator via SendMessage. With
        the default flipped ON, the SAME partial handoff now forces repair (the
        gate rejects with the rich message) with NO env var set.
        """
        monkeypatch.delenv(GATE_RAMP_ENV_VAR, raising=False)
        v = evaluate_contract_gate(_missing_next_action_envelope(), ramp_enabled=None)
        assert v.mode == GATE_MODE_FULL_VERDICT
        assert v.rejected is True
        assert v.rejection_reason  # a non-empty rich repair message is delivered
        assert len(v.anomalies) == 1  # exactly one anomaly per invalidity


# ---------------------------------------------------------------------------
# 2. OFF preserves today's 3-case behavior (would exit 0)
# ---------------------------------------------------------------------------

class TestOffPreservesThreeCase:
    def test_malformed_agent_id_not_rejected_off(self):
        v = evaluate_contract_gate(_malformed_agent_id_envelope(), ramp_enabled=False)
        assert v.mode == GATE_MODE_THREE_CASE
        assert v.rejected is False
        assert v.anomalies == ()

    def test_missing_next_action_not_rejected_off(self):
        v = evaluate_contract_gate(_missing_next_action_envelope(), ramp_enabled=False)
        assert v.mode == GATE_MODE_THREE_CASE
        assert v.rejected is False
        assert v.anomalies == ()

    def test_three_case_still_rejects_its_three_cases_off(self):
        # missing block
        assert evaluate_contract_gate(None, ramp_enabled=False).rejected is True
        # missing agent_status
        assert evaluate_contract_gate(
            {"evidence_report": _evidence()}, ramp_enabled=False
        ).rejected is True
        # bad plan_status
        bad = _valid_envelope()
        bad["agent_status"]["agent_state"] = "BOGUS"
        assert evaluate_contract_gate(bad, ramp_enabled=False).rejected is True


# ---------------------------------------------------------------------------
# 3. ON activates full-verdict: previously-exit-0 -> exit-2
# ---------------------------------------------------------------------------

class TestOnFullVerdictRejects:
    def test_malformed_agent_id_rejected_on(self):
        v = evaluate_contract_gate(_malformed_agent_id_envelope(), ramp_enabled=True)
        assert v.mode == GATE_MODE_FULL_VERDICT
        assert v.rejected is True

    def test_missing_next_action_rejected_on(self):
        v = evaluate_contract_gate(_missing_next_action_envelope(), ramp_enabled=True)
        assert v.mode == GATE_MODE_FULL_VERDICT
        assert v.rejected is True

    def test_valid_envelope_not_rejected_on(self):
        v = evaluate_contract_gate(_valid_envelope(), ramp_enabled=True)
        assert v.rejected is False
        assert v.anomalies == ()


# ---------------------------------------------------------------------------
# 4. Exactly one anomaly per invalidity (not two), typed off the NAMED enum
# ---------------------------------------------------------------------------

class TestOneAnomalyPerInvalidity:
    def test_single_defect_agent_id_one_anomaly(self):
        v = evaluate_contract_gate(_malformed_agent_id_envelope(), ramp_enabled=True)
        assert len(v.anomalies) == 1
        a = v.anomalies[0]
        # Typed off the NAMED FormErrorCode enum, not a retired token string.
        assert a["code"] == FormErrorCode.AGENT_ID_FORMAT.value
        assert a["field"] == "agent_status.agent_id"
        assert a["type"] == "contract_gate_violation"
        # NOT the historical double: no legacy anomaly types appear.
        types = {an["type"] for an in v.anomalies}
        assert "response_contract_violation" not in types
        assert "contract_validation_failure" not in types

    def test_single_defect_next_action_one_anomaly(self):
        v = evaluate_contract_gate(_missing_next_action_envelope(), ramp_enabled=True)
        assert len(v.anomalies) == 1
        a = v.anomalies[0]
        assert a["code"] == FormErrorCode.MISSING_FIELD.value
        assert a["field"] == "agent_status.next_action"

    def test_two_distinct_defects_two_anomalies_one_each(self):
        """Two invalidities -> two anomalies (one per invalidity), not fanned
        out and not doubled."""
        env = _valid_envelope()
        env["agent_status"]["agent_id"] = "BADID"
        del env["agent_status"]["next_action"]
        v = evaluate_contract_gate(env, ramp_enabled=True)
        codes = [a["code"] for a in v.anomalies]
        assert FormErrorCode.AGENT_ID_FORMAT.value in codes
        assert FormErrorCode.MISSING_FIELD.value in codes
        # exactly one anomaly per invalidity: 2 defects -> 2 anomalies
        assert len(v.anomalies) == 2

    def test_single_defect_verification_shape_one_anomaly(self):
        """R3: a declared verification.type missing its required field yields
        EXACTLY one anomaly, typed off the additive VERIFICATION_SHAPE code and
        rendered in the Inválidos group (present-but-wrong, not MISSING_FIELD)."""
        v = evaluate_contract_gate(
            _declared_type_missing_field_envelope(), ramp_enabled=True
        )
        assert v.rejected is True
        assert len(v.anomalies) == 1
        a = v.anomalies[0]
        assert a["code"] == FormErrorCode.VERIFICATION_SHAPE.value
        assert a["field"] == "evidence_report.verification.command"
        assert a["type"] == "contract_gate_violation"
        # A value-shape code renders under "Inválidos:", never "Faltan:".
        assert FormErrorCode.VERIFICATION_SHAPE.value in v.rejection_reason


# ---------------------------------------------------------------------------
# 5. Rich repair message delivered to stderr on exit 2
# ---------------------------------------------------------------------------

class TestRichRepairMessageToStderr:
    def test_reason_carries_canonical_repair_block(self):
        from gaia.contract.validator import CANONICAL_REPAIR_MESSAGE

        v = evaluate_contract_gate(_malformed_agent_id_envelope(), ramp_enabled=True)
        # The rich, canonical repair block is embedded in the rejection reason.
        assert CANONICAL_REPAIR_MESSAGE in v.rejection_reason
        # And it names the specific defect (the error summary).
        assert "AGENT_ID_FORMAT" in v.rejection_reason

    def test_handle_subagent_stop_delivers_reason_to_stderr(self, monkeypatch):
        """End-to-end wire: a full-verdict rejection surfaces the rich repair
        message on stderr (exit code 2) via subagent_stop._handle_subagent_stop."""
        import subagent_stop

        v = evaluate_contract_gate(_malformed_agent_id_envelope(), ramp_enabled=True)
        assert v.rejected is True

        # Stub adapter returning the real gate-driven rejection response.
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
        stderr_text = buf.getvalue()
        # The repair guidance reached stderr (not just stdout).
        assert "AGENT_ID_FORMAT" in stderr_text
        assert "^a[0-9a-f]{5,}$" in stderr_text


# ---------------------------------------------------------------------------
# 6. Salvage-vs-violation (T10/T11): truncation is not a hard violation
# ---------------------------------------------------------------------------

class TestSalvageVsViolation:
    def test_truncation_not_rejected(self):
        v = evaluate_contract_gate(
            _malformed_agent_id_envelope(),
            ramp_enabled=True,
            stop_reason_classification=STOP_REASON_TRUNCATION,
        )
        assert v.rejected is False
        assert v.salvaged_truncation is True
        # A salvaged truncation signals no shape anomaly (already captured as a
        # degraded row) -- avoids double-signaling.
        assert v.anomalies == ()

    def test_end_turn_violation_is_rejected(self):
        v = evaluate_contract_gate(
            _malformed_agent_id_envelope(),
            ramp_enabled=True,
            stop_reason_classification=STOP_REASON_VIOLATION,
        )
        assert v.rejected is True
        assert v.salvaged_truncation is False

    def test_unknown_stop_reason_fails_closed(self):
        """Unknown stop_reason is treated as a violation (fail closed), not a
        salvage-worthy truncation the adapter cannot confirm."""
        v = evaluate_contract_gate(
            _malformed_agent_id_envelope(),
            ramp_enabled=True,
            stop_reason_classification=STOP_REASON_UNKNOWN,
        )
        assert v.rejected is True


# ---------------------------------------------------------------------------
# 7. CAMBIO 1 -- rejection reason grouped BY NATURE (Faltan / Inválidos)
# ---------------------------------------------------------------------------

class TestGroupedRejectionReason:
    def test_missing_and_invalid_grouped_into_two_labeled_lines(self):
        """MISSING_FIELD -> 'Faltan:', value-shape codes -> 'Inválidos:', each on
        its own labeled line -- not one flat '; '-joined list."""
        v = evaluate_contract_gate(_missing_and_invalid_envelope(), ramp_enabled=True)
        assert v.rejected is True
        reason = v.rejection_reason
        assert "Faltan:" in reason
        assert "Inválidos:" in reason

        faltan_line = next(l for l in reason.splitlines() if l.startswith("Faltan:"))
        invalid_line = next(l for l in reason.splitlines() if l.startswith("Inválidos:"))
        # The omitted field lands under Faltan; the malformed value under Inválidos.
        assert FormErrorCode.MISSING_FIELD.value in faltan_line
        assert "next_action" in faltan_line
        assert FormErrorCode.AGENT_ID_FORMAT.value in invalid_line
        assert "agent_id" in invalid_line
        # The groups do not bleed into each other.
        assert FormErrorCode.MISSING_FIELD.value not in invalid_line
        assert FormErrorCode.AGENT_ID_FORMAT.value not in faltan_line

    def test_only_missing_omits_the_invalid_group(self):
        v = evaluate_contract_gate(_missing_next_action_envelope(), ramp_enabled=True)
        assert v.rejected is True
        reason = v.rejection_reason
        assert "Faltan:" in reason
        assert "Inválidos:" not in reason  # empty group omitted

    def test_only_invalid_omits_the_missing_group(self):
        v = evaluate_contract_gate(_malformed_agent_id_envelope(), ramp_enabled=True)
        assert v.rejected is True
        reason = v.rejection_reason
        assert "Inválidos:" in reason
        assert "Faltan:" not in reason  # empty group omitted


# ---------------------------------------------------------------------------
# 8. CAMBIO 2 -- honest-failure signpost on a VERIFICATION_RESULT defect
# ---------------------------------------------------------------------------

class TestHonestFailureSignpost:
    _MARKER = "NO emitas COMPLETE"

    def test_signpost_present_on_verification_defect(self):
        v = evaluate_contract_gate(
            _complete_failed_verification_envelope(), ramp_enabled=True
        )
        assert v.rejected is True
        reason = v.rejection_reason
        # The defect is named and the honest path is pointed at (retry / block),
        # not a nudge to fake a pass.
        assert FormErrorCode.VERIFICATION_RESULT.value in reason
        assert self._MARKER in reason
        assert "IN_PROGRESS" in reason
        assert "BLOCKED" in reason
        assert "verification.result='fail'" in reason

    def test_signpost_absent_without_verification_defect(self):
        v = evaluate_contract_gate(_malformed_agent_id_envelope(), ramp_enabled=True)
        assert v.rejected is True
        assert self._MARKER not in v.rejection_reason


# ---------------------------------------------------------------------------
# 9. Byte-stability guard -- prior assertions stay green after grouping
# ---------------------------------------------------------------------------

class TestByteStabilityAfterGrouping:
    def test_canonical_repair_and_code_string_survive_grouping(self):
        """CAMBIO 1/2 must not disturb the embedded CANONICAL_REPAIR_MESSAGE nor
        the raw code strings that downstream assertions/log scrapers rely on."""
        from gaia.contract.validator import CANONICAL_REPAIR_MESSAGE

        v = evaluate_contract_gate(_missing_and_invalid_envelope(), ramp_enabled=True)
        assert CANONICAL_REPAIR_MESSAGE in v.rejection_reason
        assert "AGENT_ID_FORMAT" in v.rejection_reason
