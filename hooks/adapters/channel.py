"""
Distribution Channel Detection for Gaia-Ops.

Utility module that detects whether gaia-ops is running as a Claude Code plugin
or via npm. Used by entry points to log coexistence warnings and by business
logic to adapt behavior per channel.
"""

import os
from pathlib import Path

from .types import DistributionChannel


def detect_distribution_channel() -> DistributionChannel:
    """Detect if running as Claude Code plugin or npm package."""
    # Plugin channel: CLAUDE_PLUGIN_ROOT env var is set
    if os.environ.get("CLAUDE_PLUGIN_ROOT"):
        return DistributionChannel.PLUGIN
    # npm channel: check if we're inside node_modules
    current = Path(__file__).resolve()
    if "node_modules" in current.parts:
        return DistributionChannel.NPM
    # Development: local source
    return DistributionChannel.NPM  # default to npm behavior


def get_plugin_root() -> str:
    """Get the plugin root path, works in both channels."""
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        return plugin_root
    # npm channel: walk up from this file to find the package root
    current = Path(__file__).resolve().parent.parent.parent
    return str(current)


def is_dual_channel_active() -> bool:
    """Check if both plugin and npm installations exist simultaneously."""
    has_plugin = bool(os.environ.get("CLAUDE_PLUGIN_ROOT"))
    has_npm = Path(".claude/hooks").is_symlink()
    return has_plugin and has_npm
