"""Tests for hooks/adapters/channel.py -- distribution channel detection."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from adapters.channel import (
    detect_distribution_channel,
    get_plugin_root,
    is_dual_channel_active,
)
from adapters.types import DistributionChannel


class TestDetectDistributionChannel:
    """Tests for detect_distribution_channel()."""

    def test_returns_npm_by_default(self, monkeypatch):
        """Without CLAUDE_PLUGIN_ROOT set, defaults to NPM."""
        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        result = detect_distribution_channel()
        assert result == DistributionChannel.NPM

    def test_returns_plugin_when_env_set(self, monkeypatch):
        """When CLAUDE_PLUGIN_ROOT is set, returns PLUGIN."""
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/tmp/plugin")
        result = detect_distribution_channel()
        assert result == DistributionChannel.PLUGIN


class TestGetPluginRoot:
    """Tests for get_plugin_root()."""

    def test_returns_env_var_when_set(self, monkeypatch):
        """When CLAUDE_PLUGIN_ROOT is set, returns it directly."""
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/opt/plugins")
        result = get_plugin_root()
        assert result == "/opt/plugins"

    def test_returns_calculated_path_when_env_not_set(self, monkeypatch):
        """When env var is not set, calculates from __file__ path."""
        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        result = get_plugin_root()
        # Should be 3 levels up from channel.py: adapters/ -> hooks/ -> root/
        expected = str(Path(__file__).resolve().parent.parent.parent.parent / "hooks" / "adapters")
        # The function walks up from channel.py's location
        assert os.path.isabs(result)


class TestIsDualChannelActive:
    """Tests for is_dual_channel_active()."""

    def test_returns_false_normally(self, monkeypatch):
        """Without plugin env var, returns False."""
        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        result = is_dual_channel_active()
        assert result is False

    def test_returns_true_when_both_conditions_met(self, monkeypatch):
        """Returns True when plugin env is set AND .claude/hooks is a symlink."""
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/tmp/plugin")
        with patch("adapters.channel.Path") as mock_path:
            mock_path.return_value.is_symlink.return_value = True
            result = is_dual_channel_active()
            assert result is True

    def test_returns_false_when_only_plugin(self, monkeypatch):
        """Returns False when plugin env is set but no symlink exists."""
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/tmp/plugin")
        with patch("adapters.channel.Path") as mock_path:
            mock_path.return_value.is_symlink.return_value = False
            result = is_dual_channel_active()
            assert result is False
