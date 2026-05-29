"""Unit tests for gaia.state.permissions.

Coverage:
  * No GAIA_DISPATCH_AGENT set -> all tables allowed
  * Empty GAIA_DISPATCH_AGENT -> all tables allowed
  * Curator agents (orchestrator/operator) -> all tables allowed
  * Non-curator agent -> tasks/acceptance_criteria allowed
  * Non-curator agent -> milestones/briefs/plans blocked
  * Unknown table -> fail-open (allowed)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


from gaia.state.permissions import (  # noqa: E402
    DISPATCH_PERMISSIONS,
    StateTransitionForbidden,
    _assert_dispatch_can_advance_state,
)


@pytest.fixture(autouse=True)
def clear_dispatch_env(monkeypatch):
    """Ensure GAIA_DISPATCH_AGENT is unset before each test."""
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)


class TestHumanCaller:
    """Human CLI caller (no env var) -> always allowed."""

    def test_no_env_var_allows_tasks(self):
        _assert_dispatch_can_advance_state("tasks")

    def test_no_env_var_allows_acceptance_criteria(self):
        _assert_dispatch_can_advance_state("acceptance_criteria")

    def test_no_env_var_allows_milestones(self):
        _assert_dispatch_can_advance_state("milestones")

    def test_no_env_var_allows_briefs(self):
        _assert_dispatch_can_advance_state("briefs")

    def test_no_env_var_allows_plans(self):
        _assert_dispatch_can_advance_state("plans")

    def test_empty_string_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv("GAIA_DISPATCH_AGENT", "")
        _assert_dispatch_can_advance_state("milestones")


class TestCuratorAgent:
    """Curator agents allowed on every table."""

    @pytest.mark.parametrize("curator", [
        "orchestrator", "operator", "gaia-orchestrator", "gaia-operator",
    ])
    def test_curator_allowed_on_all_tables(self, monkeypatch, curator):
        monkeypatch.setenv("GAIA_DISPATCH_AGENT", curator)
        for table in DISPATCH_PERMISSIONS.keys():
            _assert_dispatch_can_advance_state(table)


class TestSubagentPermissions:
    """Non-curator subagents allowed on leaf tables, blocked on curator-only tables."""

    @pytest.mark.parametrize("subagent", [
        "developer", "platform-architect", "gitops-operator", "gaia-system",
    ])
    def test_subagent_allowed_on_tasks(self, monkeypatch, subagent):
        monkeypatch.setenv("GAIA_DISPATCH_AGENT", subagent)
        _assert_dispatch_can_advance_state("tasks")

    @pytest.mark.parametrize("subagent", [
        "developer", "platform-architect", "gitops-operator", "gaia-system",
    ])
    def test_subagent_allowed_on_acceptance_criteria(self, monkeypatch, subagent):
        monkeypatch.setenv("GAIA_DISPATCH_AGENT", subagent)
        _assert_dispatch_can_advance_state("acceptance_criteria")

    @pytest.mark.parametrize("table", ["milestones", "briefs", "plans"])
    def test_subagent_blocked_on_curator_only_tables(self, monkeypatch, table):
        monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")
        with pytest.raises(StateTransitionForbidden, match="restricted to curator"):
            _assert_dispatch_can_advance_state(table)


class TestUnknownTable:
    """Tables not in DISPATCH_PERMISSIONS fail-open."""

    def test_unknown_table_allowed_even_for_subagent(self, monkeypatch):
        monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")
        _assert_dispatch_can_advance_state("not_a_real_table")


class TestPermissionMatrixShape:
    """The matrix itself must match D1."""

    def test_tasks_is_not_curator_only(self):
        assert DISPATCH_PERMISSIONS["tasks"]["curator_only"] is False

    def test_acceptance_criteria_is_not_curator_only(self):
        assert DISPATCH_PERMISSIONS["acceptance_criteria"]["curator_only"] is False

    def test_milestones_is_curator_only(self):
        assert DISPATCH_PERMISSIONS["milestones"]["curator_only"] is True

    def test_briefs_is_curator_only(self):
        assert DISPATCH_PERMISSIONS["briefs"]["curator_only"] is True

    def test_plans_is_curator_only(self):
        assert DISPATCH_PERMISSIONS["plans"]["curator_only"] is True


class TestExceptionType:
    """StateTransitionForbidden must be a PermissionError subclass."""

    def test_is_permission_error(self):
        assert issubclass(StateTransitionForbidden, PermissionError)
