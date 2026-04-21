"""Tests for ``tests.evals.graders.code_grader`` (T3a).

Parametrized coverage of the v1-style keyword grader:

* all ``expect_present`` keywords found -> pass
* a keyword in ``expect_absent`` leaks into the response -> fail
* a keyword in ``expect_present`` is missing from the response -> fail
* both lists empty -> trivial pass
* ``score = matched / total`` over the constraint categories (present / absent)

The scoring rule in T1's :func:`code_grader` evaluates two categories
(``expect_present``, ``expect_absent``). Each category contributes at most
one point to ``matched``/``total``; the score is the ratio.
"""

from __future__ import annotations

import pytest

from tests.evals.graders import GradeResult, code_grader


class TestCodeGraderAllPresent:
    """When every ``expect_present`` keyword appears the grader passes."""

    def test_single_keyword_present_passes(self):
        result = code_grader(
            response="push origin to metraton remote",
            expect_present=["metraton"],
        )
        assert isinstance(result, GradeResult)
        assert result.passed is True
        assert result.score == 1.0
        assert result.reasons, "reasons must explain the pass"

    def test_multiple_keywords_all_present_passes(self):
        response = "use metraton on GitHub, not aaxisdigital on Bitbucket"
        result = code_grader(
            response=response,
            expect_present=["metraton", "GitHub"],
        )
        assert result.passed is True
        assert result.score == 1.0

    def test_present_and_absent_both_satisfied_passes(self):
        response = "connect to metra-tower via Tailscale hostname"
        result = code_grader(
            response=response,
            expect_present=["Tailscale", "metra-tower"],
            expect_absent=["100.64.", "raw IP"],
        )
        assert result.passed is True
        assert result.score == 1.0


class TestCodeGraderAbsentLeak:
    """A forbidden keyword appearing in the response fails the grader."""

    def test_forbidden_keyword_leaked_fails(self):
        result = code_grader(
            response="push to aaxisdigital remote",
            expect_absent=["aaxisdigital"],
        )
        assert result.passed is False
        assert result.score == 0.0
        assert any("leaked" in r for r in result.reasons)

    def test_present_ok_but_absent_leaks_fails_at_half(self):
        """Score is ``matched / total``: 1 matched (present), 0 matched (absent) -> 0.5."""
        response = "metraton remote, but also aaxisdigital"
        result = code_grader(
            response=response,
            expect_present=["metraton"],
            expect_absent=["aaxisdigital"],
        )
        assert result.passed is False
        assert result.score == pytest.approx(0.5)


class TestCodeGraderRequiredMissing:
    """A required keyword missing from the response fails the grader."""

    def test_required_keyword_missing_fails(self):
        result = code_grader(
            response="use the remote configured for aaxisdigital",
            expect_present=["metraton"],
        )
        assert result.passed is False
        assert result.score == 0.0
        assert any("missing" in r for r in result.reasons)

    def test_one_of_many_required_missing_fails(self):
        result = code_grader(
            response="use metraton on GitHub",
            expect_present=["metraton", "Tailscale"],
        )
        assert result.passed is False
        # Still zero because the present-category contributes 0/1 to matched.
        assert result.score == 0.0


class TestCodeGraderEmptyConstraints:
    """Empty or None constraint lists behave as ``no constraints -> pass``."""

    def test_both_none_passes_trivially(self):
        result = code_grader(response="anything at all")
        assert result.passed is True
        assert result.score == 1.0

    def test_both_empty_lists_pass_trivially(self):
        result = code_grader(
            response="anything at all",
            expect_present=[],
            expect_absent=[],
        )
        assert result.passed is True
        assert result.score == 1.0

    def test_only_expect_absent_no_leak_passes(self):
        result = code_grader(
            response="clean response",
            expect_absent=["forbidden"],
        )
        assert result.passed is True
        assert result.score == 1.0

    def test_only_expect_present_empty_response_fails(self):
        result = code_grader(response="", expect_present=["needed"])
        assert result.passed is False
        assert result.score == 0.0


class TestCodeGraderScoring:
    """``score = matched / total`` across the two constraint categories."""

    @pytest.mark.parametrize(
        "response,present,absent,expected_score,expected_pass",
        [
            # Both categories satisfied -> 2/2.
            ("metraton only", ["metraton"], ["aaxisdigital"], 1.0, True),
            # Present satisfied, absent leaks -> 1/2.
            (
                "metraton plus aaxisdigital",
                ["metraton"],
                ["aaxisdigital"],
                0.5,
                False,
            ),
            # Present fails, absent satisfied -> 1/2.
            (
                "nothing useful here",
                ["metraton"],
                ["aaxisdigital"],
                0.5,
                False,
            ),
            # Both fail -> 0/2.
            (
                "aaxisdigital only",
                ["metraton"],
                ["aaxisdigital"],
                0.0,
                False,
            ),
            # Only present category active, satisfied -> 1/1.
            ("metraton", ["metraton"], None, 1.0, True),
            # Only absent category active, satisfied -> 1/1.
            ("clean", None, ["forbidden"], 1.0, True),
        ],
    )
    def test_score_is_matched_over_total(
        self, response, present, absent, expected_score, expected_pass
    ):
        result = code_grader(
            response=response,
            expect_present=present,
            expect_absent=absent,
        )
        assert result.score == pytest.approx(expected_score)
        assert result.passed is expected_pass

    def test_reasons_have_one_entry_per_active_category(self):
        # Only the present category is active -> one reason entry.
        only_present = code_grader(response="metraton", expect_present=["metraton"])
        assert len(only_present.reasons) == 1

        # Both categories active -> two reason entries.
        both = code_grader(
            response="metraton",
            expect_present=["metraton"],
            expect_absent=["aaxisdigital"],
        )
        assert len(both.reasons) == 2

    def test_matching_is_case_sensitive_substring(self):
        # ``code_grader`` documents case-sensitive substring match.
        wrong_case = code_grader(
            response="Metraton only",
            expect_present=["metraton"],
        )
        assert wrong_case.passed is False

        substring = code_grader(
            response="the metratonic remote",
            expect_present=["metraton"],
        )
        assert substring.passed is True
