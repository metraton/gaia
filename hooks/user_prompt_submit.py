#!/usr/bin/env python3
"""UserPromptSubmit hook — refreshes session liveness and emits sparse notices."""

import sys
import json
import logging
from pathlib import Path

_hooks_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_hooks_dir))
_pkg_root = str(_hooks_dir.parent)
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)

from modules.core.logging_setup import configure_hook_logging
from modules.core.stdin import has_stdin_data

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


def _build_notifications_counter() -> str:
    """Return a one-line counter of notifications due now, or "" when there are none.

    Deterministic and cheap (one COUNT query, no write), mirroring the Surface
    Routing Recommendation's zero-cost-when-empty contract: with nothing due
    this injects NOTHING (zero tokens). A reminder whose hour passes during a
    session shows here on the next prompt.

    Scoped to the current workspace plus the global rows, falling back to all
    workspaces when the workspace cannot be resolved. Never raises -- advisory only.
    """
    try:
        pkg_root = str(Path(__file__).resolve().parent.parent)
        if pkg_root not in sys.path:
            sys.path.insert(0, pkg_root)
        from gaia.store.reader import count_unread_notifications
        try:
            from gaia.project import current as _project_current
            ws = _project_current() or None
        except Exception:
            ws = None
        n = count_unread_notifications(ws)
        if not n:
            return ""
        noun = "notification" if n == 1 else "notifications"
        return f"\U0001F514 {n} {noun} due (`gaia notifications list --unread` to see them)"
    except Exception as e:
        logger.warning("notifications counter failed (advisory, skipping): %s", e)
        return ""


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
        session_id = resolve_session_id(event_data)

        try:
            from modules.session.session_registry import touch_session
            touch_session(session_id)
        except Exception as _hb_exc:
            logger.debug("touch_session failed (non-fatal): %s", _hb_exc)

        context_parts = []

        if not _extract_user_prompt(raw_input):
            logger.info("Could not extract user prompt")

        notif_counter = _build_notifications_counter()
        if notif_counter:
            context_parts.append(notif_counter)

        additional_context = "\n\n".join(context_parts)
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
