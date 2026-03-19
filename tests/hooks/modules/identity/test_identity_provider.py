#!/usr/bin/env python3
"""
Tests for Dynamic Identity Provider.

Validates:
1. Security mode produces correct identity
2. Ops mode produces correct identity with all agents
3. Core constraints are always present
4. Mode detection drives identity selection
"""

import sys
import pytest
from pathlib import Path
from unittest.mock import patch

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.identity.identity_provider import build_identity, _build_core_constraints
from modules.identity.security_identity import build_security_identity
from modules.identity.ops_identity import build_ops_identity
from modules.core.plugin_mode import clear_mode_cache
from modules.core.paths import clear_path_cache

_PATCH_MODE = "modules.identity.identity_provider.get_plugin_mode"


class TestSecurityIdentity:
    """Test security-only identity builder."""

    def test_security_identity_built(self):
        """Security identity contains the correct header."""
        identity = build_security_identity()
        assert "Gaia Security" in identity

    def test_security_identity_no_agents(self):
        """Security identity explicitly states no agent dispatch."""
        identity = build_security_identity()
        assert "do NOT have agent dispatch" in identity

    def test_security_identity_mentions_tiers(self):
        """Security identity describes the tier system."""
        identity = build_security_identity()
        assert "T0" in identity
        assert "T3" in identity


class TestOpsIdentity:
    """Test ops orchestrator identity builder."""

    def test_ops_identity_built(self):
        """Ops identity contains the correct header."""
        identity = build_ops_identity()
        assert "Gaia Ops Orchestrator" in identity

    def test_ops_identity_has_agents(self):
        """Ops identity lists all 6 agents."""
        identity = build_ops_identity()
        expected_agents = [
            "cloud-troubleshooter",
            "gitops-operator",
            "terraform-architect",
            "devops-developer",
            "speckit-planner",
            "gaia-system",
        ]
        for agent in expected_agents:
            assert agent in identity, f"Agent {agent} not found in ops identity"

    def test_ops_identity_has_sendmessage(self):
        """Ops identity mentions SendMessage tool."""
        identity = build_ops_identity()
        assert "SendMessage" in identity

    def test_ops_identity_has_dispatch_rules(self):
        """Ops identity includes dispatch rules."""
        identity = build_ops_identity()
        assert "Dispatch rules" in identity

    def test_ops_identity_has_nonce_security(self):
        """Ops identity mentions nonce-based approval."""
        identity = build_ops_identity()
        assert "nonce" in identity.lower()


class TestCoreConstraints:
    """Test core constraints applied to all modes."""

    def test_core_constraints_content(self):
        """Core constraints contain expected rules."""
        constraints = _build_core_constraints()
        assert "Trust your identity" in constraints
        assert "non-negotiable" in constraints
        assert "Never assume" in constraints


class TestIdentityProvider:
    """Test the identity provider dispatches correctly based on mode."""

    def test_security_mode_identity(self):
        """In security mode, identity contains Gaia Security."""
        with patch(_PATCH_MODE, return_value="security"):
            identity = build_identity()
        assert "Gaia Security" in identity

    def test_ops_mode_identity(self):
        """In ops mode, identity contains Gaia Ops Orchestrator."""
        with patch(_PATCH_MODE, return_value="ops"):
            identity = build_identity()
        assert "Gaia Ops Orchestrator" in identity

    def test_core_constraints_always_present_security(self):
        """Security mode includes core constraints."""
        with patch(_PATCH_MODE, return_value="security"):
            identity = build_identity()
        assert "Core Constraints" in identity
        assert "non-negotiable" in identity

    def test_core_constraints_always_present_ops(self):
        """Ops mode includes core constraints."""
        with patch(_PATCH_MODE, return_value="ops"):
            identity = build_identity()
        assert "Core Constraints" in identity
        assert "non-negotiable" in identity

    def test_identity_mode_detection(self):
        """Mock plugin_mode and verify correct identity is selected."""
        # Security mode
        with patch(_PATCH_MODE, return_value="security"):
            sec_identity = build_identity()
        # Ops mode
        with patch(_PATCH_MODE, return_value="ops"):
            ops_identity = build_identity()

        # They should be different
        assert sec_identity != ops_identity
        # Each should contain its own marker
        assert "Gaia Security" in sec_identity
        assert "Gaia Ops Orchestrator" in ops_identity
        # Neither should contain the other's marker
        assert "Gaia Ops Orchestrator" not in sec_identity
        assert "do NOT have agent dispatch" not in ops_identity
