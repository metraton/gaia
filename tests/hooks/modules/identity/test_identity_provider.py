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

    def test_ops_identity_has_orchestrator(self):
        identity = build_ops_identity()
        assert "Orchestrator" in identity

    def test_ops_identity_delegates_to_project_dispatch(self):
        """Identity delegates agent routing to the project-dispatch skill."""
        identity = build_ops_identity()
        assert "project-dispatch" in identity, (
            "Identity must reference project-dispatch skill for agent routing"
        )

    def test_ops_identity_has_sendmessage(self):
        identity = build_ops_identity()
        assert "SendMessage" in identity

    def test_ops_identity_has_dispatch_rules(self):
        identity = build_ops_identity()
        assert "Dispatch" in identity


class TestIdentityProvider:

    def test_security_mode(self):
        with patch(_PATCH_MODE, return_value="security"):
            identity = build_identity()
        assert "blocked" in identity
        assert "Orchestrator" not in identity

    def test_ops_mode(self):
        with patch(_PATCH_MODE, return_value="ops"):
            identity = build_identity()
        assert "Orchestrator" in identity

    def test_modes_are_different(self):
        with patch(_PATCH_MODE, return_value="security"):
            sec = build_identity()
        with patch(_PATCH_MODE, return_value="ops"):
            ops = build_identity()
        assert sec != ops
