"""
Approval file cleanup for the subagent stop hook.

Cleans up pending approval files after an agent completes, using the current
per-nonce file layout under .claude/cache/approvals/pending-{nonce}.json.

Provides:
    - cleanup(): Delete pending approval files that match agent session
    - consume_approval_file(): Backward-compatible alias for cleanup()
"""

import json
import logging
from pathlib import Path
from typing import Optional, Set

from ..core.paths import find_claude_dir
from ..core.state import get_session_id

logger = logging.getLogger(__name__)


def _get_approvals_dir() -> Path:
    """Return the approvals cache directory."""
    return find_claude_dir() / "cache" / "approvals"


def cleanup(
    agent_type: str,
    session_id: Optional[str] = None,
    preserve_nonces: Optional[Set[str]] = None,
) -> bool:
    """
    Delete pending-{nonce}.json files for the current session after agent completion.

    Scans .claude/cache/approvals/ for pending files scoped to the current
    session and removes them, preventing stale pending approvals from
    accumulating after the agent run finishes.

    Args:
        agent_type: The agent type that just completed (for logging).
        session_id: Session ID to scope cleanup (defaults to CLAUDE_SESSION_ID).
        preserve_nonces: Optional set of nonce strings to skip during cleanup.
            Used when an agent's final json:contract still carries an
            APPROVAL_REQUEST so that the pending file remains available for
            the user to approve or reject. When None or empty, all session
            pendings are eligible for deletion (legacy behaviour).

    Returns:
        True if any pending approval files were consumed, False otherwise.
    """
    if session_id is None:
        session_id = get_session_id()

    preserve_nonces = preserve_nonces or set()

    approvals_dir = _get_approvals_dir()
    if not approvals_dir.exists():
        return False

    consumed = False
    try:
        for pending_file in approvals_dir.glob("pending-*.json"):
            # Skip the per-session index files
            if pending_file.name.startswith("pending-index-"):
                continue
            try:
                data = json.loads(pending_file.read_text())
                if data.get("session_id") != session_id:
                    continue

                nonce = data.get("nonce", "")
                if nonce and nonce in preserve_nonces:
                    logger.info(
                        "Preserving pending nonce=%s (still in APPROVAL_REQUEST)",
                        nonce[:12],
                    )
                    continue

                pending_file.unlink(missing_ok=True)
                logger.info(
                    "Consumed pending approval for agent '%s' "
                    "(nonce: %s, command: %s)",
                    agent_type,
                    nonce or "unknown",
                    data.get("command", "unknown"),
                )
                consumed = True

            except (json.JSONDecodeError, TypeError):
                # Corrupt file -- remove it (corrupt files are never
                # preserve-eligible because we cannot read their nonce).
                pending_file.unlink(missing_ok=True)
                consumed = True
            except Exception as e:
                logger.debug(
                    "Failed to process pending file %s (non-fatal): %s",
                    pending_file.name, e,
                )
    except Exception as e:
        logger.debug("Failed to scan approvals dir (non-fatal): %s", e)

    return consumed


# Backward-compatible alias
consume_approval_file = cleanup
