#!/usr/bin/env python3
"""
Tests for Configuration Loader.

Validates:
1. JSON config loading
2. Caching behavior
3. Default configurations
"""

import os
import sys
import json
import pytest
from pathlib import Path
from unittest.mock import patch

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.core.config_loader import (
    ConfigLoader,
    get_config,
    reset_config_loader,
    get_default_config,
    DEFAULT_CONFIGS,
)


class TestConfigLoader:
    """Test ConfigLoader class."""

    @pytest.fixture
    def config_dir(self, tmp_path):
        """Create temp config directory."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        return config_dir

    @pytest.fixture
    def loader(self, config_dir):
        """Create ConfigLoader with temp directory."""
        return ConfigLoader(config_dir=config_dir)

    def test_loads_existing_config(self, loader, config_dir):
        """Test loading existing JSON config."""
        # Create config file
        config_data = {"key": "value", "number": 42}
        config_file = config_dir / "test_config.json"
        with open(config_file, "w") as f:
            json.dump(config_data, f)

        result = loader.load("test_config")
        assert result["key"] == "value"
        assert result["number"] == 42

    def test_returns_default_for_missing_config(self, loader):
        """Test returns default when config file missing."""
        default = {"default": "value"}
        result = loader.load("nonexistent", default=default)
        assert result == default

    def test_returns_empty_dict_for_missing_without_default(self, loader):
        """Test returns empty dict when no default provided."""
        result = loader.load("nonexistent")
        assert result == {}

    def test_caches_loaded_config(self, loader, config_dir):
        """Test that configs are cached."""
        config_data = {"cached": True}
        config_file = config_dir / "cached.json"
        with open(config_file, "w") as f:
            json.dump(config_data, f)

        result1 = loader.load("cached")
        result2 = loader.load("cached")
        assert result1 is result2  # Same object (cached)

    def test_reload_bypasses_cache(self, loader, config_dir):
        """Test reload forces fresh load."""
        config_file = config_dir / "reloadable.json"
        with open(config_file, "w") as f:
            json.dump({"version": 1}, f)

        result1 = loader.load("reloadable")
        assert result1["version"] == 1

        # Update file
        with open(config_file, "w") as f:
            json.dump({"version": 2}, f)

        # Regular load returns cached
        result2 = loader.load("reloadable")
        assert result2["version"] == 1  # Still cached

        # Reload gets fresh
        result3 = loader.reload("reloadable")
        assert result3["version"] == 2

    def test_clear_cache(self, loader, config_dir):
        """Test clearing all cached configs."""
        config_file = config_dir / "clearable.json"
        with open(config_file, "w") as f:
            json.dump({"data": "original"}, f)

        result1 = loader.load("clearable")

        # Update file
        with open(config_file, "w") as f:
            json.dump({"data": "updated"}, f)

        # Clear cache
        loader.clear_cache()

        # Load gets fresh data
        result2 = loader.load("clearable")
        assert result2["data"] == "updated"

    def test_handles_invalid_json(self, loader, config_dir):
        """Test handles invalid JSON gracefully."""
        config_file = config_dir / "invalid.json"
        with open(config_file, "w") as f:
            f.write("not valid json {")

        result = loader.load("invalid", default={"fallback": True})
        assert result["fallback"] is True

    def test_handles_permission_error(self, loader, config_dir):
        """Test handles permission errors gracefully."""
        config_file = config_dir / "restricted.json"
        with open(config_file, "w") as f:
            json.dump({"data": "secret"}, f)
        config_file.chmod(0o000)

        try:
            result = loader.load("restricted", default={"fallback": True})
            # Should return default on error
            assert result.get("fallback") is True
        finally:
            config_file.chmod(0o644)


class TestGetConfigFunction:
    """Test get_config convenience function."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Reset config loader before each test."""
        reset_config_loader()

    def test_returns_config(self, tmp_path):
        """Test get_config returns config data."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_file = config_dir / "test.json"
        with open(config_file, "w") as f:
            json.dump({"test": True}, f)

        with patch("modules.core.config_loader.get_hooks_config_dir", return_value=config_dir):
            reset_config_loader()
            result = get_config("test")
            assert result["test"] is True

    def test_uses_singleton(self, tmp_path):
        """Test get_config uses singleton loader."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        with patch("modules.core.config_loader.get_hooks_config_dir", return_value=config_dir):
            reset_config_loader()
            # Multiple calls should use same loader
            get_config("test1")
            get_config("test2")


class TestDefaultConfigs:
    """Test default configurations."""

    def test_safe_commands_default_exists(self):
        """Test safe_commands default config exists."""
        default = get_default_config("safe_commands")
        assert "always_safe" in default
        assert "always_safe_multiword" in default
        assert "conditional_safe" in default

    def test_blocked_commands_default_exists(self):
        """Test blocked_commands default config exists."""
        default = get_default_config("blocked_commands")
        assert "patterns" in default
        assert "keywords" in default

    def test_security_tiers_default_exists(self):
        """Test security_tiers default config exists."""
        default = get_default_config("security_tiers")
        assert "T0" in default
        assert "T1" in default
        assert "T2" in default
        assert "T3" in default
        assert default["T3"]["approval_required"] is True

    def test_thresholds_default_exists(self):
        """Test thresholds default config exists."""
        default = get_default_config("thresholds")
        assert "long_execution_seconds" in default
        assert "consecutive_failures_alert" in default

    def test_unknown_config_returns_empty(self):
        """Test unknown config name returns empty dict."""
        default = get_default_config("nonexistent_config")
        assert default == {}


class TestDefaultConfigStructure:
    """Test default config structure details."""

    def test_safe_commands_contains_common_commands(self):
        """Test safe commands list contains common safe commands."""
        default = get_default_config("safe_commands")
        always_safe = default["always_safe"]

        common_safe = ["ls", "pwd", "cat", "head", "tail", "grep"]
        for cmd in common_safe:
            assert cmd in always_safe, f"{cmd} should be in always_safe"

    def test_safe_multiword_contains_read_operations(self):
        """Test multiword safe list contains read-only operations."""
        default = get_default_config("safe_commands")
        multiword = default["always_safe_multiword"]

        read_ops = ["git status", "kubectl get", "terraform plan"]
        for op in read_ops:
            assert op in multiword, f"{op} should be in always_safe_multiword"

    def test_blocked_patterns_contains_destructive(self):
        """Test blocked patterns contains destructive operations."""
        default = get_default_config("blocked_commands")
        patterns = default["patterns"]

        # Should contain some destructive patterns
        assert len(patterns) > 0
        assert any("rm" in p or "destroy" in p for p in patterns)
