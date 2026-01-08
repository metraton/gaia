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
    get_logs_dir,
    get_metrics_dir,
    get_memory_dir,
    get_session_dir,
    get_hooks_config_dir,
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
        # tmp_path has no .claude
        with patch("modules.core.paths.Path.cwd", return_value=tmp_path):
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


class TestGetLogsDir:
    """Test get_logs_dir() function."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up test environment."""
        clear_path_cache()
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        with patch("modules.core.paths.find_claude_dir", return_value=claude_dir):
            yield claude_dir

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
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        with patch("modules.core.paths.find_claude_dir", return_value=claude_dir):
            yield claude_dir

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
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        with patch("modules.core.paths.find_claude_dir", return_value=claude_dir):
            yield claude_dir

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
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        with patch("modules.core.paths.find_claude_dir", return_value=claude_dir):
            yield claude_dir

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


class TestGetHooksConfigDir:
    """Test get_hooks_config_dir() function."""

    def test_returns_hooks_config_path(self):
        """Test returns path to hooks/config."""
        result = get_hooks_config_dir()
        assert result.name == "config"
        # Should be relative to hooks directory
        assert "hooks" in str(result.parent)


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
