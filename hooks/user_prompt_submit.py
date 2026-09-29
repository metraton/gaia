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
        # Try known field names from Claude Code hook events
        for field in ("user_message", "prompt", "message", "content"):
            if field in event and isinstance(event[field], str):
                return event[field]
        # Check nested hookEventInput
        hook_input = event.get("hookEventInput", {})
        if isinstance(hook_input, dict):
            for field in ("user_message", "prompt", "message", "content"):
                if field in hook_input and isinstance(hook_input[field], str):
                    return hook_input[field]
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return ""


def _build_notifications_counter() -> str:
    """Return a one-line unread-notifications counter, or "" when there are none.

    Deterministic and cheap (one COUNT query), mirroring the Surface Routing
    Recommendation's zero-cost-when-empty contract: if there are no unread
    headless-task notifications this injects NOTHING (zero tokens). When there
    are, it injects a single line so the orchestrator can mention it; the full
    list lands at SessionStart and detail is on-demand via `gaia notifications`.

    Scoped to the current workspace (matching how `gaia notifications add`
    stores the ``workspace`` column), falling back to all workspaces when the
    workspace cannot be resolved. Never raises -- advisory only.
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
        noun = "task notification" if n == 1 else "task notifications"
        return (
            f"\U0001F514 {n} {noun} sin ver "
            f"(`gaia notifications list --unread` para verlas)"
        )
    except Exception as e:
        logger.warning("notifications counter failed (advisory, skipping): %s", e)
        return ""


if __name__ == "__main__":
    if not has_stdin_data():
        sys.exit(0)

    try:
        raw_input = sys.stdin.read()

        # Parse the event JSON once so subsequent helpers can read fields
        # (session_id, prompt). Defensive: an unreadable payload becomes
        # an empty dict so the rest of the hook still runs.
        try:
            event_data = json.loads(raw_input) if raw_input else {}
            if not isinstance(event_data, dict):
                event_data = {}
        except (json.JSONDecodeError, TypeError):
            event_data = {}

        # Refresh liveness heartbeat for this session. Throttled inside
        # touch_session(), fully non-fatal. The session_id must come from
        # the stdin event because CLAUDE_SESSION_ID is not guaranteed to
        # be exported into the hook subprocess.
        try:
            from modules.session.session_registry import touch_session
            from modules.core.state import resolve_session_id
            touch_session(resolve_session_id(event_data))
        except Exception as _hb_exc:
            logger.debug("touch_session failed (non-fatal): %s", _hb_exc)

        # Build sparse additionalContext. DB-backed routing remains available
        # to diagnostic tools but is no longer injected into each user turn.
        # Identity now lives in agents/gaia-orchestrator.md (agent definition).
        # Pending approvals moved to SessionStart via session_manifest
        # (Phase 4) -- they are session-scoped, not turn-scoped, so
        # re-evaluating on every prompt added noise without changing the
        # answer.
        context_parts = []

        # Prompt extraction is retained for liveness diagnostics only.
        prompt_text = _extract_user_prompt(raw_input)

        # NOTE: Approval activation does not happen here. An answered
        # AskUserQuestion arrives on PostToolUse, not
        # UserPromptSubmit, so approval detection lives there now.

        if not prompt_text:
            logger.info("Could not extract user prompt")

        # Unread headless-task notifications counter. Cheap (one COUNT) and
        # zero-token when there are none.
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
