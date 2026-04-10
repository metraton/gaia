#!/usr/bin/env python3
"""ElicitationResult hook -- activates T3 approval grants when user approves via AskUserQuestion.

This hook fires after the user responds to an AskUserQuestion elicitation.
It checks if the response indicates approval and, if so, activates all
pending approval grants for the current session.

The hook NEVER blocks (always exits 0). It is purely side-effectful:
reading the user's answer and activating grants when appropriate.
"""
from __future__ import annotations

import sys
import json
import logging
import os
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from modules.core.paths import get_logs_dir
from modules.core.stdin import has_stdin_data

# Configure logging -- file only, no stderr
_log_file = get_logs_dir() / f"hooks-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [elicitation_result] %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(_log_file)],
)
logger = logging.getLogger(__name__)


def _extract_response(event: dict) -> str | None:
    """Extract the user's answer from the ElicitationResult event.

    The exact schema is not fully documented, so we probe multiple
    possible field names defensively.
    """
    # Try top-level fields first
    for field in ("result", "answer", "response", "selected", "value",
                  "hookEventInput", "elicitation_result"):
        val = event.get(field)
        if val is None:
            continue
        if isinstance(val, str) and val.strip():
            return val
        if isinstance(val, dict):
            # Nested -- look for answer/selected inside
            for inner in ("answer", "selected", "value", "result", "label"):
                inner_val = val.get(inner)
                if inner_val and isinstance(inner_val, str):
                    return inner_val
            # Check for answers dict (AskUserQuestion structured format)
            answers = val.get("answers", {})
            if answers and isinstance(answers, dict):
                first_val = next(iter(answers.values()), None)
                if first_val:
                    return str(first_val)
            # Check for options list selection
            options = val.get("options", [])
            if options and isinstance(options, list):
                for opt in options:
                    if isinstance(opt, dict) and opt.get("selected"):
                        return str(opt.get("label", opt.get("value", "")))
    return None


def _is_approval(response: str) -> bool:
    """Check if the response indicates approval."""
    normalized = response.lower().strip()
    approval_words = ["approve", "approved", "yes", "accept", "confirm", "allow"]
    return any(word in normalized for word in approval_words)


def _activate_grants(session_id: str) -> None:
    """Activate all pending approval grants for this session."""
    from modules.security.approval_grants import (
        activate_grants_for_session,
        get_pending_approvals_for_session,
    )

    pending = get_pending_approvals_for_session(session_id)
    if not pending:
        logger.info("No pending approvals to activate for session %s", session_id)
        return

    results = activate_grants_for_session(session_id)
    activated = sum(1 for r in results if r.success)
    logger.info(
        "ElicitationResult activated %d/%d pending approvals for session %s",
        activated, len(results), session_id,
    )


if __name__ == "__main__":
    if not has_stdin_data():
        sys.exit(0)

    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.exit(0)

        event = json.loads(raw)

        # Extract session_id from event or environment
        session_id = event.get("session_id") or os.environ.get("CLAUDE_SESSION_ID", "")

        # Extract user's response
        response = _extract_response(event)

        if not response:
            logger.info("No extractable response in ElicitationResult event")
            sys.exit(0)

        logger.info("ElicitationResult response: %s", response[:80])

        # Check if the response indicates approval
        if _is_approval(response):
            if session_id:
                _activate_grants(session_id)
            else:
                logger.warning("Approval detected but no session_id available")
        else:
            logger.info("ElicitationResult response is not an approval: %s", response[:40])

    except Exception as e:
        logger.error("Error in elicitation_result hook: %s", e, exc_info=True)

    # Never block -- always exit 0
    sys.exit(0)
