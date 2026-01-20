#!/usr/bin/env python3
"""
Post-tool use hook v2 - Modular Architecture

Thin entry point that uses the new modular hook system.
Reads state from pre-hook and records metrics/events.

Features:
- State sharing with pre-hook
- Audit logging
- Metrics collection
- Critical event detection
- Context updates
"""

import sys
import json
import logging
import os
import select
from pathlib import Path
from datetime import datetime, timedelta

# Add modules to path
sys.path.insert(0, str(Path(__file__).parent))

from modules.core.paths import get_logs_dir, get_session_dir
from modules.core.state import get_hook_state, clear_hook_state
from modules.audit.logger import log_execution
from modules.audit.metrics import record_metric
from modules.audit.event_detector import detect_critical_event

# Configure logging
log_file = get_logs_dir() / f"post_tool_use_v2-{os.getenv('USER', 'unknown')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ============================================================================
# CONTEXT UPDATER
# ============================================================================

class ActiveContextUpdater:
    """Update active session context for critical events."""

    def __init__(self):
        self.context_path = get_session_dir() / "context.json"

    def update_context(self, event_data: dict) -> None:
        """Update active context with event data."""
        try:
            # Load existing context
            context = {}
            if self.context_path.exists():
                with open(self.context_path, 'r') as f:
                    context = json.load(f)

            # Initialize events list if not exists
            if "critical_events" not in context:
                context["critical_events"] = []

            # Add timestamp to event
            event_data["timestamp"] = datetime.now().isoformat()

            # Append new event
            context["critical_events"].append(event_data)

            # Keep only events from last 24 hours
            retention_hours = int(os.environ.get("SESSION_RETENTION_HOURS", "24"))
            cutoff = datetime.now() - timedelta(hours=retention_hours)

            context["critical_events"] = [
                event for event in context["critical_events"]
                if datetime.fromisoformat(event.get("timestamp", "")) > cutoff
            ]

            # Update last_modified
            context["last_modified"] = datetime.now().isoformat()

            # Write back
            with open(self.context_path, 'w') as f:
                json.dump(context, f, indent=2)

            logger.info(f"Updated context with event: {event_data.get('event_type', 'unknown')}")

        except Exception as e:
            logger.error(f"Error updating active context: {e}")


# ============================================================================
# MAIN HOOK LOGIC
# ============================================================================

def post_tool_use_hook(
    tool_name: str,
    parameters: dict,
    result: any,
    duration: float,
    success: bool = True
) -> None:
    """
    Post-tool use hook implementation.

    Args:
        tool_name: Name of the tool that was invoked
        parameters: Tool parameters
        result: Tool execution result
        duration: Execution duration in seconds
        success: Whether execution was successful
    """
    try:
        # Get state from pre-hook
        pre_state = get_hook_state()
        tier = pre_state.tier if pre_state else "unknown"

        # Log execution
        exit_code = 0 if success else 1
        log_execution(
            tool_name=tool_name,
            parameters=parameters,
            result=result,
            duration=duration,
            exit_code=exit_code,
            tier=tier,
        )

        # Record metrics
        command = parameters.get("command", "") if tool_name.lower() == "bash" else tool_name
        record_metric(
            tool_name=tool_name,
            command=command,
            duration=duration,
            success=success,
            tier=tier,
        )

        # Detect critical events
        events = detect_critical_event(tool_name, parameters, result, success)

        if events:
            context_updater = ActiveContextUpdater()
            for event in events:
                context_updater.update_context(event.to_dict())

        # Clear pre-hook state
        clear_hook_state()

        logger.debug(f"Post-hook completed for {tool_name}")

    except Exception as e:
        logger.error(f"Error in post_tool_use_hook: {e}", exc_info=True)


# ============================================================================
# CLI INTERFACE
# ============================================================================

def main():
    """CLI interface for testing and metrics."""
    if len(sys.argv) < 2:
        print("Usage: python post_tool_use_v2.py --metrics")
        print("       python post_tool_use_v2.py --test")
        sys.exit(1)

    if sys.argv[1] == "--metrics":
        from modules.audit.metrics import generate_summary
        summary = generate_summary(days=7)

        print("Execution Metrics Summary")
        print(f"Period: {summary['period_days']} days")
        print(f"Total executions: {summary['total_executions']}")
        print(f"Success rate: {summary['success_rate']:.1%}")
        print(f"Average duration: {summary['avg_duration_ms']:.1f}ms")

        if summary['top_commands']:
            print("\nTop command types:")
            for cmd in summary['top_commands'][:5]:
                print(f"  {cmd['type']}: {cmd['count']}")

        if summary['tier_distribution']:
            print("\nTier distribution:")
            for tier, count in summary['tier_distribution'].items():
                print(f"  {tier}: {count}")

    elif sys.argv[1] == "--test":
        print("Testing Post-Tool Use Hook v2...")

        test_params = {"command": "kubectl get pods"}
        test_result = "pod/test-pod   1/1   Running   0   1m"

        post_tool_use_hook("bash", test_params, test_result, 0.5, True)

        print("Test completed successfully!")
        print(f"Check {get_logs_dir()} for audit logs")

    else:
        print(f"Unknown command: {sys.argv[1]}")
        sys.exit(1)


def has_stdin_data() -> bool:
    """Check if there's data available on stdin."""
    if sys.stdin.isatty():
        return False
    try:
        readable, _, _ = select.select([sys.stdin], [], [], 0)
        return bool(readable)
    except Exception:
        return not sys.stdin.isatty()


# ============================================================================
# STDIN HANDLER (Claude Code integration)
# ============================================================================

if __name__ == "__main__":
    # Check if running from CLI with arguments
    if len(sys.argv) > 1:
        main()
    elif has_stdin_data():
        try:
            stdin_data = sys.stdin.read()
            if not stdin_data.strip():
                print("Error: Empty stdin data")
                sys.exit(1)

            hook_data = json.loads(stdin_data)

            logger.info(f"Post-hook event: {hook_data.get('hook_event_name')}")

            tool_name = hook_data.get("tool_name", "")
            tool_input = hook_data.get("tool_input", {})
            tool_result = hook_data.get("tool_result", {})

            # Extract result and duration
            result = tool_result.get("output", "")
            duration = tool_result.get("duration_ms", 0) / 1000.0
            success = tool_result.get("exit_code", 0) == 0

            post_tool_use_hook(tool_name, tool_input, result, duration, success)
            sys.exit(0)

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from stdin: {e}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Error processing hook: {e}", exc_info=True)
            sys.exit(1)
    else:
        # No args and no stdin - show usage
        print("Usage: python post_tool_use_v2.py --metrics")
        print("       python post_tool_use_v2.py --test")
        sys.exit(1)
