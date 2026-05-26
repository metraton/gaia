"""
Tests for dual-mode fenced tag support in contract_validator.py (T2.1).

Verifies that both ``json:contract`` (legacy) and ``agent_contract_handoff``
(new) tags are accepted by parse_contract() and validate(), and that the
correct status field is used for each tag.
"""

import pytest

# ---------------------------------------------------------------------------
# Make the hooks package importable from the test runner
# ---------------------------------------------------------------------------
import sys
from pathlib import Path

_HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from modules.agents.contract_validator import (
    parse_contract,
    validate,
    extract_plan_status_from_output,
    _TAG_LEGACY,
    _TAG_NEW,
)


# ---------------------------------------------------------------------------
# Fixtures: minimal valid contract bodies
# ---------------------------------------------------------------------------

_LEGACY_CONTRACT_COMPLETE = """\
```json:contract
{
  "agent_status": {
    "plan_status": "COMPLETE",
    "agent_id": "a1b2c3d4e5",
    "pending_steps": [],
    "next_action": "done"
  },
  "evidence_report": {
    "patterns_checked": ["x"],
    "files_checked": ["f"],
    "commands_run": [],
    "key_outputs": ["k"],
    "verbatim_outputs": ["v"],
    "cross_layer_impacts": [],
    "open_gaps": [],
    "verification": {"method": "self-review", "checks": ["checked"], "result": "pass", "details": "ok"}
  },
  "consolidation_report": null,
  "approval_request": null
}
```
"""

_NEW_CONTRACT_COMPLETE = """\
```agent_contract_handoff
{
  "agent_status": {
    "task_status": "COMPLETE",
    "agent_id": "a1b2c3d4e5",
    "pending_steps": [],
    "next_action": "done"
  },
  "evidence_report": {
    "patterns_checked": ["x"],
    "files_checked": ["f"],
    "commands_run": [],
    "key_outputs": ["k"],
    "verbatim_outputs": ["v"],
    "cross_layer_impacts": [],
    "open_gaps": [],
    "verification": {"method": "self-review", "checks": ["checked"], "result": "pass", "details": "ok"}
  },
  "consolidation_report": null,
  "approval_request": null
}
```
"""

_LEGACY_CONTRACT_IN_PROGRESS = """\
```json:contract
{
  "agent_status": {
    "plan_status": "IN_PROGRESS",
    "agent_id": "a1b2c3d4e5",
    "pending_steps": ["step1"],
    "next_action": "continue"
  },
  "evidence_report": {
    "patterns_checked": ["x"],
    "files_checked": ["f"],
    "commands_run": [],
    "key_outputs": ["k"],
    "verbatim_outputs": ["v"],
    "cross_layer_impacts": [],
    "open_gaps": ["g"]
  },
  "consolidation_report": null,
  "approval_request": null
}
```
"""

_NEW_CONTRACT_IN_PROGRESS = """\
```agent_contract_handoff
{
  "agent_status": {
    "task_status": "IN_PROGRESS",
    "agent_id": "a1b2c3d4e5",
    "pending_steps": ["step1"],
    "next_action": "continue"
  },
  "evidence_report": {
    "patterns_checked": ["x"],
    "files_checked": ["f"],
    "commands_run": [],
    "key_outputs": ["k"],
    "verbatim_outputs": ["v"],
    "cross_layer_impacts": [],
    "open_gaps": ["g"]
  },
  "consolidation_report": null,
  "approval_request": null
}
```
"""

_NO_CONTRACT = "This output has no fenced contract block at all."


# ---------------------------------------------------------------------------
# parse_contract tests
# ---------------------------------------------------------------------------

class TestParseContractDualTag:
    def test_legacy_tag_parsed(self):
        result = parse_contract(_LEGACY_CONTRACT_COMPLETE)
        assert result is not None
        assert result.get("_contract_tag") == _TAG_LEGACY

    def test_new_tag_parsed(self):
        result = parse_contract(_NEW_CONTRACT_COMPLETE)
        assert result is not None
        assert result.get("_contract_tag") == _TAG_NEW

    def test_no_tag_returns_none(self):
        assert parse_contract(_NO_CONTRACT) is None

    def test_legacy_tag_has_plan_status(self):
        result = parse_contract(_LEGACY_CONTRACT_COMPLETE)
        assert result["agent_status"]["plan_status"] == "COMPLETE"

    def test_new_tag_has_task_status(self):
        result = parse_contract(_NEW_CONTRACT_COMPLETE)
        assert result["agent_status"]["task_status"] == "COMPLETE"

    def test_both_tags_present_first_wins_legacy(self):
        """When both tags appear, the one that comes first wins."""
        combined = _LEGACY_CONTRACT_IN_PROGRESS + "\n\n" + _NEW_CONTRACT_COMPLETE
        result = parse_contract(combined)
        assert result is not None
        assert result.get("_contract_tag") == _TAG_LEGACY

    def test_both_tags_present_first_wins_new(self):
        """When new tag appears before legacy, new tag wins."""
        combined = _NEW_CONTRACT_COMPLETE + "\n\n" + _LEGACY_CONTRACT_IN_PROGRESS
        result = parse_contract(combined)
        assert result is not None
        assert result.get("_contract_tag") == _TAG_NEW

    def test_malformed_json_returns_none(self):
        malformed = "```json:contract\n{bad json\n```"
        assert parse_contract(malformed) is None

    def test_malformed_new_tag_returns_none(self):
        malformed = "```agent_contract_handoff\n{bad json\n```"
        assert parse_contract(malformed) is None


# ---------------------------------------------------------------------------
# extract_plan_status_from_output tests
# ---------------------------------------------------------------------------

class TestExtractPlanStatusDualTag:
    def test_legacy_complete(self):
        assert extract_plan_status_from_output(_LEGACY_CONTRACT_COMPLETE) == "COMPLETE"

    def test_new_complete(self):
        assert extract_plan_status_from_output(_NEW_CONTRACT_COMPLETE) == "COMPLETE"

    def test_legacy_in_progress(self):
        assert extract_plan_status_from_output(_LEGACY_CONTRACT_IN_PROGRESS) == "IN_PROGRESS"

    def test_new_in_progress(self):
        assert extract_plan_status_from_output(_NEW_CONTRACT_IN_PROGRESS) == "IN_PROGRESS"

    def test_no_contract_empty_string(self):
        assert extract_plan_status_from_output(_NO_CONTRACT) == ""


# ---------------------------------------------------------------------------
# validate() tests -- acceptance of both tags
# ---------------------------------------------------------------------------

class TestValidateDualTag:
    _task_info = {}

    def test_legacy_complete_valid(self):
        result = validate(_LEGACY_CONTRACT_COMPLETE, self._task_info)
        assert result.is_valid, f"Expected valid, got missing: {result.missing}"

    def test_new_complete_valid(self):
        result = validate(_NEW_CONTRACT_COMPLETE, self._task_info)
        assert result.is_valid, f"Expected valid, got missing: {result.missing}"

    def test_legacy_in_progress_valid(self):
        result = validate(_LEGACY_CONTRACT_IN_PROGRESS, self._task_info)
        assert result.is_valid, f"Expected valid, got missing: {result.missing}"

    def test_new_in_progress_valid(self):
        result = validate(_NEW_CONTRACT_IN_PROGRESS, self._task_info)
        assert result.is_valid, f"Expected valid, got missing: {result.missing}"

    def test_no_contract_invalid(self):
        result = validate(_NO_CONTRACT, self._task_info)
        assert not result.is_valid
        assert "AGENT_STATUS" in result.missing

    def test_new_tag_complete_without_verification_invalid(self):
        """New tag: COMPLETE without verification.result=pass is blocking."""
        output = """\
```agent_contract_handoff
{
  "agent_status": {
    "task_status": "COMPLETE",
    "agent_id": "a1b2c3d4e5",
    "pending_steps": [],
    "next_action": "done"
  },
  "evidence_report": {
    "patterns_checked": ["x"],
    "files_checked": ["f"],
    "commands_run": [],
    "key_outputs": ["k"],
    "verbatim_outputs": ["v"],
    "cross_layer_impacts": [],
    "open_gaps": []
  },
  "consolidation_report": null,
  "approval_request": null
}
```
"""
        result = validate(output, {})
        assert not result.is_valid
        # Should flag verification issue
        assert any("VERIFICATION" in m for m in result.missing)
