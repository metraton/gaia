"""T3 boundary tests for OpenCode identity and permission compatibility."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3] / "hooks"))

from adapters.opencode import OpenCodeAdapter


def test_structured_role_context_survives_event_normalization():
    event = OpenCodeAdapter().parse_event(json.dumps({
        "event": "tool.execute.before",
        "sessionID": "ses-1",
        "agentID": "agent-1",
        "roleContext": {
            "role": "gaia-orchestrator",
            "capabilities": ["plan.manage"],
            "issuer": "opencode-runtime",
            "attestation": "ses-1:gaia-orchestrator",
            "verified": True,
        },
        "tool": "bash",
        "args": {"command": "gaia plan show brief"},
    }))

    assert event.role_context is not None
    assert event.role_context.role == "gaia-orchestrator"
    assert event.role_context.capabilities == ("plan.manage",)


def test_malformed_role_context_is_rejected_before_policy():
    with pytest.raises(ValueError, match="role_context"):
        OpenCodeAdapter().parse_event(json.dumps({
            "event": "tool.execute.before",
            "sessionID": "ses-1",
            "roleContext": {"role": "gaia-orchestrator", "capabilities": "plan.manage"},
            "tool": "bash",
        }))


def test_forged_role_name_is_rejected_when_it_disagrees_with_context():
    event = OpenCodeAdapter().parse_event(json.dumps({
        "event": "tool.execute.before",
        "sessionID": "ses-1",
        "agent": "ordinary-agent",
        "roleContext": {
            "role": "gaia-orchestrator",
            "issuer": "opencode-runtime",
            "attestation": "ses-1:gaia-orchestrator",
            "verified": True,
        },
        "tool": "bash",
        "args": {"command": "gaia plan show brief"},
    }))

    response = OpenCodeAdapter().adapt_pre_tool_use(event)
    assert response.output["action"] == "deny"
    assert "does not match" in response.output["reason"]


def test_unverified_ordinary_agent_cannot_dispatch_control_plane_task():
    event = OpenCodeAdapter().parse_event(json.dumps({
        "event": "tool.execute.before",
        "sessionID": "ses-1",
        "roleContext": {
            "role": "developer",
            "issuer": "opencode-runtime",
            "attestation": "ses-1:developer",
            "verified": True,
        },
        "tool": "task",
        "args": {"subagent_type": "developer", "prompt": "dispatch again"},
    }))

    response = OpenCodeAdapter().adapt_pre_tool_use(event)
    assert response.output["action"] == "deny"
    assert "control-plane" in response.output["reason"]
