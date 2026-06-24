"""
Approval cleanup for the subagent stop hook.

DB-only since Task E FS retirement:
  All pending approvals are stored exclusively in gaia.db (approvals table).
  cleanup() revokes PENDING DB rows for the agent's session at subagent stop,
  skipping any nonces still referenced by an in-flight APPROVAL_REQUEST.

Also performs DB-backed soft-expire of PENDING approval_grants rows whose
expires_at timestamp has passed (M3 addition).

Provides:
    - cleanup(): Revoke pending DB approvals for the agent session
    - expire_db_grants(): Soft-expire PENDING DB grants past their expires_at
    - consume_approval_file(): Backward-compatible alias for cleanup()
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Set

from ..core.state import get_session_id

logger = logging.getLogger(__name__)


def expire_db_grants(session_id: Optional[str] = None) -> int:
    """Soft-expire PENDING approval_grants rows whose expires_at has passed.

    Called at SubagentStop alongside the filesystem cleanup. Marks matching
    rows as EXPIRED (status='EXPIRED') so `gaia approvals list` reflects
    accurate state without TTL re-computation in every query.

    Args:
        session_id: Optional session to scope expiry. When None, expires
            grants across all sessions (suitable for periodic housekeeping).

    Returns:
        Number of rows updated to EXPIRED.
    """
    try:
        from gaia.store.writer import list_approval_grants, update_approval_grant_status

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        grants = list_approval_grants(
            session_id=session_id,
            status="PENDING",
            limit=500,
        )

        expired_count = 0
        for grant in grants:
            expires_at = grant.get("expires_at")
            if expires_at and expires_at < now_iso:
                result = update_approval_grant_status(
                    grant["approval_id"], "EXPIRED"
                )
                if result.get("status") == "applied":
                    expired_count += 1
                    logger.info(
                        "DB grant soft-expired: approval_id=%s",
                        grant["approval_id"][:12],
                    )

        return expired_count

    except Exception as exc:
        logger.debug("expire_db_grants (non-fatal): %s", exc)
        return 0


def cleanup(
    agent_type: str,
    session_id: Optional[str] = None,
    preserve_nonces: Optional[Set[str]] = None,
) -> bool:
    """Revoke pending DB approvals for the current session after agent completion.

    Queries the DB approvals table for PENDING rows scoped to this session and
    revokes them, preventing stale pending approvals from accumulating after the
    agent run finishes.  Rows whose approval_id is in preserve_nonces are skipped
    so that an in-flight APPROVAL_REQUEST the user still needs to act on survives
    the sweep.

    DB-only since Task E FS retirement.  No filesystem files are scanned or deleted.

    Args:
        agent_type: The agent type that just completed (for logging).
        session_id: Session ID to scope cleanup (defaults to CLAUDE_SESSION_ID).
        preserve_nonces: Optional set of approval_id strings to skip during
            cleanup.  Used when an agent's final agent_contract_handoff still
            carries an APPROVAL_REQUEST so the pending row remains for the user
            to approve or reject.  When None or empty, all session pendings are
            eligible for revocation.

    Returns:
        True if any pending DB approvals were revoked, False otherwise.
    """
    if session_id is None:
        session_id = get_session_id()

    preserve_nonces = preserve_nonces or set()

    revoked = False
    try:
        from gaia.approvals.store import list_pending, revoke
    except ImportError:
        import pathlib as _pl
        import sys as _sys
        _repo = _pl.Path(__file__).resolve().parent.parent.parent.parent.parent
        _sys.path.insert(0, str(_repo))
        try:
            from gaia.approvals.store import list_pending, revoke
        except ImportError as exc:
            logger.debug("cleanup: gaia.approvals.store unavailable (non-fatal): %s", exc)
            return False

    try:
        pending_rows = list_pending(session_id=session_id, all_sessions=False)
    except Exception as exc:
        logger.debug("cleanup: list_pending failed (non-fatal): %s", exc)
        return False

    for row in pending_rows:
        approval_id = row.get("id", "")
        if not approval_id:
            continue

        if approval_id in preserve_nonces:
            logger.info(
                "Preserving pending approval_id=%s (still in APPROVAL_REQUEST)",
                approval_id[:20],
            )
            continue

        try:
            revoke(approval_id, revoker_session=session_id)
            logger.info(
                "Revoked pending DB approval for agent '%s' "
                "(approval_id: %s)",
                agent_type,
                approval_id[:20],
            )
            revoked = True
        except ValueError as exc:
            # Approval was already transitioned (race or double-call) -- not an error.
            logger.debug(
                "cleanup: revoke skipped for approval_id=%s (non-fatal): %s",
                approval_id[:20], exc,
            )
        except Exception as exc:
            logger.debug(
                "cleanup: revoke error for approval_id=%s (non-fatal): %s",
                approval_id[:20], exc,
            )

    return revoked


# Backward-compatible alias
consume_approval_file = cleanup
