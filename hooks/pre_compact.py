#!/usr/bin/env python3
"""PreCompact hook — injects agentic-loop checkpoint instructions before context compaction.

When an agentic-loop is active, the agent needs to save its state before
compaction wipes context.  This hook detects the loop and injects a prompt
telling the agent to write continue.md + update state.json + worklog.md.

If no loop is active, this hook is a no-op (returns empty additionalContext).
All errors are caught and logged — this hook never blocks compaction.
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

from modules.core.hook_entry import run_hook
from modules.core.logging_setup import configure_hook_logging

# Configure logging -- file handler only when GAIA_DEBUG is set; no
# hooks-*.log is written by default (see modules.core.logging_setup).
configure_hook_logging("pre_compact")
logger = logging.getLogger(__name__)


def _handle_pre_compact(event) -> None:
    """Log agentic-loop checkpoint instructions before compaction.

    PLATFORM LIMITATION: Claude Code's hook-output schema does not accept
    ``hookSpecificOutput.hookEventName == "PreCompact"`` -- the validated
    discriminated union only covers PreToolUse, UserPromptSubmit,
    UserPromptExpansion, PostToolUse, PostToolUseFailure, PostToolBatch,
    Stop, SubagentStop, SessionStart, Setup, SubagentStart,
    PermissionDenied, PermissionRequest, Elicitation, ElicitationResult,
    and MessageDisplay -- and even a passing shape would go nowhere: the
    runtime's response-consumption switch (which maps
    ``hookSpecificOutput.hookEventName`` to an applied effect) has no
    ``"PreCompact"`` case at all, so `additionalContext` is unreachable
    for this event regardless of schema validity. Emitting the previous
    shape made every ``/compact`` fail Claude Code's JSON validation with
    "(root): Invalid input" and silently dropped the checkpoint prompt.
    There is currently no hook event that can inject model context in the
    narrow window *before* compaction erases it -- see pre_compact.py's
    module docstring and the hooks README for the accepted mitigation
    (UserPromptSubmit's ongoing loop-resume reminder, which is a different,
    already-working mechanism, not a substitute for this exact timing).
    This handler now only logs (for GAIA_DEBUG diagnosis) and returns a
    schema-valid empty response so compaction is never blocked.
    """
    try:
        from modules.context.agentic_loop_detector import build_precompact_prompt
        context = build_precompact_prompt()
        if context:
            logger.info(
                "PreCompact: active agentic loop detected (checkpoint prompt built, "
                "%d chars) but Claude Code does not support additionalContext "
                "injection for PreCompact -- prompt is logged only, not delivered",
                len(context),
            )
        else:
            logger.info("PreCompact: no active agentic loop, skipping")
    except Exception as e:
        logger.debug("PreCompact: agentic-loop detection failed (non-fatal): %s", e)

    # No hookSpecificOutput: PreCompact does not accept one. An empty object
    # is the schema-valid "nothing to report" response for every hook event.
    print(json.dumps({}))
    sys.exit(0)


if __name__ == "__main__":
    run_hook(_handle_pre_compact, hook_name="pre_compact")
