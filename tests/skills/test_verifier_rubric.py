"""B3 T2 (AC-2): the semantic-rubric verification-mode skill's reference module.

Matchable by ``pytest tests/ -k verifier_rubric -q``.

``skills/verification-rubric/scripts/rubric_verdict.py`` is the deterministic
assembly half of the judging cycle the skill documents: split a rubric into
discrete criteria, then aggregate already-judged per-criterion assessments
into one justified pass/fail verdict. These tests exercise both a rubric that
IS met (pass) and one that is NOT met (fail), plus the honesty-rule rejections
(no hollow assessments, no empty verdicts) and the criteria parser.

The module is loaded by path (``importlib.util.spec_from_file_location``)
rather than imported as a package, mirroring the existing script-loading
pattern in ``tests/test_scheduled_tasks.py`` -- ``skills/`` is not a Python
package and this is a skill-owned script, not shared production code.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = (
    _REPO_ROOT
    / "skills"
    / "verification-rubric"
    / "scripts"
    / "rubric_verdict.py"
)


def _load_rubric_verdict_module():
    spec = importlib.util.spec_from_file_location(
        "verification_rubric.rubric_verdict", _SCRIPT_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Register before exec: the module's `from __future__ import annotations`
    # dataclasses resolve their own module via sys.modules[__module__] during
    # class creation, which requires the module to already be registered.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


rubric_verdict = _load_rubric_verdict_module()
CriterionAssessment = rubric_verdict.CriterionAssessment
assemble_verdict = rubric_verdict.assemble_verdict
parse_rubric_criteria = rubric_verdict.parse_rubric_criteria


# --- parse_rubric_criteria --------------------------------------------------

def test_verifier_rubric_parses_bulleted_criteria():
    rubric = (
        "- error messages are clear and actionable\n"
        "- the CLI exits non-zero on failure\n"
        "- no stack trace leaks to the user\n"
    )
    criteria = parse_rubric_criteria(rubric)
    assert criteria == [
        "error messages are clear and actionable",
        "the CLI exits non-zero on failure",
        "no stack trace leaks to the user",
    ]


def test_verifier_rubric_parses_numbered_criteria():
    rubric = "1. covers the happy path\n2) covers the error path\n"
    criteria = parse_rubric_criteria(rubric)
    assert criteria == ["covers the happy path", "covers the error path"]


def test_verifier_rubric_single_paragraph_is_one_criterion():
    rubric = "the summary is accurate and reads naturally"
    assert parse_rubric_criteria(rubric) == [rubric]


def test_verifier_rubric_rejects_empty_evidence_shape():
    with pytest.raises(ValueError):
        parse_rubric_criteria("   ")


# --- assemble_verdict: rubric MET -> pass -----------------------------------

def test_verifier_rubric_assembles_pass_when_all_criteria_met():
    assessments = [
        CriterionAssessment(
            criterion="error messages are clear and actionable",
            met=True,
            reasoning="observed: 'invalid workspace: no such brief' names the "
                      "exact input and the exact problem",
        ),
        CriterionAssessment(
            criterion="the CLI exits non-zero on failure",
            met=True,
            reasoning="observed: exit code 1 on the failing invocation",
        ),
    ]
    verdict = assemble_verdict(assessments)
    assert verdict.verdict == "pass"
    assert verdict.criteria == assessments
    assert "all criteria met" in verdict.overall_reasoning


# --- assemble_verdict: rubric NOT MET -> fail -------------------------------

def test_verifier_rubric_assembles_fail_when_one_criterion_unmet():
    assessments = [
        CriterionAssessment(
            criterion="error messages are clear and actionable",
            met=True,
            reasoning="observed: message names the bad input",
        ),
        CriterionAssessment(
            criterion="no stack trace leaks to the user",
            met=False,
            reasoning="observed: a raw Python traceback was printed to stdout "
                      "on the failing path",
        ),
    ]
    verdict = assemble_verdict(assessments)
    assert verdict.verdict == "fail"
    assert "unmet" in verdict.overall_reasoning
    assert "no stack trace leaks to the user" in verdict.overall_reasoning


def test_verifier_rubric_fails_on_single_unmet_even_if_majority_pass():
    # One unmet, unwaived criterion is fail regardless of how many others
    # passed -- verdict inflation is the anti-pattern this guards against.
    assessments = [
        CriterionAssessment("a", met=True, reasoning="observed a"),
        CriterionAssessment("b", met=True, reasoning="observed b"),
        CriterionAssessment("c", met=True, reasoning="observed c"),
        CriterionAssessment("d", met=False, reasoning="observed d is absent"),
    ]
    verdict = assemble_verdict(assessments)
    assert verdict.verdict == "fail"


# --- honesty rule: no hollow verdicts ---------------------------------------

def test_verifier_rubric_rejects_assessment_with_blank_reasoning():
    assessments = [
        CriterionAssessment(criterion="looks fine", met=True, reasoning="   "),
    ]
    with pytest.raises(ValueError):
        assemble_verdict(assessments)


def test_verifier_rubric_rejects_empty_assessment_list():
    with pytest.raises(ValueError):
        assemble_verdict([])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
