#!/usr/bin/env python3
"""Mutation-survivor closure tests for tiers.py (GRIND-TOTAL, last module).

This module KILLS the killable surviving mutants inventoried for
``hooks/modules/security/tiers.py`` (AC-3 cosmic-ray baseline, session
``tiers-spike.sqlite``). Each test names the mutant it kills and the line in
tiers.py it anchors to.

The tests are honest: they assert the genuine documented contract of the code
path the mutant lives on (a specific return value), not merely "does not raise".
A trivial smoke test would let the mutant survive; this one does not.

Mutants proven GENUINELY EQUIVALENT (no honest input distinguishes them from
the original for ANY reachable input) are NOT faked with a passing assert here;
they are documented in ``tests/evals/evidence/equivalents-security-core.md``
(Category T1) and excluded from the kill-rate denominator via
``tests/evals/equivalents-tiers.skip``.

Companion tests for tiers.py also live in ``test_tiers.py``
(``TestMutationBaselineSurvivors``), which closes the earlier AC-3 baseline
survivors (L89 cached or-guard, L113/L190 ZeroIterationForLoop, L132 category
Eq flips). This file closes the final 7-survivor population from the
``tiers-spike.sqlite`` re-measurement.
"""

import sys
from pathlib import Path

# Add hooks to path (mirrors the sibling test modules).
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.tiers import (  # noqa: E402
    SecurityTier,
    _classify_command_tier_cached,
)


class TestTiersMutantClosure:
    """Kills the one behaviourally-killable survivor in the tiers-spike
    population. The other six survivors are proven equivalent (see the module
    docstring and equivalents-security-core.md Category T1)."""

    # --- _classify_command_tier_cached default arg, ReplaceFalseWithTrue
    #     (tiers.py:82, job_id 87dc4edb...) ---
    #
    # The cached classifier's signature is
    #     _classify_command_tier_cached(command, has_blocked_patterns=False)
    # The default `False` is load-bearing: a command that is NOT ultra-common,
    # NOT mutative, and NOT a T1/T2 keyword must fall through to the
    # safe-by-elimination default T0. If the default flips to `True`
    # (ReplaceFalseWithTrue), the `if has_blocked_patterns: return T3` branch
    # at L105 fires for every such command, mis-classifying a benign unknown
    # command as T3_BLOCKED.
    #
    # The public API `classify_command_tier` always passes `has_blocked`
    # explicitly (L196), so the mutant is observable ONLY by calling the cached
    # function with the default argument -- which is precisely its documented
    # internal contract: with no blocked patterns flagged, an unclassified
    # command is T0, not T3.
    def test_cached_default_no_blocked_is_safe_by_elimination(self):
        """Kills ReplaceFalseWithTrue at tiers.py:82.

        Calling the cached classifier with the DEFAULT has_blocked_patterns on
        an unknown, non-mutative command must return T0 (safe by elimination).
        With the mutated default `True`, L105 returns T3 instead.
        """
        # Not ultra-common, not mutative, no T1/T2 keyword -> default path.
        tier = _classify_command_tier_cached("some_unknown_command --flag")
        assert tier == SecurityTier.T0_READ_ONLY, (
            "cached classifier with default (no blocked patterns) must be T0; "
            "a True default would force T3 via the has_blocked_patterns branch"
        )
        assert tier != SecurityTier.T3_BLOCKED

    def test_cached_default_unknown_tool_is_t0(self):
        """Companion: a second unknown command confirms the default-False path
        is exercised, not an accident of one specific string."""
        tier = _classify_command_tier_cached("mycustomtool dostuff --verbose")
        assert tier == SecurityTier.T0_READ_ONLY
