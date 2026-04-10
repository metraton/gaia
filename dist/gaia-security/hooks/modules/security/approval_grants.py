"""
Approval grant management for T3 command passthrough.

Two-phase nonce-based approval flow:

  Phase 1 -- BLOCKING:
    bash_validator detects a T3 command, generates a cryptographic nonce,
    writes a pending-{nonce}.json file, and returns a block response that
    includes the nonce for the agent to present.

  Phase 2 -- ACTIVATION:
    The orchestrator resumes the agent with "APPROVE:{nonce}". The
    pre_tool_use hook finds the pending file, validates it (session, TTL,
    nonce match), converts it to an active grant, and deletes the pending
    file. The agent retries the command; bash_validator finds the active
    grant and allows it.

Grants are:
- Scoped to a session (CLAUDE_SESSION_ID)
- Time-limited (default 10 minutes)
- Cleaned up after use or expiry
- Stored in .claude/cache/approvals/

Security properties:
- Grants are created ONLY by the hook (not by agents)
- Nonce-activated grants are scoped to a semantic command signature
- Grants expire automatically
- The deny list (blocked_commands.py) is NEVER bypassed -- grants only
  override the dangerous verb detector
- Nonces are 128-bit random hex (cannot be guessed)
- Pending files are session-scoped (cannot be activated from another session)
- A nonce can only be activated ONCE (pending file deleted on activation)
"""

import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.paths import find_claude_dir, get_plugin_data_dir
from ..core.state import get_session_id
from .approval_scopes import (
    ApprovalSignature,
    SCOPE_SEMANTIC_SIGNATURE,
    SCOPE_VERB_FAMILY,
    SUPPORTED_SCOPE_TYPES,
    build_approval_signature,
    matches_approval_signature,
)

logger = logging.getLogger(__name__)

# Default grant TTL in minutes
DEFAULT_GRANT_TTL_MINUTES = 5

# Cleanup throttle: only run cleanup if 60+ seconds since last run
_last_cleanup_time: float = 0.0
_CLEANUP_INTERVAL_SECONDS = 60

class ActivationStatus(str, Enum):
    """Activation result statuses for pending approval flow."""
    ACTIVATED = "activated"
    NOT_FOUND = "not_found"
    NONCE_MISMATCH = "nonce_mismatch"
    SESSION_MISMATCH = "session_mismatch"
    EXPIRED = "expired"
    INVALID_SIGNATURE = "invalid_signature"
    INVALID_PENDING = "invalid_pending"
    ERROR = "error"


# Backward-compatible module-level aliases
ACTIVATION_ACTIVATED = ActivationStatus.ACTIVATED
ACTIVATION_NOT_FOUND = ActivationStatus.NOT_FOUND
ACTIVATION_NONCE_MISMATCH = ActivationStatus.NONCE_MISMATCH
ACTIVATION_SESSION_MISMATCH = ActivationStatus.SESSION_MISMATCH
ACTIVATION_EXPIRED = ActivationStatus.EXPIRED
ACTIVATION_INVALID_SIGNATURE = ActivationStatus.INVALID_SIGNATURE
ACTIVATION_INVALID_PENDING = ActivationStatus.INVALID_PENDING
ACTIVATION_ERROR = ActivationStatus.ERROR


def _is_ttl_expired(timestamp: float, ttl_minutes: int) -> bool:
    """Return True if the given timestamp is older than ttl_minutes."""
    if timestamp == 0:
        return True
    elapsed_minutes = (time.time() - timestamp) / 60
    return elapsed_minutes > ttl_minutes


@dataclass(frozen=True)
class ApprovalActivationResult:
    """Structured result for pending approval activation."""

    success: bool
    status: str
    reason: str
    grant_path: Optional[Path] = None


@dataclass
class ApprovalGrant:
    """A time-limited approval grant for T3 commands.

    Attributes:
        session_id: The Claude session that owns this grant.
        approved_verbs: Human-readable verb summary for logs/debugging.
        approved_scope: Original approval scope text from the user.
        scope_type: Approval scope mode (exact, semantic, or verb_family).
        scope_signature: Persisted ApprovalSignature payload for matching.
        granted_at: Unix timestamp when the grant was created.
        ttl_minutes: How long the grant is valid.
        used: Whether the grant has been consumed.
        multi_use: When True, the grant is NOT consumed after a single use.
            Used by SCOPE_VERB_FAMILY grants for batch operations.
    """
    session_id: str = ""
    approved_verbs: List[str] = field(default_factory=list)
    approved_scope: str = ""
    scope_type: str = SCOPE_SEMANTIC_SIGNATURE
    scope_signature: Optional[dict] = None
    granted_at: float = 0.0
    ttl_minutes: int = DEFAULT_GRANT_TTL_MINUTES
    used: bool = False
    confirmed: bool = False
    multi_use: bool = False

    def is_expired(self) -> bool:
        """Check if the grant has expired."""
        return _is_ttl_expired(self.granted_at, self.ttl_minutes)

    def is_valid(self) -> bool:
        """Check if the grant is still usable.

        Multi-use grants ignore the ``used`` flag and remain valid until
        their TTL expires.
        """
        if self.is_expired():
            return False
        if self.multi_use:
            return True
        return not self.used

    def get_signature(self) -> Optional[ApprovalSignature]:
        """Deserialize the persisted scope signature, if present."""
        if not self.scope_signature:
            return None
        try:
            return ApprovalSignature.from_dict(self.scope_signature)
        except Exception:
            return None

    def matches_command(self, command: str) -> bool:
        """Check whether a command falls inside this grant's explicit scope."""
        signature = self.get_signature()
        if signature is None:
            return False
        return matches_approval_signature(signature, command)


_grants_dir_created: bool = False

# Module-level flag: set by check_approval_grant() when it encounters and
# cleans up an expired grant for the requested command.  Callers (e.g.
# bash_validator) can read this via last_check_found_expired() to emit a
# clear expiry message instead of a generic "no grant found" block.
_last_check_found_expired: bool = False


def last_check_found_expired() -> bool:
    """Return True if the most recent check_approval_grant() call cleaned up
    an expired grant that would have matched the command."""
    return _last_check_found_expired


def _get_grants_dir() -> Path:
    """Get the directory for approval grant files."""
    global _grants_dir_created
    grants_dir = get_plugin_data_dir() / "cache" / "approvals"
    if not _grants_dir_created:
        grants_dir.mkdir(parents=True, exist_ok=True)
        _grants_dir_created = True
    return grants_dir


def _get_pending_index_path(session_id: str) -> Path:
    """Return the session-scoped pending-approval index path."""
    return _get_grants_dir() / f"pending-index-{session_id}.json"


def _read_json_file(path: Path) -> Optional[Dict[str, Any]]:
    """Read a JSON file defensively and return its dict payload."""
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _rebuild_pending_index(session_id: str) -> None:
    """Rebuild the per-session pending-approval index from authoritative files."""
    index_path = _get_pending_index_path(session_id)
    entries: List[Dict[str, Any]] = []

    for pending_file in _get_grants_dir().glob("pending-*.json"):
        if pending_file.name.startswith("pending-index-"):
            continue
        data = _read_json_file(pending_file)
        if not data or data.get("session_id") != session_id:
            continue

        nonce = data.get("nonce")
        timestamp = data.get("timestamp")
        if not nonce or not isinstance(timestamp, (int, float)):
            continue
        ttl_minutes = data.get("ttl_minutes", DEFAULT_GRANT_TTL_MINUTES)
        if _is_ttl_expired(float(timestamp), int(ttl_minutes)):
            continue

        entries.append(
            {
                "nonce": nonce,
                "pending_file": pending_file.name,
                "timestamp": float(timestamp),
            }
        )

    entries.sort(key=lambda item: item["timestamp"], reverse=True)

    if not entries:
        index_path.unlink(missing_ok=True)
        return

    index_payload = {
        "session_id": session_id,
        "latest_nonce": entries[0]["nonce"],
        "entries": entries,
    }
    index_path.write_text(json.dumps(index_payload, indent=2))


def _get_session_id() -> str:
    """Get the current session ID. Delegates to core.state.get_session_id()."""
    return get_session_id()


def get_latest_pending_approval(session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return the newest pending approval record for the current session.

    This is a deterministic helper for future orchestrator logic: it reads the
    session index, then dereferences the authoritative pending file instead of
    asking callers to parse a nonce from agent text.
    """
    if session_id is None:
        session_id = _get_session_id()

    index_path = _get_pending_index_path(session_id)

    for attempt in range(2):
        if not index_path.exists():
            return None

        index_data = _read_json_file(index_path)
        if not index_data:
            _rebuild_pending_index(session_id)
            continue

        latest_nonce = index_data.get("latest_nonce")
        entries = index_data.get("entries") or []
        pending_ref = next((entry for entry in entries if entry.get("nonce") == latest_nonce), None)
        if not latest_nonce or pending_ref is None:
            _rebuild_pending_index(session_id)
            continue

        pending_path = _get_grants_dir() / pending_ref.get("pending_file", "")
        pending_data = _read_json_file(pending_path)
        if not pending_data or pending_data.get("session_id") != session_id:
            _rebuild_pending_index(session_id)
            continue

        return pending_data

    return None


# ============================================================================
# Nonce Generation and Pending Approval Management
# ============================================================================

def generate_nonce() -> str:
    """Generate a cryptographic nonce for approval tracking.

    Returns:
        32-character hex string (128 bits of entropy).
    """
    return secrets.token_hex(16)


def write_pending_approval(
    nonce: str,
    command: str,
    danger_verb: str,
    danger_category: str,
    session_id: Optional[str] = None,
    ttl_minutes: int = DEFAULT_GRANT_TTL_MINUTES,
) -> Optional[Path]:
    """Write a pending approval file when a T3 command is blocked.

    Called by bash_validator when it detects a dangerous command and blocks it.
    The nonce is included in the block response so the agent can present it
    to the user for approval.

    Args:
        nonce: Cryptographic nonce from generate_nonce().
        command: The command that was blocked.
        danger_verb: The dangerous verb detected (e.g., "push", "apply").
        danger_category: The danger category (e.g., "MUTATIVE", "DESTRUCTIVE").
        session_id: Session ID (defaults to CLAUDE_SESSION_ID env var).
        ttl_minutes: How long the pending approval is valid before expiry.

    Returns:
        Path to the pending file, or None on failure.
    """
    if session_id is None:
        session_id = _get_session_id()

    signature = build_approval_signature(
        command,
        scope_type=SCOPE_SEMANTIC_SIGNATURE,
        danger_verb=danger_verb,
        danger_category=danger_category,
    )
    if signature is None:
        logger.error(
            "Failed to build semantic approval signature for pending command: %s",
            command,
        )
        return None

    pending_data = {
        "nonce": nonce,
        "session_id": session_id,
        "command": command,
        "danger_verb": danger_verb,
        "danger_category": danger_category,
        "scope_type": signature.scope_type,
        "scope_signature": signature.to_dict(),
        "timestamp": time.time(),
        "ttl_minutes": ttl_minutes,
    }

    try:
        grants_dir = _get_grants_dir()
        pending_file = grants_dir / f"pending-{nonce}.json"
        pending_file.write_text(json.dumps(pending_data, indent=2))
        _rebuild_pending_index(session_id)

        logger.info(
            "Pending approval written: nonce=%s, verb=%s, category=%s, session=%s",
            nonce, danger_verb, danger_category, session_id,
        )
        return pending_file

    except Exception as e:
        logger.error("Failed to write pending approval: %s", e)
        return None


def activate_pending_approval(
    nonce: str,
    session_id: Optional[str] = None,
    ttl_minutes: int = DEFAULT_GRANT_TTL_MINUTES,
) -> ApprovalActivationResult:
    """Activate a pending approval by converting it to an active grant.

    Called by the pre_tool_use hook when it detects "APPROVE:{nonce}" in a
    Task resume prompt. Validates the pending file, creates an active grant,
    and deletes the pending file.

    Args:
        nonce: The nonce from the APPROVE: token.
        session_id: Current session ID for validation.
        ttl_minutes: TTL for the active grant.

    Returns:
        Structured activation result with status and optional grant path.
    """
    if session_id is None:
        session_id = _get_session_id()

    try:
        grants_dir = _get_grants_dir()
        pending_file = grants_dir / f"pending-{nonce}.json"

        # Pending file must exist
        if not pending_file.exists():
            logger.warning(
                "Pending approval not found for nonce %s -- "
                "may have expired or already been activated",
                nonce,
            )
            return ApprovalActivationResult(
                success=False,
                status=ACTIVATION_NOT_FOUND,
                reason="Pending approval not found. It may have expired or already been used.",
            )

        # Read and validate pending data
        pending_data = json.loads(pending_file.read_text())

        # Validate nonce matches exactly
        if pending_data.get("nonce") != nonce:
            logger.warning("Nonce mismatch in pending file: expected %s", nonce)
            return ApprovalActivationResult(
                success=False,
                status=ACTIVATION_NONCE_MISMATCH,
                reason="Nonce mismatch while activating approval.",
            )

        # Validate session matches
        if pending_data.get("session_id") != session_id:
            logger.warning(
                "Session mismatch for nonce %s: pending=%s, current=%s",
                nonce, pending_data.get("session_id"), session_id,
            )
            return ApprovalActivationResult(
                success=False,
                status=ACTIVATION_SESSION_MISMATCH,
                reason="Approval was issued for a different Claude session.",
            )

        # Validate not expired
        pending_timestamp = pending_data.get("timestamp", 0)
        pending_ttl = pending_data.get("ttl_minutes", DEFAULT_GRANT_TTL_MINUTES)
        if _is_ttl_expired(pending_timestamp, pending_ttl):
            logger.warning(
                "Pending approval expired for nonce %s: TTL=%d min",
                nonce, pending_ttl,
            )
            # Clean up expired pending file
            _cleanup_grant(pending_file)
            _rebuild_pending_index(session_id)
            return ApprovalActivationResult(
                success=False,
                status=ACTIVATION_EXPIRED,
                reason="Approval nonce expired before activation.",
            )

        command = pending_data.get("command", "")
        danger_verb = pending_data.get("danger_verb", "")
        scope_signature_data = pending_data.get("scope_signature")
        if not scope_signature_data:
            logger.warning("Pending approval for nonce %s is missing scope_signature", nonce)
            _cleanup_grant(pending_file)
            _rebuild_pending_index(session_id)
            return ApprovalActivationResult(
                success=False,
                status=ACTIVATION_INVALID_PENDING,
                reason="Pending approval file is missing a semantic signature.",
            )

        signature = ApprovalSignature.from_dict(scope_signature_data)
        if signature.scope_type != SCOPE_SEMANTIC_SIGNATURE:
            logger.warning(
                "Pending approval for nonce %s has unsupported scope_type=%s",
                nonce,
                signature.scope_type,
            )
            _cleanup_grant(pending_file)
            _rebuild_pending_index(session_id)
            return ApprovalActivationResult(
                success=False,
                status=ACTIVATION_INVALID_SIGNATURE,
                reason="Pending approval uses an unsupported scope type.",
            )

        if not signature.verb and not danger_verb:
            logger.warning(
                "Could not validate semantic signature for pending approval command: %s",
                command,
            )
            return ApprovalActivationResult(
                success=False,
                status=ACTIVATION_INVALID_SIGNATURE,
                reason="Approval signature could not be validated safely.",
            )

        verbs = [signature.verb] if signature.verb else ([danger_verb.lower()] if danger_verb else [])

        # Create active grant
        grant = ApprovalGrant(
            session_id=session_id,
            approved_verbs=verbs,
            approved_scope=command,
            scope_type=signature.scope_type,
            scope_signature=signature.to_dict(),
            granted_at=time.time(),
            ttl_minutes=ttl_minutes,
        )

        grant_file = grants_dir / f"grant-{session_id}-{int(time.time() * 1000)}.json"
        grant_file.write_text(json.dumps(asdict(grant), indent=2))

        # Delete pending file (one-time activation)
        _cleanup_grant(pending_file)
        _rebuild_pending_index(session_id)

        logger.info(
            "Pending approval activated: nonce=%s, verbs=%s, grant=%s",
            nonce, verbs, grant_file.name,
        )
        return ApprovalActivationResult(
            success=True,
            status=ACTIVATION_ACTIVATED,
            reason="Pending approval activated.",
            grant_path=grant_file,
        )

    except (json.JSONDecodeError, TypeError) as e:
        logger.error("Invalid pending approval file for nonce %s: %s", nonce, e)
        return ApprovalActivationResult(
            success=False,
            status=ACTIVATION_INVALID_PENDING,
            reason="Pending approval file is invalid or corrupt.",
        )
    except Exception as e:
        logger.error("Failed to activate pending approval: %s", e)
        return ApprovalActivationResult(
            success=False,
            status=ACTIVATION_ERROR,
            reason="Unexpected error while activating approval.",
        )

def check_approval_grant(command: str, session_id: str = None) -> Optional[ApprovalGrant]:
    """Check if there is an active approval grant for a command.

    Called by the bash_validator before blocking a dangerous command.
    If a valid grant exists that matches the command, the command should
    be allowed through.

    Args:
        command: The shell command to check.
        session_id: Session ID for grant scoping (defaults to env var).

    Returns:
        The matching ApprovalGrant if found and valid, None otherwise.
    """
    global _last_check_found_expired
    _last_check_found_expired = False

    if not session_id:
        session_id = _get_session_id()

    try:
        grants_dir = _get_grants_dir()
        if not grants_dir.exists():
            return None

        # Scan grant files for this session
        for grant_file in sorted(grants_dir.glob(f"grant-{session_id}-*.json")):
            try:
                data = json.loads(grant_file.read_text())
                grant = ApprovalGrant(**data)

                # Skip expired or used grants
                if not grant.is_valid():
                    # Clean up expired grants; track if it would have matched
                    if grant.is_expired():
                        if grant.matches_command(command):
                            _last_check_found_expired = True
                        _cleanup_grant(grant_file)
                    continue

                signature = grant.get_signature()
                if signature is None or signature.scope_type not in SUPPORTED_SCOPE_TYPES:
                    logger.warning("Removing unsupported approval grant file %s", grant_file)
                    _cleanup_grant(grant_file)
                    continue

                # Check if command matches the explicit scope signature
                if grant.matches_command(command):
                    logger.info(
                        "Approval grant matched: command='%s', scope='%s', type=%s",
                        command[:80], grant.approved_scope, grant.scope_type,
                    )
                    return grant

            except (json.JSONDecodeError, TypeError) as e:
                logger.warning("Invalid grant file %s: %s", grant_file, e)
                _cleanup_grant(grant_file)
                continue

    except Exception as e:
        logger.error("Error checking approval grants: %s", e)

    return None


def consume_grant(command: str, session_id: str = None) -> bool:
    """Mark the first matching valid grant as used and persist to disk.

    Called by bash_validator immediately after check_approval_grant() returns
    a match, so that the grant can only be used once (single-use).

    Args:
        command: The shell command whose grant should be consumed.
        session_id: Session ID for grant scoping (defaults to env var).

    Returns:
        True if a grant was found and consumed, False otherwise.
    """
    if not session_id:
        session_id = _get_session_id()

    try:
        grants_dir = _get_grants_dir()
        if not grants_dir.exists():
            return False

        for grant_file in sorted(grants_dir.glob(f"grant-{session_id}-*.json")):
            try:
                data = json.loads(grant_file.read_text())
                grant = ApprovalGrant(**data)

                if not grant.is_valid():
                    if grant.is_expired():
                        _cleanup_grant(grant_file)
                    continue

                signature = grant.get_signature()
                if signature is None or signature.scope_type not in SUPPORTED_SCOPE_TYPES:
                    continue

                if grant.matches_command(command):
                    if grant.multi_use:
                        logger.info(
                            "Grant matched (multi-use, not consumed): command='%s', grant=%s",
                            command[:80], grant_file.name,
                        )
                        return True
                    data["used"] = True
                    grant_file.write_text(json.dumps(data, indent=2))
                    logger.info(
                        "Grant consumed (single-use): command='%s', grant=%s",
                        command[:80], grant_file.name,
                    )
                    return True

            except (json.JSONDecodeError, TypeError):
                continue

    except Exception as e:
        logger.error("Error consuming grant: %s", e)

    return False


def confirm_grant(command: str, session_id: str = None) -> bool:
    """Mark the first unconfirmed grant matching command as confirmed.

    Called after the native permission dialog accepts the first T3 execution.
    Subsequent T3 commands within the TTL window will see ``confirmed=True``
    and be auto-allowed without a native dialog.

    Args:
        command: The shell command whose grant should be confirmed.
        session_id: Session ID for grant scoping (defaults to env var).

    Returns:
        True if a grant was found and confirmed, False otherwise.
    """
    if not session_id:
        session_id = _get_session_id()

    try:
        grants_dir = _get_grants_dir()
        if not grants_dir.exists():
            return False

        for grant_file in sorted(grants_dir.glob(f"grant-{session_id}-*.json")):
            try:
                data = json.loads(grant_file.read_text())
                grant = ApprovalGrant(**data)

                if not grant.is_valid():
                    if grant.is_expired():
                        _cleanup_grant(grant_file)
                    continue

                if grant.confirmed:
                    continue

                signature = grant.get_signature()
                if signature is None or signature.scope_type not in SUPPORTED_SCOPE_TYPES:
                    continue

                if grant.matches_command(command):
                    data["confirmed"] = True
                    grant_file.write_text(json.dumps(data, indent=2))
                    logger.info(
                        "Grant confirmed: command='%s', grant=%s",
                        command[:80], grant_file.name,
                    )
                    return True

            except (json.JSONDecodeError, TypeError):
                continue

    except Exception as e:
        logger.error("Error confirming grant: %s", e)

    return False


def cleanup_expired_grants() -> int:
    """Remove expired grant and pending files.

    Called periodically (e.g., at hook startup) to prevent accumulation.
    Throttled to run at most once every _CLEANUP_INTERVAL_SECONDS.

    Returns:
        Number of files cleaned up.
    """
    global _last_cleanup_time
    now = time.time()
    if now - _last_cleanup_time < _CLEANUP_INTERVAL_SECONDS:
        return 0
    _last_cleanup_time = now

    cleaned = 0
    sessions_to_rebuild: set[str] = set()
    try:
        grants_dir = _get_grants_dir()
        if not grants_dir.exists():
            return 0

        # Clean up expired active grants
        for grant_file in grants_dir.glob("grant-*.json"):
            try:
                data = json.loads(grant_file.read_text())
                grant = ApprovalGrant(**data)
                signature = grant.get_signature()
                if signature is None or signature.scope_type not in SUPPORTED_SCOPE_TYPES:
                    _cleanup_grant(grant_file)
                    cleaned += 1
                    continue
                if grant.is_expired():
                    _cleanup_grant(grant_file)
                    cleaned += 1
            except Exception:
                # Corrupt file, remove it
                _cleanup_grant(grant_file)
                cleaned += 1

        # Clean up expired pending approvals
        for pending_file in grants_dir.glob("pending-*.json"):
            if pending_file.name.startswith("pending-index-"):
                continue
            try:
                data = json.loads(pending_file.read_text())
                session_id = data.get("session_id")
                if not data.get("scope_signature"):
                    _cleanup_grant(pending_file)
                    if session_id:
                        sessions_to_rebuild.add(session_id)
                    cleaned += 1
                    continue
                timestamp = data.get("timestamp", 0)
                ttl = data.get("ttl_minutes", DEFAULT_GRANT_TTL_MINUTES)
                if _is_ttl_expired(timestamp, ttl):
                    _cleanup_grant(pending_file)
                    if session_id:
                        sessions_to_rebuild.add(session_id)
                    cleaned += 1
            except Exception:
                # Corrupt file, remove it
                data = _read_json_file(pending_file)
                if data and data.get("session_id"):
                    sessions_to_rebuild.add(data["session_id"])
                _cleanup_grant(pending_file)
                cleaned += 1

    except Exception as e:
        logger.error("Error during grant cleanup: %s", e)

    for session_id in sessions_to_rebuild:
        _rebuild_pending_index(session_id)

    if cleaned:
        logger.info("Cleaned up %d expired approval/pending files", cleaned)
    return cleaned


def get_pending_approvals_for_session(
    session_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return all non-expired pending approvals for a session.

    Args:
        session_id: Session ID to filter by (defaults to current session).

    Returns:
        List of pending approval dicts, newest first.
    """
    if session_id is None:
        session_id = _get_session_id()

    results: List[Dict[str, Any]] = []
    try:
        grants_dir = _get_grants_dir()
        for pending_file in grants_dir.glob("pending-*.json"):
            if pending_file.name.startswith("pending-index-"):
                continue
            data = _read_json_file(pending_file)
            if not data or data.get("session_id") != session_id:
                continue
            timestamp = data.get("timestamp", 0)
            ttl = data.get("ttl_minutes", DEFAULT_GRANT_TTL_MINUTES)
            if _is_ttl_expired(float(timestamp), int(ttl)):
                continue
            results.append(data)
    except Exception as e:
        logger.error("Error listing pending approvals for session %s: %s", session_id, e)

    results.sort(key=lambda d: d.get("timestamp", 0), reverse=True)
    return results


def find_pending_for_command(
    session_id: str,
    command: str,
) -> Optional[str]:
    """Find an existing pending approval nonce for this command and session.

    When a subagent retries a blocked T3 command, a pending approval may
    already exist from the first attempt.  Reusing the existing nonce
    prevents the infinite-loop of generating a new approval_id on every
    retry while the user is still reviewing the first one.

    Args:
        session_id: Session to search.
        command: The command to match against pending approvals.

    Returns:
        The nonce (approval_id) if a matching pending approval exists, else None.
    """
    pending_list = get_pending_approvals_for_session(session_id)
    if not pending_list:
        return None

    # Build a signature for the incoming command to compare semantically
    target_sig = build_approval_signature(
        command,
        scope_type=SCOPE_SEMANTIC_SIGNATURE,
    )
    if target_sig is None:
        return None

    for pending_data in pending_list:
        pending_sig_data = pending_data.get("scope_signature")
        if not pending_sig_data:
            continue
        try:
            pending_sig = ApprovalSignature.from_dict(pending_sig_data)
            if matches_approval_signature(pending_sig, command):
                nonce = pending_data.get("nonce")
                if nonce:
                    logger.info(
                        "Reusing existing pending approval nonce=%s for command: %s",
                        nonce, command[:80],
                    )
                    return nonce
        except Exception:
            continue

    return None


def activate_grants_for_session(
    session_id: Optional[str] = None,
    ttl_minutes: int = DEFAULT_GRANT_TTL_MINUTES,
) -> List[ApprovalActivationResult]:
    """Activate ALL pending approvals for a session.

    Called by the ElicitationResult hook when the user approves via
    AskUserQuestion. Converts every non-expired pending approval for the
    session into an active grant.

    Args:
        session_id: Session to activate for (defaults to current session).
        ttl_minutes: TTL for the resulting active grants.

    Returns:
        List of activation results (one per pending approval).
    """
    if session_id is None:
        session_id = _get_session_id()

    pending_list = get_pending_approvals_for_session(session_id)
    results: List[ApprovalActivationResult] = []

    for pending_data in pending_list:
        nonce = pending_data.get("nonce", "")
        if not nonce:
            continue
        result = activate_pending_approval(
            nonce=nonce,
            session_id=session_id,
            ttl_minutes=ttl_minutes,
        )
        results.append(result)
        logger.info(
            "Session-wide activation: nonce=%s status=%s",
            nonce,
            getattr(result.status, "value", str(result.status)),
        )

    return results


# ============================================================================
# Batch (Verb-Family) Grant Creation
# ============================================================================

DEFAULT_BATCH_TTL_MINUTES = 10


def create_verb_family_grant(
    session_id: str,
    base_cmd: str,
    verb: str,
    danger_category: str = "",
    ttl_minutes: int = DEFAULT_BATCH_TTL_MINUTES,
) -> Optional[Path]:
    """Create a multi-use SCOPE_VERB_FAMILY grant directly (no pending phase).

    Called when the user approves a batch operation.  The resulting grant
    matches any command with the same ``base_cmd`` and ``verb``, regardless
    of arguments or non-dangerous flags, and is NOT consumed after a single
    use.  It expires after ``ttl_minutes``.

    Args:
        session_id: The Claude session that owns this grant.
        base_cmd: CLI base command (e.g., "gws", "kubectl").
        verb: The mutative verb (e.g., "modify", "delete").
        danger_category: Optional danger category for stricter matching.
        ttl_minutes: Grant lifetime in minutes (default 10).

    Returns:
        Path to the grant file, or None on failure.
    """
    from .mutative_verbs import CATEGORY_UNKNOWN, CLI_FAMILY_LOOKUP

    if not session_id or not base_cmd or not verb:
        logger.error(
            "create_verb_family_grant called with missing required args: "
            "session_id=%s, base_cmd=%s, verb=%s",
            session_id, base_cmd, verb,
        )
        return None

    resolved_category = danger_category if danger_category else CATEGORY_UNKNOWN
    cli_family = CLI_FAMILY_LOOKUP.get(base_cmd, "unknown")

    signature = ApprovalSignature(
        scope_type=SCOPE_VERB_FAMILY,
        base_cmd=base_cmd,
        cli_family=cli_family,
        danger_category=resolved_category,
        verb=verb.lower(),
        # Intentionally empty -- verb_family matching ignores these:
        semantic_tokens=(),
        normalized_flags=(),
        dangerous_flags=(),
        exact_tokens=(),
    )

    grant = ApprovalGrant(
        session_id=session_id,
        approved_verbs=[verb.lower()],
        approved_scope=f"batch:{base_cmd} {verb}",
        scope_type=SCOPE_VERB_FAMILY,
        scope_signature=signature.to_dict(),
        granted_at=time.time(),
        ttl_minutes=ttl_minutes,
        used=False,
        confirmed=False,
        multi_use=True,
    )

    try:
        grants_dir = _get_grants_dir()
        grant_file = grants_dir / f"grant-{session_id}-batch-{int(time.time() * 1000)}.json"
        grant_file.write_text(json.dumps(asdict(grant), indent=2))
        logger.info(
            "Verb-family batch grant created: base_cmd=%s, verb=%s, "
            "ttl=%d min, session=%s, file=%s",
            base_cmd, verb, ttl_minutes, session_id[:12], grant_file.name,
        )
        return grant_file

    except Exception as e:
        logger.error("Failed to create verb-family grant: %s", e)
        return None


def _cleanup_grant(grant_file: Path) -> None:
    """Remove a single grant or pending file."""
    try:
        grant_file.unlink(missing_ok=True)
    except Exception as e:
        logger.warning("Failed to remove grant file %s: %s", grant_file, e)
