#!/usr/bin/env python3
"""Tests for Dynamic Identity Provider."""

import sys
import pytest
from pathlib import Path
from unittest.mock import patch

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.identity.identity_provider import build_identity
from modules.identity.security_identity import build_security_identity
from modules.identity.ops_identity import build_ops_identity

_PATCH_MODE = "modules.identity.identity_provider.get_plugin_mode"


class TestSecurityIdentity:

    def test_security_identity_is_minimal(self):
        identity = build_security_identity()
        assert "blocked" in identity
        assert "do not attempt alternatives" in identity

    def test_security_identity_no_tiers(self):
        """Security identity should NOT explain tiers — the hook handles that."""
        identity = build_security_identity()
        assert "T0" not in identity
        assert "T3" not in identity


class TestOpsIdentity:
    """ops_identity.py is now a stub (returns empty string).
    Full identity lives in agents/gaia-orchestrator.md.
    These tests verify the agent definition file contains the required elements.
    """

    @pytest.fixture(autouse=True)
    def _load_agent_def(self):
        agent_def = Path(__file__).resolve().parents[4] / "agents" / "gaia-orchestrator.md"
        assert agent_def.exists(), "agents/gaia-orchestrator.md not found"
        self.agent_content = agent_def.read_text()

    def test_ops_identity_stub_returns_empty(self):
        """build_ops_identity() is now a stub returning empty string."""
        identity = build_ops_identity()
        assert identity == ""

    def test_agent_def_has_orchestrator(self):
        assert "Orchestrator" in self.agent_content

    def test_agent_def_has_intent_based_routing(self):
        """Agent definition references routing suggestion."""
        assert "routing suggestion" in self.agent_content, (
            "Agent def must reference routing suggestion for intent-based routing"
        )

    def test_agent_def_has_sendmessage(self):
        assert "SendMessage" in self.agent_content

    def test_agent_def_has_routing(self):
        """Agent definition documents routing."""
        assert "Routing" in self.agent_content


class TestIdentityProvider:

    def test_security_mode(self):
        with patch(_PATCH_MODE, return_value="security"):
            identity = build_identity()
        assert "blocked" in identity
        assert "Orchestrator" not in identity

    def test_ops_mode(self):
        """ops mode now returns empty string (identity moved to agent definition)."""
        with patch(_PATCH_MODE, return_value="ops"):
            identity = build_identity()
        assert identity == ""

    def test_modes_are_different(self):
        with patch(_PATCH_MODE, return_value="security"):
            sec = build_identity()
        with patch(_PATCH_MODE, return_value="ops"):
            ops = build_identity()
        assert sec != ops
