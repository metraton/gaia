"""Tests for T3_BLOCKED denial message format — D5 + D11 compliance.

These tests verify that the subagent T3_BLOCKED denial message includes the
literal skill name in the format ``Skill('<name>')`` as required by plan D5
and D11.

The canonical skill name for D11 is ``subagent-request-approval`` (role-prefixed,
Function A). The denial message is built by
``approval_messages.build_t3_blocked_denial_message()`` and consumed by
``bash_validator._validate_bash_command()`` in the subagent branch.

Satisfies: AC-4 from brief approval-model-redesign-user-in-loop-fingerprint-bound-hash-chained
Task:      T2.2 (M2, Wave 2)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup: ensure the gaia/hooks package is importable from the repo root
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from hooks.modules.security.approval_messages import (  # noqa: E402
    build_t3_blocked_denial_message,
    _SUBAGENT_APPROVAL_SKILL,
)


# ---------------------------------------------------------------------------
# AC test: T2.2 acceptance criterion (standalone function — matches plan AC path)
# ---------------------------------------------------------------------------

def test_denial_names_skill_literally():
    """The T3_BLOCKED denial message includes Skill('subagent-request-approval').

    This is the primary AC test for T2.2. Per plan D5 + D11, the denial message
    must include the literal skill name in the format ``Skill('<name>')`` so the
    subagent knows exactly which skill to load without inference.

    AC path: tests/hooks/test_denial_messages.py::test_denial_names_skill_literally
    """
    approval_id = "P-abc123"
    message = build_t3_blocked_denial_message(
        approval_id=approval_id,
        command="git push origin main",
        verb="push",
        category="MUTATIVE",
    )

    assert f"Skill('{_SUBAGENT_APPROVAL_SKILL}')" in message, (
        f"Denial message must contain literal Skill('{_SUBAGENT_APPROVAL_SKILL}'), "
        f"got: {message!r}"
    )


# ---------------------------------------------------------------------------
# Supporting tests for the full denial message contract
# ---------------------------------------------------------------------------

def test_denial_contains_t3_blocked_marker():
    """Denial message starts with [T3_BLOCKED] sentinel."""
    message = build_t3_blocked_denial_message(
        approval_id="P-deadbeef",
        command="kubectl delete pod mypod",
        verb="delete",
        category="MUTATIVE",
    )
    assert message.startswith("[T3_BLOCKED]"), (
        f"Denial message must start with [T3_BLOCKED], got: {message[:40]!r}"
    )


def test_denial_contains_approval_id():
    """Denial message includes the approval_id so the subagent can report it."""
    approval_id = "P-1234567890abcdef"
    message = build_t3_blocked_denial_message(
        approval_id=approval_id,
        command="rm -rf /tmp/old",
        verb="rm",
        category="MUTATIVE",
    )
    assert approval_id in message, (
        f"Denial message must contain approval_id={approval_id!r}, "
        f"got: {message!r}"
    )


def test_denial_contains_command():
    """Denial message includes the blocked command for operator visibility."""
    command = "terraform destroy -auto-approve"
    message = build_t3_blocked_denial_message(
        approval_id="P-aaaa",
        command=command,
        verb="destroy",
        category="MUTATIVE",
    )
    assert command in message, (
        f"Denial message must contain the original command, "
        f"got: {message!r}"
    )


def test_skill_name_constant_matches_d11():
    """_SUBAGENT_APPROVAL_SKILL matches the canonical D11 skill name."""
    assert _SUBAGENT_APPROVAL_SKILL == "subagent-request-approval", (
        f"D11 canonical name is 'subagent-request-approval', "
        f"but _SUBAGENT_APPROVAL_SKILL={_SUBAGENT_APPROVAL_SKILL!r}"
    )


def test_denial_instructs_no_retry():
    """Denial message tells the subagent not to retry the blocked command."""
    message = build_t3_blocked_denial_message(
        approval_id="P-xyz",
        command="docker push myrepo/myimage:latest",
        verb="push",
        category="MUTATIVE",
    )
    # The message must include language that discourages retry so the
    # subagent does not loop. Case-insensitive match to allow phrasing
    # changes without breaking the intent check.
    assert "do not retry" in message.lower() or "do NOT retry" in message, (
        f"Denial message must instruct the agent not to retry, "
        f"got: {message!r}"
    )
