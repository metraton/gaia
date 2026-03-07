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
from typing import List, Optional

from ..core.paths import find_claude_dir
from ..core.state import get_session_id
from .approval_scopes import (
    ApprovalSignature,
    SCOPE_EXACT_COMMAND,
    SCOPE_SEMANTIC_SIGNATURE,
    SUPPORTED_SCOPE_TYPES,
    build_approval_signature,
    matches_approval_signature,
)

logger = logging.getLogger(__name__)

# Default grant TTL in minutes
DEFAULT_GRANT_TTL_MINUTES = 10

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
        scope_type: Approval scope mode (exact or semantic).
        scope_signature: Persisted ApprovalSignature payload for matching.
        granted_at: Unix timestamp when the grant was created.
        ttl_minutes: How long the grant is valid.
        used: Whether the grant has been consumed.
    """
    session_id: str = ""
    approved_verbs: List[str] = field(default_factory=list)
    approved_scope: str = ""
    scope_type: str = SCOPE_SEMANTIC_SIGNATURE
    scope_signature: Optional[dict] = None
    granted_at: float = 0.0
    ttl_minutes: int = DEFAULT_GRANT_TTL_MINUTES
    used: bool = False

    def is_expired(self) -> bool:
        """Check if the grant has expired."""
        return _is_ttl_expired(self.granted_at, self.ttl_minutes)

    def is_valid(self) -> bool:
        """Check if the grant is still usable."""
        return not self.is_expired() and not self.used

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


def _get_grants_dir() -> Path:
    """Get the directory for approval grant files."""
    global _grants_dir_created
    grants_dir = find_claude_dir() / "cache" / "approvals"
    if not _grants_dir_created:
        grants_dir.mkdir(parents=True, exist_ok=True)
        _grants_dir_created = True
    return grants_dir


def _get_session_id() -> str:
    """Get the current session ID. Delegates to core.state.get_session_id()."""
    return get_session_id()


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
        danger_verb: The dangerous verb detected (e.g., "commit", "apply").
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

def check_approval_grant(command: str) -> Optional[ApprovalGrant]:
    """Check if there is an active approval grant for a command.

    Called by the bash_validator before blocking a dangerous command.
    If a valid grant exists that matches the command, the command should
    be allowed through.

    Args:
        command: The shell command to check.

    Returns:
        The matching ApprovalGrant if found and valid, None otherwise.
    """
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
                    # Clean up expired grants
                    if grant.is_expired():
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
            try:
                data = json.loads(pending_file.read_text())
                if not data.get("scope_signature"):
                    _cleanup_grant(pending_file)
                    cleaned += 1
                    continue
                timestamp = data.get("timestamp", 0)
                ttl = data.get("ttl_minutes", DEFAULT_GRANT_TTL_MINUTES)
                if _is_ttl_expired(timestamp, ttl):
                    _cleanup_grant(pending_file)
                    cleaned += 1
            except Exception:
                # Corrupt file, remove it
                _cleanup_grant(pending_file)
                cleaned += 1

    except Exception as e:
        logger.error("Error during grant cleanup: %s", e)

    if cleaned:
        logger.info("Cleaned up %d expired approval/pending files", cleaned)
    return cleaned


def _cleanup_grant(grant_file: Path) -> None:
    """Remove a single grant or pending file."""
    try:
        grant_file.unlink(missing_ok=True)
    except Exception as e:
        logger.warning("Failed to remove grant file %s: %s", grant_file, e)
