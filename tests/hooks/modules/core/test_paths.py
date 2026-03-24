#!/usr/bin/env python3
"""
Tests for Path Resolution Module.

Validates:
1. find_claude_dir() discovery
2. Directory helper functions
3. Path caching behavior
"""

import os
import sys
import pytest
from pathlib import Path
from unittest.mock import patch

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.core.paths import (
    find_claude_dir,
    get_plugin_data_dir,
    get_logs_dir,
    get_metrics_dir,
    get_memory_dir,
    get_session_dir,
    clear_path_cache,
)


class TestFindClaudeDir:
    """Test find_claude_dir() discovery."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Clear cache before each test."""
        clear_path_cache()

    def test_finds_claude_dir_in_current(self, tmp_path):
        """Test finding .claude in current directory."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()

        with patch("modules.core.paths.Path.cwd", return_value=tmp_path):
            clear_path_cache()
            result = find_claude_dir()
            assert result == claude_dir

    def test_finds_claude_dir_in_parent(self, tmp_path):
        """Test finding .claude in parent directory."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        child_dir = tmp_path / "child" / "grandchild"
        child_dir.mkdir(parents=True)

        with patch("modules.core.paths.Path.cwd", return_value=child_dir):
            clear_path_cache()
            result = find_claude_dir()
            assert result == claude_dir

    def test_returns_current_if_inside_claude_dir(self, tmp_path):
        """Test returns current dir if already inside .claude."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()

        with patch("modules.core.paths.Path.cwd", return_value=claude_dir):
            clear_path_cache()
            result = find_claude_dir()
            assert result == claude_dir

    def test_falls_back_to_current_claude(self, tmp_path):
        """Test fallback when no .claude found."""
        # tmp_path has no .claude, but /tmp/.claude may exist on the host.
        # Patch Path.exists to isolate from real filesystem above tmp_path.
        orig_exists = Path.exists

        def isolated_exists(p):
            if p.name == ".claude" and not str(p).startswith(str(tmp_path)):
                return False
            return orig_exists(p)

        with patch("modules.core.paths.Path.cwd", return_value=tmp_path), \
             patch.object(Path, "exists", isolated_exists):
            clear_path_cache()
            result = find_claude_dir()
            # Should return tmp_path/.claude (even if doesn't exist)
            assert result == tmp_path / ".claude"

    def test_result_is_cached(self, tmp_path):
        """Test that result is cached."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()

        with patch("modules.core.paths.Path.cwd", return_value=tmp_path):
            clear_path_cache()
            result1 = find_claude_dir()
            result2 = find_claude_dir()
            assert result1 is result2  # Same object (cached)


class TestGetPluginDataDir:
    """Test get_plugin_data_dir() resolution."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Clear cache before each test."""
        clear_path_cache()

    def test_returns_env_var_path_when_set(self, tmp_path):
        """Test uses CLAUDE_PLUGIN_DATA when set."""
        data_dir = tmp_path / "plugin-data"
        with patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": str(data_dir)}):
            clear_path_cache()
            result = get_plugin_data_dir()
            assert result == data_dir
            assert result.exists()  # created automatically

    def test_creates_directory_when_env_set(self, tmp_path):
        """Test creates CLAUDE_PLUGIN_DATA directory if it does not exist."""
        data_dir = tmp_path / "nonexistent" / "plugin-data"
        assert not data_dir.exists()
        with patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": str(data_dir)}):
            clear_path_cache()
            result = get_plugin_data_dir()
            assert result == data_dir
            assert result.exists()

    def test_falls_back_to_claude_dir_when_unset(self, tmp_path):
        """Test falls back to find_claude_dir() when env var is not set."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        with patch.dict(os.environ, {}, clear=False), \
             patch("modules.core.paths.Path.cwd", return_value=tmp_path):
            # Ensure CLAUDE_PLUGIN_DATA is not set
            os.environ.pop("CLAUDE_PLUGIN_DATA", None)
            clear_path_cache()
            result = get_plugin_data_dir()
            assert result == claude_dir

    def test_result_is_cached(self, tmp_path):
        """Test that result is cached."""
        data_dir = tmp_path / "plugin-data"
        with patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": str(data_dir)}):
            clear_path_cache()
            result1 = get_plugin_data_dir()
            result2 = get_plugin_data_dir()
            assert result1 is result2  # Same object (cached)

    def test_cache_cleared_by_clear_path_cache(self, tmp_path):
        """Test that clear_path_cache clears the plugin data dir cache too."""
        data_dir1 = tmp_path / "data1"
        data_dir2 = tmp_path / "data2"

        with patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": str(data_dir1)}):
            clear_path_cache()
            result1 = get_plugin_data_dir()

        with patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": str(data_dir2)}):
            clear_path_cache()
            result2 = get_plugin_data_dir()

        assert result1 != result2


class TestGetLogsDir:
    """Test get_logs_dir() function."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up test environment."""
        clear_path_cache()
        data_dir = tmp_path / "plugin-data"
        data_dir.mkdir()
        with patch("modules.core.paths.get_plugin_data_dir", return_value=data_dir):
            yield data_dir

    def test_returns_logs_path(self, setup):
        """Test returns correct logs path."""
        result = get_logs_dir()
        assert result.name == "logs"
        assert result.parent == setup

    def test_creates_directory_if_missing(self, setup):
        """Test creates logs directory if it doesn't exist."""
        result = get_logs_dir()
        assert result.exists()
        assert result.is_dir()


class TestGetMetricsDir:
    """Test get_metrics_dir() function."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up test environment."""
        clear_path_cache()
        data_dir = tmp_path / "plugin-data"
        data_dir.mkdir()
        with patch("modules.core.paths.get_plugin_data_dir", return_value=data_dir):
            yield data_dir

    def test_returns_metrics_path(self, setup):
        """Test returns correct metrics path."""
        result = get_metrics_dir()
        assert result.name == "metrics"
        assert result.parent == setup

    def test_creates_directory_if_missing(self, setup):
        """Test creates metrics directory if it doesn't exist."""
        result = get_metrics_dir()
        assert result.exists()
        assert result.is_dir()


class TestGetMemoryDir:
    """Test get_memory_dir() function."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up test environment."""
        clear_path_cache()
        data_dir = tmp_path / "plugin-data"
        data_dir.mkdir()
        with patch("modules.core.paths.get_plugin_data_dir", return_value=data_dir):
            yield data_dir

    def test_returns_memory_path(self, setup):
        """Test returns correct memory path."""
        result = get_memory_dir()
        assert result.name == "memory"
        assert result.parent == setup

    def test_returns_subdir_path(self, setup):
        """Test returns correct subdir path."""
        result = get_memory_dir("workflow-episodic")
        assert result.name == "workflow-episodic"
        assert result.parent.name == "memory"

    def test_creates_directory_if_missing(self, setup):
        """Test creates directory if it doesn't exist."""
        result = get_memory_dir()
        assert result.exists()
        assert result.is_dir()

    def test_creates_subdir_if_missing(self, setup):
        """Test creates subdirectory if it doesn't exist."""
        result = get_memory_dir("test-subdir")
        assert result.exists()
        assert result.is_dir()


class TestGetSessionDir:
    """Test get_session_dir() function."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up test environment."""
        clear_path_cache()
        data_dir = tmp_path / "plugin-data"
        data_dir.mkdir()
        with patch("modules.core.paths.get_plugin_data_dir", return_value=data_dir):
            yield data_dir

    def test_returns_session_active_path(self, setup):
        """Test returns correct session/active path."""
        result = get_session_dir()
        assert result.name == "active"
        assert result.parent.name == "session"

    def test_creates_directory_if_missing(self, setup):
        """Test creates directory if it doesn't exist."""
        result = get_session_dir()
        assert result.exists()
        assert result.is_dir()


class TestClearPathCache:
    """Test clear_path_cache() function."""

    def test_clears_cached_result(self, tmp_path):
        """Test that cache is cleared."""
        claude_dir1 = tmp_path / "dir1" / ".claude"
        claude_dir1.mkdir(parents=True)

        with patch("modules.core.paths.Path.cwd", return_value=tmp_path / "dir1"):
            clear_path_cache()
            result1 = find_claude_dir()

        claude_dir2 = tmp_path / "dir2" / ".claude"
        claude_dir2.mkdir(parents=True)

        with patch("modules.core.paths.Path.cwd", return_value=tmp_path / "dir2"):
            clear_path_cache()
            result2 = find_claude_dir()

        assert result1 != result2  # Different after cache clear
