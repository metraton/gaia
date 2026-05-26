"""gaia.approvals.chain -- Hash-chain walk validator and fingerprint verifier.

Hash formula (from plan D15 / D13):
    this_hash = SHA-256(COALESCE(prev_hash, '') || COALESCE(fingerprint, ''))

Genesis row (row 0 in any approval's chain):
    prev_hash IS NULL -> treated as empty string by COALESCE.
    this_hash = SHA-256('' || fingerprint) = SHA-256(fingerprint).

Chain walk:
    Iterate all events for an approval ordered by id (insertion order).
    Re-compute each this_hash from (prev_hash, fingerprint) using the same
    formula. If any row diverges, raise ChainTamperError.

Public API:
    validate_chain(approval_id, con) -> bool
    verify_fingerprint(approval_id, payload_json, con) -> bool
    ChainTamperError
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class ChainTamperError(Exception):
    """Raised when hash-chain validation detects a tampered row."""


def _sha256(value: str | None) -> str:
    """Compute SHA-256 hex digest of a string value.

    Mirrors the gaia_sha256 SQLite scalar function registered by
    gaia.store.writer._connect(). Uses COALESCE semantics: None is
    treated as an empty string.
    """
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _compute_this_hash(prev_hash: str | None, fingerprint: str | None) -> str:
    """Compute this_hash = SHA-256(COALESCE(prev_hash,'') || COALESCE(fingerprint,'')).

    This is the canonical hash formula for the approval_events chain. It
    matches the SQL expression in the ai_approval_events_hash trigger:
        gaia_sha256(COALESCE(new.prev_hash, '') || COALESCE(new.fingerprint, ''))
    """
    concatenated = (prev_hash or "") + (fingerprint or "")
    return _sha256(concatenated)


def validate_chain(approval_id: str, con: sqlite3.Connection) -> bool:
    """Walk all events for approval_id and verify the hash chain is intact.

    Iterates events in insertion order (ORDER BY id ASC). For each row,
    re-computes this_hash from (prev_hash, fingerprint) and compares against
    the stored this_hash. A mismatch means a row was tampered with after
    insertion.

    Args:
        approval_id: The P-{uuid4} prefixed approval identifier.
        con: An open sqlite3.Connection with gaia_sha256 registered.

    Returns:
        True if the chain is intact.

    Raises:
        ChainTamperError: If any row's stored this_hash does not match
            the recomputed value.
    """
    cur = con.execute(
        "SELECT id, fingerprint, prev_hash, this_hash "
        "FROM approval_events "
        "WHERE approval_id = ? "
        "ORDER BY id ASC",
        (approval_id,),
    )
    rows = cur.fetchall()

    if not rows:
        # No events yet for this approval -- chain is vacuously intact.
        return True

    for row in rows:
        event_id = row[0]
        fingerprint = row[1]
        prev_hash = row[2]
        stored_this_hash = row[3]

        expected = _compute_this_hash(prev_hash, fingerprint)

        if stored_this_hash != expected:
            raise ChainTamperError(
                f"Hash-chain tamper detected at event id={event_id} "
                f"for approval_id={approval_id!r}: "
                f"stored this_hash={stored_this_hash!r} "
                f"but recomputed={expected!r} "
                f"(prev_hash={prev_hash!r}, fingerprint={fingerprint!r})"
            )

    return True


def verify_fingerprint(
    approval_id: str,
    payload_json: str,
    con: sqlite3.Connection,
) -> bool:
    """Verify that payload_json matches the fingerprint stored in the REQUESTED event.

    Computes the canonical fingerprint of the given payload_json and compares
    it against the fingerprint stored in the REQUESTED event for this approval.

    Canonical fingerprint computation (from plan D13):
        json.dumps(payload, sort_keys=True, separators=(',', ':'))
        Then SHA-256 of the UTF-8 bytes of that canonical string.

    Args:
        approval_id: The P-{uuid4} prefixed approval identifier.
        payload_json: The canonical-JSON sealed_payload string to verify.
        con: An open sqlite3.Connection.

    Returns:
        True if the fingerprint matches.

    Raises:
        ValueError: If no REQUESTED event exists for this approval_id.
        ChainTamperError: If the computed fingerprint does not match the stored one.
    """
    # Re-canonicalize: parse and re-serialize to ensure consistent serialization
    # regardless of how the caller constructed payload_json.
    try:
        parsed = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"payload_json is not valid JSON for approval_id={approval_id!r}: {exc}"
        ) from exc

    canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    computed_fingerprint = _sha256(canonical)

    # Retrieve the fingerprint stored in the REQUESTED event.
    cur = con.execute(
        "SELECT fingerprint FROM approval_events "
        "WHERE approval_id = ? AND event_type = 'REQUESTED' "
        "ORDER BY id ASC LIMIT 1",
        (approval_id,),
    )
    row = cur.fetchone()

    if row is None:
        raise ValueError(
            f"No REQUESTED event found for approval_id={approval_id!r}. "
            "Cannot verify fingerprint without a REQUESTED baseline."
        )

    stored_fingerprint = row[0]

    if computed_fingerprint != stored_fingerprint:
        raise ChainTamperError(
            f"Fingerprint mismatch for approval_id={approval_id!r}: "
            f"stored={stored_fingerprint!r} "
            f"but computed from relayed payload={computed_fingerprint!r}. "
            "Payload may have been tampered with between REQUESTED and SHOWN."
        )

    return True


def insert_event(
    con: sqlite3.Connection,
    approval_id: str,
    event_type: str,
    *,
    agent_id: str | None = None,
    session_id: str | None = None,
    payload_json: str | None = None,
    fingerprint: str | None = None,
    metadata_json: str | None = None,
) -> int:
    """Insert an event into approval_events with this_hash computed before the INSERT.

    This is the canonical API for writing to approval_events. It:
      1. Queries the last event's this_hash for approval_id (or None if genesis).
      2. Computes this_hash = SHA-256(COALESCE(prev_hash,'') || COALESCE(fingerprint,'')).
      3. INSERTs the row with the pre-computed this_hash.

    The BEFORE UPDATE trigger on approval_events would block any post-INSERT
    UPDATE, so this_hash MUST be computed and supplied in the INSERT statement.
    This function is the single enforcement point for that invariant.

    Args:
        con: Open sqlite3.Connection (gaia_sha256 registered is not required here
             since we use Python's hashlib directly).
        approval_id: The P-{uuid4} prefixed approval identifier (must exist in approvals).
        event_type: One of the valid event_type values (see CHECK constraint).
        agent_id: Optional agent identifier.
        session_id: Optional session identifier.
        payload_json: Optional canonical-JSON sealed_payload for this event.
        fingerprint: Optional SHA-256 hex of payload_json.
        metadata_json: Optional free-form JSON for event-specific extras.

    Returns:
        The id (INTEGER) of the newly inserted row.
    """
    # Step 1: Find prev_hash from the last event for this approval.
    row = con.execute(
        "SELECT this_hash FROM approval_events "
        "WHERE approval_id = ? ORDER BY id DESC LIMIT 1",
        (approval_id,),
    ).fetchone()
    prev_hash: str | None = row[0] if row else None

    # Step 2: Compute this_hash in Python before the INSERT.
    this_hash = _compute_this_hash(prev_hash, fingerprint)

    # Step 3: INSERT with the pre-computed this_hash.
    cur = con.execute(
        "INSERT INTO approval_events "
        "(approval_id, event_type, agent_id, session_id, "
        " payload_json, fingerprint, prev_hash, this_hash, metadata_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            approval_id,
            event_type,
            agent_id,
            session_id,
            payload_json,
            fingerprint,
            prev_hash,
            this_hash,
            metadata_json,
        ),
    )
    return cur.lastrowid


def canonical_payload(payload: dict) -> str:
    """Serialize a dict to canonical JSON for fingerprinting.

    Uses the canonical form from plan D13:
        json.dumps(payload, sort_keys=True, separators=(',', ':'))

    Args:
        payload: The sealed_payload dict.

    Returns:
        Canonical JSON string (sorted keys, no whitespace).
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def fingerprint_payload(payload: dict) -> str:
    """Compute the SHA-256 fingerprint of a payload dict.

    Args:
        payload: The sealed_payload dict to fingerprint.

    Returns:
        SHA-256 hex digest of the canonical JSON bytes.
    """
    canonical = canonical_payload(payload)
    return _sha256(canonical)
