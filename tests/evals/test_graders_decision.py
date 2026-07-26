"""Tests for :func:`tests.evals.graders.decision_grader` (brief #89 AC-2).

``decision_grader`` is the paired grader for
:class:`tests.evals.runner.HookLogReplayBackend`: it parses the backend's
JSON decision payload and compares the observed ``decision`` to the
catalog's curated ``expected_decision``. It exists because
``contract_grader`` was declared on the security golden catalog
(``catalogs/security_decisions.yaml``) but never actually exercised there --
a hook_log_replay case never produces a fenced ``agent_contract_handoff``
block, so ``contract_grader`` could only ever report "no fenced block
found" if it were genuinely run against this payload.
"""

from __future__ import annotations

import json

from tests.evals.graders import GradeResult, decision_grader


def _payload(decision: str = "allow", **overrides) -> str:
    body = {
        "decision": decision,
        "reason": "[T0] read-only",
        "exit_code": 0,
        "raw_decision": "ALLOW",
    }
    body.update(overrides)
    return json.dumps(body)


def test_matching_decision_passes():
    result = decision_grader(_payload("allow"), expected_decision="allow")
    assert isinstance(result, GradeResult)
    assert result.passed is True
    assert result.score == 1.0


def test_mismatched_decision_fails():
    result = decision_grader(_payload("allow"), expected_decision="ask")
    assert result.passed is False
    assert result.score == 0.0
    assert any("decision mismatch" in r for r in result.reasons)


def test_no_expected_decision_passes_trivially():
    result = decision_grader(_payload("deny"), expected_decision=None)
    assert result.passed is True
    assert result.score == 1.0


def test_malformed_json_fails():
    result = decision_grader('{"decision": "allow"', expected_decision="allow")
    assert result.passed is False
    assert any("not valid JSON" in r for r in result.reasons)


def test_non_object_payload_fails():
    result = decision_grader("[1, 2, 3]", expected_decision="allow")
    assert result.passed is False
    assert any("JSON object" in r for r in result.reasons)


def test_every_decision_class_pins_correctly():
    for decision in ("allow", "ask", "deny"):
        result = decision_grader(_payload(decision), expected_decision=decision)
        assert result.passed is True, f"{decision}: {result.reasons}"
