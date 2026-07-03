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
configure_hook_logging("elicitation_result")
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


def _activate_grants(session_id: str, response: str = "") -> None:
    """Activate approval grants for this session.

    When *response* contains a ``[P-<nonce>]`` tag (nonce-labeled approval),
    only the specific grant identified by that nonce is activated.

    DB-only since the grant-lifecycle FS retirement: REQUESTED writes go to
    the DB (insert_requested), so activation resolves the pending by nonce
    prefix straight from the DB via ``activate_db_pending_by_prefix()``.  No
    filesystem pending file is ever written, so there is no filesystem path
    to consult and no session-wide filesystem sweep to fall back to.
    """
    from modules.security.approval_grants import (
        activate_db_pending_by_prefix,
        extract_nonce_from_label,
    )

    nonce_prefix = extract_nonce_from_label(response) if response else None
    if not nonce_prefix:
        logger.info(
            "ElicitationResult: no nonce prefix in response -- nothing to activate",
        )
        return

    logger.info(
        "ElicitationResult: nonce prefix found in response: %s", nonce_prefix,
    )
    result = activate_db_pending_by_prefix(
        nonce_prefix, current_session_id=session_id,
    )
    logger.info(
        "ElicitationResult DB activation: prefix=%s success=%s status=%s reason=%s",
        nonce_prefix,
        result.success,
        getattr(result.status, "value", str(result.status)),
        result.reason,
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
                _activate_grants(session_id, response=response)
            else:
                logger.warning("Approval detected but no session_id available")
        else:
            logger.info("ElicitationResult response is not an approval: %s", response[:40])

    except Exception as e:
        logger.error("Error in elicitation_result hook: %s", e, exc_info=True)

    # Never block -- always exit 0
    sys.exit(0)
