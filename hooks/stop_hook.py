#!/usr/bin/env python3
"""
Stop hook for Claude Code Agent System.

Fires when Claude finishes responding. It logs the adapter's quality result
(adapt_stop reports quality_sufficient=True unconditionally) and always exits 0,
allowing the stop.
"""

import os
import sys
import json
import logging
from pathlib import Path

_hooks_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_hooks_dir))
_pkg_root = str(_hooks_dir.parent)
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)

from adapters.registry import get_adapter
from modules.core.hook_entry import run_hook
from modules.core.logging_setup import configure_hook_logging

configure_hook_logging("stop_hook")
logger = logging.getLogger(__name__)


def _handle_stop(event) -> None:
    """Log the Stop event's quality result and allow the stop (exit 0).

    Args:
        event: Parsed HookEvent from the adapter layer.
    """
    adapter = get_adapter()

    quality_result = adapter.adapt_stop(event.payload)
    stop_reason = event.payload.get("stop_reason", "unknown")

    logger.info(
        "Stop: reason=%s, quality_sufficient=%s, score=%.2f",
        stop_reason,
        quality_result.quality_sufficient,
        quality_result.score,
    )

    response = adapter.format_quality_response(quality_result)
    print(json.dumps(response.output))
    sys.exit(0)


if __name__ == "__main__":
    run_hook(_handle_stop, hook_name="stop_hook")
