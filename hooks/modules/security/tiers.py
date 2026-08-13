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

    # Imported inside the function: mutative_verbs imports this module back, so
    # a module-level import would close the cycle at import time.
    from .mutative_verbs import (
        detect_mutative_command,
        CATEGORY_MUTATIVE,
        CATEGORY_READ_ONLY,
        CATEGORY_SIMULATION,
    )

    # Check for dry-run operations (T2)
    # Subordinated to the mutative detector, not short-circuiting past it: the
    # flag is a claim made by the invocation line, and this check is a raw
    # substring test that cannot tell a flag the payload READS from one it
    # ignores -- or from one merely quoted inside another argument. Where the
    # detector finds a real mutation anyway (a script whose content writes and
    # never reads the flag), returning T2 here would hand back, at tier level,
    # exactly the absolution the detector withheld.
    if "--dry-run" in command or "--plan-only" in command:
        if not detect_mutative_command(command).is_mutative:
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
    result = detect_mutative_command(command)
    if result.is_mutative:
        return SecurityTier.T3_BLOCKED
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
    3. Dry-run/simulation (T2) -- --dry-run, plan, diff, template
    4. Local validation (T1) -- validate, lint, fmt, check
    5. Mutative verb detector (T3) -- MUTATIVE verbs
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
    has_blocked = _matches_any(command, blocked_patterns)

    # The patterns are run here directly rather than through
    # ``is_blocked_command``, so the wrapper-normalization that function applies
    # does not reach them: every deny regex is ``^``-anchored on the base
    # command, and a grouping character glued to the front moves that command
    # off position 0. ``(mkfs.ext4 /dev/sda1)`` classified T0 here while the
    # permanent floor blocked it -- the forms with no mutative-verb backup
    # (``^dd``/``^fdisk``/``^mkfs``) are exactly the ones that show it. Scan the
    # unwrapped form too; strictly additive, since it can only set the flag.
    if not has_blocked:
        ungrouped = strip_grouping_wrappers(command)
        if ungrouped != command:
            has_blocked = _matches_any(ungrouped, blocked_patterns)

    # Same reasoning one token further right: a command substitution is
    # evaluated by the shell BEFORE the outer command runs, so ``echo $(rm -rf
    # /)`` reaches the same effect while presenting ``echo`` at position 0.
    # ``extract_substitutions`` returns only what would genuinely execute -- a
    # single-quoted, escaped or commented-out mention yields nothing.
    #
    # TWO departures from the two scans above, both deliberate:
    #
    # 1. Bodies go through ``is_blocked_command`` rather than the raw regexes.
    #    The raw scan has no false-positive fast path, so a body that merely
    #    QUOTES a dangerous form (``x=$(grep -rn "git push --force" .)``) would
    #    match a non-anchored deny pattern and escalate a search to permanent
    #    deny. ``is_blocked_command`` applies the READ_ONLY_BASE_CMDS carrier
    #    check first, which is what tells that mention from a use -- and it is
    #    the same function bash_validator phase 3a runs, so the two layers
    #    cannot disagree about a body.
    #
    # 2. The verdict returns T3 HERE instead of setting ``has_blocked``. The
    #    cached classifier consults its ultra-common fast path (``echo``,
    #    ``ls``, ``cat``, ``pwd`` ...) BEFORE it reads that flag, so a flag set
    #    on ``echo $(rm -rf /)`` would be discarded on the way in. Reordering
    #    the cached function instead was considered and rejected: the fast path
    #    is what currently keeps the raw-regex scan on line 208 from escalating
    #    a quoted mention carried by ``echo``, and moving the flag ahead of it
    #    would convert that suppression into a fresh false-positive class --
    #    the exact trade this work is forbidden to make.
    for inner in extract_substitutions(command):
        if is_blocked_command(inner).is_blocked:
            return SecurityTier.T3_BLOCKED

    # Use cached classification
    return _classify_command_tier_cached(command, has_blocked)
