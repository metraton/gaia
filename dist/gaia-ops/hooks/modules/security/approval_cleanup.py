"""
Approval cleanup for the subagent stop hook.

DB-only since Task E FS retirement:
  All pending approvals are stored exclusively in gaia.db (approvals table).

P-3d23 invariant (Fix A): a pending younger than its TTL MUST survive ANY
subagent's SubagentStop, regardless of that subagent's final plan_status.
SubagentStop is the normal lifecycle of the documented block -> approve ->
retry flow, and because subagents share the main session_id, revoking pendings
by session-membership at SubagentStop wiped out every other outstanding pending
in the session whenever any subagent finished as COMPLETE. cleanup() therefore
no longer revokes fresh pendings by session membership; it only EXPIRES pendings
that have genuinely aged past DEFAULT_PENDING_TTL_MINUTES (the 24h user-wait
window). Expiry transitions the row to the schema 'expired' terminal status,
distinct from a user/admin 'revoked'.

Also performs DB-backed soft-expire of PENDING approval_grants rows whose
expires_at timestamp has passed (M3 addition).

Provides:
    - cleanup(): Expire genuinely-aged pending DB approvals for the session
    - expire_db_pendings(): TTL-sweep PENDING approvals past their pending TTL
    - expire_db_grants(): Soft-expire PENDING DB grants past their expires_at
    - consume_approval_file(): Backward-compatible alias for cleanup()
"""

import json
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


def expire_db_pendings(
    agent_type: str,
    session_id: Optional[str] = None,
) -> int:
    """TTL-sweep: expire PENDING approvals aged past DEFAULT_PENDING_TTL_MINUTES.

    Mirrors expire_db_grants() but for the pending plane. A pending row is
    eligible for expiry only when its age (list_pending enriches each row with
    age_seconds) is >= the 24h pending window. Fresh pendings are left
    untouched -- this is the P-3d23 invariant: a pending within its TTL survives
    any SubagentStop.

    Each expiry transitions the row to the schema 'expired' terminal status via
    store.expire(), carrying provenance: agent_id = the agent that triggered the
    sweep and metadata reason="expired_ttl" so the auto-transition event is never
    null-provenance.

    Args:
        agent_type: The agent whose SubagentStop drove the sweep (provenance +
            logging).
        session_id: Session whose pendings are swept. TTL is the gate, not
            session-membership -- but the sweep is scoped to this session to
            match the SubagentStop trigger.

    Returns:
        Number of pendings transitioned to 'expired'.
    """
    if session_id is None:
        session_id = get_session_id()

    try:
        from gaia.approvals.store import list_pending, expire
        from modules.security.approval_grants import DEFAULT_PENDING_TTL_MINUTES
    except ImportError:
        import pathlib as _pl
        import sys as _sys
        _repo = _pl.Path(__file__).resolve().parent.parent.parent.parent.parent
        _sys.path.insert(0, str(_repo))
        try:
            from gaia.approvals.store import list_pending, expire
            from modules.security.approval_grants import DEFAULT_PENDING_TTL_MINUTES
        except ImportError as exc:
            logger.debug(
                "expire_db_pendings: dependencies unavailable (non-fatal): %s", exc
            )
            return 0

    try:
        pending_rows = list_pending(session_id=session_id, all_sessions=False)
    except Exception as exc:
        logger.debug("expire_db_pendings: list_pending failed (non-fatal): %s", exc)
        return 0

    ttl_seconds = DEFAULT_PENDING_TTL_MINUTES * 60
    metadata = json.dumps(
        {"reason": "expired_ttl", "source": "approval_cleanup.cleanup"}
    )

    expired = 0
    for row in pending_rows:
        approval_id = row.get("id", "")
        if not approval_id:
            continue

        age_seconds = row.get("age_seconds", 0.0) or 0.0
        if age_seconds < ttl_seconds:
            # Fresh pending -- MUST survive (P-3d23 invariant).
            continue

        try:
            expire(
                approval_id,
                expirer_session=session_id,
                agent_id=agent_type,
                metadata_json=metadata,
            )
            logger.info(
                "Expired pending DB approval past TTL for agent '%s' "
                "(approval_id: %s, age=%.0fs >= %ds)",
                agent_type,
                approval_id[:20],
                age_seconds,
                ttl_seconds,
            )
            expired += 1
        except ValueError as exc:
            # Already transitioned (race or double-call) -- not an error.
            logger.debug(
                "expire_db_pendings: expire skipped for approval_id=%s "
                "(non-fatal): %s",
                approval_id[:20], exc,
            )
        except Exception as exc:
            logger.debug(
                "expire_db_pendings: expire error for approval_id=%s "
                "(non-fatal): %s",
                approval_id[:20], exc,
            )

    return expired


def cleanup(
    agent_type: str,
    session_id: Optional[str] = None,
    preserve_nonces: Optional[Set[str]] = None,
) -> bool:
    """Expire genuinely-aged pending DB approvals at subagent stop.

    P-3d23 invariant (Fix A): cleanup() no longer revokes fresh pendings by
    session-membership. SubagentStop is the normal lifecycle of the documented
    block -> approve -> retry flow, and subagents share the main session_id, so
    revoking every session pending at Stop wiped out outstanding approvals the
    user still needed to act on. cleanup() now only EXPIRES pendings that have
    aged past DEFAULT_PENDING_TTL_MINUTES (the 24h user-wait window); a pending
    within its TTL ALWAYS survives, regardless of the stopping subagent's
    plan_status.

    DB-only since Task E FS retirement. No filesystem files are scanned or
    deleted.

    Args:
        agent_type: The agent type that just completed (provenance + logging).
        session_id: Session ID to scope the TTL sweep (defaults to
            CLAUDE_SESSION_ID).
        preserve_nonces: Optional set of approval_id strings the agent's final
            agent_contract_handoff still references via APPROVAL_REQUEST. With
            Fix A these are protected by their TTL already (they are fresh by
            construction), so this set is now belt-and-suspenders: it guarantees
            an explicitly-referenced pending is never expired even at a TTL edge.
            It is no longer the only thing protecting a fresh pending.

    Returns:
        True if any pending DB approvals were expired, False otherwise.
    """
    if session_id is None:
        session_id = get_session_id()

    preserve_nonces = preserve_nonces or set()

    try:
        from gaia.approvals.store import list_pending, expire
        from modules.security.approval_grants import DEFAULT_PENDING_TTL_MINUTES
    except ImportError:
        import pathlib as _pl
        import sys as _sys
        _repo = _pl.Path(__file__).resolve().parent.parent.parent.parent.parent
        _sys.path.insert(0, str(_repo))
        try:
            from gaia.approvals.store import list_pending, expire
            from modules.security.approval_grants import DEFAULT_PENDING_TTL_MINUTES
        except ImportError as exc:
            logger.debug("cleanup: gaia.approvals.store unavailable (non-fatal): %s", exc)
            return False

    try:
        pending_rows = list_pending(session_id=session_id, all_sessions=False)
    except Exception as exc:
        logger.debug("cleanup: list_pending failed (non-fatal): %s", exc)
        return False

    ttl_seconds = DEFAULT_PENDING_TTL_MINUTES * 60
    metadata = json.dumps(
        {"reason": "expired_ttl", "source": "approval_cleanup.cleanup"}
    )

    expired = False
    for row in pending_rows:
        approval_id = row.get("id", "")
        if not approval_id:
            continue

        age_seconds = row.get("age_seconds", 0.0) or 0.0
        if age_seconds < ttl_seconds:
            # Fresh pending -- MUST survive (P-3d23 invariant). preserve_nonces
            # is no longer load-bearing here; the TTL gate already protects it.
            continue

        if approval_id in preserve_nonces:
            # Belt-and-suspenders: an explicitly APPROVAL_REQUEST-referenced
            # pending is never expired, even at the TTL edge.
            logger.info(
                "Preserving pending approval_id=%s (still in APPROVAL_REQUEST)",
                approval_id[:20],
            )
            continue

        try:
            expire(
                approval_id,
                expirer_session=session_id,
                agent_id=agent_type,
                metadata_json=metadata,
            )
            logger.info(
                "Expired pending DB approval past TTL for agent '%s' "
                "(approval_id: %s, age=%.0fs >= %ds)",
                agent_type,
                approval_id[:20],
                age_seconds,
                ttl_seconds,
            )
            expired = True
        except ValueError as exc:
            # Approval was already transitioned (race or double-call) -- not an error.
            logger.debug(
                "cleanup: expire skipped for approval_id=%s (non-fatal): %s",
                approval_id[:20], exc,
            )
        except Exception as exc:
            logger.debug(
                "cleanup: expire error for approval_id=%s (non-fatal): %s",
                approval_id[:20], exc,
            )

    return expired


# Backward-compatible alias
consume_approval_file = cleanup
