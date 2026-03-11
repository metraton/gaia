#!/usr/bin/env python3
"""
SubagentStart hook for Claude Code Agent System.

Fires when a subagent is spawned. Injects agent-specific context such as
surface routing data, investigation briefs, and contract sections relevant
to the agent's domain. For MVP: logs the event and returns minimal context.
Full context injection will be wired to context_provider in a future iteration.

Architecture:
- Uses adapter layer to parse SubagentStart event
- Calls adapter.adapt_subagent_start() for context preparation
- Returns context result via adapter format_context_response()
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
    format='%(asctime)s [subagent_start] %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(_log_file)],
)
logger = logging.getLogger(__name__)


def _handle_subagent_start(stdin_data: str) -> None:
    """Process a SubagentStart event.

    Prepares agent-specific context for injection.
    For MVP, returns minimal context (passthrough).

    Args:
        stdin_data: Raw JSON from stdin.
    """
    adapter = ClaudeCodeAdapter()

    try:
        event = adapter.parse_event(stdin_data)
    except ValueError as e:
        logger.error("Adapter parse failed: %s", e)
        sys.exit(1)

    # Parse subagent start event via adapter
    context_result = adapter.adapt_subagent_start(event.payload)
    agent_type = event.payload.get("agent_type", "unknown")

    logger.info(
        "SubagentStart: agent_type=%s, context_injected=%s",
        agent_type,
        context_result.context_injected,
    )

    # Format and output context response
    response = adapter.format_context_response(context_result)
    print(json.dumps(response.output))
    sys.exit(0)


# ============================================================================
# STDIN HANDLER (Claude Code integration)
# ============================================================================

if __name__ == "__main__":
    if has_stdin_data():
        try:
            stdin_data = sys.stdin.read()
            _handle_subagent_start(stdin_data)
        except Exception as e:
            logger.error("Error processing SubagentStart hook: %s", e)
            sys.exit(1)
    else:
        print("Usage: echo '{...}' | python subagent_start.py  (stdin mode)")
        sys.exit(1)