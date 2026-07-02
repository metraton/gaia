#!/usr/bin/env python3
"""
Tests for the plugin registry membership helper (has_plugin).

Gaia ships as a single unified plugin; the former runtime mode-detection
layer has been removed. Only ``has_plugin`` remains.
"""

import json
import sys
import pytest
from pathlib import Path
from unittest.mock import patch

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.core.plugin_mode import has_plugin
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


class TestHasPlugin:
    """Test has_plugin() function."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        """Clear caches and patch data dir."""
        clear_path_cache()
        self.tmp_path = tmp_path
        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        with patch(_PATCH_TARGET, return_value=tmp_path):
            yield

    def test_has_plugin_found(self):
        """has_plugin returns True when plugin is in registry."""
        _write_registry(self.tmp_path, ["gaia"])
        assert has_plugin("gaia") is True

    def test_has_plugin_not_found(self):
        """has_plugin returns False when plugin is not in registry."""
        _write_registry(self.tmp_path, ["gaia"])
        assert has_plugin("other-plugin") is False

    def test_has_plugin_no_registry(self):
        """has_plugin returns False when no registry exists."""
        assert has_plugin("gaia") is False
