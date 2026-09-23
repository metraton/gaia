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
- Time-limited by one window for every lane, gaia.store.writer's
  APPROVAL_WINDOW_MINUTES (30), counted from the decision and mirrored here as
  DEFAULT_GRANT_TTL_MINUTES
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
    ``APPROVAL_GRANT_TTL_MINUTES`` (the 30-minute approval window, reflected
    by DEFAULT_GRANT_TTL_MINUTES above).

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
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.approvals.store import ActivationStatus, ApprovalActivationResult

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
    a module-level import would crash hook load in that window. The 30-minute
    fallback equals the canonical value, so the two never disagree even if the
    lazy import is briefly unavailable.
    """
    try:
        from gaia.store.writer import APPROVAL_GRANT_TTL_MINUTES as _ttl
        return _ttl
    except Exception:
        return 30


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
    """Return the neutral presentation commands in order, preserving the list interface."""
    # Import locally because adapters initialization reaches this module.
    from adapters.consent_presentation import payload_commands as neutral_payload_commands

    return list(neutral_payload_commands(payload))


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
    agent_id: Optional[str] = None,
) -> Optional[Path]:
    """Write a pending approval record when a Write/Edit to a protected path is blocked.

    The payload is sealed by gaia.approvals.core.seal_request; ``agent_id``
    names the requester and is recorded as ``unattributed`` when absent.

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
      - risk_level, rollback_hint, verification, impact, rationale from
        context when available (verification/impact seal exactly like
        rollback_hint always did: present when the caller's context carries
        them, None -- rendered as "not declared" by consent_presentation.py's
        existing VISIBLE_FIELDS table -- when it does not)

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
    requester = agent_id or "unattributed"
    db_approval_id = f"P-{nonce}"
    try:
        from gaia.approvals.core import seal_request
        from gaia.approvals.store import insert_requested
        sealed_payload = seal_request(
            "file_write", [{"path": file_path}],
            what=ctx.get("description") or f"Modify the protected file {file_path}",
            session_id=session_id, agent_id=requester,
            rollback=ctx.get("rollback"), verification=ctx.get("verification"),
            impact=ctx.get("impact"), risk_level=ctx.get("risk", "medium") or "medium",
        )
        stored_id = insert_requested(
            sealed_payload,
            agent_id=requester,
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
    """Activate one sealed request through the host-neutral atomic service."""
    from gaia.approvals import store

    session_id = current_session_id or _get_session_id()
    shown_payload = None
    row = store.get_by_id(approval_id)
    if row and row.get("payload_json"):
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            shown_payload = build_shown_event_payload(
                payload,
                approval_id,
                presented_question=presented_question,
                presented_label=presented_label,
            )
    return store.activate_approval_atomically(
        approval_id,
        approver_session=session_id,
        shown_payload=shown_payload,
        ttl_minutes=ttl_minutes,
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

DEFAULT_COMMAND_SET_TTL_MINUTES = DEFAULT_GRANT_TTL_MINUTES


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
