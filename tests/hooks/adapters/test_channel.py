"""Tests for hooks/adapters/channel.py -- distribution channel detection."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from adapters.channel import is_dual_channel_active


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
