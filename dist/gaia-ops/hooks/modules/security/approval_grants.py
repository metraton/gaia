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
- Time-limited (default 10 minutes; DB grants use APPROVAL_GRANT_TTL_MINUTES)
- Cleaned up after use or expiry
- Stored AUTHORITATIVELY in the DB (``approval_grants`` in gaia.db) since the
  Brief 71 cutover. The filesystem plane (.claude/cache/approvals/) is the
  DEPRECATED fallback retained only for grants minted before the cutover; new
  grants are created and consumed through the DB plane (gaia.store.writer).

Security properties:
- Grants are created ONLY by the hook (not by agents)
- Nonce-activated grants are scoped to a semantic command signature
- Grants expire automatically
- The deny list (blocked_commands.py) is NEVER bypassed -- grants only
  override the dangerous verb detector
- Nonces are 128-bit random hex (cannot be guessed)
- A nonce can only be activated ONCE (DB row marked CONSUMED on activation;
  legacy pending files are deleted on activation)
- DB grants are session-AGNOSTIC by design: the block-approve-retry flow
  legitimately spans sessions, so replay protection comes from the CONSUMED
  status + TTL, not from session scoping (see the DB-backed model note below)

=============================================================================
Grant lifetime (DB-backed model -- Brief 71 cutover)
=============================================================================
The authoritative grant plane is the DB (``approval_grants`` in gaia.db), not
the filesystem files this module also maintains for the legacy fallback path.
The current model is:

1.  A SCOPE_SEMANTIC_SIGNATURE grant is created when the user approves a
    pending approval via AskUserQuestion. It carries a semantic signature
    (base command + semantic tokens + normalized flags), is **session-agnostic**
    (see check_db_semantic_grant in gaia.store.writer), and lives for
    ``APPROVAL_GRANT_TTL_MINUTES`` (60 minutes, the value reflected by
    DEFAULT_GRANT_TTL_MINUTES above).

2.  The grant is **consumed on the matching retry**, NOT at SubagentStop and
    NOT when a sub-agent ends. The first time a command whose signature matches
    the grant runs, bash_validator marks the DB row CONSUMED
    (consume_db_semantic_grant) for replay protection. Because the grant is
    session-agnostic, the consuming retry may run under a different session than
    the one that was blocked -- the block-approve-retry flow legitimately spans
    sessions (block under the subagent session, approve under the orchestrator
    session, retry under the subagent session).

3.  The semantic signature normalizes shell redirects out (``2>&1``, ``> file``)
    so a retry that only appends a redirect REUSES the existing grant rather
    than minting a new approval_id (the double-approval fix). Identity-bearing
    tokens -- including the ``-C <path>`` working directory -- still bind, so a
    genuinely different operation does NOT match the same grant.

Operators who want one consent to cover a batch of related commands should
use the COMMAND_SET grant mechanism (see ``create_command_set_grant()``).
Each command in the set is approved explicitly by the user and consumed
individually.  The legacy verb_family path has been removed.

NOTE: the filesystem helpers below (write_pending_approval, the
grant-{session}-*.json scanners, consume_session_grants) are the DEPRECATED
fallback plane retained for grants created before the DB cutover. The active
flow runs through the DB plane in gaia.store.writer.
"""

import json
import logging
import os
import re
import secrets
import subprocess
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.paths import find_claude_dir, get_plugin_data_dir
from ..core.state import get_session_id
from .approval_scopes import (
    ApprovalSignature,
    SCOPE_FILE_PATH,
    SCOPE_SEMANTIC_SIGNATURE,
    SUPPORTED_SCOPE_TYPES,
    build_approval_signature,
    build_file_path_signature,
    matches_approval_signature,
    matches_file_path_approval,
)

logger = logging.getLogger(__name__)


def _grant_ttl_minutes() -> int:
    """Resolve the active-grant TTL default from gaia.store.writer.

    The single source of truth for the GRANT lifetime is
    gaia.store.writer.APPROVAL_GRANT_TTL_MINUTES (Brief 71, Change 3a) -- the
    dependency leaf both the DB grant plane and this filesystem plane import
    without a circular import (writer never imports this module back). We resolve
    it lazily here, mirroring every other gaia.store import in this file, because
    the hooks package can be imported before the `gaia` package is on sys.path;
    a module-level import would crash hook load in that window. The 60-minute
    fallback equals the canonical value, so the two never disagree even if the
    lazy import is briefly unavailable.
    """
    try:
        from gaia.store.writer import APPROVAL_GRANT_TTL_MINUTES as _ttl
        return _ttl
    except Exception:
        return 60


# Default GRANT TTL in minutes -- the active-grant retry window. Moved 5 -> 60
# (Change 3a) so a cross-session human-in-the-loop approval does not expire
# before it is consumed; sourced from APPROVAL_GRANT_TTL_MINUTES in writer.
DEFAULT_GRANT_TTL_MINUTES = _grant_ttl_minutes()

# Default PENDING TTL in minutes (24 hours). DELIBERATELY distinct from the grant
# TTL: this is how long an UNANSWERED approval waits for the user, so the human
# can return the next day. It is NOT unified with DEFAULT_GRANT_TTL_MINUTES --
# conflating the approval-wait window with the post-approval grant window would
# be a regression. See tests/hooks/test_pending_scanner_cleanup.py::TestTTLConstants.
DEFAULT_PENDING_TTL_MINUTES = 1440

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
    """Return True if the given timestamp is older than ttl_minutes.

    A ttl_minutes of 0 means "no expiry" -- always returns False.
    """
    if ttl_minutes == 0:
        return False
    if timestamp == 0:
        return True
    elapsed_minutes = (time.time() - timestamp) / 60
    return elapsed_minutes > ttl_minutes


def _is_rejected(data: Dict[str, Any]) -> bool:
    """Return True if a pending approval has been rejected."""
    return data.get("status") == "rejected"


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
        multi_use: When True, the grant is NOT consumed after a single use.
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
        if _is_rejected(data):
            continue

        nonce = data.get("nonce")
        timestamp = data.get("timestamp")
        if not nonce or not isinstance(timestamp, (int, float)):
            continue
        ttl_minutes = data.get("ttl_minutes", DEFAULT_PENDING_TTL_MINUTES)
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


# Regex for extracting a nonce from an AskUserQuestion approve label.
# Only matches labels that start with "Approve" and contain [P-<hex>].
_APPROVE_NONCE_RE = re.compile(r"^Approve\b.*\[P-([a-f0-9]+)\]")


def extract_nonce_from_label(label: str) -> Optional[str]:
    """Extract the nonce from an AskUserQuestion option label.

    Approve labels may contain a ``[P-<hex>]`` tag that identifies the
    pending approval to activate.  Reject labels never carry a nonce,
    even if one is superficially present in the text.

    Args:
        label: The option label string (e.g. ``"Approve -- git push origin main [P-e68be5b8]"``).

    Returns:
        The hex nonce string if found in an Approve label, otherwise ``None``.
    """
    m = _APPROVE_NONCE_RE.search(label)
    return m.group(1) if m else None


def load_pending_by_nonce_prefix(prefix: str) -> Optional[Dict[str, Any]]:
    """Load a pending approval file whose nonce starts with the given prefix.

    The ``[P-<hex>]`` tag in AskUserQuestion labels carries the first 8
    characters of the full 32-character nonce.  This function scans the
    grants directory for a matching ``pending-{nonce}.json`` file and
    returns its parsed contents.

    If multiple files match (extremely unlikely with 8 hex chars), the
    most recent one (by timestamp) is returned.

    Args:
        prefix: Hex prefix extracted from a ``[P-xxx]`` label (typically 8 chars).

    Returns:
        The parsed pending approval dict, or ``None`` if no match was found.
    """
    try:
        grants_dir = _get_grants_dir()
        candidates: List[Dict[str, Any]] = []

        for pending_file in grants_dir.glob("pending-*.json"):
            if pending_file.name.startswith("pending-index-"):
                continue
            # Extract nonce from filename: pending-{nonce}.json
            fname_nonce = pending_file.stem.removeprefix("pending-")
            if not fname_nonce.startswith(prefix):
                continue
            data = _read_json_file(pending_file)
            if data and not _is_rejected(data):
                candidates.append(data)

        if not candidates:
            logger.info("No pending approval found for nonce prefix %s", prefix)
            return None

        # Return newest by timestamp
        candidates.sort(key=lambda d: d.get("timestamp", 0), reverse=True)
        logger.info(
            "Found pending approval for nonce prefix %s: full_nonce=%s",
            prefix, candidates[0].get("nonce", "?")[:12],
        )
        return candidates[0]

    except Exception as e:
        logger.error("Error loading pending by nonce prefix %s: %s", prefix, e)
        return None


# ------------------------------------------------------------------ #
# Environment snapshot capture
# ------------------------------------------------------------------ #

# CLI families whose environment state is worth capturing at blocking time.
_GIT_CMD_PATTERN = re.compile(r"\bgit\b")

_ENV_SNAPSHOT_TIMEOUT_SECONDS = 2


def _run_git_query(args: List[str], cwd: Optional[str] = None) -> Optional[str]:
    """Run a git sub-command and return stripped stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            timeout=_ENV_SNAPSHOT_TIMEOUT_SECONDS,
            cwd=cwd,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def capture_environment_snapshot(
    command: str,
    cwd: Optional[str] = None,
) -> Dict[str, Any]:
    """Capture relevant environment state at the time a command is blocked.

    Designed to be fast (<2 s) and failure-tolerant -- a failed capture
    returns an empty dict and MUST NOT prevent the pending file from being
    written.

    Currently supports:
    - **git** commands: local HEAD, remote HEAD (origin/main), current branch.

    Extensible to kubectl, terraform, etc. in future iterations.

    Args:
        command: The blocked command string.
        cwd: Working directory context (used for git queries).

    Returns:
        A dict with captured state, or ``{}`` if nothing could be captured
        or the command class is not yet supported.
    """
    if not _GIT_CMD_PATTERN.search(command):
        return {}

    try:
        snapshot: Dict[str, Any] = {"command_class": "git"}

        head = _run_git_query(["rev-parse", "HEAD"], cwd=cwd)
        if head:
            snapshot["local_head"] = head

        branch = _run_git_query(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
        if branch:
            snapshot["branch"] = branch

        remote_head = _run_git_query(
            ["rev-parse", "origin/main"], cwd=cwd,
        )
        if remote_head:
            snapshot["remote_head"] = remote_head

        return snapshot

    except Exception as exc:
        logger.debug("Environment snapshot capture failed: %s", exc)
        return {}


def write_pending_approval(
    nonce: str,
    command: str,
    danger_verb: str,
    danger_category: str,
    session_id: Optional[str] = None,
    ttl_minutes: int = DEFAULT_PENDING_TTL_MINUTES,
    context: Optional[Dict[str, Any]] = None,
    cwd: Optional[str] = None,
    environment: Optional[Dict[str, Any]] = None,
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
        ttl_minutes: How long the pending approval is valid before expiry
            (0 = no expiry).
        context: Optional dict with enriched context (source, description,
            risk, rollback, branch, files_changed, etc.).
        cwd: Optional working directory where the command was invoked.
        environment: Optional dict with environment state at blocking time.
            If not provided, auto-captured via capture_environment_snapshot().

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

    # Auto-capture environment if not explicitly provided.
    if environment is None:
        try:
            environment = capture_environment_snapshot(command, cwd=cwd)
        except Exception as exc:
            logger.debug("Auto environment capture failed (non-fatal): %s", exc)
            environment = {}

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
        "context": context or {},
        "environment": environment,
    }
    if cwd is not None:
        pending_data["cwd"] = cwd

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
        pending_ttl = pending_data.get("ttl_minutes", DEFAULT_PENDING_TTL_MINUTES)
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
        if signature.scope_type not in (SCOPE_SEMANTIC_SIGNATURE, SCOPE_FILE_PATH):
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

        # For file-path scopes, verb validation is not applicable.
        if signature.scope_type == SCOPE_FILE_PATH:
            verbs = ["write"]
        elif not signature.verb and not danger_verb:
            logger.warning(
                "Could not validate semantic signature for pending approval command: %s",
                command,
            )
            return ApprovalActivationResult(
                success=False,
                status=ACTIVATION_INVALID_SIGNATURE,
                reason="Approval signature could not be validated safely.",
            )
        else:
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

        grant_file = grants_dir / f"grant-{session_id}-{int(time.time() * 1000)}-{nonce[:8]}.json"
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

def activate_cross_session_pending(
    pending_data: dict,
    ttl_minutes: int = DEFAULT_GRANT_TTL_MINUTES,
    session_id: Optional[str] = None,
) -> ApprovalActivationResult:
    """Create an active grant from a pending file that belongs to a prior session.

    Called ONLY when the user has already confirmed approval via AskUserQuestion.
    Unlike activate_pending_approval(), this function skips the session_id equality
    check because the pending file is from a previous session whose nonce can never
    match the current session.  All other validation (nonce presence, TTL, signature)
    is performed normally.

    The new grant is created under the CURRENT session ID so that
    check_approval_grant() can find it when the dispatched agent runs the command.
    confirmed is set to True directly because the human has already approved.

    Args:
        pending_data: The dict loaded from a pending-{nonce}.json file.
        ttl_minutes: TTL for the active grant (default DEFAULT_GRANT_TTL_MINUTES).
        session_id: Optional explicit session ID to use for the new grant.  When
            provided this value is used directly, which avoids relying on the
            CLAUDE_SESSION_ID environment variable -- important when the function
            is called from a dispatched agent's subprocess where the env var may
            not be set.  Defaults to None, which falls back to _get_session_id()
            (backward compatible).

    Returns:
        Structured activation result with status and optional grant path.
    """
    current_session_id = session_id if session_id is not None else _get_session_id()

    try:
        grants_dir = _get_grants_dir()

        # Validate required fields
        nonce = pending_data.get("nonce")
        if not nonce:
            return ApprovalActivationResult(
                success=False,
                status=ACTIVATION_INVALID_PENDING,
                reason="Pending approval file is missing a nonce.",
            )

        pending_file = grants_dir / f"pending-{nonce}.json"

        # Validate not expired (TTL check still applies)
        pending_timestamp = pending_data.get("timestamp", 0)
        pending_ttl = pending_data.get("ttl_minutes", DEFAULT_PENDING_TTL_MINUTES)
        if _is_ttl_expired(pending_timestamp, pending_ttl):
            logger.warning(
                "Cross-session pending approval expired for nonce %s: TTL=%d min",
                nonce, pending_ttl,
            )
            _cleanup_grant(pending_file)
            prior_session_id = pending_data.get("session_id", "unknown")
            _rebuild_pending_index(prior_session_id)
            return ApprovalActivationResult(
                success=False,
                status=ACTIVATION_EXPIRED,
                reason="Approval nonce expired before cross-session activation.",
            )

        command = pending_data.get("command", "")
        danger_verb = pending_data.get("danger_verb", "")
        scope_signature_data = pending_data.get("scope_signature")
        if not scope_signature_data:
            logger.warning(
                "Cross-session pending approval for nonce %s is missing scope_signature",
                nonce,
            )
            _cleanup_grant(pending_file)
            prior_session_id = pending_data.get("session_id", "unknown")
            _rebuild_pending_index(prior_session_id)
            return ApprovalActivationResult(
                success=False,
                status=ACTIVATION_INVALID_PENDING,
                reason="Pending approval file is missing a semantic signature.",
            )

        signature = ApprovalSignature.from_dict(scope_signature_data)
        if signature.scope_type not in (SCOPE_SEMANTIC_SIGNATURE, SCOPE_FILE_PATH):
            logger.warning(
                "Cross-session pending for nonce %s has unsupported scope_type=%s",
                nonce,
                signature.scope_type,
            )
            _cleanup_grant(pending_file)
            prior_session_id = pending_data.get("session_id", "unknown")
            _rebuild_pending_index(prior_session_id)
            return ApprovalActivationResult(
                success=False,
                status=ACTIVATION_INVALID_SIGNATURE,
                reason="Pending approval uses an unsupported scope type.",
            )

        # For file-path scopes, verb validation is not applicable.
        if signature.scope_type == SCOPE_FILE_PATH:
            verbs = ["write"]
        elif not signature.verb and not danger_verb:
            logger.warning(
                "Could not validate semantic signature for cross-session command: %s",
                command,
            )
            return ApprovalActivationResult(
                success=False,
                status=ACTIVATION_INVALID_SIGNATURE,
                reason="Approval signature could not be validated safely.",
            )
        else:
            verbs = [signature.verb] if signature.verb else ([danger_verb.lower()] if danger_verb else [])

        # Create active grant under the CURRENT session; confirmed=True because
        # the human already approved via AskUserQuestion.
        grant = ApprovalGrant(
            session_id=current_session_id,
            approved_verbs=verbs,
            approved_scope=command,
            scope_type=signature.scope_type,
            scope_signature=signature.to_dict(),
            granted_at=time.time(),
            ttl_minutes=ttl_minutes,
            confirmed=True,
        )

        grant_file = grants_dir / f"grant-{current_session_id}-{int(time.time() * 1000)}-{nonce[:8]}.json"
        grant_file.write_text(json.dumps(asdict(grant), indent=2))

        # Delete the old pending file (one-time activation)
        _cleanup_grant(pending_file)
        prior_session_id = pending_data.get("session_id", "unknown")
        _rebuild_pending_index(prior_session_id)

        logger.info(
            "Cross-session pending activated: nonce=%s, prior_session=%s, "
            "current_session=%s, verbs=%s, grant=%s",
            nonce, prior_session_id, current_session_id, verbs, grant_file.name,
        )
        return ApprovalActivationResult(
            success=True,
            status=ACTIVATION_ACTIVATED,
            reason="Cross-session pending approval activated.",
            grant_path=grant_file,
        )

    except (json.JSONDecodeError, TypeError) as e:
        logger.error("Invalid pending approval data for cross-session activation: %s", e)
        return ApprovalActivationResult(
            success=False,
            status=ACTIVATION_INVALID_PENDING,
            reason="Pending approval data is invalid or corrupt.",
        )
    except Exception as e:
        logger.error("Failed to activate cross-session pending approval: %s", e)
        return ApprovalActivationResult(
            success=False,
            status=ACTIVATION_ERROR,
            reason="Unexpected error while activating cross-session approval.",
        )


def check_approval_grant(command: str, session_id: str = None) -> Optional[ApprovalGrant]:
    """Check if there is an active approval grant for a command.

    Called by the bash_validator before blocking a dangerous command.
    If a valid grant exists that matches the command, the command should
    be allowed through.

    Primary path (DB): check_db_semantic_grant() in gaia.store.writer is
    consulted first.  When a DB row is found it is wrapped as an ApprovalGrant
    with confirmed=True so downstream consumers see the same interface.

    Fallback path (filesystem): the legacy grant-{session}-*.json files are
    scanned when no DB row is found.  This path is DEPRECATED -- it remains
    for backward compatibility with grants created before the DB cutover and
    will be removed in a future migration.

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

    # ------------------------------------------------------------------ #
    # DB-primary path (Brief 71 CHECK-side cutover)
    # ------------------------------------------------------------------ #
    try:
        from gaia.store.writer import check_db_semantic_grant
        db_row = check_db_semantic_grant(command, session_id=session_id)
        if db_row is not None:
            # Reconstruct an ApprovalGrant from DB row so callers see the
            # same interface.  The row stores the scope_signature in
            # command_set_json under the key 'scope_signature'.
            import json as _j
            row_data = _j.loads(db_row.get("command_set_json") or "{}")
            sig_dict = row_data.get("scope_signature")
            grant = ApprovalGrant(
                session_id=db_row.get("session_id", session_id),
                approved_verbs=[],
                approved_scope=row_data.get("command", command),
                scope_type=SCOPE_SEMANTIC_SIGNATURE,
                scope_signature=sig_dict,
                granted_at=0.0,  # TTL enforced by DB expires_at; not re-checked here
                ttl_minutes=0,   # 0 = no TTL (already filtered by check_db_semantic_grant)
                used=False,
                confirmed=True,  # DB grants are always user-approved
                multi_use=False,
            )
            # Attach the approval_id so bash_validator can consume it.
            grant._db_approval_id = db_row.get("approval_id")
            logger.info(
                "Approval grant matched (DB path): command='%s', approval_id=%s",
                command[:80], (db_row.get("approval_id") or "?")[:16],
            )
            return grant
    except Exception as _db_err:
        logger.debug(
            "check_approval_grant: DB path unavailable (%s), falling through to filesystem",
            _db_err,
        )

    # ------------------------------------------------------------------ #
    # DEPRECATED filesystem fallback
    # Retained for grants created before the DB cutover.
    #
    # Security guard: before returning a filesystem grant, verify that
    # the DB does NOT already have a CONSUMED grant for this command.
    # If the DB shows the grant was consumed (e.g. by bash_validator in a
    # prior call), the filesystem grant must NOT be returned -- it is a
    # stale copy that would bypass replay protection.
    #
    # This guard now delegates to the consolidated, session-agnostic
    # _consumed_grant_exists() in gaia.store.writer (Brief 71, Change 4). The
    # previous inline copy here was session-locked (`AND session_id=?`), which
    # reintroduced the cross-session bug: a grant CONSUMED under one session went
    # unseen by a retry in another, letting a stale filesystem copy bypass replay
    # protection. The single helper is queried session-agnostic, so a consumed
    # command stays consumed across every session.
    # ------------------------------------------------------------------ #

    # Check DB for a CONSUMED grant matching this command (replay guard).
    try:
        import gaia.store.writer as _sw
        _con = _sw._connect()
        try:
            if _sw._consumed_grant_exists(command, _con):
                logger.info(
                    "Filesystem fallback suppressed: DB shows grant already "
                    "CONSUMED for command='%s'", command[:80],
                )
                return None
        finally:
            _con.close()
    except Exception:
        pass

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
                        "Approval grant matched (filesystem fallback): "
                        "command='%s', scope='%s', type=%s",
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


def consume_session_grants(session_id: str = None) -> int:
    """Consume confirmed grants on the LEGACY FILESYSTEM plane for a session.

    Called at SubagentStop. Scope is the deprecated FS plane ONLY: it sweeps
    ``grant-{session_id}-*.json`` files under the approvals cache dir and marks
    confirmed ones used (multi-use grants too, since the session is over).

    This is a NO-OP for grants on the authoritative DB plane (post Brief 71):
    DB semantic grants are consumed on the MATCHING RETRY via
    ``consume_db_semantic_grant`` (see the module docstring, "DB-backed model"),
    NOT at SubagentStop. There is therefore no DB cleanup gap here -- DB replay
    protection is handled at consume-on-retry time, and this function
    intentionally does not (and must not) touch the DB plane. It remains live
    only to drain pre-cutover FS grants; new sessions that never write an FS
    grant simply get a return value of 0.

    Args:
        session_id: Session ID to scope consumption (defaults to env var).

    Returns:
        Number of legacy FS grants consumed (0 when no FS grants exist).
    """
    if not session_id:
        session_id = _get_session_id()

    consumed_count = 0
    try:
        grants_dir = _get_grants_dir()
        if not grants_dir.exists():
            return 0

        for grant_file in sorted(grants_dir.glob(f"grant-{session_id}-*.json")):
            try:
                data = json.loads(grant_file.read_text())
                grant = ApprovalGrant(**data)

                if grant.used:
                    continue  # already consumed

                if not grant.is_valid():
                    if grant.is_expired():
                        _cleanup_grant(grant_file)
                    continue

                # Consume all confirmed grants (single-use and multi-use)
                if grant.confirmed:
                    data["used"] = True
                    grant_file.write_text(json.dumps(data, indent=2))
                    consumed_count += 1
                    logger.info(
                        "Grant consumed at SubagentStop: grant=%s, multi_use=%s",
                        grant_file.name, grant.multi_use,
                    )

            except (json.JSONDecodeError, TypeError):
                continue

    except Exception as e:
        logger.error("Error consuming session grants: %s", e)

    return consumed_count


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


def cleanup_expired_grants(force: bool = False) -> int:
    """Remove expired grant, pending, and stale pending-index files.

    Called periodically (e.g., at hook startup) to prevent accumulation.
    Throttled to run at most once every ``_CLEANUP_INTERVAL_SECONDS`` --
    callers that need to bypass the throttle (e.g., SessionStart, manual
    CLI flush) can pass ``force=True``.

    Args:
        force: When True, run cleanup regardless of the throttle. The
            throttle exists to keep pre_tool_use cheap on bursty traffic;
            session-lifecycle callers should bypass it so users do not
            wait up to 60s for the first sweep of the session.

    Returns:
        Number of files cleaned up (grants + pending + index files).
    """
    global _last_cleanup_time
    now = time.time()
    if not force and now - _last_cleanup_time < _CLEANUP_INTERVAL_SECONDS:
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
                if _is_rejected(data):
                    _cleanup_grant(pending_file)
                    if session_id:
                        sessions_to_rebuild.add(session_id)
                    cleaned += 1
                    continue
                timestamp = data.get("timestamp", 0)
                ttl = data.get("ttl_minutes", DEFAULT_PENDING_TTL_MINUTES)
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

        # Sweep orphan pending-index files. An index entry is orphan when
        # its pending_file no longer exists on disk; an index file is orphan
        # when none of its entries point to live pending files. Corrupt /
        # unreadable index files are also removed -- the next write_pending
        # call rebuilds the index from authoritative pending-{nonce}.json
        # files, so there is no data loss risk.
        for index_file in grants_dir.glob("pending-index-*.json"):
            try:
                data = _read_json_file(index_file)
                if not data:
                    index_file.unlink(missing_ok=True)
                    cleaned += 1
                    logger.info(
                        "cleanup_expired: removed corrupt index %s",
                        index_file.name,
                    )
                    continue
                entries = data.get("entries") or []
                valid_entries = [
                    e for e in entries
                    if isinstance(e, dict)
                    and (grants_dir / e.get("pending_file", "")).exists()
                ]
                if not valid_entries:
                    index_file.unlink(missing_ok=True)
                    cleaned += 1
                    logger.info(
                        "cleanup_expired: removed orphan index %s "
                        "(0/%d entries point to live pendings)",
                        index_file.name,
                        len(entries),
                    )
            except Exception as exc:
                logger.debug(
                    "Index sweep failed for %s (non-fatal): %s",
                    index_file.name, exc,
                )

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
            if _is_rejected(data):
                continue
            timestamp = data.get("timestamp", 0)
            ttl = data.get("ttl_minutes", DEFAULT_PENDING_TTL_MINUTES)
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


def reject_pending(nonce_prefix: str) -> bool:
    """Mark a pending approval as rejected without deleting the file.

    Finds the pending file whose nonce starts with ``nonce_prefix``, sets
    ``status`` to ``"rejected"`` and ``rejected_at`` to the current time,
    writes the file back, and rebuilds the session index.

    Rejected pendings are invisible to all readers (``_is_rejected`` filter)
    and are cleaned up by the pending scanner on its next sweep.

    Args:
        nonce_prefix: Hex prefix of the nonce (typically 8 chars from ``[P-xxx]``).

    Returns:
        True if a matching pending was found and rejected, False otherwise.
    """
    try:
        grants_dir = _get_grants_dir()
        for pending_file in grants_dir.glob("pending-*.json"):
            if pending_file.name.startswith("pending-index-"):
                continue
            fname_nonce = pending_file.stem.removeprefix("pending-")
            if not fname_nonce.startswith(nonce_prefix):
                continue
            data = _read_json_file(pending_file)
            if not data or _is_rejected(data):
                continue
            data["status"] = "rejected"
            data["rejected_at"] = time.time()
            pending_file.write_text(json.dumps(data, indent=2))
            session_id = data.get("session_id")
            if session_id:
                _rebuild_pending_index(session_id)
            logger.info(
                "Pending approval rejected: nonce_prefix=%s, nonce=%s",
                nonce_prefix, data.get("nonce", "?"),
            )
            return True
    except Exception as e:
        logger.error("Error rejecting pending approval for prefix %s: %s", nonce_prefix, e)
    return False


def write_pending_approval_for_file(
    nonce: str,
    file_path: str,
    session_id: Optional[str] = None,
    ttl_minutes: int = DEFAULT_PENDING_TTL_MINUTES,
    context: Optional[Dict[str, Any]] = None,
) -> Optional[Path]:
    """Write a pending approval file when a Write/Edit to a protected path is blocked.

    Analogous to write_pending_approval() but uses SCOPE_FILE_PATH so that
    the file path (not a shell command) is the scope identifier.

    Args:
        nonce: Cryptographic nonce from generate_nonce().
        file_path: The absolute path of the file being written/edited.
        session_id: Session ID (defaults to CLAUDE_SESSION_ID env var).
        ttl_minutes: How long the pending approval is valid before expiry
            (0 = no expiry).
        context: Optional dict with enriched context (source, description,
            risk, rollback, branch, files_changed, etc.).

    Returns:
        Path to the pending file, or None on failure.
    """
    if session_id is None:
        session_id = _get_session_id()

    signature = build_file_path_signature(file_path)
    if signature is None:
        logger.error(
            "Failed to build file-path approval signature for pending file: %s",
            file_path,
        )
        return None

    pending_data = {
        "nonce": nonce,
        "session_id": session_id,
        "command": file_path,
        "danger_verb": "write",
        "danger_category": "FILE_WRITE",
        "scope_type": signature.scope_type,
        "scope_signature": signature.to_dict(),
        "timestamp": time.time(),
        "ttl_minutes": ttl_minutes,
        "context": context or {},
    }

    try:
        grants_dir = _get_grants_dir()
        pending_file = grants_dir / f"pending-{nonce}.json"
        pending_file.write_text(json.dumps(pending_data, indent=2))
        _rebuild_pending_index(session_id)

        logger.info(
            "Pending file-path approval written: nonce=%s, file=%s, session=%s",
            nonce, file_path, session_id,
        )
        return pending_file

    except Exception as e:
        logger.error("Failed to write pending file-path approval: %s", e)
        return None


def check_approval_grant_for_file(
    file_path: str,
    session_id: str = None,
) -> Optional[ApprovalGrant]:
    """Check if there is an active approval grant for a Write/Edit file path.

    Called by _adapt_write_edit before blocking a protected-path write. If
    a valid SCOPE_FILE_PATH grant exists for this path, the write should be
    allowed through.

    Args:
        file_path: The file path being written/edited.
        session_id: Session ID for grant scoping (defaults to env var).

    Returns:
        The matching ApprovalGrant if found and valid, None otherwise.
    """
    if not session_id:
        session_id = _get_session_id()

    try:
        grants_dir = _get_grants_dir()
        if not grants_dir.exists():
            return None

        for grant_file in sorted(grants_dir.glob(f"grant-{session_id}-*.json")):
            try:
                data = json.loads(grant_file.read_text())
                grant = ApprovalGrant(**data)

                if not grant.is_valid():
                    if grant.is_expired():
                        _cleanup_grant(grant_file)
                    continue

                signature = grant.get_signature()
                if signature is None or signature.scope_type != SCOPE_FILE_PATH:
                    continue

                if matches_file_path_approval(signature, file_path):
                    logger.info(
                        "File-path approval grant matched: file='%s', grant=%s",
                        file_path, grant_file.name,
                    )
                    return grant

            except (json.JSONDecodeError, TypeError) as e:
                logger.warning("Invalid grant file %s: %s", grant_file, e)
                _cleanup_grant(grant_file)
                continue

    except Exception as e:
        logger.error("Error checking file-path approval grants: %s", e)

    return None


def find_pending_for_file(
    session_id: str,
    file_path: str,
) -> Optional[str]:
    """Find an existing pending approval nonce for this file path and session.

    When a subagent retries a blocked Write/Edit, a pending approval may
    already exist from the first attempt.  Reusing the existing nonce
    prevents generating a new approval_id on every retry while the user
    reviews the first one.

    Args:
        session_id: Session to search.
        file_path: The file path to match against pending approvals.

    Returns:
        The nonce (approval_id) if a matching pending approval exists, else None.
    """
    pending_list = get_pending_approvals_for_session(session_id)
    if not pending_list:
        return None

    stripped = file_path.strip() if file_path else ""
    for pending_data in pending_list:
        pending_sig_data = pending_data.get("scope_signature")
        if not pending_sig_data:
            continue
        try:
            pending_sig = ApprovalSignature.from_dict(pending_sig_data)
            if matches_file_path_approval(pending_sig, stripped):
                nonce = pending_data.get("nonce")
                if nonce:
                    logger.info(
                        "Reusing existing pending file-path approval nonce=%s for file: %s",
                        nonce, file_path,
                    )
                    return nonce
        except Exception:
            continue

    return None


def activate_db_pending_by_prefix(
    nonce_prefix: str,
    current_session_id: Optional[str] = None,
    ttl_minutes: int = DEFAULT_GRANT_TTL_MINUTES,
) -> ApprovalActivationResult:
    """Activate a DB-stored pending approval by its nonce prefix.

    Called when ``load_pending_by_nonce_prefix()`` returns None (because M2
    migrated REQUESTED writes to DB only -- no filesystem pending file is
    written any more).  This function bridges the gap:

      1. Looks up the approval row in the DB using ``id LIKE 'P-<prefix>%'``
         with ``status='pending'``.
      2. Writes SHOWN + APPROVED events via ``gaia.approvals.store``.
      3. Creates a filesystem grant file so that ``check_approval_grant()``
         (which still reads the filesystem) can find it on the subagent retry.

    Cross-session semantics: the DB approval was created under the subagent's
    session.  The filesystem grant is created under ``current_session_id`` so
    that the re-dispatched subagent (which shares or sees the same session)
    finds the grant file.

    Args:
        nonce_prefix: First 8 hex chars extracted from the ``[P-xxx]`` label
            in the AskUserQuestion answer.
        current_session_id: Session doing the activation (orchestrator or
            resumed subagent).  Defaults to ``_get_session_id()``.
        ttl_minutes: TTL for the created filesystem grant.

    Returns:
        ``ApprovalActivationResult`` with success=True and a grant_path when
        the activation succeeded; success=False otherwise.
    """
    if current_session_id is None:
        current_session_id = _get_session_id()

    try:
        # Step 1: Find the DB pending approval by prefix.
        from gaia.approvals.store import get_pending, record_event, approve, get_by_id
        import json as _json

        # Query all pending approvals and match by prefix (cross-session --
        # use all_sessions=True because the approval was created by the
        # subagent whose session may differ from the orchestrator's).
        all_pending = get_pending(all_sessions=True)
        matched_row = None
        for row in all_pending:
            row_id = row.get("id", "")
            # approval_id format: P-{uuid4_hex} -- prefix follows "P-"
            if row_id.startswith(f"P-{nonce_prefix}"):
                matched_row = row
                break

        if matched_row is None:
            logger.info(
                "activate_db_pending_by_prefix: no DB pending found for prefix %s",
                nonce_prefix,
            )
            return ApprovalActivationResult(
                success=False,
                status=ACTIVATION_NOT_FOUND,
                reason=f"No DB pending approval found for nonce prefix {nonce_prefix!r}.",
            )

        approval_id = matched_row["id"]
        payload_json_str = matched_row.get("payload_json")
        originating_session = matched_row.get("session_id", "")
        agent_id = matched_row.get("agent_id")

        # Step 2: Parse payload to get the exact command.
        if not payload_json_str:
            logger.warning(
                "activate_db_pending_by_prefix: approval %s has no payload_json",
                approval_id,
            )
            return ApprovalActivationResult(
                success=False,
                status=ACTIVATION_INVALID_PENDING,
                reason="DB pending approval is missing payload_json.",
            )

        try:
            payload = _json.loads(payload_json_str)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "activate_db_pending_by_prefix: could not parse payload_json for %s: %s",
                approval_id, exc,
            )
            return ApprovalActivationResult(
                success=False,
                status=ACTIVATION_INVALID_PENDING,
                reason="DB pending approval has invalid payload_json.",
            )

        # Multi-command (COMMAND_SET) detection. A payload carrying a
        # ``command_set`` list of more than one {command, rationale} item is a
        # batch the user approved under ONE consent. It must NOT be degraded to
        # a single command (the historic bug at this site) -- it activates into
        # a COMMAND_SET grant via the dedicated branch below. A set of length
        # <= 1 falls through to the singular SCOPE_SEMANTIC_SIGNATURE path so we
        # never mint a COMMAND_SET grant for one command.
        raw_command_set = payload.get("command_set")
        command_set_items: list = []
        if isinstance(raw_command_set, list):
            for _item in raw_command_set:
                if isinstance(_item, dict) and _item.get("command"):
                    command_set_items.append(
                        {
                            "command": _item["command"],
                            "rationale": _item.get("rationale", ""),
                        }
                    )
        is_command_set = len(command_set_items) > 1

        command = payload.get("exact_content") or payload.get("commands", [None])[0] or ""
        if is_command_set and not command:
            # For a command_set the first item is a safe stand-in for the
            # singular display/signature path; the set itself is authoritative.
            command = command_set_items[0]["command"]
        if not command:
            logger.warning(
                "activate_db_pending_by_prefix: no command found in payload for %s",
                approval_id,
            )
            return ApprovalActivationResult(
                success=False,
                status=ACTIVATION_INVALID_PENDING,
                reason="Could not extract command from DB pending approval payload.",
            )

        # Step 3: Write SHOWN + APPROVED events and flip status in DB.
        try:
            record_event(
                approval_id,
                "SHOWN",
                agent_id=agent_id,
                session_id=current_session_id,
            )
            approve(
                approval_id,
                approver_session=current_session_id,
                agent_id=agent_id,
            )
            logger.info(
                "activate_db_pending_by_prefix: DB transition complete for %s "
                "(SHOWN + APPROVED, status=approved)",
                approval_id,
            )
        except ValueError as ve:
            # transition() raises ValueError when status != 'pending' (e.g. already approved).
            logger.warning(
                "activate_db_pending_by_prefix: DB transition failed for %s: %s "
                "(approval may have been processed already)",
                approval_id, ve,
            )
            # If the approval is already approved, we can still create the
            # filesystem grant if it doesn't exist yet -- don't abort.
            current_row = get_by_id(approval_id)
            if current_row and current_row.get("status") != "approved":
                return ApprovalActivationResult(
                    success=False,
                    status=ACTIVATION_ERROR,
                    reason=f"DB transition failed: {ve}",
                )

        # Step 3b: COMMAND_SET branch. When the approved payload carries a set
        # of more than one command, create ONE COMMAND_SET grant covering the
        # whole batch instead of a singular SCOPE_SEMANTIC_SIGNATURE grant. The
        # set is consumed item-by-item (byte-for-byte) by bash_validator's
        # match_command_set_grant / mark_command_set_item_consumed path -- the
        # consume side is unchanged; this is the create side that was orphaned.
        #
        # Precondition: ``command_set`` in the payload is already pre-filtered to
        # mutative commands by ``_intake_command_set_pending`` (handoff_persister,
        # the only producer of these pending records in production). Activation
        # therefore assumes every item is consumable and does NOT re-filter here;
        # do not add a filtering step at this site -- it would silently drop items
        # the user already consented to under one grant.
        if is_command_set:
            created = create_command_set_grant(
                command_set_items,
                approval_id,
                session_id=current_session_id,
                agent_id=agent_id,
                ttl_minutes=DEFAULT_COMMAND_SET_TTL_MINUTES,
            )
            if not created:
                logger.error(
                    "activate_db_pending_by_prefix: COMMAND_SET grant creation "
                    "failed for approval_id=%s (items=%d)",
                    approval_id[:16], len(command_set_items),
                )
                return ApprovalActivationResult(
                    success=False,
                    status=ACTIVATION_ERROR,
                    reason="Failed to create COMMAND_SET grant from approved payload.",
                )
            logger.info(
                "activate_db_pending_by_prefix: COMMAND_SET grant created: "
                "approval_id=%s, items=%d, ttl=%d min, originating_session=%s, "
                "current_session=%s",
                approval_id[:16], len(command_set_items),
                DEFAULT_COMMAND_SET_TTL_MINUTES,
                (originating_session or "")[:12],
                current_session_id[:12],
            )
            return ApprovalActivationResult(
                success=True,
                status=ACTIVATION_ACTIVATED,
                reason=(
                    "DB pending approval activated as a COMMAND_SET grant "
                    f"({len(command_set_items)} commands under one consent)."
                ),
                grant_path=None,
            )

        # Step 4: Rebuild approval signature from the command so the
        # filesystem grant has a valid scope_signature for check_approval_grant().
        from .approval_scopes import build_approval_signature, SCOPE_SEMANTIC_SIGNATURE

        # Extract verb from payload for signature building.
        operation_str = payload.get("operation", "")
        danger_verb = ""
        danger_category = "MUTATIVE"
        # The operation field is typically "{CATEGORY} command intercepted: {verb}"
        if "intercepted:" in operation_str:
            parts = operation_str.split("intercepted:")
            if len(parts) == 2:
                left = parts[0].strip()
                danger_verb = parts[1].strip()
                danger_category = left.split()[0] if left.split() else "MUTATIVE"

        signature = build_approval_signature(
            command,
            scope_type=SCOPE_SEMANTIC_SIGNATURE,
            danger_verb=danger_verb,
            danger_category=danger_category,
        )
        if signature is None:
            logger.warning(
                "activate_db_pending_by_prefix: could not build signature for "
                "command='%s' -- using command string as fallback verb",
                command[:80],
            )
            # Fallback: build a minimal signature using the first token as verb.
            first_token = command.split()[0] if command.strip() else "unknown"
            signature = build_approval_signature(
                command,
                scope_type=SCOPE_SEMANTIC_SIGNATURE,
                danger_verb=first_token,
                danger_category=danger_category,
            )
        if signature is None:
            return ApprovalActivationResult(
                success=False,
                status=ACTIVATION_INVALID_SIGNATURE,
                reason="Could not build approval signature for DB-pending command.",
            )

        verbs = [signature.verb] if signature.verb else ([danger_verb.lower()] if danger_verb else ["write"])

        # Step 5a: Insert a SCOPE_SEMANTIC_SIGNATURE row into approval_grants DB.
        # This is the DB-primary path (CHECK-side cutover, Brief 71 FASE 2).
        # The row is keyed by approval_id so check_db_semantic_grant() can find it
        # cross-session without relying on filesystem files.
        db_grant_inserted = False
        try:
            from gaia.store.writer import insert_semantic_grant
            result_sg = insert_semantic_grant(
                approval_id=approval_id,
                command=command,
                scope_signature=signature.to_dict(),
                agent_id=agent_id,
                session_id=current_session_id,
                ttl_minutes=ttl_minutes,
            )
            if result_sg.get("status") == "applied":
                db_grant_inserted = True
                logger.info(
                    "activate_db_pending_by_prefix: DB semantic grant inserted: "
                    "approval_id=%s, session=%s",
                    approval_id[:16], current_session_id[:12],
                )
            else:
                logger.warning(
                    "activate_db_pending_by_prefix: DB semantic grant insert failed "
                    "(non-fatal, falling back to filesystem): %s",
                    result_sg,
                )
        except Exception as _sg_err:
            logger.warning(
                "activate_db_pending_by_prefix: DB semantic grant insert error "
                "(non-fatal, falling back to filesystem): %s",
                _sg_err,
            )

        # Step 5b: Create filesystem grant under current_session_id.
        # DEPRECATED: check_approval_grant() now prefers the DB path (Step 5a).
        # The filesystem grant is retained as a fallback for any legacy consumers
        # that still read filesystem directly.  It will be removed in a future
        # migration once the DB path is stable in production.
        grant = ApprovalGrant(
            session_id=current_session_id,
            approved_verbs=verbs,
            approved_scope=command,
            scope_type=signature.scope_type,
            scope_signature=signature.to_dict(),
            granted_at=time.time(),
            ttl_minutes=ttl_minutes,
            confirmed=True,  # user already approved via AskUserQuestion
        )

        grants_dir = _get_grants_dir()
        nonce_suffix = approval_id.replace("P-", "")[:8]
        grant_file = grants_dir / (
            f"grant-{current_session_id}-{int(time.time() * 1000)}-{nonce_suffix}.json"
        )
        grant_file.write_text(json.dumps(asdict(grant), indent=2))

        logger.info(
            "activate_db_pending_by_prefix: %s grant created: "
            "approval_id=%s, prefix=%s, originating_session=%s, "
            "current_session=%s, command='%s', grant=%s",
            "DB+filesystem" if db_grant_inserted else "filesystem-only",
            approval_id[:16], nonce_prefix,
            (originating_session or "")[:12],
            current_session_id[:12],
            command[:80],
            grant_file.name,
        )
        return ApprovalActivationResult(
            success=True,
            status=ACTIVATION_ACTIVATED,
            reason=(
                "DB pending approval activated (SHOWN + APPROVED written, "
                "DB semantic grant inserted, filesystem grant created)."
                if db_grant_inserted
                else "DB pending approval activated (SHOWN + APPROVED written, filesystem grant created)."
            ),
            grant_path=grant_file,
        )

    except Exception as exc:
        logger.error(
            "activate_db_pending_by_prefix: unexpected error for prefix %s: %s",
            nonce_prefix, exc, exc_info=True,
        )
        return ApprovalActivationResult(
            success=False,
            status=ACTIVATION_ERROR,
            reason=f"Unexpected error activating DB pending: {exc}",
        )


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
# Command-Set Grant Creation and Matching (M3 / D4 / D10)
# ============================================================================
# Replaces the SCOPE_VERB_FAMILY multi-use grant design.
# A command_set grant binds an approval_id to an explicit list of commands
# (each with a rationale). Matching is byte-for-byte (D10): no whitespace
# normalization, no quote canonicalization, no shell expansion. Wrapping an
# approved command (adding cd, redirect, pipe, flag) produces a different
# string and requires fresh approval. Each item in the set is single-use.

# COMMAND_SET grant TTL in minutes. Aligned to the singular active-grant TTL
# (DEFAULT_GRANT_TTL_MINUTES / APPROVAL_GRANT_TTL_MINUTES = 60) so a batch of
# commands approved under one consent gets the same cross-session retry window
# as a single approved command -- the block-approve-retry flow legitimately
# spans sessions, and a shorter window would expire the batch before the
# subagent could consume every item.
DEFAULT_COMMAND_SET_TTL_MINUTES = 60


def create_command_set_grant(
    command_set: list,
    approval_id: str,
    *,
    session_id: str | None = None,
    agent_id: str | None = None,
    ttl_minutes: int = DEFAULT_COMMAND_SET_TTL_MINUTES,
    db_path=None,
) -> bool:
    """Create a COMMAND_SET approval grant persisted to the DB.

    Each item in ``command_set`` is a dict with ``command`` (str) and
    ``rationale`` (str).  The ``approval_id`` nonce identifies this grant;
    it is the value the user sees in the APPROVAL_REQUEST and echoes back.

    Matching at execution time is byte-for-byte (D10):
    - No whitespace normalization
    - No quote canonicalization
    - No shell expansion
    - No cd-prefix stripping

    Args:
        command_set: List of dicts [{"command": str, "rationale": str}, ...].
        approval_id: Unique nonce (32-char hex from generate_nonce()).
        session_id: CLAUDE_SESSION_ID (defaults to current session).
        agent_id: Agent identifier for audit trail.
        ttl_minutes: Grant lifetime (default 10 min). Enforced at query time.
        db_path: Optional explicit DB path override (used by tests).

    Returns:
        True if the grant was created successfully, False on error.
    """
    if not command_set or not approval_id:
        logger.error(
            "create_command_set_grant: missing required args "
            "(command_set len=%d, approval_id=%r)",
            len(command_set) if command_set else 0,
            approval_id,
        )
        return False

    if session_id is None:
        session_id = _get_session_id()

    from datetime import datetime, timezone, timedelta
    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        from gaia.store.writer import insert_approval_grant
        result = insert_approval_grant(
            approval_id=approval_id,
            command_set=command_set,
            agent_id=agent_id,
            session_id=session_id,
            scope="COMMAND_SET",
            expires_at=expires_at,
            db_path=db_path,
        )
        if result.get("status") == "applied":
            logger.info(
                "command_set grant created: approval_id=%s, items=%d, ttl=%d min",
                approval_id[:12], len(command_set), ttl_minutes,
            )
            return True
        logger.error(
            "command_set grant creation failed: %s", result.get("reason", "unknown")
        )
        return False
    except Exception as exc:
        logger.error("create_command_set_grant error: %s", exc)
        return False


def match_command_set_grant(
    retried_command: str,
    *,
    db_path=None,
) -> tuple | None:
    """Find an active COMMAND_SET grant containing ``retried_command``.

    Matching is byte-for-byte (D10): the ``command`` field of each
    command_set item is compared character-by-character against
    ``retried_command``.  No normalization of any kind is applied.

    The grant must:
    - Have scope COMMAND_SET
    - Have status PENDING (not CONSUMED, REVOKED, or EXPIRED)
    - Not be past its expires_at timestamp
    - Contain ``retried_command`` at an index that has NOT been consumed

    The lookup is SESSION-AGNOSTIC (Brief 71), exactly like the singular path
    (``check_db_semantic_grant``). The block-approve-retry flow legitimately
    spans sessions, and CLAUDE_SESSION_ID is not guaranteed to be exported into
    the bash subprocess -- where ``get_session_id()`` falls back to the literal
    ``"default"``. A session_id filter therefore silently dropped every grant
    created under the real session, letting approved COMMAND_SET commands run
    WITHOUT being consumed (the consumption-bypass bug). Replay protection is
    preserved by the conjunction of the byte-for-byte match, status='PENDING'
    plus per-index ``consumed_indexes_json``, and the expires_at TTL -- none of
    which depend on which session is asking. See
    ``gaia.store.writer.list_command_set_grants_agnostic`` for the full
    security-boundary rationale.

    Args:
        retried_command: The exact command string the agent wants to run.
        db_path: Optional explicit DB path override (used by tests).

    Returns:
        Tuple of (approval_id: str, index: int) if a match is found, else None.
        The caller should call mark_command_set_item_consumed(approval_id, index)
        after successful execution.
    """
    try:
        from gaia.store.writer import list_command_set_grants_agnostic
        from datetime import datetime, timezone

        grants = list_command_set_grants_agnostic(
            status="PENDING",
            db_path=db_path,
        )

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        for grant in grants:
            # Check expiry
            expires_at = grant.get("expires_at")
            if expires_at and expires_at < now_iso:
                # Mark as expired in DB (best-effort)
                try:
                    from gaia.store.writer import update_approval_grant_status
                    update_approval_grant_status(
                        grant["approval_id"], "EXPIRED", db_path=db_path
                    )
                except Exception:
                    pass
                continue

            # Scope check
            if grant.get("scope") != "COMMAND_SET":
                continue

            command_set = []
            try:
                import json as _json
                command_set = _json.loads(grant.get("command_set_json") or "[]")
            except Exception:
                continue

            consumed_indexes = []
            try:
                import json as _json
                consumed_indexes = _json.loads(grant.get("consumed_indexes_json") or "[]")
            except Exception:
                pass

            for idx, item in enumerate(command_set):
                if idx in consumed_indexes:
                    continue
                # Byte-for-byte match (D10) -- no normalization
                if item.get("command") == retried_command:
                    logger.info(
                        "command_set grant matched: approval_id=%s, index=%d, command=%r",
                        grant["approval_id"][:12], idx, retried_command[:80],
                    )
                    return (grant["approval_id"], idx)

    except Exception as exc:
        logger.error("match_command_set_grant error: %s", exc)

    return None




def _cleanup_grant(grant_file: Path) -> None:
    """Remove a single grant or pending file."""
    try:
        grant_file.unlink(missing_ok=True)
    except Exception as e:
        logger.warning("Failed to remove grant file %s: %s", grant_file, e)
