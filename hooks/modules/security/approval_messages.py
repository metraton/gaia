"""Canonical approval/resume text used by hooks, skills, and tests."""
from __future__ import annotations

from .approval_constants import NONCE_APPROVAL_PREFIX

CANONICAL_APPROVAL_TOKEN = "APPROVE:<nonce>"
CANONICAL_APPROVAL_TOKEN_FORMAT = f"{NONCE_APPROVAL_PREFIX}<32-char-hex>"
LATEST_BLOCKED_COMMAND_PHRASE = "latest blocked command"
CANONICAL_APPROVAL_TOKEN_GUIDANCE = (
    f"Use only {CANONICAL_APPROVAL_TOKEN} from the {LATEST_BLOCKED_COMMAND_PHRASE}."
)
CANONICAL_APPROVAL_FORMAT_GUIDANCE = (
    f"Use only {CANONICAL_APPROVAL_TOKEN_FORMAT} from the {LATEST_BLOCKED_COMMAND_PHRASE}."
)


def build_activation_failed_message(nonce: str, status: str, reason: str) -> str:
    """Return the canonical deny message for failed nonce activation."""
    return (
        "[ERROR] Approval activation failed\n\n"
        f"Nonce: {nonce}\n"
        f"Status: {status}\n"
        f"Reason: {reason}\n\n"
        "Request a fresh approval by retrying the blocked command so the hook "
        "can issue a new nonce."
    )


def build_invalid_nonce_message() -> str:
    """Return the canonical deny message for malformed approval tokens."""
    return (
        "[ERROR] Invalid approval token\n\n"
        f"Expected format: {CANONICAL_APPROVAL_TOKEN_FORMAT}\n\n"
        "The token after APPROVE: must be the 32-character hex nonce from the latest "
        "blocked command. Do not use an operation name, scope label, or placeholder "
        "after APPROVE: (for example, APPROVE:commit is invalid).\n\n"
        "Retry the blocked command to generate a fresh nonce, then resume with "
        f"the exact token. {CANONICAL_APPROVAL_FORMAT_GUIDANCE}"
    )


def build_deprecated_approval_message() -> str:
    """Return the canonical deny message for removed legacy approval syntax."""
    return (
        "[ERROR] Deprecated approval format\n\n"
        "String-based approval tokens are no longer supported.\n"
        f"{CANONICAL_APPROVAL_FORMAT_GUIDANCE}"
    )


def build_pending_approval_unavailable_message() -> str:
    """Return the canonical deny message for pending-approval persistence failures."""
    return (
        "Approval workflow unavailable: failed to persist the pending approval "
        "record for this command. Retry once. If it fails again, inspect the "
        "hook logs before proceeding."
    )


def build_t3_degraded_block_message() -> str:
    """Return the canonical reason for a T3 denied because approval could not persist.

    Used when the approval-persistence retry loop is exhausted for a T3 command
    that is NOT deny-listed. The command has already been classified as
    state-mutating at this point, so the only two outcomes are denying it or
    granting it; this branch denies, and records an always-on
    ``t3_degraded_block`` audit event.
    """
    return (
        "Approval persistence unavailable after retries: this T3 command is "
        "DENIED rather than allowed through the failure. It was already "
        "classified as state-mutating, so passing it here would grant exactly "
        "the consent the approval record could not capture. A synthetic "
        "'t3_degraded_block' audit event was recorded "
        "(reason=approval_persist_failed). Retry once the approval store is "
        "reachable again."
    )


def build_t3_approval_instructions(nonce: str | None = None) -> str:
    """Return T3 approval block data.

    Kept minimal: just the facts (tier, nonce).  Workflow instructions
    live in skills (subagent-request-approval, orchestrator-present-approval, security-tiers) so
    the hook doesn't duplicate or conflict with them.
    """
    nonce_line = f"NONCE:{nonce}" if nonce else "NONCE:unavailable (retry command to generate)"
    return (
        f"[T3_APPROVAL_REQUIRED] {nonce_line}\n"
        "Load the approval skill for next steps."
    )


# Canonical skill name for subagent approval workflow (D11 — role-prefixed).
# This constant is the single source of truth referenced by:
#   - build_t3_blocked_denial_message()  (below)
#   - tests/hooks/test_denial_messages.py
_SUBAGENT_APPROVAL_SKILL = "subagent-request-approval"


def _phrases_request(request_line: str, target: str) -> str:
    """The instruction a phraseless reactive denial carries in place of reporting its id."""
    return (
        "Nothing is shown to the user without your phrases. Request the "
        "signature with this line, writing each <...> for the user:\n"
        f"  {request_line}\n"
        "Then report APPROVAL_REQUEST with the approval_id that line prints; "
        "it replaces the one below, which was sealed without phrases.\n"
        f"Do NOT retry this {target} before the user decides.\n"
    )


def build_t3_blocked_denial_message(
    approval_id: str,
    command: str,
    verb: str,
    category: str,
    guidance: str = "",
    request_line: str = "",
) -> str:
    """Return the canonical T3_BLOCKED denial message for subagent context.

    Per plan D5 + D11, the message must include the literal skill name in the
    format ``Load Skill('<name>')`` so the subagent knows exactly which skill
    to load without inference.

    Args:
        approval_id: The P-{hex} approval identifier from the DB or filesystem.
        command: The full command string that was blocked.
        verb: The detected mutative verb (e.g. 'delete', 'push').
        category: The verb category (e.g. 'MUTATIVE').
        guidance: The non-mutating way to reach the same outcome, when the
            classifier knows one. Rendered as its own line so a command with a
            safe equivalent says what it is; a refusal that names none leaves
            the agent hunting for a spelling that passes, which is the
            behaviour the no-elusion rule exists to prevent.
        request_line: ``gaia.approvals.core.request_line`` of the named
            request when it carries no phrases: the denial then asks for that
            request instead of reporting this approval_id, which no host shows.

    Returns:
        The denial message string to embed in the hook response.
    """
    guidance_line = f"Instead: {guidance}\n" if guidance else ""
    next_step = (
        _phrases_request(request_line, "command") if request_line else
        "Do NOT retry this command. Report APPROVAL_REQUEST with this"
        " approval_id in your contract row.\n"
    )
    return (
        f"[T3_BLOCKED] This command requires user approval.\n"
        f"T3 command blocked. Load Skill('{_SUBAGENT_APPROVAL_SKILL}') to emit"
        f" the approval payload and await user decision.\n"
        f"{next_step}"
        f"Command: {command}\n"
        f"Verb: '{verb}' ({category})\n"
        f"{guidance_line}"
        f"approval_id: {approval_id}"
    )


def build_protected_write_denial_message(
    approval_id: str,
    path: str,
    tool_name: str,
    window_minutes: int,
    request_line: str = "",
) -> str:
    """Return the denial a subagent reads when a Write/Edit on a protected path is blocked.

    ``request_line`` works as in :func:`build_t3_blocked_denial_message`.
    """
    next_step = (
        _phrases_request(request_line, "operation") if request_line else
        "Do NOT retry this operation. Report APPROVAL_REQUEST with this approval_id "
        "in your contract row.\n"
    )
    return (
        f"[T3_BLOCKED] This file modification requires user approval.\n"
        f"{next_step}"
        f"The approval expires. Once the user decides, the grant for this path stays "
        f"usable for {window_minutes} minutes and then lapses on its own. The clock "
        f"starts at their DECISION, not at this request, so the wait for an answer "
        f"costs nothing -- but everything after it (your re-dispatch, grounding, the "
        f"edits and the tests between them) is spent inside that one window, and "
        f"nothing you do extends it. A write attempted after it lapses is blocked "
        f"again under a NEW approval_id; this one will not work twice.\n"
        f"File: {path}\n"
        f"Tool: {tool_name}\n"
        f"approval_id: {approval_id}"
    )
