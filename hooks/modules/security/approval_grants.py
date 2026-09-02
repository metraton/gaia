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
- Time-limited, and the window is per LANE, not global: a Bash/semantic grant
  lives APPROVAL_GRANT_TTL_MINUTES (5), mirrored here as
  DEFAULT_GRANT_TTL_MINUTES, because it is consumed at the first matching
  retry; a protected-path Write/Edit grant lives FILE_PATH_GRANT_TTL_MINUTES
  (30), because it stays reusable across the several Edits one file-level fix
  takes
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
    ``APPROVAL_GRANT_TTL_MINUTES`` (5 minutes, the value reflected by
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
grant-{session}-*.json scanners) are the DEPRECATED fallback plane retained for
grants created before the DB cutover. The active flow runs through the DB plane
in gaia.store.writer.
"""

from __future__ import annotations

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
    a module-level import would crash hook load in that window. The 5-minute
    fallback equals the canonical value, so the two never disagree even if the
    lazy import is briefly unavailable.
    """
    try:
        from gaia.store.writer import APPROVAL_GRANT_TTL_MINUTES as _ttl
        return _ttl
    except Exception:
        return 5


# Default GRANT TTL in minutes -- the active-grant retry window (approvals
# redesign, M1). The grant is consumed AT THE MATCH, so a short 5-minute window
# is enough to cover the block -> approve -> retry round trip; sourced from
# APPROVAL_GRANT_TTL_MINUTES in writer (the single point of truth).
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
    CHAIN_TAMPER_DETECTED = "chain_tamper_detected"


# Backward-compatible module-level aliases
ACTIVATION_ACTIVATED = ActivationStatus.ACTIVATED
ACTIVATION_NOT_FOUND = ActivationStatus.NOT_FOUND
ACTIVATION_NONCE_MISMATCH = ActivationStatus.NONCE_MISMATCH
ACTIVATION_SESSION_MISMATCH = ActivationStatus.SESSION_MISMATCH
ACTIVATION_EXPIRED = ActivationStatus.EXPIRED
ACTIVATION_INVALID_SIGNATURE = ActivationStatus.INVALID_SIGNATURE
ACTIVATION_INVALID_PENDING = ActivationStatus.INVALID_PENDING
ACTIVATION_ERROR = ActivationStatus.ERROR
ACTIVATION_CHAIN_TAMPER_DETECTED = ActivationStatus.CHAIN_TAMPER_DETECTED


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


# Only an affirmative native label carrying one complete canonical id can
# authorize activation. The closing anchor prevents suffix text from turning a
# display fragment into an identity-bearing answer.
_APPROVE_ID_RE = re.compile(r"^Approve\b.*\[(P-[a-f0-9]{32})\]\s*$")


def extract_approval_id_from_label(label: str) -> Optional[str]:
    """Extract one complete canonical id from an affirmative native label.

    Approve labels may contain a ``[P-<hex>]`` tag that identifies the
    pending approval to activate.  Reject labels never carry a nonce,
    even if one is superficially present in the text.

    Args:
        label: An Approve label ending in ``[P-<32 lowercase hex>]``.

    Returns:
        The canonical approval id, otherwise ``None``.
    """
    m = _APPROVE_ID_RE.search(label)
    return m.group(1) if m else None


def extract_nonce_from_label(label: str) -> Optional[str]:
    """Compatibility name returning only a complete canonical approval id."""
    return extract_approval_id_from_label(label)


def load_pending_by_nonce_prefix(prefix: str) -> Optional[Dict[str, Any]]:
    """Load a pending approval whose nonce starts with the given prefix.

    The ``[P-<hex>]`` tag in AskUserQuestion labels carries the first 8
    characters of the full nonce.  DB-backed since the pending plane moved
    fully to gaia.db: queries ``gaia.approvals.store.get_pending`` (all
    sessions -- the approval may have been created by a subagent whose session
    differs from the resolver's) and matches DB rows whose approval_id is
    ``P-{prefix}...``, returning the legacy pending dict shape.

    If multiple rows match (extremely unlikely with 8 hex chars), the most
    recent one (by created_at timestamp) is returned.

    Args:
        prefix: Hex prefix extracted from a ``[P-xxx]`` label (typically 8 chars).

    Returns:
        The pending approval dict, or ``None`` if no match was found.
    """
    try:
        from gaia.approvals.store import get_pending
        rows = get_pending(all_sessions=True)
        candidates: List[Dict[str, Any]] = []

        for row in rows:
            approval_id = row.get("id", "")
            nonce = approval_id[2:] if approval_id.startswith("P-") else approval_id
            if not nonce.startswith(prefix):
                continue
            mapped = _db_row_to_pending_dict(row)
            if mapped is not None:
                candidates.append(mapped)

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


# ============================================================================
# Consent surface -- render it whole, then persist what the user saw
# ============================================================================
# The consent surface is the AskUserQuestion text the user reads before
# deciding: the labeled sealed fields plus EVERY command the approval covers.
# Two defects lived here and are closed by this section:
#
#   1. A COMMAND_SET payload carries N commands, but ``exact_content`` holds
#      only command [0]. A presentation built from that single field asks for
#      consent to one command and activates a grant for N.
#      ``render_consent_surface`` renders the whole set, indexed, and
#      ``verify_consent_surface_completeness`` rejects any surface that shows
#      fewer commands than the payload covers.
#   2. The SHOWN event was written with no payload, so after the fact there was
#      no way to establish what text the user was shown -- neither to prove nor
#      to disprove that something was hidden. ``build_shown_event_payload``
#      makes the SHOWN event carry the full question text.
#
# The completeness verdict is RECORDED, never enforced at activation. The user
# has already consented by the time activation runs; refusing there would leave
# them unable to approve anything while the presentation side is being fixed.
# Prevention belongs to the template (``skills/orchestrator-present-approval``);
# this layer makes a violation visible in the append-only chain.

# Provenance of the persisted consent surface. ``captured`` is the verbatim
# question text the presentation layer passed in -- the probative record.
# ``reconstructed`` is rendered here from the fingerprint-verified sealed
# payload when the caller passed nothing: weaker (it proves what the canonical
# surface for this payload IS, not what was typed) but never absent.
CONSENT_SURFACE_CAPTURED = "captured"
CONSENT_SURFACE_RECONSTRUCTED = "reconstructed"

# Stands in for a surface when the payload seals no command to present. The
# neutral renderer refuses such a payload, and this function runs on the
# activation path inside build_shown_event_payload, which must not raise.
_CONSENT_SURFACE_NO_COMMAND = (
    "GAIA T3 APPROVAL REQUEST\n"
    "No exact command was sealed with this request; there is nothing to present."
)


def payload_commands(payload: Dict[str, Any]) -> List[str]:
    """Return every command the user must be shown for this payload, in order.

    A ``command_set`` of more than one item is authoritative: those N commands
    are what one consent covers, and ``exact_content`` is merely the singular
    stand-in for the first of them (see ``activate_db_pending_by_id``).
    Falls back to ``commands``, then to the single ``exact_content`` -- which
    for a SCOPE_FILE_PATH pending is the blocked file path, not a command.
    """
    raw_set = payload.get("command_set")
    if isinstance(raw_set, list):
        from_set = [
            item["command"]
            for item in raw_set
            if isinstance(item, dict) and item.get("command")
        ]
        if len(from_set) > 1:
            return from_set

    raw_commands = payload.get("commands")
    from_list = (
        [c for c in raw_commands if isinstance(c, str) and c]
        if isinstance(raw_commands, list)
        else []
    )
    if len(from_list) > 1:
        return from_list

    single = payload.get("exact_content") or ""
    if single:
        return [single]
    return from_list


def render_consent_surface(
    payload: Dict[str, Any],
    approval_id: str = "",
) -> str:
    """Render the user-visible consent surface for a sealed payload.

    Delegates to the one harness-neutral renderer over the one field table, so
    this layer chooses no labels and no field set of its own: the presented
    text, the reconstructed audit record and the completeness tripwire cannot
    render a payload differently from one another or from another host.

    The binding is ``UNBOUND_PRESENTATION`` because a surface reconstructed
    from a payload has no host call to bind to. Every field a user reads comes
    from the payload; only the correlation line reflects that absence, and a
    correlation identifies one consent attempt rather than the payload.

    The import is function-level: ``adapters/__init__`` reaches
    ``claude_code``, which reaches this module, so importing at module level
    would close that cycle.
    """
    from adapters.consent_presentation import (
        UNBOUND_PRESENTATION,
        envelope_from_sealed_payload,
        render_native_text,
    )

    if not payload_commands(payload):
        return _CONSENT_SURFACE_NO_COMMAND
    envelope = envelope_from_sealed_payload(
        payload, approval_id=approval_id, binding=UNBOUND_PRESENTATION
    )
    return render_native_text(envelope)


def render_approve_label(payload: Dict[str, Any], approval_id: str) -> str:
    """Render the Approve option label with its complete machine identity.

    Claude Code provides no separate approval metadata with the structured
    answer, so the native label is the identity channel. It keeps a concise
    human action while carrying the complete canonical id in brackets. A batch
    label names the command count so the label does not imply a single command.
    """
    action = payload.get("operation", "") or "approve operation"
    count = len(payload_commands(payload))
    if count > 1:
        action = f"{action} ({count} commands)"
    return f"Approve -- {action} [{approval_id}]"


def verify_consent_surface_completeness(
    surface: str,
    payload: Dict[str, Any],
) -> tuple[bool, List[str]]:
    """Check that a consent surface shows every command the payload covers.

    Returns ``(complete, missing_commands)``. A surface is complete only when
    each command appears verbatim in the text -- the property that makes a
    COMMAND_SET impossible to present as one command. Only presence is
    checked, not layout: an orchestrator may wrap or reorder the block, but it
    cannot omit a command the consent will cover.
    """
    commands = payload_commands(payload)
    text = surface or ""
    missing = [command for command in commands if command not in text]
    return (not missing, missing)


def build_shown_event_payload(
    payload: Dict[str, Any],
    approval_id: str,
    presented_question: Optional[str] = None,
    presented_label: Optional[str] = None,
) -> str:
    """Build the canonical-JSON payload for a SHOWN event.

    The point of the record is the FULL question text, not a summary of it: a
    summary cannot settle afterwards whether a command was hidden from the
    user. When ``presented_question`` is supplied it is stored verbatim and
    marked ``captured``; otherwise the surface is rendered from the sealed
    payload and marked ``reconstructed``.

    ``complete``/``missing_commands`` carry the completeness verdict for the
    stored surface, so an under-showing presentation is detectable in the
    chain rather than silently accepted.
    """
    if presented_question:
        surface = presented_question
        source = CONSENT_SURFACE_CAPTURED
    else:
        surface = render_consent_surface(payload, approval_id)
        source = CONSENT_SURFACE_RECONSTRUCTED

    complete, missing = verify_consent_surface_completeness(surface, payload)
    commands = payload_commands(payload)

    record: Dict[str, Any] = {
        "approval_id": approval_id,
        "approve_label": presented_label or render_approve_label(payload, approval_id),
        "command_count": len(commands),
        "commands_shown": commands,
        "complete": complete,
        "consent_surface": surface,
        "consent_surface_source": source,
        "missing_commands": missing,
        "scope": payload.get("scope", ""),
    }

    try:
        from gaia.approvals.chain import canonical_payload
        return canonical_payload(record)
    except Exception:
        return json.dumps(record, sort_keys=True, separators=(",", ":"))


def consent_surface_from_shown_event(event: Dict[str, Any]) -> Optional[str]:
    """Extract the persisted consent surface from a SHOWN event row.

    Returns None when the event carries no payload or no surface text -- the
    signal that this approval's consent surface was never recorded (every
    SHOWN event written before this layer existed).
    """
    if (event or {}).get("event_type") != "SHOWN":
        return None
    raw = event.get("payload_json")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    surface = parsed.get("consent_surface")
    return surface if isinstance(surface, str) and surface.strip() else None


@dataclass(frozen=True)
class ConsentSurfaceAudit:
    """What can be established after the fact about one approval's surface.

    ``auditable`` False is the detectable defect: either no SHOWN event exists
    or the event stored no surface text, so what the user saw cannot be
    reconstructed from the chain.
    """

    approval_id: str
    auditable: bool
    reason: str
    consent_surface: Optional[str] = None
    source: Optional[str] = None
    complete: Optional[bool] = None
    missing_commands: List[str] = field(default_factory=list)
    command_count: Optional[int] = None


def audit_consent_surface(approval_id: str) -> ConsentSurfaceAudit:
    """Report whether an approval's consent surface is recoverable from the chain.

    Reads the approval's events and inspects the LAST SHOWN event (the surface
    the decision was taken on). Non-fatal by construction: a DB error is
    reported as not-auditable rather than raised, because this is an audit
    reader, never part of the activation path.
    """
    try:
        from gaia.approvals.store import replay_for_approval
        events = replay_for_approval(approval_id)
    except Exception as exc:
        return ConsentSurfaceAudit(
            approval_id=approval_id,
            auditable=False,
            reason=f"could not read approval events: {exc}",
        )

    shown = [e for e in events if e.get("event_type") == "SHOWN"]
    if not shown:
        return ConsentSurfaceAudit(
            approval_id=approval_id,
            auditable=False,
            reason="no SHOWN event recorded for this approval",
        )

    last = shown[-1]
    surface = consent_surface_from_shown_event(last)
    if surface is None:
        return ConsentSurfaceAudit(
            approval_id=approval_id,
            auditable=False,
            reason="SHOWN event carries no consent_surface text",
        )

    try:
        parsed = json.loads(last.get("payload_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        parsed = {}

    return ConsentSurfaceAudit(
        approval_id=approval_id,
        auditable=True,
        reason="consent surface recorded",
        consent_surface=surface,
        source=parsed.get("consent_surface_source"),
        complete=parsed.get("complete"),
        missing_commands=list(parsed.get("missing_commands") or []),
        command_count=parsed.get("command_count"),
    )


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


def check_approval_grant(command: str, session_id: str = None) -> Optional[ApprovalGrant]:
    """Check if there is an active approval grant for a command.

    Called by the bash_validator before blocking a dangerous command.
    If a valid grant exists that matches the command, the command should
    be allowed through.

    DB-only since G2 cutover: check_db_semantic_grant() in gaia.store.writer
    is the sole source of truth. When a DB row is found it is wrapped as an
    ApprovalGrant with confirmed=True so downstream consumers see the same
    interface. The legacy filesystem fallback has been retired.

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
        from gaia.store.writer import check_db_semantic_grant
        db_row = check_db_semantic_grant(command, session_id=session_id)
        if db_row is not None:
            # Reconstruct an ApprovalGrant from DB row so callers see the
            # same interface.  The row stores the scope_signature in
            # command_set_json under the key 'scope_signature'.
            import json as _j
            row_data = _j.loads(db_row.get("command_set_json") or "{}")
            sig_dict = row_data.get("scope_signature")
            # Derive approved_verbs from the persisted scope_signature the same
            # way the FS activation paths do: deserialise the signature and use
            # its verb field (falls back to an empty list when absent).
            _approved_verbs: List[str] = []
            if sig_dict:
                try:
                    _sig = ApprovalSignature.from_dict(sig_dict)
                    if _sig.verb:
                        _approved_verbs = [_sig.verb]
                except Exception:
                    pass
            grant = ApprovalGrant(
                session_id=db_row.get("session_id", session_id),
                approved_verbs=_approved_verbs,
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
                "Approval grant matched (DB): command='%s', approval_id=%s",
                command[:80], (db_row.get("approval_id") or "?")[:16],
            )
            return grant
    except Exception as _db_err:
        logger.error(
            "check_approval_grant: DB lookup failed: %s",
            _db_err,
        )

    return None


def consume_grant(command: str, session_id: str = None) -> bool:
    """Mark the matching DB semantic grant as CONSUMED (replay protection).

    DB-only since G2 cutover.  Called by bash_validator as a secondary
    consume step after check_approval_grant() returns a match.  When
    bash_validator already holds a ``_db_approval_id`` it calls
    ``consume_db_semantic_grant`` directly; this function handles any
    remaining cases where only the command string is available.

    Args:
        command: The shell command whose grant should be consumed.
        session_id: Accepted for signature compatibility; not used for the
            DB lookup (grants are session-agnostic, per Brief 71).

    Returns:
        True if a matching PENDING grant was found and consumed, False otherwise.
    """
    try:
        from gaia.store.writer import check_db_semantic_grant, consume_db_semantic_grant
        db_row = check_db_semantic_grant(command, session_id=session_id)
        if db_row is not None:
            approval_id = db_row.get("approval_id")
            if approval_id:
                consumed = consume_db_semantic_grant(approval_id)
                if consumed:
                    logger.info(
                        "Grant consumed (DB): command='%s', approval_id=%s",
                        command[:80], approval_id[:16],
                    )
                else:
                    logger.debug(
                        "consume_grant: DB grant already consumed or not found: "
                        "approval_id=%s", approval_id[:16],
                    )
                return consumed
    except Exception as e:
        logger.error("Error consuming grant (DB): %s", e)

    return False


def confirm_grant(command: str, session_id: str = None) -> bool:
    """Set confirmed=1 on the first PENDING DB grant matching command.

    DB-only since G3 cutover.  Called after the native permission dialog
    accepts the first T3 execution.  Subsequent T3 commands within the TTL
    window will see confirmed=True and be auto-allowed without a native dialog.

    The matching approval_id is found via check_db_semantic_grant() (which
    returns PENDING grants), then confirm_db_grant() sets confirmed=1.

    Args:
        command: The shell command whose grant should be confirmed.
        session_id: Session ID for grant scoping (defaults to env var).

    Returns:
        True if a matching PENDING grant was found and confirmed, False otherwise.
    """
    if not session_id:
        session_id = _get_session_id()

    try:
        from gaia.store.writer import check_db_semantic_grant, confirm_db_grant
        db_row = check_db_semantic_grant(command, session_id=session_id)
        if db_row is None:
            logger.debug("confirm_grant: no DB grant found for command='%s'", command[:80])
            return False
        approval_id = db_row.get("approval_id")
        if not approval_id:
            return False
        result = confirm_db_grant(approval_id)
        if result.get("status") == "applied":
            logger.info(
                "Grant confirmed (DB): command='%s', approval_id=%s",
                command[:80], approval_id[:16],
            )
            return True
        logger.debug(
            "confirm_grant: confirm_db_grant returned %s for approval_id=%s",
            result.get("status"), approval_id[:16],
        )
    except Exception as e:
        logger.error("Error confirming grant (DB): %s", e)

    return False


def cleanup_expired_grants(force: bool = False) -> int:
    """Clean up expired DB approval grants.

    The authoritative grant plane is the DB (``approval_grants`` in gaia.db).
    This function calls ``cleanup_expired_db_grants()`` to mark expired DB rows
    as EXPIRED.  The legacy filesystem pending/index sweep has been retired:
    no ``pending-*.json`` or ``pending-index-*.json`` files are written any
    more, so there is nothing on disk to sweep.

    Called periodically (e.g., at hook startup) to prevent accumulation.
    Throttled to run at most once every ``_CLEANUP_INTERVAL_SECONDS`` --
    callers that need to bypass the throttle (e.g., SessionStart, manual
    CLI flush) can pass ``force=True``.

    Args:
        force: When True, run cleanup regardless of the throttle.

    Returns:
        Number of expired DB grant rows marked EXPIRED.
    """
    global _last_cleanup_time
    now = time.time()
    if not force and now - _last_cleanup_time < _CLEANUP_INTERVAL_SECONDS:
        return 0
    _last_cleanup_time = now

    cleaned = 0

    # DB grant expiry sweep -- the sole grant plane since the DB cutover.
    try:
        from gaia.store.writer import cleanup_expired_db_grants
        db_cleaned = cleanup_expired_db_grants()
        if db_cleaned:
            logger.info("Marked %d expired DB approval_grants rows as EXPIRED", db_cleaned)
            cleaned += db_cleaned
    except Exception as _db_exc:
        logger.debug("cleanup_expired_grants: DB sweep failed (non-fatal): %s", _db_exc)

    if cleaned:
        logger.info("Cleaned up %d expired approval grant(s)", cleaned)
    return cleaned


def _db_row_to_pending_dict(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convert a gaia.approvals.store pending row into the legacy pending dict.

    The legacy filesystem pending dict shape (nonce, command, danger_verb,
    danger_category, scope_type, scope_signature, timestamp, context, ...) is
    still what readers like ``bin/cli/approvals.py`` expect. This mapping is the
    DB-backed equivalent of the filesystem ``pending-{nonce}.json`` payload --
    mirrors ``_scan_pending_shared`` in ``bin/cli/approvals.py``.

    Returns None when the row cannot be parsed.
    """
    payload_json = row.get("payload_json") or "{}"
    try:
        payload = json.loads(payload_json)
    except (json.JSONDecodeError, TypeError):
        return None

    command = (
        payload.get("exact_content")
        or (payload.get("commands") or [None])[0]
        or payload.get("operation")
        or ""
    )

    operation = payload.get("operation", "")
    danger_verb = "unknown"
    danger_category = "MUTATIVE"
    if ": " in operation:
        danger_verb = operation.rsplit(": ", 1)[-1].strip()
    if " command intercepted" in operation:
        danger_category = operation.split(" command intercepted")[0].strip()

    created_at_str = row.get("created_at", "")
    ts: float = 0.0
    if created_at_str:
        try:
            from datetime import datetime as _dt, timezone as _tz
            dt = _dt.strptime(created_at_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_tz.utc)
            ts = dt.timestamp()
        except (ValueError, TypeError):
            ts = 0.0

    approval_id = row.get("id", "")
    nonce = approval_id[2:] if approval_id.startswith("P-") else approval_id

    return {
        "nonce": nonce,
        "session_id": row.get("session_id", ""),
        "command": command,
        "danger_verb": danger_verb,
        "danger_category": danger_category,
        "scope_type": payload.get("scope", SCOPE_SEMANTIC_SIGNATURE),
        "scope_signature": payload.get("scope_signature"),
        "timestamp": ts,
        "context": {
            "description": payload.get("rationale", ""),
            "risk": payload.get("risk_level", "medium"),
            "rollback": payload.get("rollback_hint"),
            "source": "db",
        },
    }


def get_pending_approvals_for_session(
    session_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return all non-expired pending approvals for a session.

    DB-backed since the pending plane moved fully to gaia.db: delegates to
    ``gaia.approvals.store.get_pending`` and maps each DB row into the legacy
    pending dict shape via ``_db_row_to_pending_dict``.

    Args:
        session_id: Session ID to filter by (defaults to current session).

    Returns:
        List of pending approval dicts, newest first.
    """
    if session_id is None:
        session_id = _get_session_id()

    results: List[Dict[str, Any]] = []
    try:
        from gaia.approvals.store import get_pending
        rows = get_pending(session_id=session_id)
        for row in rows:
            mapped = _db_row_to_pending_dict(row)
            if mapped is not None:
                results.append(mapped)
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


def write_pending_approval_for_file(
    nonce: str,
    file_path: str,
    session_id: Optional[str] = None,
    ttl_minutes: int = DEFAULT_PENDING_TTL_MINUTES,
    context: Optional[Dict[str, Any]] = None,
) -> Optional[Path]:
    """Write a pending approval record when a Write/Edit to a protected path is blocked.

    DB-primary since Task E of the approval redesign: persists to
    gaia.approvals.store (gaia.db) first using insert_requested() with
    approval_id = "P-" + nonce.  The filesystem write is removed entirely --
    file-path pendings live in the DB exactly like T3 command pendings and are
    read on demand via `gaia approvals`.

    The sealed_payload uses:
      - exact_content  = file_path          (the blocked file path)
      - operation      = "FILE_WRITE command intercepted: write"
      - scope          = SCOPE_FILE_PATH constant
      - scope_signature = serialised ApprovalSignature for check/activation
      - risk_level, rollback_hint, rationale from context when available

    Args:
        nonce: Cryptographic nonce from generate_nonce().  The DB row is stored
            under approval_id = "P-" + nonce.
        file_path: The absolute path of the file being written/edited.
        session_id: Session ID (defaults to the current host session id).
        ttl_minutes: How long the pending approval is valid before expiry
            (0 = no expiry; ignored by DB which uses TTL at query time).
        context: Optional dict with enriched context (source, description,
            risk, rollback, branch, files_changed, etc.).

    Returns:
        A sentinel Path whose name encodes the approval_id on success (the DB
        row, not a real file), or None on failure.  Callers only check for
        None to detect failure; they do not read the returned path.
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

    ctx = context or {}
    sealed_payload: Dict[str, Any] = {
        "operation": "FILE_WRITE command intercepted: write",
        "exact_content": file_path,
        "scope": SCOPE_FILE_PATH,
        "scope_signature": signature.to_dict(),
        "risk_level": ctx.get("risk", "medium") or "medium",
        "rollback_hint": ctx.get("rollback"),
        "rationale": (
            ctx.get("description")
            or f"Protected-path write to {file_path!r} requires user approval."
        ),
        "commands": [file_path],
    }

    db_approval_id = f"P-{nonce}"
    try:
        from gaia.approvals.store import insert_requested
        stored_id = insert_requested(
            sealed_payload,
            agent_id=None,
            session_id=session_id,
            approval_id=db_approval_id,
        )
        logger.info(
            "Pending file-path approval written to DB: approval_id=%s, file=%s, session=%s",
            stored_id, file_path, session_id,
        )
        # Return a sentinel Path so callers can distinguish success (non-None)
        # from failure (None).  The path is not written to disk.
        return Path(stored_id)

    except Exception as e:
        logger.error("Failed to write pending file-path approval to DB: %s", e)
        return None


def check_approval_grant_for_file(
    file_path: str,
    session_id: str = None,  # noqa: ARG001 — kept for signature compatibility
) -> Optional[dict]:
    """Check if there is an active approval grant for a Write/Edit file path.

    DB-only since Task E full migration: queries approval_grants via
    check_db_file_path_grant(), whose predicate is three-part and all three
    parts are load-bearing -- scope='SCOPE_FILE_PATH', status='PENDING' (this
    lane never advances a row to ACTIVE; PENDING IS the usable state), and
    expires_at not yet past, which is what actually retires the grant since
    nothing consumes it. Callers only check truthiness of the return value
    (None = no grant, any dict = grant found).

    Called by _adapt_write_edit before blocking a protected-path write. If
    a valid SCOPE_FILE_PATH grant exists for this path, the write should be
    allowed through.

    Args:
        file_path: The file path being written/edited.
        session_id: Accepted for signature compatibility; not used (DB lookup
            is cross-session by design — same rationale as semantic grants).

    Returns:
        A dict with grant row data when a matching grant is found, None otherwise.
    """
    try:
        from gaia.store.writer import check_db_file_path_grant
        row = check_db_file_path_grant(file_path)
        if row is not None:
            logger.info(
                "File-path DB grant matched: file=%r, approval_id=%s",
                file_path, str(row.get("approval_id", ""))[:16],
            )
            return row
    except Exception as e:
        logger.warning(
            "check_approval_grant_for_file: DB lookup failed (non-fatal): %s", e,
        )

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

    Reuse is bounded by PENDING_REUSE_WINDOW_MINUTES: the retry this serves is
    the one that happens while the user is deciding, and only that. Presentation
    is session-owned and nothing re-homes approvals.session_id, so a pending
    outliving its session can never be decided -- and handing it back would let
    it own the path until the 24h sweep. Past the window the caller mints
    instead, and the store supersedes the stale row.

    DB-primary since Task E: queries gaia.approvals.store for SCOPE_FILE_PATH
    pending rows whose payload.exact_content matches the target path.
    No filesystem fallback is needed because write_pending_approval_for_file
    now writes exclusively to the DB.

    Args:
        session_id: Session to search (used when all_sessions query unavailable).
        file_path: The file path to match against pending approvals.

    Returns:
        The nonce part of the approval_id (approval_id without "P-" prefix)
        if a matching pending approval exists in the DB, else None.
    """
    stripped = file_path.strip() if file_path else ""
    if not stripped:
        return None

    # DB path: query all pending rows (all_sessions=True). The host session id
    # inside a subagent is the subagent's id, not the orchestrator's, so
    # session-scoping would silently miss the row.
    try:
        from gaia.approvals.store import PENDING_REUSE_WINDOW_MINUTES, list_pending
        window_seconds = PENDING_REUSE_WINDOW_MINUTES * 60
        rows = list_pending(all_sessions=True)
        for row in rows:
            payload_json = row.get("payload_json") or "{}"
            try:
                payload = json.loads(payload_json)
            except (json.JSONDecodeError, TypeError):
                continue
            # SCOPE_FILE_PATH pendings are identified by their scope field.
            if payload.get("scope") != SCOPE_FILE_PATH:
                continue
            # list_pending already computes age_seconds off created_at.
            if float(row.get("age_seconds") or 0.0) > window_seconds:
                continue
            # exact_content holds the file path.
            if payload.get("exact_content", "").strip() == stripped:
                approval_id = row.get("id", "")
                if approval_id.startswith("P-"):
                    nonce = approval_id[2:]
                    logger.info(
                        "Reusing existing DB file-path pending approval_id=%s for file: %s",
                        approval_id, file_path,
                    )
                    return nonce
    except Exception as exc:
        logger.debug("find_pending_for_file: DB query failed (non-fatal): %s", exc)

    return None


def activate_db_pending_by_id(
    approval_id: str,
    current_session_id: Optional[str] = None,
    ttl_minutes: int = DEFAULT_GRANT_TTL_MINUTES,
    presented_question: Optional[str] = None,
    presented_label: Optional[str] = None,
) -> ApprovalActivationResult:
    """Activate one DB-stored pending approval by exact canonical id.

    Called when ``load_pending_by_nonce_prefix()`` returns None (because M2
    migrated REQUESTED writes to DB only -- no filesystem pending file is
    written any more).  This function bridges the gap:

      1. Looks up the approval row by exact id and requires status=pending.
      2. Parses payload_json from the DB row.
      2b. [HARD INTEGRITY CHECK] Calls ``verify_fingerprint()`` to confirm the
          payload has not changed since the REQUESTED event sealed it.  If the
          fingerprint does not match (``ChainTamperError``) or the REQUESTED
          event is missing (``ValueError``), activation FAILS CLOSED -- a
          ``FAILED`` audit event is written and the approval is NOT activated.
          This is the enforcement point for the presentation-role guarantee:
          the command the orchestrator shows the user MUST equal what the
          subagent generated.
      3. Writes SHOWN + APPROVED events via ``gaia.approvals.store``.  The
         SHOWN event carries the consent surface -- the full question text the
         user decided on -- so the surface is auditable afterwards.
      4. Creates a filesystem grant file so that ``check_approval_grant()``
         (which still reads the filesystem) can find it on the subagent retry.

    Cross-session semantics: the DB approval was created under the subagent's
    session.  The filesystem grant is created under ``current_session_id`` so
    that the re-dispatched subagent (which shares or sees the same session)
    finds the grant file.

    Args:
        approval_id: Complete canonical id extracted from the native label.
        current_session_id: Session doing the activation (orchestrator or
            resumed subagent).  Defaults to ``_get_session_id()``.
        ttl_minutes: TTL for the created filesystem grant.
        presented_question: The verbatim AskUserQuestion question text the user
            was shown, when the calling surface has it (the AskUserQuestion
            ``tool_input`` carries it).  Recorded in the SHOWN event as the
            ``captured`` consent surface.  When omitted, the surface is
            reconstructed from the sealed payload instead -- never left empty.
        presented_label: The verbatim Approve option label the user selected.

    Returns:
        ``ApprovalActivationResult`` with success=True and a grant_path when
        the activation succeeded; success=False otherwise.
    """
    if current_session_id is None:
        current_session_id = _get_session_id()
    if not isinstance(approval_id, str) or re.fullmatch(
        r"P-[a-f0-9]{32}", approval_id
    ) is None:
        return ApprovalActivationResult(
            success=False,
            status=ACTIVATION_NOT_FOUND,
            reason="Activation requires a canonical approval_id P-<32 lowercase hex>.",
        )

    try:
        # Step 1: Find exactly the signed DB pending approval.
        from gaia.approvals.store import record_event, approve, get_by_id
        import json as _json

        matched_row = get_by_id(approval_id)
        if matched_row is None or matched_row.get("status") != "pending":
            logger.info(
                "activate_db_pending_by_id: no pending DB approval found for id %s",
                approval_id,
            )
            return ApprovalActivationResult(
                success=False,
                status=ACTIVATION_NOT_FOUND,
                reason=f"No pending DB approval found for approval_id {approval_id!r}.",
            )

        payload_json_str = matched_row.get("payload_json")
        originating_session = matched_row.get("session_id", "")
        agent_id = matched_row.get("agent_id")

        # Step 2: Parse payload to get the exact command.
        if not payload_json_str:
            logger.warning(
                "activate_db_pending_by_id: approval %s has no payload_json",
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
                "activate_db_pending_by_id: could not parse payload_json for %s: %s",
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
        # a COMMAND_SET grant via the dedicated branch below. A plan-first
        # (``request_type == "COMMAND_SET"``) set of exactly one command is
        # ALSO routed there: it is the proactive single-command request-set
        # path (`gaia approvals request-set` with one ``--command``), and it
        # must keep the same index-tracked, exact-fingerprint lifecycle a
        # longer set gets -- not silently downgrade into the looser
        # SCOPE_SEMANTIC_SIGNATURE path below. A length-1 set from the legacy
        # (non-plan-first) producer still falls through to that singular path;
        # batching a single command under that shape buys nothing the ordinary
        # singular grant doesn't already give.
        raw_command_set = payload.get("command_set")
        command_set_items: list = []
        if isinstance(raw_command_set, list):
            # ``fingerprint`` is recomputed here rather than trusted from the
            # payload -- it is sha256(command), so recomputing from the
            # command string this loop already validated is self-consistent
            # regardless of whether the producer included it. This is what
            # lets ``reserve_plan_command`` (gaia.store.writer) match at
            # retry: it compares the retried command's freshly computed
            # fingerprint against ``item["fingerprint"]`` for the exact same
            # index, and a missing/empty key never matches by construction.
            from gaia.approvals.command_set import command_fingerprint

            for _item in raw_command_set:
                if isinstance(_item, dict) and _item.get("command"):
                    command_set_items.append(
                        {
                            "command": _item["command"],
                            "rationale": _item.get("rationale", ""),
                            "fingerprint": command_fingerprint(_item["command"]),
                        }
                    )
        request_fingerprint_value = payload.get("request_fingerprint")
        is_plan_first = (
            payload.get("request_type") == "COMMAND_SET"
            and isinstance(request_fingerprint_value, str)
            and bool(request_fingerprint_value)
        )
        is_command_set = len(command_set_items) > 1 or (
            len(command_set_items) == 1 and is_plan_first
        )

        command = payload.get("exact_content") or payload.get("commands", [None])[0] or ""
        if is_command_set and not command:
            # For a command_set the first item is a safe stand-in for the
            # singular display/signature path; the set itself is authoritative.
            command = command_set_items[0]["command"]
        if not command:
            logger.warning(
                "activate_db_pending_by_id: no command found in payload for %s",
                approval_id,
            )
            return ApprovalActivationResult(
                success=False,
                status=ACTIVATION_INVALID_PENDING,
                reason="Could not extract command from DB pending approval payload.",
            )

        # Step 2b: HARD fingerprint integrity check (Task A -- presentation-role
        # guarantee).
        #
        # The approval flow has three non-violable roles:
        #   - generation  (subagent)  -- sealed via fingerprint at REQUESTED time
        #   - presentation (orchestrator) -- must show verbatim; enforced HERE
        #   - approval    (user)      -- sole authority; recorded in APPROVED event
        #
        # verify_fingerprint() re-derives SHA-256 of the canonical payload and
        # compares it against the fingerprint stored in the REQUESTED event.
        # A mismatch means the payload was altered between generation and
        # presentation -- which would allow the orchestrator to show the user a
        # different command than the subagent generated.  We MUST NOT activate
        # such a tampered approval: activation is refused and a FAILED event is
        # written for audit.
        try:
            from gaia.approvals.chain import verify_fingerprint, ChainTamperError
            from gaia.approvals.store import _open_db as _chain_open_db
            _fp_con = _chain_open_db()
            try:
                verify_fingerprint(approval_id, payload_json_str, _fp_con)
            finally:
                _fp_con.close()
        except Exception as _fp_exc:
            # Determine whether this is a tamper or a missing REQUESTED event.
            _is_tamper = _fp_exc.__class__.__name__ == "ChainTamperError"
            _tamper_label = "fingerprint_mismatch" if _is_tamper else "missing_requested_event"
            logger.error(
                "activate_db_pending_by_id: INTEGRITY VIOLATION for %s "
                "(%s) -- refusing to activate: %s",
                approval_id, _tamper_label, _fp_exc,
            )
            # Record a FAILED audit event so the refusal is in the append-only chain.
            try:
                import json as _meta_json
                _metadata = _meta_json.dumps({
                    "integrity_check": _tamper_label,
                    "error": str(_fp_exc),
                    "activating_session": current_session_id,
                })
                record_event(
                    approval_id,
                    "FAILED",
                    agent_id=agent_id,
                    session_id=current_session_id,
                    metadata_json=_metadata,
                )
            except Exception as _audit_err:
                logger.error(
                    "activate_db_pending_by_id: also failed to record FAILED "
                    "audit event for %s: %s",
                    approval_id, _audit_err,
                )
            return ApprovalActivationResult(
                success=False,
                status=ACTIVATION_CHAIN_TAMPER_DETECTED,
                reason=(
                    f"Activation refused: payload integrity check failed for "
                    f"{approval_id!r} ({_tamper_label}). "
                    "The command presented to the user may differ from what the "
                    "subagent generated. A FAILED audit event has been recorded."
                ),
            )

        # Step 3: Write SHOWN + APPROVED events and flip status in DB.
        #
        # The SHOWN event carries the consent surface -- the full question text
        # the decision was taken on (see build_shown_event_payload). Without it
        # the chain records THAT the user was shown something but not WHAT, so a
        # dispute about a hidden command cannot be settled either way.
        #
        # No ``fingerprint`` is passed: this_hash is SHA-256(prev_hash ||
        # fingerprint), so leaving it NULL keeps every hash in the chain
        # byte-identical to what it was before this payload existed, and the
        # sealed-payload fingerprint activation verifies belongs to REQUESTED,
        # not to SHOWN.
        # Plan-first COMMAND_SET activation is deliberately handled before the
        # legacy singular path.  The decision and executable grant share one
        # SQLite transaction; never expose an APPROVED row without its grant.
        if is_command_set and is_plan_first:
            from gaia.approvals.store import activate_command_set_atomically

            try:
                activated = activate_command_set_atomically(
                    approval_id,
                    command_set_items,
                    request_fingerprint=request_fingerprint_value,
                    shown_payload=build_shown_event_payload(
                        payload,
                        approval_id,
                        presented_question=presented_question,
                        presented_label=presented_label,
                    ),
                    approver_session=current_session_id,
                    agent_id=agent_id,
                )
            except Exception as exc:
                logger.error(
                    "activate_db_pending_by_id: atomic COMMAND_SET activation "
                    "failed for %s: %s", approval_id, exc,
                )
                return ApprovalActivationResult(
                    success=False,
                    status=ACTIVATION_ERROR,
                    reason=f"Atomic COMMAND_SET activation failed: {exc}",
                )
            return ApprovalActivationResult(
                success=True,
                status=ACTIVATION_ACTIVATED,
                reason=(
                    "DB pending approval activated atomically as a plan-first "
                    f"COMMAND_SET grant ({len(command_set_items)} commands under one consent)."
                    + (" (idempotent retry)." if activated.get("idempotent") else "")
                ),
                grant_path=None,
            )

        try:
            record_event(
                approval_id,
                "SHOWN",
                agent_id=agent_id,
                session_id=current_session_id,
                payload_json=build_shown_event_payload(
                    payload,
                    approval_id,
                    presented_question=presented_question,
                    presented_label=presented_label,
                ),
            )
            approve(
                approval_id,
                approver_session=current_session_id,
                agent_id=agent_id,
            )
            logger.info(
                "activate_db_pending_by_id: DB transition complete for %s "
                "(SHOWN + APPROVED, status=approved)",
                approval_id,
            )
        except ValueError as ve:
            # transition() raises ValueError when status != 'pending' (e.g. already approved).
            logger.warning(
                "activate_db_pending_by_id: DB transition failed for %s: %s "
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
        # whole batch instead of a singular SCOPE_SEMANTIC_SIGNATURE grant.
        #
        # Two producers, two consume-side shapes -- branch on which produced
        # this payload:
        #
        # 1. Plan-first (``request_type == "COMMAND_SET"``, carries
        #    ``request_fingerprint``): the ONLY producer in production today is
        #    ``gaia approvals request-set`` (``cmd_request_set`` in
        #    ``bin/cli/approvals.py``). Its execution-time check is
        #    ``reserve_plan_command`` / ``settle_plan_command``
        #    (``gaia.store.writer``), which matches ONLY rows with
        #    ``source='plan-first'`` -- exactly what ``insert_plan_command_set``
        #    writes and ``create_command_set_grant`` (below) does not. Before
        #    this branch existed, EVERY command_set payload -- including this
        #    one -- was routed through ``create_command_set_grant``, so an
        #    AskUserQuestion approval of a ``request-set`` pending reported
        #    success but the retried command re-blocked anyway (confirmed live:
        #    see the COMMAND_SET activation-gap tests). Reuses the identical
        #    call ``cmd_approve``'s CLI-only admin path already makes, so the
        #    two entry points now activate a request-set pending identically.
        # 2. Legacy (no ``request_fingerprint``): the chain-intake producer this
        #    shape was originally built for (``_validate_compound_command`` ->
        #    ``decide_t3_outcome(command_set=...)``) is unreachable in production
        #    as of the "Compound T3 execution is disabled" change -- but this
        #    branch is kept as a defensive fallback rather than deleted, since
        #    ``create_command_set_grant`` / ``match_command_set_grant`` /
        #    ``mark_command_set_item_consumed`` still have other consumers
        #    (tests exercising the create-side and consume-side of this shape
        #    directly) that this change does not touch.
        if is_command_set:
            if is_plan_first:
                from gaia.store.writer import insert_plan_command_set

                applied = insert_plan_command_set(
                    approval_id,
                    command_set_items,
                    request_fingerprint=request_fingerprint_value,
                    agent_id=agent_id,
                    session_id=current_session_id,
                )
                if applied.get("status") != "applied":
                    logger.error(
                        "activate_db_pending_by_id: plan-first COMMAND_SET "
                        "grant creation failed for approval_id=%s (items=%d): %s",
                        approval_id[:16], len(command_set_items),
                        applied.get("reason", "unknown"),
                    )
                    return ApprovalActivationResult(
                        success=False,
                        status=ACTIVATION_ERROR,
                        reason=(
                            "Failed to create plan-first COMMAND_SET grant from "
                            f"approved payload: {applied.get('reason', 'unknown')}"
                        ),
                    )
                logger.info(
                    "activate_db_pending_by_id: plan-first COMMAND_SET grant "
                    "created (source=plan-first): approval_id=%s, items=%d, "
                    "originating_session=%s, current_session=%s",
                    approval_id[:16], len(command_set_items),
                    (originating_session or "")[:12],
                    current_session_id[:12],
                )
                return ApprovalActivationResult(
                    success=True,
                    status=ACTIVATION_ACTIVATED,
                    reason=(
                        "DB pending approval activated as a plan-first COMMAND_SET "
                        f"grant ({len(command_set_items)} commands under one consent)."
                    ),
                    grant_path=None,
                )

            created = create_command_set_grant(
                command_set_items,
                approval_id,
                session_id=current_session_id,
                agent_id=agent_id,
                ttl_minutes=DEFAULT_COMMAND_SET_TTL_MINUTES,
            )
            if not created:
                logger.error(
                    "activate_db_pending_by_id: COMMAND_SET grant creation "
                    "failed for approval_id=%s (items=%d)",
                    approval_id[:16], len(command_set_items),
                )
                return ApprovalActivationResult(
                    success=False,
                    status=ACTIVATION_ERROR,
                    reason="Failed to create COMMAND_SET grant from approved payload.",
                )
            logger.info(
                "activate_db_pending_by_id: COMMAND_SET grant created: "
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

        # Step 3c: SCOPE_FILE_PATH branch. When the payload carries a
        # SCOPE_FILE_PATH scope (a protected-file Write/Edit pending),
        # create a SCOPE_FILE_PATH DB grant so that check_approval_grant_for_file()
        # can find it on the subagent retry via check_db_file_path_grant().
        # No filesystem grant file is written -- the DB is the sole grant store
        # since the Task E full migration.
        if payload.get("scope") == SCOPE_FILE_PATH:
            file_path = payload.get("exact_content", "")
            if not file_path:
                logger.warning(
                    "activate_db_pending_by_id: SCOPE_FILE_PATH pending %s "
                    "has no exact_content (file path) -- cannot create grant",
                    approval_id,
                )
                return ApprovalActivationResult(
                    success=False,
                    status=ACTIVATION_INVALID_PENDING,
                    reason="SCOPE_FILE_PATH pending is missing the file path (exact_content).",
                )

            fp_signature = build_file_path_signature(file_path)
            if fp_signature is None:
                logger.warning(
                    "activate_db_pending_by_id: could not build file-path signature "
                    "for file=%r in pending %s",
                    file_path, approval_id,
                )
                return ApprovalActivationResult(
                    success=False,
                    status=ACTIVATION_INVALID_SIGNATURE,
                    reason="Could not build SCOPE_FILE_PATH signature for approved file path.",
                )

            # Write DB grant (replaces the former filesystem grant write).
            #
            # ``ttl_minutes`` is deliberately NOT forwarded. That parameter
            # carries the Bash/semantic lane's window
            # (DEFAULT_GRANT_TTL_MINUTES, 5 minutes), calibrated for a grant
            # consumed at the first match; forwarding it silently overrode
            # insert_file_path_grant's own FILE_PATH_GRANT_TTL_MINUTES (30) and
            # gave a protected-path Write/Edit grant a 5-minute window -- less
            # time than the multi-Edit fix it exists to authorise. The writer
            # owns this lane's window; letting its default apply keeps one
            # point of truth per lane.
            try:
                from gaia.store.writer import insert_file_path_grant
                result_fp = insert_file_path_grant(
                    approval_id=approval_id,
                    file_path=file_path,
                    scope_signature=fp_signature.to_dict(),
                    agent_id=None,
                    session_id=current_session_id,
                )
            except Exception as _fp_err:
                logger.error(
                    "activate_db_pending_by_id: SCOPE_FILE_PATH DB grant insert error: %s",
                    _fp_err,
                )
                return ApprovalActivationResult(
                    success=False,
                    status=ACTIVATION_ERROR,
                    reason=f"SCOPE_FILE_PATH DB grant insert error: {_fp_err}",
                )

            if result_fp.get("status") != "applied":
                logger.error(
                    "activate_db_pending_by_id: SCOPE_FILE_PATH DB grant insert failed: %s",
                    result_fp,
                )
                return ApprovalActivationResult(
                    success=False,
                    status=ACTIVATION_ERROR,
                    reason=f"SCOPE_FILE_PATH DB grant insert failed: {result_fp.get('reason', 'unknown')}",
                )

            logger.info(
                "activate_db_pending_by_id: SCOPE_FILE_PATH DB grant inserted: "
                "approval_id=%s, file=%r",
                approval_id[:16], file_path,
            )
            return ApprovalActivationResult(
                success=True,
                status=ACTIVATION_ACTIVATED,
                reason="DB SCOPE_FILE_PATH pending activated (DB grant inserted for file-path check).",
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
                "activate_db_pending_by_id: could not build signature for "
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

        # Step 5: Insert a SCOPE_SEMANTIC_SIGNATURE row into approval_grants DB.
        # This is the sole grant path since Task E retired the filesystem dual-write.
        # The row is keyed by approval_id so check_db_semantic_grant() can find it
        # cross-session without relying on filesystem files.
        # Task A's verify_fingerprint enforcement (Step 2b above) is preserved
        # exactly -- we only removed the filesystem grant write, not the check.
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
                logger.info(
                    "activate_db_pending_by_id: DB semantic grant inserted: "
                    "approval_id=%s, session=%s",
                    approval_id[:16], current_session_id[:12],
                )
                return ApprovalActivationResult(
                    success=True,
                    status=ACTIVATION_ACTIVATED,
                    reason=(
                        "DB pending approval activated (SHOWN + APPROVED written, "
                        "DB semantic grant inserted)."
                    ),
                    grant_path=None,
                )
            else:
                logger.error(
                    "activate_db_pending_by_id: DB semantic grant insert failed: %s",
                    result_sg,
                )
                return ApprovalActivationResult(
                    success=False,
                    status=ACTIVATION_ERROR,
                    reason=f"DB semantic grant insert failed: {result_sg.get('reason', 'unknown')}",
                )
        except Exception as _sg_err:
            logger.error(
                "activate_db_pending_by_id: DB semantic grant insert error: %s",
                _sg_err,
            )
            return ApprovalActivationResult(
                success=False,
                status=ACTIVATION_ERROR,
                reason=f"DB semantic grant insert error: {_sg_err}",
            )

    except Exception as exc:
        logger.error(
            "activate_db_pending_by_id: unexpected error for approval_id %s: %s",
            approval_id, exc, exc_info=True,
        )
        return ApprovalActivationResult(
            success=False,
            status=ACTIVATION_ERROR,
            reason=f"Unexpected error activating DB pending: {exc}",
        )


def activate_db_pending_by_prefix(
    nonce_prefix: str,
    current_session_id: Optional[str] = None,
    ttl_minutes: int = DEFAULT_GRANT_TTL_MINUTES,
    presented_question: Optional[str] = None,
    presented_label: Optional[str] = None,
) -> ApprovalActivationResult:
    """Compatibility helper for legacy direct callers; never use for consent."""
    try:
        from gaia.approvals.store import get_pending

        candidates = [
            row.get("id", "")
            for row in get_pending(all_sessions=True)
            if row.get("id", "").startswith(f"P-{nonce_prefix}")
        ]
        if len(candidates) != 1:
            return ApprovalActivationResult(
                success=False,
                status=ACTIVATION_NOT_FOUND,
                reason=(
                    f"Legacy nonce prefix {nonce_prefix!r} resolved to "
                    f"{len(candidates)} pending approvals."
                ),
            )
        return activate_db_pending_by_id(
            candidates[0],
            current_session_id=current_session_id,
            ttl_minutes=ttl_minutes,
            presented_question=presented_question,
            presented_label=presented_label,
        )
    except Exception as exc:
        return ApprovalActivationResult(
            success=False,
            status=ACTIVATION_ERROR,
            reason=f"Legacy prefix lookup failed: {exc}",
        )


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
# (DEFAULT_GRANT_TTL_MINUTES / APPROVAL_GRANT_TTL_MINUTES = 5) so a batch of
# commands approved under one consent gets the same short retry window as a
# single approved command -- the block-approve-retry flow is same-session and
# single-use, so 5 minutes is enough to consume every item while keeping the
# grant's live window tight.
DEFAULT_COMMAND_SET_TTL_MINUTES = 5


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
        session_id: Host session id (defaults to current session).
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
    spans sessions, and the host session id is not guaranteed to be exported
    into the bash subprocess -- where ``get_session_id()`` falls back to the
    literal ``"default"``. A session_id filter therefore silently dropped every grant
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
