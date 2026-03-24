"""
Distribution Channel Detection for Gaia-Ops.

Utility module that detects whether gaia-ops is running as a Claude Code plugin
or via npm. Used by entry points to log coexistence warnings and by business
logic to adapt behavior per channel.
"""

import os
from pathlib import Path


def is_dual_channel_active() -> bool:
    """Check if both plugin and npm installations exist simultaneously."""
    has_plugin = bool(os.environ.get("CLAUDE_PLUGIN_ROOT"))
    has_npm = Path(".claude/hooks").is_symlink()
    return has_plugin and has_npm
