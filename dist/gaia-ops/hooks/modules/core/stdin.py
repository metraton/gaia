"""
Stdin availability check for hook entrypoints.

Provides a single ``has_stdin_data()`` helper that determines whether the
current process has data available on stdin. This replaces the duplicate
implementations that previously lived in ``adapters.utils``.
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
