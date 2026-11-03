"""
Shared logging setup for hook entry points.

``configure_hook_logging()`` is the one place every hook entry point
(pre_tool_use, post_tool_use, session_start, ...) wires Python logging. The
file log it can write is plain-text debug output with no schema and no
consumer -- unlike the audit log (``modules.audit.logger``), nothing in Gaia
reads it back -- so the file handler is gated behind ``GAIA_DEBUG``: by default
the root logger gets a ``NullHandler`` and no file is created.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

from .paths import get_logs_dir

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _debug_enabled() -> bool:
    """Whether GAIA_DEBUG opts the caller into file-based hook logging."""
    return os.environ.get("GAIA_DEBUG", "").strip().lower() in _TRUE_VALUES


def configure_hook_logging(hook_name: str) -> None:
    """Configure root logging for a hook entry point.

    Args:
        hook_name: Human-readable hook name used in the log format's
            bracketed prefix when file logging is enabled (matches the
            entry point's file name, e.g. "pre_tool_use").

    By default (``GAIA_DEBUG`` unset), attaches a ``NullHandler`` to the
    root logger when it has no handler, and no ``hooks-*.log`` file is created.
    When ``GAIA_DEBUG`` is truthy, attaches a ``FileHandler`` writing to
    ``<data home>/logs/hooks-YYYY-MM-DD.log``.
    """
    root = logging.getLogger()
    if _debug_enabled():
        log_file = get_logs_dir() / f"hooks-{datetime.now().strftime('%Y-%m-%d')}.log"
        logging.basicConfig(
            level=logging.INFO,
            format=f"%(asctime)s [{hook_name}] %(name)s - %(levelname)s - %(message)s",
            handlers=[logging.FileHandler(log_file)],
        )
    elif not root.handlers:
        root.addHandler(logging.NullHandler())


__all__ = ["configure_hook_logging"]
