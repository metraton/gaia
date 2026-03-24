"""Canonical approval/resume text used by hooks, skills, and tests."""
from __future__ import annotations

from .approval_constants import NONCE_APPROVAL_PREFIX

CANONICAL_APPROVAL_TOKEN = "APPROVE:<nonce>"
CANONICAL_APPROVAL_TOKEN_FORMAT = f"{NONCE_APPROVAL_PREFIX}<32-char-hex>"
LATEST_BLOCKED_COMMAND_PHRASE = "latest blocked command"
AWAITING_APPROVAL_STATUS = "AWAITING_APPROVAL"

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


def build_t3_approval_instructions(nonce: str | None = None) -> str:
    """Return T3 approval block data.

    Kept minimal: just the facts (tier, nonce).  Workflow instructions
    live in skills (approval, orchestrator-approval, security-tiers) so
    the hook doesn't duplicate or conflict with them.
    """
    nonce_line = f"NONCE:{nonce}" if nonce else "NONCE:unavailable (retry command to generate)"
    return (
        f"[T3_APPROVAL_REQUIRED] {nonce_line}\n"
        "Load the approval skill for next steps."
    )
