"""
Distribution Channel Detection for Gaia.

Detects whether the plugin channel (``CLAUDE_PLUGIN_ROOT`` set) and the npm
channel (``.claude/hooks`` a symlink) are both active; ``utils.warn_if_dual_channel``
logs the warning.
"""

import os
from pathlib import Path


def is_dual_channel_active() -> bool:
    """Check if both plugin and npm installations exist simultaneously."""
    has_plugin = bool(os.environ.get("CLAUDE_PLUGIN_ROOT"))
    has_npm = Path(".claude/hooks").is_symlink()
    return has_plugin and has_npm
