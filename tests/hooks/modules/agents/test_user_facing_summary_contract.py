#!/usr/bin/env python3
"""Behavior tests for the optional ``user_facing_summary`` contract field (Option A).

The field is the single human-audience field in the contract: a brief prose
summary the subagent writes once for the user, which the orchestrator relays
near-verbatim on a single-agent COMPLETE instead of re-synthesizing key_outputs.

These tests exercise the REAL validators and REAL parse helpers -- nothing is
mocked, no preconditions are injected. The contract is built exactly as a
subagent would emit it and run through the production validation path. The
load-bearing guarantee is backward compatibility: a contract WITH the field and
a contract WITHOUT it must both validate identically, and neither validator may
require the field.
"""

import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.agents.contract_validator import (  # noqa: E402
    parse_user_facing_summary as cv_parse_user_facing_summary,
    validate,
)
from modules.agents.response_contract import (  # noqa: E402
    parse_user_facing_summary as rc_parse_user_facing_summary,
    validate_response_contract,
)


_BASE_EVIDENCE = {
    "patterns_checked": ["read contract layer"],
    "files_checked": ["hooks/modules/agents/response_contract.py"],
    "commands_run": ["`pytest -q` -> ok"],
    "key_outputs": ["validator accepts the optional field"],
    "verbatim_outputs": ["ok"],
    "cross_layer_impacts": ["none"],
    "open_gaps": ["none"],
    "verification": {
        "method": "test",
        "checks": ["pytest passed for the contract validators"],
        "result": "pass",
        "details": "field is optional and additive",
    },
}

_BASE_STATUS = {
    "agent_state": "COMPLETE",
    "pending_steps": [],
    "next_action": "done",
    "agent_id": "a99002",
}


def _wrap(contract: dict) -> str:
    """Render a contract dict as the fenced agent_contract_handoff block."""
    return (
        "Some agent prose here.\n\n"
        "```agent_contract_handoff\n"
        + json.dumps(contract, indent=2)
        + "\n```\n"
    )


def _contract(*, with_summary: bool) -> dict:
    c = {
        "agent_status": dict(_BASE_STATUS),
        "evidence_report": dict(_BASE_EVIDENCE),
        "consolidation_report": None,
        "approval_request": None,
    }
    if with_summary:
        c["user_facing_summary"] = (
            "Applied two changes to the repo and ran the contract test slice; "
            "all green. Restart Claude Code to pick up the new build."
        )
    return c


# ---------------------------------------------------------------------------
# Backward compatibility: WITH vs WITHOUT must validate identically
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def test_contract_with_summary_validates(self):
        """A COMPLETE contract carrying user_facing_summary is valid."""
        output = _wrap(_contract(with_summary=True))
        result = validate(output, task_info={})
        assert result.is_valid, result.error_message
        assert result.missing == []

    def test_contract_without_summary_validates(self):
        """The same contract without the field validates identically (fallback)."""
        output = _wrap(_contract(with_summary=False))
        result = validate(output, task_info={})
        assert result.is_valid, result.error_message
        assert result.missing == []

    def test_with_and_without_produce_same_validity(self):
        """Adding the field changes nothing about validity -- proves additive."""
        with_res = validate(_wrap(_contract(with_summary=True)), task_info={})
        without_res = validate(_wrap(_contract(with_summary=False)), task_info={})
        assert with_res.is_valid == without_res.is_valid is True
        assert with_res.missing == without_res.missing == []

    def test_response_contract_validator_accepts_with_summary(self):
        """The deterministic response_contract validator accepts the field too."""
        output = _wrap(_contract(with_summary=True))
        v = validate_response_contract(output, task_agent_id="a99002")
        assert v.valid, (v.missing, v.invalid)

    def test_response_contract_validator_accepts_without_summary(self):
        """And validates identically when the field is absent."""
        output = _wrap(_contract(with_summary=False))
        v = validate_response_contract(output, task_agent_id="a99002")
        assert v.valid, (v.missing, v.invalid)

    def test_validator_does_not_require_the_field(self):
        """The field name never appears in the missing list -- it is not required."""
        output = _wrap(_contract(with_summary=False))
        result = validate(output, task_info={})
        v = validate_response_contract(output, task_agent_id="a99002")
        joined = " ".join(result.missing + v.missing + v.invalid).lower()
        assert "user_facing_summary" not in joined
        assert "user-facing" not in joined


# ---------------------------------------------------------------------------
# Parse helpers: real extraction behavior
# ---------------------------------------------------------------------------

class TestParseUserFacingSummary:
    def test_returns_text_when_present(self):
        output = _wrap(_contract(with_summary=True))
        from modules.agents.contract_validator import parse_contract
        parsed = parse_contract(output)
        assert parsed is not None
        text = cv_parse_user_facing_summary(parsed)
        assert text is not None
        assert "Applied two changes" in text

    def test_returns_none_when_absent(self):
        from modules.agents.contract_validator import parse_contract
        parsed = parse_contract(_wrap(_contract(with_summary=False)))
        assert cv_parse_user_facing_summary(parsed) is None

    def test_response_contract_parser_from_raw_output(self):
        """The response_contract variant parses straight from agent_output."""
        output = _wrap(_contract(with_summary=True))
        text = rc_parse_user_facing_summary(output)
        assert text is not None and "Restart Claude Code" in text

    def test_blank_summary_is_none(self):
        from modules.agents.contract_validator import parse_contract
        c = _contract(with_summary=False)
        c["user_facing_summary"] = "   "
        parsed = parse_contract(_wrap(c))
        assert cv_parse_user_facing_summary(parsed) is None

    def test_non_string_summary_is_none(self):
        from modules.agents.contract_validator import parse_contract
        c = _contract(with_summary=False)
        c["user_facing_summary"] = {"text": "not a string"}
        parsed = parse_contract(_wrap(c))
        # Non-string must not raise and must not be returned as a value.
        assert cv_parse_user_facing_summary(parsed) is None
        # And the contract still validates -- a malformed optional field never blocks.
        assert validate(_wrap(c), task_info={}).is_valid


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
