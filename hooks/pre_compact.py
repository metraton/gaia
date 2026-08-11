#!/usr/bin/env python3
"""PreCompact hook — a schema-valid no-op registered for the PreCompact event.

The event carries no deliverable payload: Claude Code neither validates nor
consumes ``hookSpecificOutput`` for PreCompact (see ``_handle_pre_compact``),
so there is nothing this hook can inject into the model's context. It stays
registered so the event has a well-formed responder that can never block
compaction, and so a future capability has a wired entry point.

All errors are caught — this hook never blocks compaction.
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
    """Emit the schema-valid empty response for PreCompact.

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
    "(root): Invalid input". There is currently no hook event that can inject
    model context in the narrow window *before* compaction erases it; the
    post-compaction refresh happens instead at SessionStart with
    ``source == "compact"``. So this handler only logs for GAIA_DEBUG
    diagnosis and returns a schema-valid empty response.
    """
    logger.info("PreCompact: no deliverable payload for this event, returning {}")

    # No hookSpecificOutput: PreCompact does not accept one. An empty object
    # is the schema-valid "nothing to report" response for every hook event.
    print(json.dumps({}))
    sys.exit(0)


if __name__ == "__main__":
    run_hook(_handle_pre_compact, hook_name="pre_compact")
