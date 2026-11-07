"""
Shared utility functions for Gaia hooks.

The dual-channel warning, and ``has_stdin_data``, defined in
``modules.core.stdin`` and re-exported here.
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
