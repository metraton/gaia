#!/usr/bin/env python3
"""
TaskCompleted hook for Claude Code Agent System.

Fires when a task is marked complete. Verifies that completion criteria are
met before allowing the task to close. For MVP: logs the event and allows
completion (passthrough). Quality checks will be wired in a future iteration.

Architecture:
- Uses adapter layer to parse TaskCompleted event
- Calls adapter.adapt_task_completed() for criteria verification
- Returns verification result via adapter format_verification_response()
"""

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
configure_hook_logging("task_completed")
logger = logging.getLogger(__name__)


def _handle_task_completed(event) -> None:
    """Process a TaskCompleted event.

    Checks whether task completion criteria are met.
    For MVP, always allows completion.

    Args:
        event: Parsed HookEvent from the adapter layer.
    """
    adapter = get_adapter()

    # Parse task completed event via adapter
    verification_result = adapter.adapt_task_completed(event.payload)
    task_id = event.payload.get("task_id", "unknown")

    logger.info(
        "TaskCompleted: task_id=%s, criteria_met=%s, block=%s",
        task_id,
        verification_result.criteria_met,
        verification_result.block_completion,
    )

    # Format and output verification response
    response = adapter.format_verification_response(verification_result)
    print(json.dumps(response.output))
    sys.exit(0)


# ============================================================================
# STDIN HANDLER (Claude Code integration)
# ============================================================================

if __name__ == "__main__":
    run_hook(_handle_task_completed, hook_name="task_completed")
