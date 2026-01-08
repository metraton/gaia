"""
Security tier definitions and classification.

Tiers:
- T0: Read-only operations
- T1: Validation operations (plan, lint, check)
- T2: Dry-run operations
- T3: Destructive/state-modifying operations (require approval)
"""

import re
import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class SecurityTier(str, Enum):
    """Security tier classification for commands."""

    T0_READ_ONLY = "T0"      # describe, get, show, list operations
    T1_VALIDATION = "T1"     # validate, plan, template, lint operations
    T2_DRY_RUN = "T2"        # --dry-run, --plan-only operations
    T3_BLOCKED = "T3"        # apply, reconcile, deploy operations (require approval)

    def __str__(self) -> str:
        return self.value

    @property
    def requires_approval(self) -> bool:
        """Check if this tier requires user approval."""
        return self == SecurityTier.T3_BLOCKED

    @property
    def description(self) -> str:
        """Human-readable description of the tier."""
        descriptions = {
            SecurityTier.T0_READ_ONLY: "Read-only operation",
            SecurityTier.T1_VALIDATION: "Validation operation",
            SecurityTier.T2_DRY_RUN: "Dry-run operation",
            SecurityTier.T3_BLOCKED: "State-modifying operation (requires approval)",
        }
        return descriptions.get(self, "Unknown tier")


# Validation patterns for T1 classification
VALIDATION_PATTERNS = [
    r"\bvalidate\b",
    r"\bplan\b",
    r"\btemplate\b",
    r"\blint\b",
    r"\bcheck\b",
    r"\bfmt\b",
]


def classify_command_tier(
    command: str,
    is_read_only_func=None,
    blocked_patterns: Optional[list] = None
) -> SecurityTier:
    """
    Classify command into security tier.

    Classification order:
    1. Check for blocked operations (T3)
    2. Check for dry-run operations (T2)
    3. Check for validation operations (T1)
    4. Check for read-only operations (T0)
    5. Default to T3 (blocked) for unknown commands

    Args:
        command: Shell command to classify
        is_read_only_func: Optional function to check if command is read-only
        blocked_patterns: Optional list of blocked command patterns

    Returns:
        SecurityTier classification
    """
    if not command or not command.strip():
        return SecurityTier.T3_BLOCKED

    command = command.strip()

    # Import here to avoid circular imports
    if blocked_patterns is None:
        from .blocked_commands import get_blocked_patterns
        blocked_patterns = get_blocked_patterns()

    # Check for blocked operations first (T3)
    for pattern in blocked_patterns:
        if re.search(pattern, command, re.IGNORECASE):
            return SecurityTier.T3_BLOCKED

    # Check for dry-run operations (T2)
    if "--dry-run" in command or "--plan-only" in command:
        return SecurityTier.T2_DRY_RUN

    # Check for validation operations (T1)
    for pattern in VALIDATION_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return SecurityTier.T1_VALIDATION

    # Check for read-only operations (T0)
    if is_read_only_func:
        is_safe, _ = is_read_only_func(command)
        if is_safe:
            return SecurityTier.T0_READ_ONLY
    else:
        # Import and use default function
        from .safe_commands import is_read_only_command
        is_safe, _ = is_read_only_command(command)
        if is_safe:
            return SecurityTier.T0_READ_ONLY

    # Default to blocked for unknown commands
    return SecurityTier.T3_BLOCKED


def tier_from_string(tier_str: str) -> SecurityTier:
    """
    Convert string to SecurityTier.

    Args:
        tier_str: Tier string like "T0", "T1", "T2", "T3"

    Returns:
        SecurityTier enum value
    """
    tier_map = {
        "T0": SecurityTier.T0_READ_ONLY,
        "T1": SecurityTier.T1_VALIDATION,
        "T2": SecurityTier.T2_DRY_RUN,
        "T3": SecurityTier.T3_BLOCKED,
    }
    return tier_map.get(tier_str.upper(), SecurityTier.T3_BLOCKED)
