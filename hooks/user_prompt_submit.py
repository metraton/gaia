#!/usr/bin/env python3
"""UserPromptSubmit hook — refreshes session liveness and emits sparse notices."""

import sys
import json
import logging
from pathlib import Path
from typing import Optional

_hooks_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_hooks_dir))
_pkg_root = str(_hooks_dir.parent)
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)

from modules.core.logging_setup import configure_hook_logging
from modules.core.stdin import has_stdin_data
from modules.session.session_lifecycle import notifications_counter, prompt_context

# Configure logging -- file handler only when GAIA_DEBUG is set; no
# hooks-*.log is written by default (see modules.core.logging_setup).
configure_hook_logging("user_prompt_submit")
logger = logging.getLogger(__name__)


def _extract_user_prompt(raw_input: str) -> str:
    """Extract user prompt text from stdin event.

    The UserPromptSubmit event is JSON with the user's message.
    Try known field names; return empty string if extraction fails.
    """
    try:
        event = json.loads(raw_input)
        for field in ("user_message", "prompt", "message", "content"):
            if field in event and isinstance(event[field], str):
                return event[field]
        hook_input = event.get("hookEventInput", {})
        if isinstance(hook_input, dict):
            for field in ("user_message", "prompt", "message", "content"):
                if field in hook_input and isinstance(hook_input[field], str):
                    return hook_input[field]
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return ""


def _current_workspace() -> Optional[str]:
    """Return the workspace this prompt's cwd and environment resolve to, or None."""
    try:
        from gaia.project import current as _project_current
        return _project_current() or None
    except Exception:
        return None


def _build_notifications_counter() -> str:
    """Return the due-notifications line for the current workspace, or ""."""
    return notifications_counter(_current_workspace())


if __name__ == "__main__":
    if not has_stdin_data():
        sys.exit(0)

    try:
        raw_input = sys.stdin.read()

        try:
            event_data = json.loads(raw_input) if raw_input else {}
            if not isinstance(event_data, dict):
                event_data = {}
        except (json.JSONDecodeError, TypeError):
            event_data = {}

        # The session id comes from the stdin event: CLAUDE_SESSION_ID is not
        # guaranteed to be exported into the hook subprocess.
        from modules.core.state import resolve_session_id

        additional_context = prompt_context(
            resolve_session_id(event_data), _current_workspace(),
        )
        if not _extract_user_prompt(raw_input):
            logger.info("Could not extract user prompt")
        logger.info("Context injected (%d chars)", len(additional_context))

        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": additional_context,
            }
        }))
        sys.exit(0)

    except Exception as e:
        logger.error("Error in user_prompt_submit: %s", e, exc_info=True)
        sys.exit(0)
