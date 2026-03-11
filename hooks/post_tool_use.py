#!/usr/bin/env python3
"""
Post-tool use hook - Thin gate.

Reads state from pre-hook, logs execution via audit module, detects critical
events, and writes them to session context via session_context_writer.

Architecture:
- Thin gate: parse_event -> audit log -> detect events -> write context -> exit
- Business logic in modules.audit.* and modules.session.session_context_writer
- Metrics CLI eliminated (use bin/gaia-metrics.js instead)
"""

import sys
import json
import logging
import time
from pathlib import Path
from datetime import datetime

# Add modules to path
sys.path.insert(0, str(Path(__file__).parent))

from modules.core.paths import get_logs_dir
from adapters.claude_code import ClaudeCodeAdapter
from modules.core.stdin import has_stdin_data
from adapters.utils import warn_if_dual_channel
from modules.core.state import get_hook_state, clear_hook_state
from modules.audit.logger import log_execution
from modules.audit.event_detector import detect_critical_event
from modules.session.session_context_writer import SessionContextWriter

# Configure logging
log_file = get_logs_dir() / f"hooks-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [post_tool_use] %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(log_file)],
)
logger = logging.getLogger(__name__)


def post_tool_use_hook(
    tool_name: str,
    parameters: dict,
    result: object,
    duration: float,
    success: bool = True,
) -> None:
    """Post-tool use hook: audit + event detection + context update."""
    try:
        pre_state = get_hook_state()
        tier = pre_state.tier if pre_state else "unknown"

        # Prefer wall-clock duration from pre-hook timestamp
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

    if has_stdin_data():
        try:
            adapter = ClaudeCodeAdapter()
            warn_if_dual_channel()

            event = adapter.parse_event(sys.stdin.read())
            tool_result_data = adapter.parse_post_tool_use(event.payload)
            logger.info("Post-hook event: %s", event.payload.get("hook_event_name"))

            raw_tool_result = event.payload.get("tool_result", {})
            post_tool_use_hook(
                tool_name=tool_result_data.tool_name,
                parameters=event.payload.get("tool_input", {}),
                result=tool_result_data.output,
                duration=raw_tool_result.get("duration_ms", 0) / 1000.0,
                success=tool_result_data.exit_code == 0,
            )
            sys.exit(0)

        except ValueError as e:
            logger.error("Adapter parse failed: %s", e)
            print(f"HOOK ERROR: {e}", file=sys.stderr)
            sys.exit(1)
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON from stdin: %s", e)
            print(f"HOOK ERROR: Invalid JSON from stdin: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            logger.error("Error processing hook: %s", e, exc_info=True)
            print(f"HOOK ERROR: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("Usage: echo '{...}' | python post_tool_use.py  (stdin mode)")
        print("       python post_tool_use.py --test           (self-test mode)")
        sys.exit(1)
