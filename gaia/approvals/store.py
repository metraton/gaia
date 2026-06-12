"""gaia.approvals.store -- DB writer for approval lifecycle and audit events.

This module is the canonical API for writing to the approvals and approval_events
tables. All writes to approval_events MUST go through chain.insert_event() to
preserve the hash-chain invariant (D16 deviation: hash in app layer).

Direct SQL inserts to approval_events from outside this module are a contract
violation: validate_chain() will detect any row that was not written through
chain.insert_event() because its this_hash will not link correctly to the chain.

Public API::

    insert_requested(sealed_payload, *, agent_id, session_id, con=None)
        -> approval_id (str, P-{uuid4} prefixed)

    record_event(approval_id, event_type, *, agent_id, session_id,
                 fingerprint, payload_json, metadata_json, con=None)
        -> event row id (int)

    get_pending(session_id=None, all_sessions=False, con=None)
        -> list[dict]  -- pending approval rows

    list_pending(all_sessions=False, session_id=None, con=None)
        -> list[dict]  -- CLI-facing alias for get_pending with age_seconds enrichment

    approve(approval_id, approver_session, *, agent_id=None, con=None)
        -> None  -- convenience wrapper: pending -> approved

    reject(approval_id, approver_session, *, agent_id=None, con=None)
        -> None  -- convenience wrapper: pending -> rejected

    transition(approval_id, from_status, to_status, event_payload, *,
               agent_id, session_id, con=None)
        -> None  -- state machine wrapper; raises if from_status does not match

    replay_for_approval(approval_id, con=None)
        -> list[dict]  -- ordered approval_events rows for an approval

TODO (Cut Point 1 — T2.2 or similar): The bash_validator classifier does not
intercept `npm run <script>` commands whose postinstall is mutative. A hook
that calls store.insert_requested() should detect this pattern and classify it
as T3 before it executes. Until that gap is closed, scripts that invoke
postinstall-mutative npm commands bypass the approval gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .chain import (
    canonical_payload,
    fingerprint_payload,
    insert_event,
)


# ---------------------------------------------------------------------------
# Approval ID generation
# ---------------------------------------------------------------------------

_APPROVAL_ID_PREFIX = "P-"

# Length (in hex chars) of the content-derived suffix for COMMAND_SET ids.
# 32 hex chars == 128 bits of the SHA-256 digest, matching the visual length of
# the uuid4 suffix used by singular approvals (uuid4.hex is also 32 chars).
_COMMAND_SET_ID_HEX_LEN = 32


def _generate_approval_id() -> str:
    """Generate a unique approval ID with the P- prefix.

    Format: P-{uuid4_hex}
    Example: P-3f2504e04f8911d39a0c0305e82c3301

    Used for SINGULAR T3 approvals (the hook-block path), where the id only
    needs to be unique and is relayed verbatim by the subagent. For the
    plan-first COMMAND_SET path -- where the orchestrator must reproduce the id
    from the command_set it reads in the contract, with no DB lookup -- use
    ``derive_command_set_id()`` instead.
    """
    return f"{_APPROVAL_ID_PREFIX}{uuid.uuid4().hex}"


def derive_command_set_id(commands: List[str]) -> str:
    """Deterministically derive a COMMAND_SET approval_id from its command list.

    The plan-first COMMAND_SET id is content-derived rather than random so that
    BOTH the hook (at SubagentStop intake) and the orchestrator (from the
    command_set it reads in the contract) compute the SAME id without any DB
    lookup. This closes the cross-session miss where the orchestrator could not
    reproduce a uuid4 minted at SubagentStop (Claude Code issue #5812: the
    SubagentStop output never reaches the parent).

    Format: ``P-<first 32 hex of sha256(canonical([{"command": c}, ...]))>``

    Canonicalization reuses ``chain.canonical_payload`` -- the SAME machinery
    that produces the fingerprint -- so there is exactly one canonicalization in
    the system, not a second one. The hash is taken over the ordered list of
    ``{"command": <str>}`` items, so the id is:

      * **order-sensitive** -- a different command order yields a different id
        (the consume side matches commands positionally, so order is load-bearing);
      * **content-only** -- it depends solely on the command strings, not on
        rationale, session, agent, or timestamp, so the two sides need only the
        command list (which both have) to agree.

    Idempotency consequence (acceptable, and consistent with the existing
    fingerprint dedup in ``insert_requested``): two identical command lists map
    to the same id. No per-attempt salt is added -- both sides could not derive
    a salt they do not share.

    Args:
        commands: Ordered list of command strings (the mutative/T3 commands the
            COMMAND_SET grant will cover). Both the intake and the orchestrator
            MUST pass the SAME post-filter list for the ids to match.

    Returns:
        A ``P-{32 hex}`` approval_id deterministically derived from ``commands``.
    """
    # Build a minimal, stable structure over the command strings ONLY. We do not
    # fold in rationale/operation/scope because the orchestrator must reproduce
    # the id from the command_set alone and those fields may differ between the
    # subagent's emission and the intake's neutral defaults.
    canon = canonical_payload({"command_set_commands": list(commands)})
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    return f"{_APPROVAL_ID_PREFIX}{digest[:_COMMAND_SET_ID_HEX_LEN]}"


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 (Z suffix)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

def _open_db() -> sqlite3.Connection:
    """Open a connection to ~/.gaia/gaia.db.

    Uses gaia.store.writer._connect() to ensure the schema is materialized and
    gaia_sha256 is registered. This makes store.py safe to call in production
    contexts where the DB may not yet exist.
    """
    from gaia.store.writer import _connect
    return _connect()


def _get_con(con: Optional[sqlite3.Connection]) -> tuple[sqlite3.Connection, bool]:
    """Return (connection, owned) where owned=True means the caller must close it."""
    if con is not None:
        return con, False
    return _open_db(), True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def insert_requested(
    sealed_payload: Dict[str, Any],
    *,
    agent_id: Optional[str] = None,
    session_id: Optional[str] = None,
    approval_id: Optional[str] = None,
    con: Optional[sqlite3.Connection] = None,
) -> str:
    """Insert a new approval row and emit a REQUESTED audit event.

    This is the canonical entry point for the T3 hook intercept. It:
      1. Generates a P-{uuid4} approval_id (unless one is supplied -- see below).
      2. Computes fingerprint = SHA-256(canonical_json(sealed_payload)).
      3. Inserts a row into approvals with status='pending'.
      4. Calls chain.insert_event() to write REQUESTED to approval_events
         with the hash-chain linked correctly (prev_hash from chain tip).
      5. Commits and returns the approval_id.

    Args:
        sealed_payload: Dict with the T3 operation details (operation,
            exact_content, scope, risk_level, rollback_hint, rationale, commands).
        agent_id: Optional agent identifier (e.g., agent_id from session context).
        session_id: Optional session identifier (CLAUDE_SESSION_ID).
        approval_id: Optional caller-supplied approval_id. When provided, it is
            used as the pending row id instead of minting a fresh P-{uuid4}.
            This is the plan-first COMMAND_SET path: the intake derives a
            CONTENT-derived id via ``derive_command_set_id()`` so the
            orchestrator can reproduce it from the command_set without a DB
            lookup. The singular T3 hook-block path leaves this None and keeps
            the uuid4 id. The fingerprint idempotency check below runs FIRST in
            either case, so a supplied id only takes effect when no pending row
            with the same fingerprint already exists.
        con: Optional open sqlite3.Connection. When provided, the caller owns
            connection lifecycle (no commit or close). When None, a fresh
            connection to ~/.gaia/gaia.db is opened, committed, and closed.

    Returns:
        The approval_id string used for the pending row. When an existing
        pending approval already carries the same fingerprint, that existing id
        is returned unchanged (fingerprint idempotency -- see below).
    """
    # Compute the fingerprint FIRST so we can check for an existing pending with
    # the same byte-binding before minting anything.
    fp = fingerprint_payload(sealed_payload)
    canon_json = canonical_payload(sealed_payload)

    _con, owned = _get_con(con)
    try:
        # Fingerprint idempotency (Brief 71 byte-binding, session-agnostic):
        # if a pending approval with this exact fingerprint already exists, reuse
        # it instead of minting a new P-. The fingerprint is SHA-256 of the
        # canonical sealed_payload, so an identical payload -- regardless of which
        # session requests it -- maps to one approval. Without this, a
        # cross-session retry of the same blocked command would mint a fresh P-
        # on every pass, and the user would face an endless stream of duplicate
        # approvals for the same operation. We deliberately do NOT emit a second
        # REQUESTED event for the reused id: the append-only hash chain (D15)
        # records each approval's REQUESTED exactly once, and a duplicate
        # REQUESTED would break the one-REQUESTED-per-approval invariant.
        existing = _con.execute(
            "SELECT id FROM approvals "
            "WHERE status = 'pending' AND fingerprint = ? "
            "ORDER BY created_at ASC LIMIT 1",
            (fp,),
        ).fetchone()
        if existing is not None:
            existing_id = existing[0] if not hasattr(existing, "keys") else existing["id"]
            # No INSERT and no REQUESTED event: the chain already holds this
            # approval's REQUESTED from when it was first minted. Fingerprint
            # dedup wins over any caller-supplied approval_id: an identical
            # payload maps to the one pending row that already exists.
            return existing_id

        # Use the caller-supplied id (plan-first COMMAND_SET: content-derived,
        # reproducible by the orchestrator) when given, else mint a uuid4 id
        # (singular T3 hook-block path).
        if approval_id is None:
            approval_id = _generate_approval_id()

        # Insert the parent approval row.
        _con.execute(
            "INSERT INTO approvals "
            "(id, agent_id, session_id, status, fingerprint, payload_json) "
            "VALUES (?, ?, ?, 'pending', ?, ?)",
            (approval_id, agent_id, session_id, fp, canon_json),
        )

        # Emit REQUESTED event via chain.insert_event() -- the ONLY
        # authorized path to write to approval_events (D16).
        insert_event(
            _con,
            approval_id,
            "REQUESTED",
            agent_id=agent_id,
            session_id=session_id,
            payload_json=canon_json,
            fingerprint=fp,
        )

        if owned:
            _con.commit()
    finally:
        if owned:
            _con.close()

    return approval_id


def record_event(
    approval_id: str,
    event_type: str,
    *,
    agent_id: Optional[str] = None,
    session_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
    payload_json: Optional[str] = None,
    metadata_json: Optional[str] = None,
    con: Optional[sqlite3.Connection] = None,
) -> int:
    """Append an event to the approval_events chain for an existing approval.

    Routes through chain.insert_event() to preserve the hash-chain invariant.
    This is the canonical way to add non-REQUESTED events (SHOWN, APPROVED,
    REJECTED, EXECUTED, FAILED, NOOP, REVOKED, REVERTED).

    Args:
        approval_id: The P-{uuid4} approval identifier (must exist in approvals).
        event_type: One of the valid event_type values.
        agent_id: Optional agent identifier.
        session_id: Optional session identifier.
        fingerprint: Optional SHA-256 hex of payload_json.
        payload_json: Optional canonical-JSON sealed_payload for this event.
        metadata_json: Optional free-form JSON for event-specific extras.
        con: Optional open connection (see insert_requested for semantics).

    Returns:
        The integer id of the newly inserted event row.
    """
    _con, owned = _get_con(con)
    try:
        event_id = insert_event(
            _con,
            approval_id,
            event_type,
            agent_id=agent_id,
            session_id=session_id,
            payload_json=payload_json,
            fingerprint=fingerprint,
            metadata_json=metadata_json,
        )
        if owned:
            _con.commit()
    finally:
        if owned:
            _con.close()
    return event_id


def get_pending(
    session_id: Optional[str] = None,
    all_sessions: bool = False,
    con: Optional[sqlite3.Connection] = None,
) -> List[Dict[str, Any]]:
    """Query approval rows with status='pending'.

    Args:
        session_id: When provided and all_sessions=False, filter by this
            session_id (current-session view).
        all_sessions: When True, return pending approvals from all sessions
            (cross-session recovery, D9 — local machine only).
        con: Optional open connection.

    Returns:
        List of dicts with approval row fields.
    """
    _con, owned = _get_con(con)
    try:
        if all_sessions or session_id is None:
            cur = _con.execute(
                "SELECT id, agent_id, session_id, status, fingerprint, "
                "payload_json, created_at, decided_at "
                "FROM approvals WHERE status = 'pending' ORDER BY created_at ASC"
            )
        else:
            cur = _con.execute(
                "SELECT id, agent_id, session_id, status, fingerprint, "
                "payload_json, created_at, decided_at "
                "FROM approvals "
                "WHERE status = 'pending' AND session_id = ? "
                "ORDER BY created_at ASC",
                (session_id,),
            )
        rows = cur.fetchall()
        # Convert to plain dicts (sqlite3.Row or tuple depending on row_factory).
        result = []
        for row in rows:
            if hasattr(row, "keys"):
                result.append(dict(row))
            else:
                keys = [
                    "id", "agent_id", "session_id", "status", "fingerprint",
                    "payload_json", "created_at", "decided_at",
                ]
                result.append(dict(zip(keys, row)))
        return result
    finally:
        if owned:
            _con.close()


def list_pending(
    all_sessions: bool = False,
    session_id: Optional[str] = None,
    con: Optional[sqlite3.Connection] = None,
) -> List[Dict[str, Any]]:
    """Query pending approvals with age enrichment -- CLI-facing API for T3.1.

    This is the primary query surface consumed by ``gaia approvals list`` and
    ``gaia approvals pending --all-sessions``. It delegates to get_pending()
    for the DB query and adds an ``age_seconds`` field to each row so the CLI
    can display human-readable ages without needing to recompute them.

    Staleness threshold: approvals older than 3600 seconds (1 hour) are marked
    with ``stale=True`` in the returned dict. This is a cosmetic display hint for
    the CLI -- it is deliberately NOT tied to either TTL constant (the 24h pending
    window or the 60-min grant window); it only flags "this has been waiting a
    while" so the list can surface aging approvals.

    Args:
        all_sessions: When True, return pending approvals from all sessions
            (cross-session recovery, D9 -- local machine only).
            When False (default), filter by session_id when provided.
        session_id: The current session identifier. Ignored when
            all_sessions=True.
        con: Optional open sqlite3.Connection.

    Returns:
        List of dicts with approval row fields plus:
            age_seconds (float): seconds since created_at.
            stale (bool): True when age_seconds > 3600.
    """
    rows = get_pending(session_id=session_id, all_sessions=all_sessions, con=con)
    now = datetime.now(timezone.utc)
    result = []
    for row in rows:
        row = dict(row)
        created_at_str = row.get("created_at")
        age_seconds: float = 0.0
        if created_at_str:
            try:
                # Parse ISO-8601 Z-suffix timestamp produced by _now_iso().
                created_dt = datetime.strptime(created_at_str, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc
                )
                age_seconds = (now - created_dt).total_seconds()
            except (ValueError, TypeError):
                age_seconds = 0.0
        row["age_seconds"] = age_seconds
        row["stale"] = age_seconds > 3600.0
        result.append(row)
    return result


def approve(
    approval_id: str,
    approver_session: str,
    *,
    agent_id: Optional[str] = None,
    con: Optional[sqlite3.Connection] = None,
) -> None:
    """Convenience wrapper: transition a pending approval to approved.

    Inserts an APPROVED event and updates approvals.status to 'approved'.
    This is the cross-session grant path: a user (or agent) in session S2
    can approve a pending approval created in session S1.

    Args:
        approval_id: The P-{uuid4} approval identifier.
        approver_session: The session_id of the approving session (may differ
            from the requesting session -- cross-session approval).
        agent_id: Optional agent identifier for the APPROVED event.
        con: Optional open connection.

    Raises:
        ValueError: If the approval is not in 'pending' status.
        ValueError: If the approval_id does not exist.
    """
    transition(
        approval_id,
        from_status="pending",
        to_status="approved",
        agent_id=agent_id,
        session_id=approver_session,
        con=con,
    )


def reject(
    approval_id: str,
    approver_session: str,
    *,
    agent_id: Optional[str] = None,
    con: Optional[sqlite3.Connection] = None,
) -> None:
    """Convenience wrapper: transition a pending approval to rejected.

    Inserts a REJECTED event and updates approvals.status to 'rejected'.

    Args:
        approval_id: The P-{uuid4} approval identifier.
        approver_session: The session_id of the rejecting session.
        agent_id: Optional agent identifier for the REJECTED event.
        con: Optional open connection.

    Raises:
        ValueError: If the approval is not in 'pending' status.
        ValueError: If the approval_id does not exist.
    """
    transition(
        approval_id,
        from_status="pending",
        to_status="rejected",
        agent_id=agent_id,
        session_id=approver_session,
        con=con,
    )


def transition(
    approval_id: str,
    from_status: str,
    to_status: str,
    event_payload: Optional[Dict[str, Any]] = None,
    *,
    agent_id: Optional[str] = None,
    session_id: Optional[str] = None,
    con: Optional[sqlite3.Connection] = None,
) -> None:
    """State machine wrapper for approval status transitions.

    Reads the current status of the approval, checks it matches from_status,
    updates it to to_status, and appends the corresponding event via
    chain.insert_event(). All steps run in one transaction.

    Args:
        approval_id: The P-{uuid4} approval identifier.
        from_status: Expected current status (guard). Must match the actual
            stored status or this function raises ValueError.
        to_status: New status to write.
        event_payload: Optional dict for the event's payload_json and fingerprint.
        agent_id: Optional agent identifier for the event.
        session_id: Optional session identifier for the event.
        con: Optional open connection.

    Raises:
        ValueError: If the stored status does not match from_status.
        ValueError: If the approval_id does not exist.
    """
    # Derive the event_type from the to_status transition.
    _STATUS_TO_EVENT: dict[str, str] = {
        "approved": "APPROVED",
        "rejected": "REJECTED",
        "revoked": "REVOKED",
    }
    event_type = _STATUS_TO_EVENT.get(to_status, to_status.upper())

    payload_json_str: Optional[str] = None
    fp: Optional[str] = None
    if event_payload:
        payload_json_str = canonical_payload(event_payload)
        fp = fingerprint_payload(event_payload)

    _con, owned = _get_con(con)
    try:
        row = _con.execute(
            "SELECT status FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Approval not found: {approval_id!r}")
        actual_status = row[0] if not hasattr(row, "keys") else row["status"]
        if actual_status != from_status:
            raise ValueError(
                f"Cannot transition approval {approval_id!r}: "
                f"expected status={from_status!r} but found {actual_status!r}"
            )
        _con.execute(
            "UPDATE approvals SET status = ?, decided_at = ? WHERE id = ?",
            (to_status, _now_iso(), approval_id),
        )
        insert_event(
            _con,
            approval_id,
            event_type,
            agent_id=agent_id,
            session_id=session_id,
            payload_json=payload_json_str,
            fingerprint=fp,
        )
        if owned:
            _con.commit()
    finally:
        if owned:
            _con.close()


def get_by_id(
    approval_id: str,
    con: Optional[sqlite3.Connection] = None,
) -> Optional[Dict[str, Any]]:
    """Return a single approval row by its id, or None if not found.

    Args:
        approval_id: The P-{uuid4} approval identifier.
        con: Optional open connection.

    Returns:
        Dict with approval row fields, or None if not found.
    """
    _con, owned = _get_con(con)
    try:
        cur = _con.execute(
            "SELECT id, agent_id, session_id, status, fingerprint, "
            "payload_json, created_at, decided_at "
            "FROM approvals WHERE id = ?",
            (approval_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        keys = [
            "id", "agent_id", "session_id", "status", "fingerprint",
            "payload_json", "created_at", "decided_at",
        ]
        if hasattr(row, "keys"):
            return dict(row)
        return dict(zip(keys, row))
    finally:
        if owned:
            _con.close()


def revoke(
    approval_id: str,
    revoker_session: str,
    *,
    agent_id: Optional[str] = None,
    con: Optional[sqlite3.Connection] = None,
) -> None:
    """Revoke a pending approval (user or admin cancellation before execution).

    Inserts a REVOKED event and updates approvals.status to 'revoked'.
    Unlike reject() which is a user decision on a presented choice, revoke()
    is an administrative cancellation before the approval has been decided.

    Args:
        approval_id: The P-{uuid4} approval identifier.
        revoker_session: The session_id of the revoking session.
        agent_id: Optional agent identifier for the REVOKED event.
        con: Optional open connection.

    Raises:
        ValueError: If the approval is not in 'pending' status.
        ValueError: If the approval_id does not exist.
    """
    transition(
        approval_id,
        from_status="pending",
        to_status="revoked",
        agent_id=agent_id,
        session_id=revoker_session,
        con=con,
    )


def list_all(
    status: Optional[str] = None,
    limit: int = 50,
    con: Optional[sqlite3.Connection] = None,
) -> List[Dict[str, Any]]:
    """Return approvals across all sessions, newest first.

    Used by ``gaia approvals history`` to show a temporal view of recent
    approvals regardless of status.

    Args:
        status: Optional status filter (e.g. 'pending', 'approved', 'rejected').
            When None, all statuses are returned.
        limit: Maximum number of rows to return. Defaults to 50.
        con: Optional open connection.

    Returns:
        List of dicts with approval row fields plus age_seconds, ordered
        newest first (created_at DESC).
    """
    _con, owned = _get_con(con)
    try:
        if status is not None:
            cur = _con.execute(
                "SELECT id, agent_id, session_id, status, fingerprint, "
                "payload_json, created_at, decided_at "
                "FROM approvals WHERE status = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cur = _con.execute(
                "SELECT id, agent_id, session_id, status, fingerprint, "
                "payload_json, created_at, decided_at "
                "FROM approvals "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        rows = cur.fetchall()
        keys = [
            "id", "agent_id", "session_id", "status", "fingerprint",
            "payload_json", "created_at", "decided_at",
        ]
        now = datetime.now(timezone.utc)
        result = []
        for row in rows:
            if hasattr(row, "keys"):
                d = dict(row)
            else:
                d = dict(zip(keys, row))
            # Enrich with age_seconds.
            created_at_str = d.get("created_at")
            age_seconds: float = 0.0
            if created_at_str:
                try:
                    created_dt = datetime.strptime(
                        created_at_str, "%Y-%m-%dT%H:%M:%SZ"
                    ).replace(tzinfo=timezone.utc)
                    age_seconds = (now - created_dt).total_seconds()
                except (ValueError, TypeError):
                    pass
            d["age_seconds"] = age_seconds
            result.append(d)
        return result
    finally:
        if owned:
            _con.close()


def get_executed_payload(
    approval_id: str,
    con: Optional[sqlite3.Connection] = None,
) -> Optional[Dict[str, Any]]:
    """Return the parsed sealed_payload from the EXECUTED event for an approval.

    Used by ``gaia approvals replay`` to re-present the commands that were
    run under a given approval.

    Args:
        approval_id: The P-{uuid4} approval identifier.
        con: Optional open connection.

    Returns:
        Parsed payload dict from the most recent EXECUTED event, or None if
        no EXECUTED event exists (i.e., approval was never executed).
    """
    _con, owned = _get_con(con)
    try:
        cur = _con.execute(
            "SELECT payload_json FROM approval_events "
            "WHERE approval_id = ? AND event_type = 'EXECUTED' "
            "ORDER BY id DESC LIMIT 1",
            (approval_id,),
        )
        row = cur.fetchone()
        if row is None:
            # Fall back to the REQUESTED event payload -- the command set
            # is captured there and may not be re-stored at EXECUTED time.
            approval_row = get_by_id(approval_id, con=_con)
            if approval_row and approval_row.get("payload_json"):
                try:
                    return json.loads(approval_row["payload_json"])
                except (json.JSONDecodeError, TypeError):
                    return None
            return None
        payload_json = row[0]
        if not payload_json:
            # No payload in EXECUTED event -- fall back to approval row.
            approval_row = get_by_id(approval_id, con=_con)
            if approval_row and approval_row.get("payload_json"):
                try:
                    return json.loads(approval_row["payload_json"])
                except (json.JSONDecodeError, TypeError):
                    return None
            return None
        try:
            return json.loads(payload_json)
        except (json.JSONDecodeError, TypeError):
            return None
    finally:
        if owned:
            _con.close()


def get_history(
    approval_id: str,
    con: Optional[sqlite3.Connection] = None,
) -> List[Dict[str, Any]]:
    """Return all approval_events for an approval in insertion order.

    Alias for replay_for_approval(), named for CLI discoverability.

    Args:
        approval_id: The P-{uuid4} approval identifier.
        con: Optional open connection.

    Returns:
        List of dicts with all approval_events columns, ordered by id ASC.
    """
    return replay_for_approval(approval_id, con=con)


def replay_for_approval(
    approval_id: str,
    con: Optional[sqlite3.Connection] = None,
) -> List[Dict[str, Any]]:
    """Return all approval_events rows for an approval in insertion order.

    Useful for replaying what happened for a given approval (audit view)
    and for verify_fingerprint before inserting a SHOWN event.

    Args:
        approval_id: The P-{uuid4} approval identifier.
        con: Optional open connection.

    Returns:
        List of dicts with all approval_events columns, ordered by id ASC.
    """
    _con, owned = _get_con(con)
    try:
        cur = _con.execute(
            "SELECT id, approval_id, event_type, agent_id, session_id, "
            "payload_json, fingerprint, prev_hash, this_hash, "
            "metadata_json, created_at "
            "FROM approval_events "
            "WHERE approval_id = ? ORDER BY id ASC",
            (approval_id,),
        )
        rows = cur.fetchall()
        result = []
        keys = [
            "id", "approval_id", "event_type", "agent_id", "session_id",
            "payload_json", "fingerprint", "prev_hash", "this_hash",
            "metadata_json", "created_at",
        ]
        for row in rows:
            if hasattr(row, "keys"):
                result.append(dict(row))
            else:
                result.append(dict(zip(keys, row)))
        return result
    finally:
        if owned:
            _con.close()
