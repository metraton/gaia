#!/usr/bin/env python3
"""Tests for the subagent memory-write enforcement guard.

Closes the runtime enforcement gap documented in skills/memory/SKILL.md
("Who writes"): only the orchestrator and `gaia-operator` mutate memory
directly via the CLI; a subagent dispatched into a task must NOT run
`gaia memory add|edit|append|reclassify|delete|link` -- it proposes via a
`memorialize_suggestions` block instead.

Two layers are covered:
  1. The guard module directly (detection + agent scoping).
  2. The guard wired into BashValidator.validate() (end-to-end runtime path).
"""

import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
for _p in [str(HOOKS_DIR), str(PLUGIN_ROOT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from modules.security.subagent_memory_write_guard import (
    ALLOWED_AGENTS,
    MEMORY_WRITE_VERBS,
    REJECTION_MESSAGE,
    check,
    is_memory_write_attempt,
    rejection_message,
)
from modules.tools.bash_validator import BashValidator
from modules.security.tiers import SecurityTier


# ---------------------------------------------------------------------------
# Layer 1: detection (is_memory_write_attempt)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("verb", sorted(MEMORY_WRITE_VERBS))
def test_detects_each_write_verb(verb):
    assert is_memory_write_attempt(f"gaia memory {verb} foo --body x") is True


@pytest.mark.parametrize(
    "cmd",
    [
        "gaia memory search foo",
        "gaia memory show foo",
        "gaia memory list",
        "gaia memory stats",
        "gaia memory get-relevant --query x",
        "gaia memory conflicts",
        "gaia memory episode-show 1",
    ],
)
def test_read_verbs_not_flagged(cmd):
    assert is_memory_write_attempt(cmd) is False


def test_detects_redispatched_form():
    assert is_memory_write_attempt(
        "python3 /home/x/bin/gaia memory add foo --body y"
    ) is True


def test_detects_compound_chain():
    assert is_memory_write_attempt("cd /repo && gaia memory add foo --body y") is True


def test_unrelated_command_not_flagged():
    assert is_memory_write_attempt("gaia doctor") is False
    assert is_memory_write_attempt("git add .") is False
    assert is_memory_write_attempt("") is False


# ---------------------------------------------------------------------------
# Layer 1: agent scoping (check)
# ---------------------------------------------------------------------------

def test_orchestrator_allowed():
    """The orchestrator (is_subagent False) is never blocked here."""
    allowed, reason = check(
        "gaia memory add foo --body x", is_subagent=False, agent_type=""
    )
    assert allowed is True
    assert reason is None


def test_subagent_write_blocked():
    allowed, reason = check(
        "gaia memory add foo --body x", is_subagent=True, agent_type="developer"
    )
    assert allowed is False
    assert reason is not None
    assert "memorialize_suggestions" in reason
    assert "developer" in reason  # names the offending agent


@pytest.mark.parametrize("operator", sorted(ALLOWED_AGENTS))
def test_allowlisted_agent_allowed(operator):
    """gaia-operator is the sanctioned memory writer even as a subagent."""
    allowed, reason = check(
        "gaia memory add foo --body x", is_subagent=True, agent_type=operator
    )
    assert allowed is True
    assert reason is None


def test_subagent_read_allowed():
    allowed, reason = check(
        "gaia memory search foo", is_subagent=True, agent_type="developer"
    )
    assert allowed is True
    assert reason is None


def test_rejection_message_without_agent():
    assert rejection_message() == REJECTION_MESSAGE


# ---------------------------------------------------------------------------
# Layer 2: end-to-end through BashValidator.validate()
# ---------------------------------------------------------------------------

@pytest.fixture()
def validator():
    return BashValidator()


@pytest.mark.parametrize(
    "cmd",
    [
        "gaia memory add project_foo --body x",
        "gaia memory append foo --body x",
        "gaia memory reclassify foo --status graduated",
        "gaia memory link a b --kind relates",
    ],
)
def test_e2e_subagent_write_blocked(validator, cmd):
    """The previously-ungated (T0-by-elimination) verbs are now blocked."""
    r = validator.validate(cmd, is_subagent=True, agent_type="developer")
    assert r.allowed is False
    assert r.tier == SecurityTier.T3_BLOCKED
    assert "memorialize_suggestions" in r.reason


@pytest.mark.parametrize(
    "cmd",
    [
        "gaia memory add project_foo --body x",
        "gaia memory append foo --body x",
        "gaia memory reclassify foo --status graduated",
        "gaia memory link a b --kind relates",
    ],
)
def test_e2e_orchestrator_write_allowed(validator, cmd):
    """Legitimate orchestrator path is unaffected (stays T0/allowed)."""
    r = validator.validate(cmd, is_subagent=False, agent_type="")
    assert r.allowed is True


@pytest.mark.parametrize(
    "cmd",
    [
        "gaia memory add project_foo --body x",
        "gaia memory append foo --body x",
        "gaia memory reclassify foo --status graduated",
        "gaia memory link a b --kind relates",
    ],
)
def test_e2e_operator_write_allowed(validator, cmd):
    """gaia-operator (subagent, but the sanctioned writer) is unaffected."""
    r = validator.validate(cmd, is_subagent=True, agent_type="gaia-operator")
    assert r.allowed is True


def test_e2e_subagent_read_allowed(validator):
    r = validator.validate(
        "gaia memory search foo", is_subagent=True, agent_type="developer"
    )
    assert r.allowed is True


def test_e2e_subagent_compound_write_blocked(validator):
    r = validator.validate(
        "cd /repo && gaia memory add foo --body y",
        is_subagent=True, agent_type="developer",
    )
    assert r.allowed is False
    assert r.tier == SecurityTier.T3_BLOCKED
