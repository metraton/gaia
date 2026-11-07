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
  approval via approval_grants.py)
"""
from __future__ import annotations

import re
import logging
from enum import Enum
from functools import lru_cache

from .shell_grouping import strip_grouping_wrappers
from .shell_substitution import extract_substitutions

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

# Commands that fast-path to T0. Only commands that are read-only whatever
# their flags belong here: "git branch" has the mutative variants -D and -M.
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

    # Imported inside the function: mutative_verbs imports this module back, so
    # a module-level import would close the cycle at import time.
    from .mutative_verbs import (
        detect_mutative_command,
        CATEGORY_MUTATIVE,
        CATEGORY_READ_ONLY,
        CATEGORY_SIMULATION,
    )

    # A cheap tier word can be an argument or path rather than the operation
    # being executed. Let the semantic detector establish the mutative floor
    # before lexical T1/T2 patterns classify genuinely non-mutative commands.
    result = detect_mutative_command(command)
    if result.is_mutative:
        return SecurityTier.T3_BLOCKED

    # Subordinated to the mutative detector above: the flag is a claim made by
    # the invocation line, and this raw substring test cannot tell a flag the
    # payload reads from one it ignores or one merely quoted in another argument.
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

    if result.category == CATEGORY_SIMULATION:
        return SecurityTier.T2_DRY_RUN
    if result.category == CATEGORY_READ_ONLY:
        return SecurityTier.T0_READ_ONLY

    # Not blocked, not mutative -> safe by elimination (T0)
    return SecurityTier.T0_READ_ONLY


def _matches_any(command: str, patterns) -> bool:
    """Return True when *command* matches any of the pre-compiled *patterns*."""
    return any(pattern.search(command) for pattern in patterns)


def classify_command_tier(
    command: str,
    *,
    pre_computed_tier: "SecurityTier | None" = None,
) -> SecurityTier:
    """
    Classify command into security tier.

    NOTE: This function is used for tier metadata AFTER the bash_validator has
    already enforced security decisions. The bash_validator's own validation
    order (blocked -> safe -> dangerous verbs -> GitOps -> tier) is the primary
    security gate.

    If *pre_computed_tier* is provided (e.g. from a ``BashValidationResult``
    that already determined the tier during validation), it is returned
    immediately without re-computing.

    Classification order (when no pre-computed tier):
    1. Ultra-common T0 fast-path (ls, git status, etc.)
    2. Blocked patterns (T3) -- checked against pre-compiled patterns
    3. Mutative verb detector (T3) -- MUTATIVE verbs
    4. Dry-run/simulation (T2) -- --dry-run, plan, diff, template
    5. Local validation (T1) -- validate, lint, fmt, check
    6. Default T0 for everything else (safe by elimination)

    Args:
        command: Shell command to classify
        pre_computed_tier: Optional tier already determined by an upstream
            validator.  When provided the function returns it directly.

    Returns:
        SecurityTier classification
    """
    # Fast path: caller already knows the tier (e.g. BashValidationResult).
    if pre_computed_tier is not None:
        return pre_computed_tier

    if not command or not command.strip():
        return SecurityTier.T3_BLOCKED

    command = command.strip()

    # Import here to avoid circular imports
    from .blocked_commands import get_blocked_patterns, is_blocked_command
    blocked_patterns = get_blocked_patterns()

    # Check for blocked operations first (T3)
    # This must be done before caching since blocked_patterns come from module state
    # A raw pattern hit counts only when the floor itself agrees: the floor's
    # carriers (a search pattern, a Gaia CLI value) quote a dangerous form
    # without running it, and a tier the validator does not enforce is the
    # layer disagreement this module exists to avoid.
    has_blocked = (
        _matches_any(command, blocked_patterns)
        and is_blocked_command(command).is_blocked
    )

    # The raw side sees no wrapper normalization: every deny regex is
    # ``^``-anchored on the base command, and a grouping character glued to the
    # front moves it off position 0 (``^dd``/``^fdisk``/``^mkfs`` have no
    # mutative-verb backup). Scan the unwrapped form too; it can only set the flag.
    if not has_blocked:
        ungrouped = strip_grouping_wrappers(command)
        if ungrouped != command:
            has_blocked = _matches_any(ungrouped, blocked_patterns)

    # A command substitution runs BEFORE the outer command, so ``echo $(rm -rf
    # /)`` presents ``echo`` at position 0; ``extract_substitutions`` returns
    # only what would genuinely execute. Two departures from the scans above:
    #
    # 1. Bodies go through ``is_blocked_command`` rather than the raw regexes,
    #    which have no READ_ONLY_BASE_CMDS carrier check and would escalate a
    #    body that merely QUOTES a dangerous form (``grep -rn "git push --force"``).
    #
    # 2. The verdict returns T3 here instead of setting ``has_blocked``: the
    #    cached classifier takes its ultra-common fast path (``echo``, ``ls``)
    #    BEFORE it reads that flag, so a flag set on ``echo $(rm -rf /)`` would
    #    be discarded. That same fast path is what keeps the raw-regex scan
    #    above from escalating a quoted mention carried by ``echo``.
    substitution_bodies = extract_substitutions(command)
    for inner in substitution_bodies:
        if is_blocked_command(inner).is_blocked:
            return SecurityTier.T3_BLOCKED

    # The loop above answers for the permanent floor only. A body that is merely
    # mutative would still classify T0, because the fast path answers for the
    # CARRIER (``echo``, ``ls``, ``cat``) before the detector is consulted, so
    # the detector's verdict is taken here. Only commands that carry a
    # substitution reach it; ``extract_substitutions`` returns nothing for a
    # quoted mention.
    if substitution_bodies:
        from .mutative_verbs import detect_mutative_command
        if detect_mutative_command(command).is_mutative:
            return SecurityTier.T3_BLOCKED

    # Use cached classification
    return _classify_command_tier_cached(command, has_blocked)
