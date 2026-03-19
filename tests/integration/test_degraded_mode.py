#!/usr/bin/env python3
"""
T027: Degraded Mode Validation Tests.

Verifies the system works correctly when project-context.json is MISSING.
The security pipeline must not depend on project-context for its core function
(command validation). Context-dependent features should degrade gracefully.

Modules under test:
  - tools/context/context_provider.py
  - tools/context/surface_router.py
  - hooks/adapters/claude_code.py + hooks/modules/tools/bash_validator.py
"""

import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup (follows existing integration test conventions)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"
TOOLS_DIR = REPO_ROOT / "tools" / "context"
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(TOOLS_DIR))


# ============================================================================
# Test Suite: PreToolUse works without project-context.json
# ============================================================================

class TestPreToolWithoutProjectContext:
    """Security validation must work even if project-context.json does not exist."""

    def test_safe_command_allowed_without_context(self, tmp_path, monkeypatch):
        """PreToolUse hook allows safe commands without project-context.json."""
        from adapters.claude_code import ClaudeCodeAdapter
        from adapters.types import HookEventType
        from modules.tools.bash_validator import BashValidator

        adapter = ClaudeCodeAdapter()
        stdin_json = json.dumps({
            "hook_event_name": "PreToolUse",
            "session_id": "degraded-test",
            "tool_name": "Bash",
            "tool_input": {"command": "kubectl get pods"},
        })

        event = adapter.parse_event(stdin_json)
        assert event.event_type == HookEventType.PRE_TOOL_USE

        validation_req = adapter.parse_pre_tool_use(event.payload)
        validator = BashValidator()
        result = validator.validate(validation_req.command)

        # Security works independently of project-context
        assert result.allowed is True

    def test_blocked_command_denied_without_context(self, tmp_path, monkeypatch):
        """PreToolUse hook blocks dangerous commands without project-context.json."""
        from adapters.claude_code import ClaudeCodeAdapter
        from modules.tools.bash_validator import BashValidator

        adapter = ClaudeCodeAdapter()
        stdin_json = json.dumps({
            "hook_event_name": "PreToolUse",
            "session_id": "degraded-test",
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /"},
        })

        event = adapter.parse_event(stdin_json)
        validation_req = adapter.parse_pre_tool_use(event.payload)
        validator = BashValidator()
        result = validator.validate(validation_req.command)

        # Blocked commands stay blocked regardless of context
        assert result.allowed is False
        assert result.block_response is None  # exit 2 path


# ============================================================================
# Test Suite: context_provider degrades on missing files
# ============================================================================

class TestContextProviderDegradedMode:
    """context_provider should handle missing files without crashing unexpectedly."""

    def test_load_project_context_exits_on_missing(self, tmp_path):
        """load_project_context calls sys.exit(1) when file is missing."""
        from context_provider import load_project_context

        missing_path = tmp_path / "nonexistent" / "project-context.json"
        with pytest.raises(SystemExit) as exc_info:
            load_project_context(missing_path)
        assert exc_info.value.code == 1

    def test_load_universal_rules_returns_empty_on_missing(self, tmp_path):
        """load_universal_rules returns empty rule sets when file is missing."""
        from context_provider import load_universal_rules

        result = load_universal_rules(
            "devops-developer",
            rules_file=tmp_path / "nonexistent-rules.json",
        )
        assert result == {"universal": [], "agent_specific": []}

    def test_detect_cloud_provider_defaults_to_gcp_on_empty(self):
        """detect_cloud_provider returns gcp when context has no provider info."""
        from context_provider import detect_cloud_provider

        result = detect_cloud_provider({})
        assert result == "gcp"

    def test_detect_cloud_provider_reads_metadata(self):
        """detect_cloud_provider reads from metadata.cloud_provider."""
        from context_provider import detect_cloud_provider

        result = detect_cloud_provider({"metadata": {"cloud_provider": "aws"}})
        assert result == "aws"


# ============================================================================
# Test Suite: surface_router degrades on missing config
# ============================================================================

class TestSurfaceRouterDegradedMode:
    """surface_router should return safe defaults when config is missing."""

    def test_load_config_returns_default_on_missing(self, tmp_path):
        """load_surface_routing_config returns default when file is missing."""
        from surface_router import load_surface_routing_config

        result = load_surface_routing_config(
            config_file=tmp_path / "nonexistent-routing.json"
        )
        assert result["version"] == "missing"
        assert result["reconnaissance_agent"] == "devops-developer"
        assert result["surfaces"] == {}

    def test_load_config_returns_default_on_invalid_json(self, tmp_path):
        """load_surface_routing_config returns default on malformed JSON."""
        from surface_router import load_surface_routing_config

        bad_file = tmp_path / "bad-routing.json"
        bad_file.write_text("{invalid json}")

        result = load_surface_routing_config(config_file=bad_file)
        assert result["version"] == "invalid"
        assert result["surfaces"] == {}

    def test_classify_surfaces_returns_reconnaissance_on_empty_config(self):
        """classify_surfaces returns reconnaissance routing with empty surfaces."""
        from surface_router import classify_surfaces

        result = classify_surfaces(
            "check pod health",
            current_agent="devops-developer",
            routing_config={"surfaces": {}, "reconnaissance_agent": "devops-developer"},
        )
        assert result["active_surfaces"] == []
        assert result["dispatch_mode"] == "reconnaissance"
        assert result["confidence"] == 0.0
        assert "devops-developer" in result["recommended_agents"]

    def test_classify_surfaces_uses_agent_fallback(self):
        """classify_surfaces falls back to agent surface when no signals match."""
        from surface_router import classify_surfaces

        routing_config = {
            "surfaces": {
                "app_ci_tooling": {
                    "primary_agent": "devops-developer",
                    "signals": {"keywords": ["xyznonexistent"]},
                },
            },
            "reconnaissance_agent": "devops-developer",
        }

        result = classify_surfaces(
            "do something generic",
            current_agent="devops-developer",
            routing_config=routing_config,
        )
        # Falls back to the agent's own surface
        assert "app_ci_tooling" in result["active_surfaces"]

    def test_build_investigation_brief_with_empty_routing(self):
        """build_investigation_brief works with empty routing results."""
        from surface_router import build_investigation_brief

        routing_config = {"surfaces": {}, "reconnaissance_agent": "devops-developer"}

        result = build_investigation_brief(
            "generic task",
            "devops-developer",
            {},
            routing_config=routing_config,
        )
        assert result["agent_role"] == "reconnaissance"
        assert result["active_surfaces"] == []
        assert not result["cross_check_required"]
