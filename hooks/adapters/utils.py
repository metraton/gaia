"""
Shared utility functions for Gaia-Ops hooks.

Centralizes common patterns that were previously copy-pasted across all hook
entry points (has_stdin_data, dual-channel warnings, etc.).
"""

import logging
import select
import sys

logger = logging.getLogger(__name__)


def has_stdin_data() -> bool:
    """Check if there is data available on stdin."""
    if sys.stdin.isatty():
        return False
    try:
        readable, _, _ = select.select([sys.stdin], [], [], 0)
        return bool(readable)
    except Exception:
        return not sys.stdin.isatty()


def warn_if_dual_channel() -> None:
    """Log a warning if both plugin and npm distribution channels are active."""
    from adapters.channel import is_dual_channel_active

    if is_dual_channel_active():
        logger.warning(
            "Both plugin and npm channels detected. Plugin channel takes precedence."
        )
