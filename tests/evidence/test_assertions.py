"""Tests for the declarative assertion DSL.

Contract:
    evaluate(assert_spec: dict, data: Any) -> bool

    assert_spec is one of:
      { op: "contains"|"equals"|"gte"|"lte"|"eq"|"ne"
        |"has_field"|"length_gte"|"length_eq"|"matches",
        path: "<dotted.path>",   # optional for has_field root; required otherwise
        value: <literal>          # optional for has_field
      }

Path resolution:
  - Dotted segments traverse dict keys ("a.b.c").
  - Root "" (empty path) targets `data` itself.
  - Missing segment -> evaluate returns False (KeyError swallowed).
"""
from __future__ import annotations

import pytest

from hooks.modules.evidence.assertions import evaluate


class TestContains:
    def test_contains_passes_when_substring_present(self) -> None:
        assert evaluate({"op": "contains", "path": "msg", "value": "ok"},
                        {"msg": "status ok here"}) is True

    def test_contains_fails_when_substring_absent(self) -> None:
        assert evaluate({"op": "contains", "path": "msg", "value": "ok"},
                        {"msg": "error only"}) is False


class TestEquals:
    def test_equals_passes_on_exact_match(self) -> None:
        assert evaluate({"op": "equals", "path": "x", "value": "v"},
                        {"x": "v"}) is True

    def test_equals_fails_on_mismatch(self) -> None:
        assert evaluate({"op": "equals", "path": "x", "value": "v"},
                        {"x": "other"}) is False


class TestGte:
    def test_gte_passes_when_greater(self) -> None:
        assert evaluate({"op": "gte", "path": "n", "value": 7},
                        {"n": 10}) is True

    def test_gte_passes_when_equal(self) -> None:
        assert evaluate({"op": "gte", "path": "n", "value": 7},
                        {"n": 7}) is True

    def test_gte_fails_when_less(self) -> None:
        assert evaluate({"op": "gte", "path": "n", "value": 7},
                        {"n": 6}) is False


class TestLte:
    def test_lte_passes_when_less(self) -> None:
        assert evaluate({"op": "lte", "path": "n", "value": 200},
                        {"n": 150}) is True

    def test_lte_fails_when_greater(self) -> None:
        assert evaluate({"op": "lte", "path": "n", "value": 200},
                        {"n": 300}) is False


class TestEq:
    def test_eq_alias_of_equals(self) -> None:
        assert evaluate({"op": "eq", "path": "x", "value": 5},
                        {"x": 5}) is True
        assert evaluate({"op": "eq", "path": "x", "value": 5},
                        {"x": 6}) is False


class TestNe:
    def test_ne_passes_when_different(self) -> None:
        assert evaluate({"op": "ne", "path": "x", "value": 5},
                        {"x": 6}) is True

    def test_ne_fails_when_equal(self) -> None:
        assert evaluate({"op": "ne", "path": "x", "value": 5},
                        {"x": 5}) is False


class TestHasField:
    def test_has_field_present(self) -> None:
        assert evaluate({"op": "has_field", "path": "timestamp"},
                        {"timestamp": "2026-04-16T10:00:00"}) is True

    def test_has_field_absent(self) -> None:
        assert evaluate({"op": "has_field", "path": "timestamp"},
                        {"other": "x"}) is False


class TestLengthGte:
    def test_length_gte_on_list_pass(self) -> None:
        """7 elements, assert >= 7 -> pass."""
        assert evaluate({"op": "length_gte", "path": "cases", "value": 7},
                        {"cases": list(range(7))}) is True

    def test_length_gte_strict_above_pass(self) -> None:
        """8 elements, assert >= 7 -> pass."""
        assert evaluate({"op": "length_gte", "path": "cases", "value": 7},
                        {"cases": list(range(8))}) is True

    def test_length_gte_below_threshold_fail(self) -> None:
        """5 elements, assert >= 7 -> fail."""
        assert evaluate({"op": "length_gte", "path": "cases", "value": 7},
                        {"cases": list(range(5))}) is False


class TestLengthEq:
    def test_length_eq_exact(self) -> None:
        assert evaluate({"op": "length_eq", "path": "xs", "value": 3},
                        {"xs": [1, 2, 3]}) is True

    def test_length_eq_mismatch(self) -> None:
        assert evaluate({"op": "length_eq", "path": "xs", "value": 3},
                        {"xs": [1, 2]}) is False


class TestMatches:
    def test_matches_regex_pass(self) -> None:
        """matches uses re.search; partial match counts."""
        assert evaluate({"op": "matches", "path": "ver",
                         "value": r"^\d+\.\d+\.\d+$"},
                        {"ver": "4.7.2"}) is True

    def test_matches_regex_fail(self) -> None:
        assert evaluate({"op": "matches", "path": "ver",
                         "value": r"^\d+\.\d+\.\d+$"},
                        {"ver": "not-semver"}) is False


class TestPathEdgeCases:
    def test_missing_path_returns_false(self) -> None:
        """Path targets a key that does not exist -- no crash, False."""
        assert evaluate({"op": "equals", "path": "nope.key",
                         "value": "x"},
                        {"other": {}}) is False

    def test_nested_dotted_path(self) -> None:
        """Dotted path traverses nested dicts."""
        data = {"outer": {"inner": {"leaf": 42}}}
        assert evaluate({"op": "equals", "path": "outer.inner.leaf",
                         "value": 42}, data) is True

    def test_empty_path_targets_root(self) -> None:
        """Path '' means the whole data object."""
        assert evaluate({"op": "length_gte", "path": "", "value": 2},
                        [1, 2, 3]) is True


class TestUnknownOp:
    def test_unknown_op_raises(self) -> None:
        """An op not in the allowed set must fail fast, not silently return False."""
        with pytest.raises(ValueError):
            evaluate({"op": "pretend_this_works", "path": "x", "value": 1},
                     {"x": 1})
