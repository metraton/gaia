#!/usr/bin/env python3
"""
Post-tool use hook - Thin gate.

Architecture:
- Uses adapter layer to parse and process the full PostToolUse lifecycle
- All business logic lives in ClaudeCodeAdapter.adapt_post_tool_use()
- This file is stdin/stdout glue only
"""

import sys
import json
import logging
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from modules.core.paths import get_logs_dir
from adapters.claude_code import ClaudeCodeAdapter
from modules.core.stdin import has_stdin_data
from adapters.utils import warn_if_dual_channel
from modules.core.hook_entry import run_hook

# Configure logging
log_file = get_logs_dir() / f"hooks-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [post_tool_use] %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(log_file)],
)
logger = logging.getLogger(__name__)


def _handle_post_tool_use(event) -> None:
    """Process a PostToolUse event.

    Delegates all business logic to the adapter.

    Args:
        event: Parsed HookEvent from the adapter layer.
    """
    adapter = ClaudeCodeAdapter()
    response = adapter.adapt_post_tool_use(event)

    if response.output:
        print(json.dumps(response.output))
    sys.exit(response.exit_code)


# ============================================================================
# Backward-compatible API for direct callers (e.g. --test mode)
# ============================================================================

def post_tool_use_hook(
    tool_name: str,
    parameters: dict,
    result: object,
    duration: float,
    success: bool = True,
) -> None:
    """Post-tool use hook: audit + event detection + context update.

    Backward-compatible entry point that calls the same modules as the adapter.
    """
    import time
    from modules.core.state import get_hook_state, clear_hook_state
    from modules.audit.logger import log_execution
    from modules.audit.event_detector import detect_critical_event
    from modules.session.session_context_writer import SessionContextWriter
    from modules.security.approval_grants import check_approval_grant, confirm_grant

    try:
        pre_state = get_hook_state()
        tier = pre_state.tier if pre_state else "unknown"

        computed_duration = duration
        if pre_state and pre_state.start_time_epoch > 0:
            computed_duration = time.time() - pre_state.start_time_epoch

        log_execution(
            tool_name=tool_name,
            parameters=parameters,
            result=result,
            duration=computed_duration,
            exit_code=0 if success else 1,
            tier=tier,
        )

        if tool_name == "Bash" and success:
            command = parameters.get("command", "")
            if command:
                grant = check_approval_grant(command)
                if grant is not None and not grant.confirmed:
                    confirm_grant(command)
                    logger.info(
                        "T3 grant confirmed post-execution: %s", command[:80],
                    )

        events = detect_critical_event(tool_name, parameters, result, success)
        if events:
            writer = SessionContextWriter()
            for event in events:
                writer.update_context(event.to_dict())

        clear_hook_state()
        logger.debug("Post-hook completed for %s", tool_name)

    except Exception as e:
        logger.error("Error in post_tool_use_hook: %s", e, exc_info=True)


# ============================================================================
# STDIN HANDLER (Claude Code integration)
# ============================================================================

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        post_tool_use_hook(
            "bash", {"command": "kubectl get pods"},
            "pod/test-pod   1/1   Running   0   1m", 0.5, True,
        )
        print(f"Test completed. Check {get_logs_dir()} for audit logs")
        sys.exit(0)

    run_hook(_handle_post_tool_use, hook_name="post_tool_use")
