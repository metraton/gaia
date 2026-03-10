"""
Security tier definitions and classification.

Provides tier metadata for commands after bash_validator has already
enforced security decisions. The bash_validator is the primary gate;
this module's classify_command_tier() is used for logging and state
tracking.

Tiers:
- T0: Read-only operations (safe by elimination)
- T1: Validation operations (validate, lint, fmt, check) -- local only
- T2: Simulation operations (plan, diff, dry-run) -- may contact remote APIs
- T3: State-modifying operations (mutative_verbs.py detection,
  nonce-based approval via approval_grants.py)
"""

import re
import logging
from enum import Enum
from functools import lru_cache

logger = logging.getLogger(__name__)


class SecurityTier(str, Enum):
    """Security tier classification for commands."""

    T0_READ_ONLY = "T0"      # describe, get, show, list operations
    T1_VALIDATION = "T1"     # validate, lint, fmt, check (local only)
    T2_DRY_RUN = "T2"        # plan, diff, dry-run, template (simulation)
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


# T1: Local validation (no remote API calls)
T1_PATTERNS = [
    r"\bvalidate\b",
    r"\blint\b",
    r"\bcheck\b",
    r"\bfmt\b",
]

# T2: Simulation (may contact remote APIs, but no state changes)
T2_PATTERNS = [
    r"\bplan\b",
    r"\btemplate\b",
    r"\bdiff\b",
]

# Ultra-common commands that should fast-path to T0
# These are commands that appear in >80% of sessions
# NOTE: Only include commands that are ALWAYS read-only regardless of flags.
# "git branch" was removed because it has mutative variants (-D, -m, -M, etc.).
ULTRA_COMMON_T0_COMMANDS = frozenset({
    "ls", "pwd", "cat", "echo", "git status", "git diff",
    "git log", "kubectl get",
})


@lru_cache(maxsize=512)
def _classify_command_tier_cached(
    command: str,
    has_blocked_patterns: bool = False,
) -> SecurityTier:
    """
    Classify command into security tier with LRU cache.

    This is the internal cached implementation. Use classify_command_tier() instead.
    """
    if not command or not command.strip():
        return SecurityTier.T3_BLOCKED

    command = command.strip()

    # Fast-path: Ultra-common T0 commands
    words = command.split()
    if len(words) >= 2:
        prefix2 = f"{words[0]} {words[1]}"
        if prefix2 in ULTRA_COMMON_T0_COMMANDS:
            return SecurityTier.T0_READ_ONLY
    if len(words) >= 1:
        if words[0] in ULTRA_COMMON_T0_COMMANDS:
            return SecurityTier.T0_READ_ONLY

    # Blocked patterns already checked externally
    if has_blocked_patterns:
        return SecurityTier.T3_BLOCKED

    # Check for dry-run operations (T2)
    if "--dry-run" in command or "--plan-only" in command:
        return SecurityTier.T2_DRY_RUN

    # Check for simulation operations (T2: plan, diff, template)
    for pattern in T2_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return SecurityTier.T2_DRY_RUN

    # Check for local validation operations (T1: validate, lint, fmt, check)
    for pattern in T1_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return SecurityTier.T1_VALIDATION

    # Use the mutative verb detector for T3 classification
    from .mutative_verbs import (
        detect_mutative_command,
        CATEGORY_MUTATIVE,
        CATEGORY_READ_ONLY,
        CATEGORY_SIMULATION,
    )
    result = detect_mutative_command(command)
    if result.is_mutative:
        return SecurityTier.T3_BLOCKED
    if result.category == CATEGORY_SIMULATION:
        return SecurityTier.T2_DRY_RUN
    if result.category == CATEGORY_READ_ONLY:
        return SecurityTier.T0_READ_ONLY

    # Not blocked, not mutative -> safe by elimination (T0)
    return SecurityTier.T0_READ_ONLY


def classify_command_tier(command: str) -> SecurityTier:
    """
    Classify command into security tier.

    NOTE: This function is used for tier metadata AFTER the bash_validator has
    already enforced security decisions. The bash_validator's own validation
    order (blocked -> safe -> dangerous verbs -> GitOps -> tier) is the primary
    security gate.

    Classification order:
    1. Ultra-common T0 fast-path (ls, git status, etc.)
    2. Blocked patterns (T3) -- checked against pre-compiled patterns
    3. Dry-run/simulation (T2) -- --dry-run, plan, diff, template
    4. Local validation (T1) -- validate, lint, fmt, check
    5. Mutative verb detector (T3) -- MUTATIVE verbs
    6. Default T0 for everything else (safe by elimination)

    Args:
        command: Shell command to classify

    Returns:
        SecurityTier classification
    """
    if not command or not command.strip():
        return SecurityTier.T3_BLOCKED

    command = command.strip()

    # Import here to avoid circular imports
    from .blocked_commands import get_blocked_patterns
    blocked_patterns = get_blocked_patterns()

    # Check for blocked operations first (T3)
    # This must be done before caching since blocked_patterns come from module state
    has_blocked = False
    for pattern in blocked_patterns:
        if pattern.search(command):
            has_blocked = True
            break

    # Use cached classification
    return _classify_command_tier_cached(command, has_blocked)


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
