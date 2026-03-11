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
from datetime import datetime
from pathlib import Path

# Add hooks dir to path for adapter imports
sys.path.insert(0, str(Path(__file__).parent))

from adapters.claude_code import ClaudeCodeAdapter
from adapters.utils import has_stdin_data
from modules.core.paths import get_logs_dir

# Configure logging
_log_file = get_logs_dir() / f"hooks-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [task_completed] %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(_log_file)],
)
logger = logging.getLogger(__name__)


def _handle_task_completed(stdin_data: str) -> None:
    """Process a TaskCompleted event.

    Checks whether task completion criteria are met.
    For MVP, always allows completion.

    Args:
        stdin_data: Raw JSON from stdin.
    """
    adapter = ClaudeCodeAdapter()

    try:
        event = adapter.parse_event(stdin_data)
    except ValueError as e:
        logger.error("Adapter parse failed: %s", e)
        sys.exit(1)

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
    if has_stdin_data():
        try:
            stdin_data = sys.stdin.read()
            _handle_task_completed(stdin_data)
        except Exception as e:
            logger.error("Error processing TaskCompleted hook: %s", e)
            sys.exit(1)
    else:
        print("Usage: echo '{...}' | python task_completed.py  (stdin mode)")
        sys.exit(1)