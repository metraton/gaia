#!/usr/bin/env python3
"""SubagentStart hook — logs agent dispatch, records skill snapshots,
and forwards cached project context into the subagent.

PreToolUse:Agent builds and caches the context; this hook reads the
cache and returns it as additionalContext so it reaches the subagent
(not the orchestrator)."""

import sys
import json
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from adapters.claude_code import ClaudeCodeAdapter
from modules.core.hook_entry import run_hook
from modules.core.paths import get_logs_dir
from modules.audit.workflow_recorder import record_agent_skill_snapshot

# Configure logging
_log_file = get_logs_dir() / f"hooks-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [subagent_start] %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(_log_file)],
)
logger = logging.getLogger(__name__)


def _handle_subagent_start(event) -> None:
    """Record skill snapshot and log the agent dispatch."""
    adapter = ClaudeCodeAdapter()

    context_result = adapter.adapt_subagent_start(event.payload)
    agent_type = event.payload.get("agent_type", "unknown")
    task_description = event.payload.get("task_description", "")

    if agent_type and agent_type != "unknown":
        skill_snapshot = record_agent_skill_snapshot(
            agent_type,
            session_context={
                "timestamp": datetime.now().isoformat(),
                "session_id": event.session_id,
            },
            task_description=task_description,
        )
        logger.info(
            "Recorded runtime defaults for %s (skills=%s)",
            agent_type,
            skill_snapshot.get("skills_count", 0),
        )

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
