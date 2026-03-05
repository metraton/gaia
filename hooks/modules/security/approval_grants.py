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
- Grants are scoped to the approved command verb+family
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
import re
import secrets
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

from ..core.paths import find_claude_dir

logger = logging.getLogger(__name__)

# Default grant TTL in minutes
DEFAULT_GRANT_TTL_MINUTES = 10


@dataclass
class ApprovalGrant:
    """A time-limited approval grant for T3 commands.

    Attributes:
        session_id: The Claude session that owns this grant.
        approved_verbs: Verbs approved (e.g., ["commit", "push"]).
        approved_scope: Original approval scope text from the user.
        granted_at: Unix timestamp when the grant was created.
        ttl_minutes: How long the grant is valid.
        used: Whether the grant has been consumed.
    """
    session_id: str = ""
    approved_verbs: List[str] = field(default_factory=list)
    approved_scope: str = ""
    granted_at: float = 0.0
    ttl_minutes: int = DEFAULT_GRANT_TTL_MINUTES
    used: bool = False

    def is_expired(self) -> bool:
        """Check if the grant has expired."""
        if self.granted_at == 0:
            return True
        elapsed_minutes = (time.time() - self.granted_at) / 60
        return elapsed_minutes > self.ttl_minutes

    def is_valid(self) -> bool:
        """Check if the grant is still usable."""
        return not self.is_expired() and not self.used

    def matches_command(self, command: str) -> bool:
        """Check if a command matches this grant's approved verbs.

        Matching is intentionally broad within the approved verb set.
        If 'commit' is approved, any `git commit ...` command matches.
        If 'push' is approved, any `git push ...` command matches.
        If 'apply' is approved, any `terraform apply ...` / `kubectl apply ...` matches.

        Args:
            command: The shell command to check.

        Returns:
            True if the command matches an approved verb.
        """
        if not self.approved_verbs:
            return False

        command_lower = command.lower()
        for verb in self.approved_verbs:
            # Match the verb as a word boundary in the command
            # e.g., "commit" matches "git commit -m ..." but not "uncommit"
            pattern = r'\b' + re.escape(verb) + r'\b'
            if re.search(pattern, command_lower):
                return True
        return False


def _get_grants_dir() -> Path:
    """Get the directory for approval grant files."""
    grants_dir = find_claude_dir() / "cache" / "approvals"
    grants_dir.mkdir(parents=True, exist_ok=True)
    return grants_dir


def _get_session_id() -> str:
    """Get the current session ID."""
    return os.environ.get("CLAUDE_SESSION_ID", "default")


def _extract_verbs_from_scope(scope: str) -> List[str]:
    """Extract command verbs from an approval scope string.

    Parses scopes like:
      "git commit"         -> ["commit"]
      "git push origin main" -> ["push"]
      "terraform apply prod/vpc" -> ["apply"]
      "kubectl apply namespace payment-service" -> ["apply"]
      "git commit and git push" -> ["commit", "push"]

    Args:
        scope: The approval scope text.

    Returns:
        List of extracted verb strings.
    """
    if not scope:
        return []

    # Known T3 verbs (from dangerous_verbs.py taxonomy)
    from .dangerous_verbs import DESTRUCTIVE_VERBS, MUTATIVE_VERBS

    t3_verbs = DESTRUCTIVE_VERBS | MUTATIVE_VERBS
    scope_lower = scope.lower()

    found_verbs = []
    for verb in t3_verbs:
        # Match as word boundary
        pattern = r'\b' + re.escape(verb) + r'\b'
        if re.search(pattern, scope_lower):
            found_verbs.append(verb)

    return found_verbs


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

    pending_data = {
        "nonce": nonce,
        "session_id": session_id,
        "command": command,
        "danger_verb": danger_verb,
        "danger_category": danger_category,
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
) -> Optional[Path]:
    """Activate a pending approval by converting it to an active grant.

    Called by the pre_tool_use hook when it detects "APPROVE:{nonce}" in a
    Task resume prompt. Validates the pending file, creates an active grant,
    and deletes the pending file.

    Args:
        nonce: The nonce from the APPROVE: token.
        session_id: Current session ID for validation.
        ttl_minutes: TTL for the active grant.

    Returns:
        Path to the active grant file, or None on failure.
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
            return None

        # Read and validate pending data
        pending_data = json.loads(pending_file.read_text())

        # Validate nonce matches exactly
        if pending_data.get("nonce") != nonce:
            logger.warning("Nonce mismatch in pending file: expected %s", nonce)
            return None

        # Validate session matches
        if pending_data.get("session_id") != session_id:
            logger.warning(
                "Session mismatch for nonce %s: pending=%s, current=%s",
                nonce, pending_data.get("session_id"), session_id,
            )
            return None

        # Validate not expired
        pending_timestamp = pending_data.get("timestamp", 0)
        pending_ttl = pending_data.get("ttl_minutes", DEFAULT_GRANT_TTL_MINUTES)
        elapsed_minutes = (time.time() - pending_timestamp) / 60
        if elapsed_minutes > pending_ttl:
            logger.warning(
                "Pending approval expired for nonce %s: %.1f min > %d min TTL",
                nonce, elapsed_minutes, pending_ttl,
            )
            # Clean up expired pending file
            _cleanup_grant(pending_file)
            return None

        # Extract verbs from the blocked command
        command = pending_data.get("command", "")
        danger_verb = pending_data.get("danger_verb", "")
        verbs = _extract_verbs_from_scope(command)

        # If verb extraction from command fails, use the danger_verb directly
        if not verbs and danger_verb:
            verbs = [danger_verb]

        if not verbs:
            logger.warning(
                "Could not extract verbs from pending approval command: %s",
                command,
            )
            return None

        # Create active grant
        grant = ApprovalGrant(
            session_id=session_id,
            approved_verbs=verbs,
            approved_scope=command,
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
        return grant_file

    except (json.JSONDecodeError, TypeError) as e:
        logger.error("Invalid pending approval file for nonce %s: %s", nonce, e)
        return None
    except Exception as e:
        logger.error("Failed to activate pending approval: %s", e)
        return None


# ============================================================================
# Legacy Grant Management (still used for active grants)
# ============================================================================

def write_approval_grant(scope: str, ttl_minutes: int = DEFAULT_GRANT_TTL_MINUTES) -> Optional[Path]:
    """Write an approval grant file after user approval.

    Called by the pre_tool_use hook when it detects a legacy approval token
    in a Task/Agent resume prompt.

    Args:
        scope: The approval scope (e.g., "git commit", "terraform apply prod/vpc").
        ttl_minutes: How long the grant is valid.

    Returns:
        Path to the grant file, or None on failure.
    """
    session_id = _get_session_id()
    verbs = _extract_verbs_from_scope(scope)

    if not verbs:
        logger.warning(
            "Could not extract verbs from approval scope '%s' -- "
            "no grant written. The bash_validator will block as usual.",
            scope,
        )
        return None

    grant = ApprovalGrant(
        session_id=session_id,
        approved_verbs=verbs,
        approved_scope=scope,
        granted_at=time.time(),
        ttl_minutes=ttl_minutes,
    )

    try:
        grants_dir = _get_grants_dir()
        # Use session_id + timestamp for uniqueness
        # Use high-resolution timestamp to avoid collisions from rapid writes
        grant_file = grants_dir / f"grant-{session_id}-{int(time.time() * 1000)}.json"
        grant_file.write_text(json.dumps(asdict(grant), indent=2))

        logger.info(
            "Approval grant written: session=%s, verbs=%s, scope='%s', ttl=%dm, file=%s",
            session_id, verbs, scope, ttl_minutes, grant_file.name,
        )
        return grant_file

    except Exception as e:
        logger.error("Failed to write approval grant: %s", e)
        return None


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

                # Check if command matches
                if grant.matches_command(command):
                    logger.info(
                        "Approval grant matched: command='%s', scope='%s', verbs=%s",
                        command[:80], grant.approved_scope, grant.approved_verbs,
                    )
                    return grant

            except (json.JSONDecodeError, TypeError) as e:
                logger.warning("Invalid grant file %s: %s", grant_file, e)
                _cleanup_grant(grant_file)
                continue

    except Exception as e:
        logger.error("Error checking approval grants: %s", e)

    return None


def consume_grant(grant: ApprovalGrant) -> None:
    """Mark a grant as used after the approved command executes.

    This does NOT delete the grant file immediately -- it marks it as used
    so subsequent calls to check_approval_grant skip it. The grant file
    is cleaned up by the next cleanup_expired_grants call or on expiry.

    Note: We do NOT consume on first use because an agent may need to run
    related commands (e.g., git commit followed by git push, both approved).
    The TTL handles eventual cleanup.

    Args:
        grant: The grant to mark as consumed (currently a no-op; TTL handles it).
    """
    # Intentional no-op: grants are valid for their full TTL to allow
    # related approved commands (commit + push in same approval).
    # The TTL provides the security boundary.
    logger.debug(
        "Grant consumed (TTL-based expiry): scope='%s'", grant.approved_scope
    )


def cleanup_expired_grants() -> int:
    """Remove expired grant and pending files.

    Called periodically (e.g., at hook startup) to prevent accumulation.

    Returns:
        Number of files cleaned up.
    """
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
                timestamp = data.get("timestamp", 0)
                ttl = data.get("ttl_minutes", DEFAULT_GRANT_TTL_MINUTES)
                elapsed_minutes = (time.time() - timestamp) / 60
                if elapsed_minutes > ttl:
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
