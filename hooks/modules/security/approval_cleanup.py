"""
Approval cleanup for the subagent stop hook.

All pending approvals are stored exclusively in gaia.db (approvals table).

A pending younger than its TTL MUST survive ANY
subagent's SubagentStop, regardless of that subagent's final agent_state.
SubagentStop is the normal lifecycle of the documented block -> approve ->
retry flow, and because subagents share the main session_id, revoking pendings
by session-membership at SubagentStop would wipe out every other outstanding
pending in the session whenever any subagent finished as COMPLETE. cleanup()
therefore never revokes fresh pendings; it only EXPIRES pendings that have
aged past DEFAULT_PENDING_TTL_MINUTES (the 24h user-wait window). Expiry
transitions the row to the schema 'expired' terminal status, distinct from a
user/admin 'revoked'.

The EXPIRE sweep is GLOBAL across sessions (list_pending(all_sessions=True)):
SubagentStop is the only periodic sweep trigger, so a session-scoped sweep
would never reap past-TTL pendings orphaned by dead/other sessions and they
accumulate forever. The age gate (age_seconds >= TTL) is the sole guard and is
session-independent, so a FRESH pending in ANY session always survives.

expire_db_grants() soft-expires PENDING approval_grants rows whose expires_at
timestamp has passed.

Reaping belongs to SubagentStop and nowhere else. SessionStart calls
count_stale_db_pendings() instead: an approval is the user's, and a sweep at
launch can transition one they are about to grant in another window.

Provides:
    - cleanup(): Expire past-TTL pending DB approvals across all sessions
    - count_stale_db_pendings(): Count past-TTL pendings, transitioning none
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

    Marks matching rows as EXPIRED (status='EXPIRED') so `gaia approvals list`
    reflects accurate state without TTL re-computation in every query.

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


def count_stale_db_pendings() -> int:
    """Count pendings past their TTL WITHOUT transitioning any of them.

    SessionStart must not run the expiry sweep: an approval belongs to the
    USER's session, and a sweep firing at launch can transition one the user
    is about to grant in another window. The moment a pending
    genuinely goes stale is the moment its own agent finishes without using it,
    which is SubagentStop -- the only place where that knowledge exists.

    So SessionStart reports and SubagentStop reaps. Returns 0 on any failure:
    a count is never worth blocking session start over.
    """
    try:
        from gaia.approvals.store import list_pending
        from modules.security.approval_grants import DEFAULT_PENDING_TTL_MINUTES
    except ImportError as exc:
        logger.debug("count_stale_db_pendings: unavailable (non-fatal): %s", exc)
        return 0

    try:
        rows = list_pending(session_id=None, all_sessions=True)
    except Exception as exc:
        logger.debug("count_stale_db_pendings: list_pending failed: %s", exc)
        return 0

    ttl_seconds = DEFAULT_PENDING_TTL_MINUTES * 60
    return sum(
        1 for row in rows
        if (row.get("age_seconds", 0.0) or 0.0) >= ttl_seconds
    )


def expire_db_pendings(
    agent_type: str,
    session_id: Optional[str] = None,
) -> int:
    """TTL-sweep: expire PENDING approvals aged past DEFAULT_PENDING_TTL_MINUTES.

    Mirrors expire_db_grants() but for the pending plane. A pending row is
    eligible for expiry only when its age (list_pending enriches each row with
    age_seconds) is >= the 24h pending window. Fresh pendings are left
    untouched: a pending within its TTL survives any SubagentStop.

    Each expiry transitions the row to the schema 'expired' terminal status via
    store.expire(), carrying provenance: agent_id = agent_type and metadata
    reason="expired_ttl" so the auto-transition event is never null-provenance.

    Args:
        agent_type: Recorded as agent_id on each expiry event (provenance +
            logging).
        session_id: Recorded as the expirer_session provenance on each expiry
            event. The sweep itself is GLOBAL (all_sessions=True): TTL is the
            only gate, so past-TTL pendings from any session -- including
            dead/other sessions -- are reaped, while fresh pendings in any
            session survive.

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
        # all_sessions=True: a session-scoped list never reaps past-TTL
        # pendings of dead sessions. The age gate below protects fresh ones.
        pending_rows = list_pending(session_id=session_id, all_sessions=True)
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
            # expire() raises ValueError when the row is no longer pending
            # (race or double-call) or does not exist.
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

    SubagentStop is the normal lifecycle of the documented block -> approve ->
    retry flow, and subagents share the main session_id, so revoking pendings
    by session membership at Stop would wipe approvals the user still has to
    act on. cleanup() therefore only EXPIRES pendings that have aged past
    DEFAULT_PENDING_TTL_MINUTES (the 24h user-wait window); a pending within
    its TTL survives regardless of the stopping subagent's agent_state.

    DB-only: no filesystem files are scanned or deleted.

    Args:
        agent_type: The agent type that just completed (provenance + logging).
        session_id: Recorded as the expirer_session provenance on each expiry
            event (defaults to the current host session id). The sweep itself
            covers all sessions.
        preserve_nonces: Optional set of approval_id strings the agent's final
            agent_contract_handoff still references via APPROVAL_REQUEST. A
            referenced pending is never expired, even at a TTL edge; a fresh
            pending is already protected by its TTL.

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
        # all_sessions=True: SubagentStop is the only periodic sweep trigger,
        # so a session-scoped sweep never reaps past-TTL pendings left by dead
        # or other sessions. The age gate below is the sole guard: a fresh
        # pending in any session is under the TTL and is skipped.
        pending_rows = list_pending(session_id=session_id, all_sessions=True)
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
            continue

        if approval_id in preserve_nonces:
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
            # expire() raises ValueError when the row is no longer pending
            # (race or double-call) or does not exist.
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
