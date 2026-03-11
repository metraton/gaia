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

import sys
import json
import logging
from datetime import datetime
from pathlib import Path

# Add hooks dir to path for adapter imports
sys.path.insert(0, str(Path(__file__).parent))

from adapters.claude_code import ClaudeCodeAdapter
from modules.core.hook_entry import run_hook
from modules.core.paths import get_logs_dir

# Configure logging
_log_file = get_logs_dir() / f"hooks-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [stop_hook] %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(_log_file)],
)
logger = logging.getLogger(__name__)


def _handle_stop(event) -> None:
    """Process a Stop event.

    Evaluates response quality and decides whether to allow the stop.
    For MVP, always allows stop (exit 0).

    Args:
        event: Parsed HookEvent from the adapter layer.
    """
    adapter = ClaudeCodeAdapter()

    # Parse stop event via adapter
    quality_result = adapter.adapt_stop(event.payload)
    stop_reason = event.payload.get("stop_reason", "unknown")

    logger.info(
        "Stop: reason=%s, quality_sufficient=%s, score=%.2f",
        stop_reason,
        quality_result.quality_sufficient,
        quality_result.score,
    )

    # Format and output quality response
    response = adapter.format_quality_response(quality_result)
    print(json.dumps(response.output))
    sys.exit(0)


# ============================================================================
# STDIN HANDLER (Claude Code integration)
# ============================================================================

if __name__ == "__main__":
    run_hook(_handle_stop, hook_name="stop_hook")