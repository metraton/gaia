#!/usr/bin/env python3
"""
Pre-tool use hook - Thin Gate Architecture.

Entry point for Bash and Task/Agent tool validation. The hook is the primary
security gate: with Bash(*) in the settings.json allow list, all commands
reach this hook regardless of settings.json permissions.

Architecture:
- Uses adapter layer to parse and process the full PreToolUse lifecycle
- All business logic lives in ClaudeCodeAdapter.adapt_pre_tool_use()
- This file is stdin/stdout glue only

The former backward-compatible API (pre_tool_use_hook / _handle_* / main) was
retired: it diverged from the real path (no delegate-mode gate, no CLI-only
guard, no identity injection, no born-at-dispatch row) and its module-level
imports made every hook invocation pay for a lane only tests used. Tests
drive adapters.claude_code.ClaudeCodeAdapter.adapt_pre_tool_use directly
(see tests/fixtures/pretool_adapter.py).
"""
from __future__ import annotations

import sys
import json
import logging
from pathlib import Path

_hooks_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_hooks_dir))
_pkg_root = str(_hooks_dir.parent)
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)
from modules.core.hook_trace import record_hook_invocation
from modules.core.logging_setup import configure_hook_logging

# Adapter layer -- get_adapter() is the single construction point (registry),
# so this entry point never names the concrete host class.
from adapters.registry import get_adapter
from modules.core.stdin import has_stdin_data
from adapters.utils import warn_if_dual_channel

# Configure logging -- file handler only when GAIA_DEBUG is set (see
# modules.core.logging_setup); no hooks-*.log is written by default.
configure_hook_logging("pre_tool_use")
logger = logging.getLogger(__name__)


# ============================================================================
# STDIN HANDLER (Claude Code integration)
# ============================================================================

if __name__ == "__main__":
    if has_stdin_data():
        try:
            adapter = get_adapter()
            warn_if_dual_channel()

            stdin_data = sys.stdin.read()

            try:
                event = adapter.parse_event(stdin_data)
            except ValueError as e:
                error_msg = str(e)
                logger.error(f"Adapter parse failed: {error_msg}")
                print(f"HOOK ERROR: {error_msg}", file=sys.stderr)
                if "Empty stdin" in error_msg:
                    print(f"Error: {error_msg}")
                sys.exit(1)

            response = adapter.adapt_pre_tool_use(event)

            # Always-on invocation trace. This entry point does not go through
            # modules.core.hook_entry.run_hook, so it records its own line --
            # and it is the one hook whose rejection is carried by the
            # permissionDecision rather than by the exit code, hence the
            # explicit `blocked` flag instead of the exit-code default.
            _decision = None
            if isinstance(response.output, dict):
                _decision = response.output.get("hookSpecificOutput", {}).get(
                    "permissionDecision"
                )
            record_hook_invocation(
                "pre_tool_use",
                payload=getattr(event, "payload", None),
                exit_code=response.exit_code,
                blocked=_decision in ("block", "deny") or response.exit_code == 2,
                extra={"decision": _decision} if _decision else None,
            )

            if isinstance(response.output, dict) and response.output:
                hook_output = response.output.get("hookSpecificOutput", {})
                decision = hook_output.get("permissionDecision")
                if decision in ("block", "deny"):
                    reason = hook_output.get("permissionDecisionReason", "Command blocked by hook policy")
                    summary = reason.split('\n')[0]
                    print(f"BLOCKED: {summary}", file=sys.stderr)
                elif decision == "ask":
                    reason = hook_output.get("permissionDecisionReason", "")
                    summary = reason.split('\n')[0]
                    print(f"T3: {summary}", file=sys.stderr)
                print(json.dumps(response.output))
                sys.exit(response.exit_code)
            elif isinstance(response.output, str) and response.output:
                summary = response.output.split('\n')[0]
                label = "HOOK ERROR" if response.exit_code == 1 else "BLOCKED"
                print(f"{label}: {summary}", file=sys.stderr)
                print(response.output)
                sys.exit(response.exit_code)
            else:
                sys.exit(0)

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from stdin: {e}")
            print(f"HOOK ERROR: Invalid JSON from stdin: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            logger.error(f"Error processing hook: {e}", exc_info=True)
            print(f"HOOK ERROR: {str(e)}", file=sys.stderr)
            print(f"Hook error: {str(e)}")
            sys.exit(1)
    else:
        print("Usage: echo '{\"hook_event_name\":\"PreToolUse\",\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"ls\"}}' | python pre_tool_use.py")
        sys.exit(1)
