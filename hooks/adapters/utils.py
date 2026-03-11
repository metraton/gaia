"""
Shared utility functions for Gaia-Ops hooks.

Centralizes common patterns that were previously copy-pasted across all hook
entry points (has_stdin_data, dual-channel warnings, etc.).

``has_stdin_data`` is now defined in ``modules.core.stdin`` and re-exported
here for backward compatibility with existing imports.
"""

import logging

from modules.core.stdin import has_stdin_data  # noqa: F401 -- re-export

logger = logging.getLogger(__name__)


def warn_if_dual_channel() -> None:
    """Log a warning if both plugin and npm distribution channels are active."""
    from adapters.channel import is_dual_channel_active

    if is_dual_channel_active():
        logger.warning(
            "Both plugin and npm channels detected. Plugin channel takes precedence."
        )
