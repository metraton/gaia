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


def build_t3_degraded_allow_message() -> str:
    """Return the canonical reason for a Q3 degraded-allow (non-blocking) T3.

    Used when the approval-persistence retry loop is exhausted for a T3 command
    that is NOT deny-listed. Per the Q3 policy the hook then ALLOWS the command
    to proceed (rather than hanging an unattended/headless run on a native
    ask dialog) and records an always-on ``t3_degraded_allow`` audit event. The
    allow is bounded: deny-listed destructive commands never reach this branch
    (they are stopped by the harness deny-list pre-hook and by
    ``is_blocked_command`` at Phase 3a), and the branch re-asserts
    ``is_blocked_command`` before allowing as defense-in-depth.
    """
    return (
        "Approval persistence unavailable after retries: proceeding under the "
        "T3 degraded-allow policy so this unattended operation is not blocked "
        "on a human prompt. A synthetic 't3_degraded_allow' audit event was "
        "recorded (reason=approval_persist_failed). This does NOT bypass the "
        "destructive deny-list, which is enforced before this point."
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


def build_t3_blocked_denial_message(
    approval_id: str,
    command: str,
    verb: str,
    category: str,
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

    Returns:
        The denial message string to embed in the hook response.
    """
    return (
        f"[T3_BLOCKED] This command requires user approval.\n"
        f"T3 command blocked. Load Skill('{_SUBAGENT_APPROVAL_SKILL}') to emit"
        f" the approval payload and await user decision.\n"
        f"Do NOT retry this command. Report APPROVAL_REQUEST with this"
        f" approval_id in your agent_contract_handoff.\n"
        f"Command: {command}\n"
        f"Verb: '{verb}' ({category})\n"
        f"approval_id: {approval_id}"
    )
