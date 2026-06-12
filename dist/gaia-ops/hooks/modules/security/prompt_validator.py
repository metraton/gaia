"""Validate and classify resume prompts for security decision-making.

Subsystem 1 of the pre_tool_use Task/Agent path.
Runs FIRST -- if invalid, nothing else loads.

Responsibilities:
- Validates prompt is not empty, not malformed
- Detects deprecated approval phrases
- Detects nonce patterns
- Returns: classification (nonce/malformed_nonce/deprecated/standard)
"""

from .approval_constants import (
    NONCE_APPROVAL_PREFIX,
    NONCE_APPROVAL_PATTERN,
    DEPRECATED_APPROVAL_PHRASES,
)


def classify_resume_prompt(prompt: str) -> str:
    """Classify a resume prompt into one of four categories.

    Args:
        prompt: The resume prompt string.

    Returns:
        'nonce' -- valid nonce approval token present
        'malformed_nonce' -- APPROVE: prefix present but invalid nonce
        'deprecated' -- deprecated approval phrase detected
        'standard' -- normal resume prompt (no approval indicators)
    """
    stripped_prompt = prompt.strip()
    if NONCE_APPROVAL_PATTERN.search(prompt):
        return "nonce"
    if stripped_prompt.startswith(NONCE_APPROVAL_PREFIX):
        return "malformed_nonce"
    prompt_lower = prompt.lower()
    if any(phrase in prompt_lower for phrase in DEPRECATED_APPROVAL_PHRASES):
        return "deprecated"
    return "standard"
