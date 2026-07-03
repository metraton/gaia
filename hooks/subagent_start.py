#!/usr/bin/env python3
"""SubagentStart hook — logs agent dispatch, records skill snapshots,
and forwards cached project context into the subagent.

PreToolUse:Agent builds and caches the context; this hook reads the
cache and returns it as additionalContext so it reaches the subagent
(not the orchestrator)."""

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
configure_hook_logging("subagent_start")
logger = logging.getLogger(__name__)


def _handle_subagent_start(event) -> None:
    """Record skill snapshot and log the agent dispatch."""
    adapter = get_adapter()

    context_result = adapter.adapt_subagent_start(event.payload)
    agent_type = event.payload.get("agent_type", "unknown")
    task_description = event.payload.get("task_description", "")

    logger.info(
        "SubagentStart: agent_type=%s, context_injected=%s",
        agent_type,
        context_result.context_injected,
    )

    response = adapter.format_context_response(context_result)
    print(json.dumps(response.output))
    sys.exit(0)


# ============================================================================
# STDIN HANDLER (Claude Code integration)
# ============================================================================

if __name__ == "__main__":
    run_hook(_handle_subagent_start, hook_name="subagent_start")
