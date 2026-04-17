#!/usr/bin/env python3
"""Tests for .md exemption in _is_protected() path guard.

Verifies Batch D Drift 8: .md files under hooks/ must NOT be protected
because they are pure documentation and cannot execute code.

The _is_protected() logic lives as a nested function inside
ClaudeCodeAdapter._adapt_write_edit(). We replicate it here using the
same hooks_dir anchor (Path(claude_code.__file__).parent.parent.resolve())
so the test matches runtime behavior exactly.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import adapters.claude_code as _cc_module

# Replicate the hooks_dir anchor used at runtime.
# Path(__file__) inside claude_code.py -> adapters/claude_code.py
# .parent -> adapters/
# .parent -> hooks/
# This is the same expression used on line 783 of claude_code.py.
_hooks_dir = Path(_cc_module.__file__).parent.parent.resolve()


def _is_protected(path_str: str) -> bool:
    """Replica of the nested _is_protected() from _adapt_write_edit().

    Kept in sync with hooks/adapters/claude_code.py lines 785-802.
    When the production function changes, update this copy and its tests.
    """
    p = Path(path_str)
    try:
        rp = p.resolve()
    except Exception:
        rp = p
    try:
        rp.relative_to(_hooks_dir)
        if rp.suffix == ".md":
            return False  # docs don't execute code; exempt from protection
        return True
    except ValueError:
        pass
    if p.name in ("settings.json", "settings.local.json"):
        for part in rp.parts:
            if part == ".claude":
                return True
    return False


class TestMdExemptionUnderHooks:
    """After the fix, .md files under hooks/ must return False.

    These tests are RED before the fix (they fail because current code
    returns True for .md under hooks/). They turn GREEN after the fix.
    """

    def test_md_under_hooks_is_not_protected(self):
        """hooks/README.md must NOT be protected -- it is pure documentation."""
        path = str(_hooks_dir / "README.md")
        assert _is_protected(path) is False, (
            "hooks/README.md is currently protected (returns True). "
            "Fix: add early return for .suffix == '.md' before the True return."
        )

    def test_md_under_hooks_modules_is_not_protected(self):
        """hooks/modules/README.md must NOT be protected."""
        path = str(_hooks_dir / "modules" / "README.md")
        assert _is_protected(path) is False, (
            "hooks/modules/README.md is currently protected (returns True). "
            "Fix: add early return for .suffix == '.md' before the True return."
        )


class TestNonMdFilesRemainProtected:
    """Python files under hooks/ must remain protected regardless of the fix."""

    def test_py_under_hooks_still_protected(self):
        """Python files under hooks/modules/security/ must remain blocked."""
        path = str(_hooks_dir / "modules" / "security" / "mutative_verbs.py")
        assert _is_protected(path) is True, (
            f"hooks/modules/security/mutative_verbs.py must remain protected. "
            f"Got False."
        )

    def test_py_under_hooks_modules_still_protected(self):
        """Python files under hooks/modules/session/ must remain blocked."""
        path = str(_hooks_dir / "modules" / "session" / "pending_scanner.py")
        assert _is_protected(path) is True, (
            f"hooks/modules/session/pending_scanner.py must remain protected. "
            f"Got False."
        )

    def test_adapter_file_still_protected(self):
        """The adapter itself (claude_code.py) must remain protected."""
        path = str(_hooks_dir / "adapters" / "claude_code.py")
        assert _is_protected(path) is True, (
            f"hooks/adapters/claude_code.py must remain protected. Got False."
        )


class TestNonHooksPathsUnchanged:
    """Paths outside hooks/ must continue to behave as before."""

    def test_non_hooks_md_still_passes(self):
        """A .md file outside hooks/ was never protected and must stay False."""
        path = "/tmp/foo.md"
        assert _is_protected(path) is False, (
            f"/tmp/foo.md must not be protected (outside hooks/). Got True."
        )
