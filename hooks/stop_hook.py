#!/usr/bin/env python3
"""
Stop hook for Claude Code Agent System.

Fires when Claude finishes responding. Evaluates whether the response has
adequate evidence quality. For MVP: logs the event and allows stop (exit 0).
Quality check logic will be wired in a future iteration.

Architecture:
- Uses adapter layer to parse Stop event
- Calls adapter.adapt_stop() for quality assessment
- Returns quality result via adapter format_quality_response()
- Exit code 0 = allow stop, exit code 2 = continue instead of stop
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

# Configure logging -- file handler only when GAIA_DEBUG is set; no
# hooks-*.log is written by default (see modules.core.logging_setup).
configure_hook_logging("stop_hook")
logger = logging.getLogger(__name__)


def _handle_stop(event) -> None:
    """Process a Stop event.

    Evaluates response quality and decides whether to allow the stop.
    For MVP, always allows stop (exit 0).

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


# ============================================================================
# STDIN HANDLER (Claude Code integration)
# ============================================================================

if __name__ == "__main__":
    run_hook(_handle_stop, hook_name="stop_hook")
