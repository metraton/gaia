"""
test_install_detector_workspace.py -- AC-4 verification.

install_detector.resolve_workspace() delegates purely to gaia.project.current()
without reimplementing the fallback logic. B0 owns the three-level fallback;
this module only tests that delegation is pure (no transformation, no branch).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure hooks/ is on the path
_HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from modules.install_detector import resolve_workspace


def test_uses_identity_directly():
    """AC-4: resolve_workspace() delegates to gaia.project.current() and returns its value unchanged.

    Two sub-cases:
      1. Mock returns "global"              -> resolve_workspace() == "global"
      2. Mock returns "github.com/x/repo"  -> resolve_workspace() == "github.com/x/repo"

    No branch in install_detector transforms the return value.
    """
    with patch("gaia.project.current", return_value="global") as mock_current:
        result = resolve_workspace()
        assert result == "global", f"Expected 'global', got {result!r}"
        mock_current.assert_called_once()

    with patch("gaia.project.current", return_value="github.com/metraton/me") as mock_current:
        result = resolve_workspace()
        assert result == "github.com/metraton/me", (
            f"Expected 'github.com/metraton/me', got {result!r}"
        )
        mock_current.assert_called_once()


def test_resolve_workspace_passes_cwd_when_given():
    """resolve_workspace(cwd=<path>) passes cwd to gaia.project.current()."""
    test_cwd = "/tmp/test-project"
    with patch("gaia.project.current", return_value="test-project") as mock_current:
        result = resolve_workspace(cwd=test_cwd)
        assert result == "test-project"
        mock_current.assert_called_once_with(test_cwd)
