#!/usr/bin/env python3
"""Mutation-survivor closure tests for mutative_verbs.py (GRIND-TOTAL).

This module exists to KILL the surviving mutants inventoried for
``hooks/modules/security/mutative_verbs.py`` (baseline 55.78% kill /
325 survivors over 735 specs). Each test targets the EXACT non-mutated
outcome of a code path so the corresponding mutant fails an assertion when it
lives.

The tests are honest: they assert specific values and branch directions
(category, verb, confidence, cli_family, reason substrings, dangerous_flags,
boundary indices, truthiness) — not merely ``is_mutative``. The dominant
survivor cause is that the legacy suite only asserts ``is_mutative`` and never
the rest of the MutativeResult, so operator/number/boolean mutants on the
*reason/verb/confidence/category* arms survive untouched. These tests pin
those fields.

Classes are grouped by function (mirrors the sibling
test_blocked_commands_mutants.py / test_approval_grants_mutants.py layout).
"""

import sys
from pathlib import Path

import pytest

# Add hooks to path (mirrors the sibling test modules).
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import modules.security.mutative_verbs as mv
from modules.security.mutative_verbs import (
    detect_mutative_command,
    MutativeResult,
)
