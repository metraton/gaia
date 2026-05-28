"""
Tests for blocking contract promotions (T2.2) and T2.3 clause parsers.

T2.2 Blocking promotions:
1. verification.result must be "pass" when task_status/plan_status is COMPLETE
2. approval_request.rollback must be present when approval_request is present
3. approval_request.verification must be present when approval_request is present

T2.3 Clause parsers (positive + negative cases):
- parse_update_contracts
- parse_loop_state + _check_loop_state_blocking
- parse_rollback_executed
- parse_context_consumption
- parse_memory_suggestions
"""

import pytest
import sys
from pathlib import Path

_HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from modules.agents.contract_validator import (
    validate,
    parse_contract,
    parse_update_contracts,
    parse_loop_state,
    _check_loop_state_blocking,
    parse_rollback_executed,
    parse_context_consumption,
    parse_memory_suggestions,
)
from modules.agents.response_contract import validate_response_contract


# ---------------------------------------------------------------------------
# Helpers: build minimal valid COMPLETE contract with verification pass
# ---------------------------------------------------------------------------

def _make_complete_output(
    *,
    with_verification: bool = True,
    verification_result: str = "pass",
    with_approval_request: bool = False,
    approval_rollback: bool = True,
    approval_verification: bool = True,
    tag: str = "agent_contract_handoff",
    status_field: str = "plan_status",
) -> str:
    """Build a minimal contract output string."""
    verification_block = ""
    if with_verification:
        verification_block = (
            f',\n    "verification": {{"method": "self-review", '
            f'"checks": ["checked"], "result": "{verification_result}", "details": "ok"}}'
        )

    approval_block = "null"
    if with_approval_request:
        rollback_val = '"rollback": "git revert HEAD"' if approval_rollback else ""
        verif_val = '"verification": "confirm clean"' if approval_verification else ""
        fields = [
            '"operation": "apply changes"',
            '"exact_content": "gaia apply"',
            '"scope": "workspace"',
            '"risk_level": "HIGH"',
        ]
        if rollback_val:
            fields.append(rollback_val)
        if verif_val:
            fields.append(verif_val)
        approval_block = "{" + ", ".join(fields) + "}"

    return f"""\
```{tag}
{{
  "agent_status": {{
    "{status_field}": "COMPLETE",
    "agent_id": "a1b2c3d4e5",
    "pending_steps": [],
    "next_action": "done"
  }},
  "evidence_report": {{
    "patterns_checked": ["x"],
    "files_checked": ["f"],
    "commands_run": [],
    "key_outputs": ["k"],
    "verbatim_outputs": ["v"],
    "cross_layer_impacts": [],
    "open_gaps": []{verification_block}
  }},
  "consolidation_report": null,
  "approval_request": {approval_block}
}}
```
"""


def _make_approval_request_output(
    *,
    with_rollback: bool = True,
    with_verification: bool = True,
) -> str:
    fields = [
        '"operation": "apply iac"',
        '"exact_content": "terraform apply"',
        '"scope": "workspace"',
        '"risk_level": "HIGH"',
    ]
    if with_rollback:
        fields.append('"rollback": "terraform destroy"')
    if with_verification:
        fields.append('"verification": "confirm no drift"')
    approval_json = "{" + ", ".join(fields) + "}"
    return f"""\
```agent_contract_handoff
{{
  "agent_status": {{
    "plan_status": "APPROVAL_REQUEST",
    "agent_id": "a1b2c3d4e5",
    "pending_steps": ["apply"],
    "next_action": "wait for approval"
  }},
  "evidence_report": {{
    "patterns_checked": ["x"],
    "files_checked": ["f"],
    "commands_run": [],
    "key_outputs": ["k"],
    "verbatim_outputs": ["v"],
    "cross_layer_impacts": [],
    "open_gaps": []
  }},
  "consolidation_report": null,
  "approval_request": {approval_json}
}}
```
"""


# ============================================================================
# T2.2 Blocking promotions: validate() (contract_validator path)
# ============================================================================

class TestBlockingPromotions:
    _task_info = {}

    # -- verification.result for COMPLETE --

    def test_complete_with_verification_pass_is_valid(self):
        output = _make_complete_output(with_verification=True, verification_result="pass")
        result = validate(output, self._task_info)
        assert result.is_valid, f"Expected valid. Missing: {result.missing}"

    def test_complete_without_verification_is_invalid(self):
        output = _make_complete_output(with_verification=False)
        result = validate(output, self._task_info)
        assert not result.is_valid
        assert any("VERIFICATION" in m for m in result.missing), (
            f"Expected VERIFICATION error. Got: {result.missing}"
        )

    def test_complete_with_verification_fail_is_invalid(self):
        output = _make_complete_output(with_verification=True, verification_result="fail")
        result = validate(output, self._task_info)
        assert not result.is_valid
        assert any("VERIFICATION_RESULT_MUST_BE_PASS" in m for m in result.missing), (
            f"Expected VERIFICATION_RESULT_MUST_BE_PASS. Got: {result.missing}"
        )

    def test_in_progress_without_verification_is_valid(self):
        """IN_PROGRESS does not require verification.result."""
        output = """\
```agent_contract_handoff
{
  "agent_status": {
    "plan_status": "IN_PROGRESS",
    "agent_id": "a1b2c3d4e5",
    "pending_steps": ["s"],
    "next_action": "continue"
  },
  "evidence_report": {
    "patterns_checked": ["x"], "files_checked": ["f"],
    "commands_run": [], "key_outputs": ["k"],
    "verbatim_outputs": ["v"], "cross_layer_impacts": [], "open_gaps": ["g"]
  },
  "consolidation_report": null, "approval_request": null
}
```
"""
        result = validate(output, self._task_info)
        assert result.is_valid, f"Expected valid. Missing: {result.missing}"

    # -- approval_request.rollback (blocking) --

    def test_approval_request_with_rollback_and_verification_valid(self):
        output = _make_complete_output(
            with_approval_request=True,
            approval_rollback=True,
            approval_verification=True,
        )
        result = validate(output, self._task_info)
        assert result.is_valid, f"Expected valid. Missing: {result.missing}"

    def test_approval_request_missing_rollback_is_invalid(self):
        output = _make_complete_output(
            with_approval_request=True,
            approval_rollback=False,
            approval_verification=True,
        )
        result = validate(output, self._task_info)
        assert not result.is_valid
        assert any("ROLLBACK" in m for m in result.missing), (
            f"Expected ROLLBACK error. Got: {result.missing}"
        )

    def test_approval_request_missing_verification_is_invalid(self):
        output = _make_complete_output(
            with_approval_request=True,
            approval_rollback=True,
            approval_verification=False,
        )
        result = validate(output, self._task_info)
        assert not result.is_valid
        assert any("VERIFICATION" in m for m in result.missing), (
            f"Expected VERIFICATION error. Got: {result.missing}"
        )

    def test_approval_request_missing_both_rollback_and_verification_is_invalid(self):
        output = _make_complete_output(
            with_approval_request=True,
            approval_rollback=False,
            approval_verification=False,
        )
        result = validate(output, self._task_info)
        assert not result.is_valid
        missing_upper = [m.upper() for m in result.missing]
        assert any("ROLLBACK" in m for m in missing_upper)
        assert any("VERIFICATION" in m for m in missing_upper)

    def test_no_approval_request_null_is_valid(self):
        """approval_request: null -- no rollback/verification check applies."""
        output = _make_complete_output(with_approval_request=False)
        result = validate(output, self._task_info)
        assert result.is_valid, f"Expected valid. Missing: {result.missing}"


# ============================================================================
# T2.2 Blocking promotions: validate_response_contract() (response_contract path)
# ============================================================================

class TestResponseContractBlockingPromotions:
    def test_complete_with_verification_pass_valid(self):
        output = _make_complete_output(with_verification=True, verification_result="pass")
        result = validate_response_contract(output)
        assert result.valid, f"Expected valid. Missing: {result.missing}"

    def test_complete_without_verification_invalid(self):
        output = _make_complete_output(with_verification=False)
        result = validate_response_contract(output)
        assert not result.valid
        assert any("VERIFICATION" in m for m in result.missing)

    def test_approval_request_status_missing_rollback_invalid(self):
        output = _make_approval_request_output(with_rollback=False, with_verification=True)
        result = validate_response_contract(output)
        assert not result.valid
        assert any("ROLLBACK" in m for m in result.missing)

    def test_approval_request_status_missing_verification_invalid(self):
        output = _make_approval_request_output(with_rollback=True, with_verification=False)
        result = validate_response_contract(output)
        assert not result.valid
        assert any("VERIFICATION" in m for m in result.missing)

    def test_approval_request_status_with_both_valid(self):
        output = _make_approval_request_output(with_rollback=True, with_verification=True)
        result = validate_response_contract(output)
        assert result.valid, f"Expected valid. Missing: {result.missing}"


# ============================================================================
# T2.3 Clause parsers: parse_update_contracts
# ============================================================================

class TestParseUpdateContracts:
    def test_valid_single_entry(self):
        contract = {
            "update_contracts": [
                {"contract": "application_services", "payload": {"key": "val"}}
            ]
        }
        result = parse_update_contracts(contract)
        assert len(result) == 1
        assert result[0]["contract"] == "application_services"

    def test_valid_multiple_entries(self):
        contract = {
            "update_contracts": [
                {"contract": "c1", "payload": {}},
                {"contract": "c2", "payload": {"x": 1}},
            ]
        }
        result = parse_update_contracts(contract)
        assert len(result) == 2

    def test_absent_returns_empty_list(self):
        assert parse_update_contracts({}) == []

    def test_non_array_returns_empty_list(self):
        assert parse_update_contracts({"update_contracts": "not-an-array"}) == []

    def test_entry_missing_contract_key_skipped(self):
        contract = {
            "update_contracts": [
                {"payload": {"x": 1}},  # missing "contract"
                {"contract": "good", "payload": {}},
            ]
        }
        result = parse_update_contracts(contract)
        assert len(result) == 1
        assert result[0]["contract"] == "good"

    def test_entry_missing_payload_key_skipped(self):
        contract = {
            "update_contracts": [
                {"contract": "c1"},  # missing "payload"
                {"contract": "c2", "payload": {}},
            ]
        }
        result = parse_update_contracts(contract)
        assert len(result) == 1

    def test_non_dict_entry_skipped(self):
        contract = {
            "update_contracts": ["not-an-object", {"contract": "c1", "payload": {}}]
        }
        result = parse_update_contracts(contract)
        assert len(result) == 1

    def test_empty_array_returns_empty_list(self):
        assert parse_update_contracts({"update_contracts": []}) == []


# ============================================================================
# T2.3 Clause parsers: parse_loop_state + blocking check
# ============================================================================

class TestParseLoopState:
    def test_valid_loop_state(self):
        contract = {
            "loop_state": {
                "iteration": 2,
                "max_iterations": 5,
                "metric": 0.7,
                "threshold": 0.9,
            }
        }
        result = parse_loop_state(contract)
        assert result is not None
        assert result["iteration"] == 2
        assert result["max_iterations"] == 5
        assert result["metric"] == pytest.approx(0.7)
        assert result["threshold"] == pytest.approx(0.9)

    def test_absent_returns_none(self):
        assert parse_loop_state({}) is None

    def test_null_metric_allowed(self):
        contract = {
            "loop_state": {
                "iteration": 1,
                "max_iterations": 3,
                "metric": None,
                "threshold": None,
            }
        }
        result = parse_loop_state(contract)
        assert result is not None
        assert result["metric"] is None
        assert result["threshold"] is None

    def test_non_dict_returns_none(self):
        assert parse_loop_state({"loop_state": "invalid"}) is None

    def test_missing_iteration_returns_none(self):
        contract = {"loop_state": {"max_iterations": 5, "metric": 0.5, "threshold": 0.9}}
        # iteration key is missing entirely
        result = parse_loop_state(contract)
        assert result is None


class TestLoopStateBlockingCheck:
    def test_complete_with_incomplete_loop_is_blocking(self):
        """COMPLETE + iteration < max_iterations + metric < threshold -> error."""
        contract = {
            "loop_state": {
                "iteration": 2,
                "max_iterations": 5,
                "metric": 0.6,
                "threshold": 0.9,
            }
        }
        error = _check_loop_state_blocking(contract, "COMPLETE")
        assert error is not None
        assert "LOOP_STATE_INCOMPLETE" in error

    def test_complete_with_metric_at_threshold_not_blocking(self):
        """metric == threshold -> not blocking."""
        contract = {
            "loop_state": {
                "iteration": 2,
                "max_iterations": 5,
                "metric": 0.9,
                "threshold": 0.9,
            }
        }
        assert _check_loop_state_blocking(contract, "COMPLETE") is None

    def test_complete_with_metric_above_threshold_not_blocking(self):
        contract = {
            "loop_state": {
                "iteration": 2,
                "max_iterations": 5,
                "metric": 0.95,
                "threshold": 0.9,
            }
        }
        assert _check_loop_state_blocking(contract, "COMPLETE") is None

    def test_complete_with_null_metric_not_blocking(self):
        contract = {
            "loop_state": {
                "iteration": 2,
                "max_iterations": 5,
                "metric": None,
                "threshold": 0.9,
            }
        }
        assert _check_loop_state_blocking(contract, "COMPLETE") is None

    def test_in_progress_never_blocking(self):
        """Loop-state check only applies when status is COMPLETE."""
        contract = {
            "loop_state": {
                "iteration": 1,
                "max_iterations": 5,
                "metric": 0.1,
                "threshold": 0.9,
            }
        }
        assert _check_loop_state_blocking(contract, "IN_PROGRESS") is None

    def test_complete_at_max_iterations_not_blocking(self):
        """iteration == max_iterations -> not blocking (loop is exhausted)."""
        contract = {
            "loop_state": {
                "iteration": 5,
                "max_iterations": 5,
                "metric": 0.1,
                "threshold": 0.9,
            }
        }
        assert _check_loop_state_blocking(contract, "COMPLETE") is None

    def test_no_loop_state_not_blocking(self):
        assert _check_loop_state_blocking({}, "COMPLETE") is None

    def test_loop_state_blocking_propagates_to_validate(self):
        """validate() must reject COMPLETE when loop_state blocking condition holds."""
        output = f"""\
```agent_contract_handoff
{{
  "agent_status": {{
    "plan_status": "COMPLETE",
    "agent_id": "a1b2c3d4e5",
    "pending_steps": [],
    "next_action": "done"
  }},
  "evidence_report": {{
    "patterns_checked": ["x"], "files_checked": ["f"],
    "commands_run": [], "key_outputs": ["k"],
    "verbatim_outputs": ["v"], "cross_layer_impacts": [], "open_gaps": [],
    "verification": {{"method": "self-review", "checks": ["c"], "result": "pass", "details": "ok"}}
  }},
  "consolidation_report": null,
  "approval_request": null,
  "loop_state": {{
    "iteration": 1,
    "max_iterations": 5,
    "metric": 0.3,
    "threshold": 0.9
  }}
}}
```
"""
        result = validate(output, {})
        assert not result.is_valid
        assert any("LOOP_STATE_INCOMPLETE" in m for m in result.missing)


# ============================================================================
# T2.3 Clause parsers: parse_rollback_executed
# ============================================================================

class TestParseRollbackExecuted:
    def test_absent_returns_sentinel(self):
        result = parse_rollback_executed({})
        assert result == "ABSENT"

    def test_null_value_returns_none(self):
        result = parse_rollback_executed({"rollback_executed": None})
        assert result is None

    def test_string_value_returned(self):
        result = parse_rollback_executed({"rollback_executed": "git revert abc"})
        assert result == "git revert abc"

    def test_non_string_coerced(self):
        result = parse_rollback_executed({"rollback_executed": 42})
        assert result == "42"


# ============================================================================
# T2.3 Clause parsers: parse_context_consumption
# ============================================================================

class TestParseContextConsumption:
    def test_absent_returns_none(self):
        assert parse_context_consumption({}) is None

    def test_valid_full(self):
        contract = {
            "context_consumption": {"tokens_used": 50000, "pct_window": 0.45}
        }
        result = parse_context_consumption(contract)
        assert result is not None
        assert result["tokens_used"] == 50000
        assert result["pct_window"] == pytest.approx(0.45)

    def test_null_values_allowed(self):
        contract = {
            "context_consumption": {"tokens_used": None, "pct_window": None}
        }
        result = parse_context_consumption(contract)
        assert result is not None
        assert result["tokens_used"] is None
        assert result["pct_window"] is None

    def test_non_dict_returns_none(self):
        assert parse_context_consumption({"context_consumption": "bad"}) is None

    def test_non_numeric_coerces_to_none(self):
        contract = {
            "context_consumption": {"tokens_used": "not-a-number", "pct_window": 0.5}
        }
        result = parse_context_consumption(contract)
        assert result is not None
        assert result["tokens_used"] is None  # unparseable -> None


# ============================================================================
# T2.3 Clause parsers: parse_memory_suggestions
# ============================================================================

class TestParseMemorySuggestions:
    def test_absent_returns_empty_list(self):
        assert parse_memory_suggestions({}) == []

    def test_valid_list(self):
        contract = {
            "memory_suggestions": ["remember X", "note Y"]
        }
        result = parse_memory_suggestions(contract)
        assert result == ["remember X", "note Y"]

    def test_empty_list(self):
        assert parse_memory_suggestions({"memory_suggestions": []}) == []

    def test_non_list_returns_empty(self):
        assert parse_memory_suggestions({"memory_suggestions": "not-a-list"}) == []

    def test_non_string_items_coerced(self):
        contract = {"memory_suggestions": [42, None, "ok"]}
        result = parse_memory_suggestions(contract)
        # None is filtered (item is not None check), others coerced
        assert "42" in result
        assert "ok" in result
        assert None not in result
