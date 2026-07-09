"""
AC-11 -- stop_reason isolated in the adapter (M5, decision #5).

Proves two things:

  1. The portable core (``gaia.contract.validator`` + ``gaia.contract.
     crosscheck``) validates an envelope IDENTICALLY whether ``stop_reason``
     is absent, present with a value the adapter treats as "truncation"
     (``max_tokens``), or present with a value the adapter treats as
     "violation" (``end_turn``). The core never brands its verdict on this
     field because it never reads it -- structurally verified by scanning
     the core module *source text* for the substring, not just behaviourally
     by example.

  2. The max_tokens -> truncation / end_turn -> violation mapping lives
     ONLY in the adapter (``hooks/adapters/claude_code.py::classify_stop_reason``)
     and nowhere in the core.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gaia.contract.crosscheck import validate as validate_full
from gaia.contract.validator import validate_form


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="module")
def _hooks_on_path():
    """Add hooks/ to sys.path so ``adapters.claude_code`` resolves.

    Mirrors the pattern used by tests/hooks/adapters/*.py -- this is the only
    place in this test module that touches the hooks/ tree, and it never
    imports it into gaia.contract.* (that import boundary is what AC-2/AC-11
    protect).
    """
    root = Path(__file__).resolve().parent.parent.parent
    hooks_dir = root / "hooks"
    sys.path.insert(0, str(hooks_dir))
    yield
    try:
        sys.path.remove(str(hooks_dir))
    except ValueError:
        pass


def _valid_complete_envelope() -> dict:
    return {
        "agent_status": {
            "plan_status": "COMPLETE",
            "agent_id": "a1b2c3",
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
            "verification": {"method": "test", "result": "pass", "details": "ok"},
        },
        "consolidation_report": None,
        "approval_request": None,
    }


def _broken_envelope() -> dict:
    """A shape-invalid envelope (bad plan_status) -- the interesting case for
    stop_reason isolation, since a truncated turn is exactly where an agent's
    envelope is most likely to be incomplete/malformed."""
    env = _valid_complete_envelope()
    env["agent_status"]["plan_status"] = "BOGUS"
    return env


# ---------------------------------------------------------------------------
# 1. Core agnosticism -- behavioural proof
# ---------------------------------------------------------------------------


class TestCoreValidatesIdenticallyRegardlessOfStopReason:
    def test_valid_envelope_identical_with_and_without_stop_reason(self):
        without = validate_form(_valid_complete_envelope())

        with_max_tokens = dict(_valid_complete_envelope())
        with_max_tokens["stop_reason"] = "max_tokens"
        with_it = validate_form(with_max_tokens)

        with_end_turn = dict(_valid_complete_envelope())
        with_end_turn["stop_reason"] = "end_turn"
        with_end = validate_form(with_end_turn)

        assert without.ok == with_it.ok == with_end.ok is True
        assert without.errors == with_it.errors == with_end.errors == ()
        assert (
            without.repair_message
            == with_it.repair_message
            == with_end.repair_message
        )

    def test_broken_envelope_identical_with_and_without_stop_reason(self):
        without = validate_form(_broken_envelope())

        with_max_tokens = dict(_broken_envelope())
        with_max_tokens["stop_reason"] = "max_tokens"
        with_it = validate_form(with_max_tokens)

        with_end_turn = dict(_broken_envelope())
        with_end_turn["stop_reason"] = "end_turn"
        with_end = validate_form(with_end_turn)

        assert without.ok is False
        assert without.codes == with_it.codes == with_end.codes
        assert without.error_summary() == with_it.error_summary() == with_end.error_summary()
        assert without.repair_message == with_it.repair_message == with_end.repair_message

    def test_full_verdict_layer_identical_with_and_without_stop_reason(self, tmp_path):
        """Same proof through the composed form+crosscheck entry point
        (gaia.contract.crosscheck.validate), with no gaia.db on disk so the
        cross-check layer degrades gracefully (AC-3) and only the form
        layer's stop_reason-agnosticism is under test here."""
        db_path = tmp_path / "absent-gaia.db"

        without = validate_full(_valid_complete_envelope(), db_path=db_path)

        with_stop_reason = dict(_valid_complete_envelope())
        with_stop_reason["stop_reason"] = "max_tokens"
        with_it = validate_full(with_stop_reason, db_path=db_path)

        assert without.ok == with_it.ok is True
        assert without.form.errors == with_it.form.errors == ()
        assert without.crosscheck == with_it.crosscheck


# ---------------------------------------------------------------------------
# 2. Core agnosticism -- structural proof (source text never mentions it)
# ---------------------------------------------------------------------------


class TestCoreSourceNeverReferencesStopReason:
    def test_validator_module_source_has_no_stop_reason(self):
        import gaia.contract.validator as validator_mod

        source = Path(validator_mod.__file__).read_text()
        assert "stop_reason" not in source

    def test_crosscheck_module_source_has_no_stop_reason(self):
        import gaia.contract.crosscheck as crosscheck_mod

        source = Path(crosscheck_mod.__file__).read_text()
        assert "stop_reason" not in source

    def test_validate_form_signature_has_no_stop_reason_parameter(self):
        import inspect

        sig = inspect.signature(validate_form)
        assert "stop_reason" not in sig.parameters


# ---------------------------------------------------------------------------
# 3. Adapter owns the mapping exclusively
# ---------------------------------------------------------------------------


class TestAdapterOwnsTheStopReasonMapping:
    def test_max_tokens_maps_to_truncation(self):
        from adapters.claude_code import STOP_REASON_TRUNCATION, classify_stop_reason

        assert classify_stop_reason("max_tokens") == STOP_REASON_TRUNCATION

    def test_end_turn_maps_to_violation(self):
        from adapters.claude_code import STOP_REASON_VIOLATION, classify_stop_reason

        assert classify_stop_reason("end_turn") == STOP_REASON_VIOLATION

    @pytest.mark.parametrize("stop_reason", [None, "", "tool_use", "some_new_reason"])
    def test_unrecognized_or_absent_reason_maps_to_unknown(self, stop_reason):
        from adapters.claude_code import STOP_REASON_UNKNOWN, classify_stop_reason

        assert classify_stop_reason(stop_reason) == STOP_REASON_UNKNOWN

    def test_mapping_function_is_not_reachable_from_the_core_package(self):
        """The mapping is adapter-only: gaia.contract.* never imports it, and
        it is not re-exported from either core module's public surface."""
        import gaia.contract.crosscheck as crosscheck_mod
        import gaia.contract.validator as validator_mod

        assert "classify_stop_reason" not in dir(validator_mod)
        assert "classify_stop_reason" not in dir(crosscheck_mod)
        assert "classify_stop_reason" not in validator_mod.__all__
