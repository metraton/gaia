#!/usr/bin/env python3
"""Unit coverage for the shared grouping/substitution normalization.

The behavioural consequences live in the classifier truth table and the
protected-path guard's own suite. What is pinned HERE is the one property
those cannot show directly: the asymmetry between the opener and the closer.

An unconditional trailing strip would close the hole just as well and would
also chew the end off ``ls -la $(pwd)``, so the rule is balance -- a closer is
a wrapper remnant only when the string carries no opener for it. Losing that
distinction is invisible in a suite that only checks whether dangerous forms
are gated.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.shell_grouping import strip_grouping_wrappers


@pytest.mark.parametrize("wrapped,expected", [
    ("(rm -rf /)", "rm -rf /"),
    ("( rm -rf / )", "rm -rf /"),
    ("((rm -rf /))", "rm -rf /"),
    ("{ rm -rf /", "rm -rf /"),
    ("$(rm -rf /)", "rm -rf /"),
    ("`rm -rf /`", "rm -rf /"),
    ("$((1+2))", "1+2"),
    # An operator split cuts the opener off the component that carries the
    # command, leaving an orphan closer that defeats the tail-anchored deny
    # regexes. That remnant is the whole reason the closer is stripped at all.
    ("rm -rf /)", "rm -rf /"),
    ("ls)", "ls"),
    ("}", ""),
])
def test_wrapper_is_removed(wrapped, expected):
    assert strip_grouping_wrappers(wrapped) == expected


@pytest.mark.parametrize("command", [
    # Balanced: the substitution is an argument, not a wrapper.
    "ls -la $(pwd)",
    "echo $(git rev-parse HEAD)",
    "ls -la `pwd`",
    r"find /tmp -type f \( -name a -o -name b \)",
    "kubectl get pods -o json",
    'grep -rn "(rm -rf /)" README.md',
    'gaia contract add evidence_report.open_gaps "(rm -rf /tmp/x) never ran"',
    "",
])
def test_unwrapped_command_is_untouched(command):
    assert strip_grouping_wrappers(command) == command


def test_result_is_idempotent():
    """Analysis forms are compared against the input to decide whether a second
    classification pass is worth running, so a second application must be a
    no-op or that comparison means nothing."""
    once = strip_grouping_wrappers("((cp a .claude/hooks/b.py))")
    assert strip_grouping_wrappers(once) == once


def test_peeling_is_bounded():
    """Pathological nesting terminates instead of looping."""
    assert strip_grouping_wrappers("(" * 200 + "rm -rf /" + ")" * 200)
