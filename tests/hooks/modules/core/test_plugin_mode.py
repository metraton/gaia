#!/usr/bin/env python3
"""
Tests for Plugin Mode Detection Module.

Validates:
1. Default mode is security (most restrictive)
2. Environment variable fallback
3. Registry-based detection
4. Registry precedence over env var
5. Convenience functions
6. Cache clearing
"""

import json
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import patch

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.core.plugin_mode import (
    get_plugin_mode,
    has_plugin,
    is_ops_mode,
    is_security_mode,
    clear_mode_cache,
    DEFAULT_MODE,
    VALID_MODES,
)
from modules.core.paths import clear_path_cache

# Patch target: the source module that get_plugin_data_dir lives in,
# since plugin_mode.py imports it via `from .paths import get_plugin_data_dir`
_PATCH_TARGET = "modules.core.paths.get_plugin_data_dir"


def _write_registry(tmp_path, installed_plugins):
    """Helper to write a plugin-registry.json file."""
    registry = {"installed": [{"name": name} for name in installed_plugins]}
    registry_path = tmp_path / "plugin-registry.json"
    registry_path.write_text(json.dumps(registry))
    return registry_path


class TestDefaultMode:
    """Test default mode when no registry or env var is present."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        """Clear caches and patch data dir to an empty tmp_path."""
        clear_path_cache()
        clear_mode_cache()
        monkeypatch.delenv("GAIA_PLUGIN_MODE", raising=False)
        with patch(_PATCH_TARGET, return_value=tmp_path):
            yield
        clear_mode_cache()

    def test_default_mode_is_security(self):
        """No registry, no env var -> returns 'security'."""
        assert get_plugin_mode() == "security"
        assert get_plugin_mode() == DEFAULT_MODE


class TestEnvVarFallback:
    """Test GAIA_PLUGIN_MODE environment variable detection."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        """Clear caches and patch data dir to an empty tmp_path (no registry)."""
        clear_path_cache()
        clear_mode_cache()
        monkeypatch.delenv("GAIA_PLUGIN_MODE", raising=False)
        with patch(_PATCH_TARGET, return_value=tmp_path):
            yield
        clear_mode_cache()

    def test_env_var_ops(self, monkeypatch):
        """GAIA_PLUGIN_MODE=ops -> returns 'ops'."""
        monkeypatch.setenv("GAIA_PLUGIN_MODE", "ops")
        clear_mode_cache()
        assert get_plugin_mode() == "ops"

    def test_env_var_security(self, monkeypatch):
        """GAIA_PLUGIN_MODE=security -> returns 'security'."""
        monkeypatch.setenv("GAIA_PLUGIN_MODE", "security")
        clear_mode_cache()
        assert get_plugin_mode() == "security"

    def test_env_var_case_insensitive(self, monkeypatch):
        """GAIA_PLUGIN_MODE=OPS (uppercase) -> returns 'ops'."""
        monkeypatch.setenv("GAIA_PLUGIN_MODE", "OPS")
        clear_mode_cache()
        assert get_plugin_mode() == "ops"

    def test_env_var_invalid_falls_to_default(self, monkeypatch):
        """GAIA_PLUGIN_MODE=invalid -> returns 'security' (default)."""
        monkeypatch.setenv("GAIA_PLUGIN_MODE", "invalid")
        clear_mode_cache()
        assert get_plugin_mode() == "security"


class TestRegistryDetection:
    """Test plugin-registry.json based detection."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        """Clear caches and patch data dir."""
        clear_path_cache()
        clear_mode_cache()
        self.tmp_path = tmp_path
        monkeypatch.delenv("GAIA_PLUGIN_MODE", raising=False)
        with patch(_PATCH_TARGET, return_value=tmp_path):
            yield
        clear_mode_cache()

    def test_registry_ops(self):
        """Registry has gaia-ops -> returns 'ops'."""
        _write_registry(self.tmp_path, ["gaia-ops"])
        assert get_plugin_mode() == "ops"

    def test_registry_security_only(self):
        """Registry has only gaia-security -> returns 'security'."""
        _write_registry(self.tmp_path, ["gaia-security"])
        assert get_plugin_mode() == "security"

    def test_registry_both_plugins(self):
        """Registry has both gaia-ops and gaia-security -> returns 'ops' (ops wins)."""
        _write_registry(self.tmp_path, ["gaia-security", "gaia-ops"])
        assert get_plugin_mode() == "ops"

    def test_registry_unknown_plugins(self):
        """Registry has unknown plugins only -> falls through to default."""
        _write_registry(self.tmp_path, ["other-plugin"])
        assert get_plugin_mode() == "security"

    def test_registry_takes_precedence_over_env(self, monkeypatch):
        """Registry says ops, env says security -> returns 'ops' (registry wins)."""
        _write_registry(self.tmp_path, ["gaia-ops"])
        monkeypatch.setenv("GAIA_PLUGIN_MODE", "security")
        clear_mode_cache()
        assert get_plugin_mode() == "ops"


class TestHasPlugin:
    """Test has_plugin() function."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        """Clear caches and patch data dir."""
        clear_path_cache()
        clear_mode_cache()
        self.tmp_path = tmp_path
        with patch(_PATCH_TARGET, return_value=tmp_path):
            yield
        clear_mode_cache()

    def test_has_plugin_found(self):
        """has_plugin returns True when plugin is in registry."""
        _write_registry(self.tmp_path, ["gaia-ops", "gaia-security"])
        assert has_plugin("gaia-ops") is True
        assert has_plugin("gaia-security") is True

    def test_has_plugin_not_found(self):
        """has_plugin returns False when plugin is not in registry."""
        _write_registry(self.tmp_path, ["gaia-security"])
        assert has_plugin("gaia-ops") is False

    def test_has_plugin_no_registry(self):
        """has_plugin returns False when no registry exists."""
        assert has_plugin("gaia-ops") is False


class TestConvenienceFunctions:
    """Test is_ops_mode() and is_security_mode()."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        """Clear caches and patch data dir."""
        clear_path_cache()
        clear_mode_cache()
        monkeypatch.delenv("GAIA_PLUGIN_MODE", raising=False)
        with patch(_PATCH_TARGET, return_value=tmp_path):
            yield
        clear_mode_cache()

    def test_is_ops_mode(self, monkeypatch):
        """is_ops_mode() returns True in ops mode."""
        monkeypatch.setenv("GAIA_PLUGIN_MODE", "ops")
        clear_mode_cache()
        assert is_ops_mode() is True
        assert is_security_mode() is False

    def test_is_security_mode(self):
        """is_security_mode() returns True in security mode (default)."""
        assert is_security_mode() is True
        assert is_ops_mode() is False


class TestCacheClearing:
    """Test cache clearing behavior."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        """Clear caches and patch data dir."""
        clear_path_cache()
        clear_mode_cache()
        self.tmp_path = tmp_path
        monkeypatch.delenv("GAIA_PLUGIN_MODE", raising=False)
        with patch(_PATCH_TARGET, return_value=tmp_path):
            yield
        clear_mode_cache()

    def test_cache_cleared(self, monkeypatch):
        """clear_mode_cache allows mode to be re-evaluated."""
        # First call: default security
        assert get_plugin_mode() == "security"

        # Change env var
        monkeypatch.setenv("GAIA_PLUGIN_MODE", "ops")
        # Still cached as security
        assert get_plugin_mode() == "security"

        # Clear cache -> now picks up the env var
        clear_mode_cache()
        assert get_plugin_mode() == "ops"

    def test_clear_path_cache_also_clears_mode_cache(self, monkeypatch):
        """clear_path_cache() cascades to clear_mode_cache()."""
        assert get_plugin_mode() == "security"

        monkeypatch.setenv("GAIA_PLUGIN_MODE", "ops")
        # Clear via path cache (should cascade)
        clear_path_cache()
        assert get_plugin_mode() == "ops"
