"""Canonical approval/resume text used by hooks, skills, and tests."""

from .approval_constants import NONCE_APPROVAL_PREFIX

CANONICAL_APPROVAL_TOKEN = "APPROVE:<nonce>"
CANONICAL_APPROVAL_TOKEN_FORMAT = f"{NONCE_APPROVAL_PREFIX}<32-char-hex>"
LATEST_BLOCKED_COMMAND_PHRASE = "latest blocked command"
PENDING_APPROVAL_STATUS = "PENDING_APPROVAL"

CANONICAL_APPROVAL_TOKEN_GUIDANCE = (
    f"Use only {CANONICAL_APPROVAL_TOKEN} from the {LATEST_BLOCKED_COMMAND_PHRASE}."
)
CANONICAL_APPROVAL_FORMAT_GUIDANCE = (
    f"Use only {CANONICAL_APPROVAL_TOKEN_FORMAT} from the {LATEST_BLOCKED_COMMAND_PHRASE}."
)


def build_activation_failed_message(nonce: str, status: str, reason: str) -> str:
    """Return the canonical deny message for failed nonce activation."""
    return (
        "❌ Approval activation failed\n\n"
        f"Nonce: {nonce}\n"
        f"Status: {status}\n"
        f"Reason: {reason}\n\n"
        "Request a fresh approval by retrying the blocked command so the hook "
        "can issue a new nonce."
    )


def build_invalid_nonce_message() -> str:
    """Return the canonical deny message for malformed approval tokens."""
    return (
        "❌ Invalid approval token\n\n"
        f"Expected format: {CANONICAL_APPROVAL_TOKEN_FORMAT}\n\n"
        "The resume prompt contains an approval prefix but not a valid nonce. "
        "Retry the blocked command to generate a fresh nonce, then resume with "
        f"the exact token. {CANONICAL_APPROVAL_FORMAT_GUIDANCE}"
    )


def build_deprecated_approval_message() -> str:
    """Return the canonical deny message for removed legacy approval syntax."""
    return (
        "❌ Deprecated approval format\n\n"
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
    """Return explicit T3 approval workflow steps for blocked commands."""
    if nonce:
        step_two = f"2. Include the approval code NONCE:{nonce} in your {PENDING_APPROVAL_STATUS} output.\n"
        step_four = (
            f"4. Wait for explicit user approval. When resumed, expect APPROVE:{nonce} "
            f"and then retry the command. {CANONICAL_APPROVAL_TOKEN_GUIDANCE}\n"
        )
    else:
        step_two = f"2. Retry the blocked command if you need a fresh approval code for {PENDING_APPROVAL_STATUS}.\n"
        step_four = (
            f"4. Wait for explicit user approval before executing. {CANONICAL_APPROVAL_TOKEN_GUIDANCE}\n"
        )

    return (
        "This is a T3 (state-modifying) operation. Follow the approval workflow:\n"
        "1. Present a plan with scope, impact, and rollback steps.\n"
        f"{step_two}"
        f"3. Set PLAN_STATUS: {PENDING_APPROVAL_STATUS}.\n"
        f"{step_four}"
    )
