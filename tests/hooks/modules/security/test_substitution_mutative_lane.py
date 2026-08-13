#!/usr/bin/env python3
"""The command-substitution lane of the mutative detector.

The truth table pins the VERDICTS this lane produces. Three properties it
cannot show live here, because each is about how the lane reaches a verdict
rather than which verdict it reaches:

* the body inherits the working directory of the command that carries it,
* the depth bound resolves CLOSED, and
* the lane only ever ADDS a verdict.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.mutative_verbs import (  # noqa: E402
    _MAX_SUBSTITUTION_RECURSION_DEPTH,
    detect_mutative_command,
)


@pytest.fixture
def read_only_script(tmp_path):
    """A real script file whose content is read-only, at a RELATIVE name.

    The name has to be relative for the point to exist at all: an absolute path
    resolves the same from anywhere, so only a relative one can be resolved
    against the wrong directory.
    """
    (tmp_path / "probe.mjs").write_text("console.log('ok');\n")
    return tmp_path


def test_body_inherits_the_carriers_working_directory(read_only_script):
    """A relative script inside a body resolves where the command would run it.

    Losing this does not fail safe. The script-file lane treats a path it
    cannot open as conservatively mutative, so a body resolved against the
    wrong directory turns a legitimate read into an approval prompt -- the
    exact shape of the cwd regression this repository has already paid for
    once, and the reason the lane sits AFTER the peels that fold `cd` into the
    effective directory rather than before them.
    """
    correct = detect_mutative_command(
        "ls $(node probe.mjs)", cwd=str(read_only_script),
    )
    assert correct.is_mutative is False

    wrong = detect_mutative_command("ls $(node probe.mjs)", cwd="/nonexistent")
    assert wrong.is_mutative is True
    assert "script-file-unreadable" in wrong.verb


def test_a_body_that_navigates_overrides_the_inherited_directory(read_only_script):
    """`$(cd X && ...)` is judged from X, exactly as it would be run alone."""
    result = detect_mutative_command(
        f"ls $(cd {read_only_script} && node probe.mjs)", cwd="/nonexistent",
    )
    assert result.is_mutative is False


def nest(levels: int, inner: str) -> str:
    out = inner
    for _ in range(levels):
        out = f"echo $({out})"
    return out


def test_below_the_bound_a_read_body_stays_free():
    at_bound = nest(_MAX_SUBSTITUTION_RECURSION_DEPTH, "pwd")
    assert detect_mutative_command(at_bound).is_mutative is False


def test_past_the_bound_the_verdict_closes_rather_than_opens():
    """Exceeding the bound must tighten the answer, never release it.

    A bound that let the command through once exceeded would not be a limit at
    all -- it would be a published bypass, usable by nesting one level past the
    number. The body is deliberately NOT read here; the conservative verdict is
    reached BECAUSE it was not read.
    """
    for extra in (1, 2, 5):
        past = nest(_MAX_SUBSTITUTION_RECURSION_DEPTH + extra, "pwd")
        result = detect_mutative_command(past)
        assert result.is_mutative is True
        assert result.verb == "substitution-depth-exceeded"


def test_the_deepest_reachable_mutation_is_still_named():
    """Within the bound the real verb is reported, not the conservative one."""
    result = detect_mutative_command(
        nest(_MAX_SUBSTITUTION_RECURSION_DEPTH - 1, "kubectl delete deploy web"),
    )
    assert result.is_mutative is True
    assert result.verb == "delete"


@pytest.mark.parametrize("command", [
    "echo $(pwd)",
    "wc -l $(ls)",
    "echo $(dirname $(pwd))",
    "grep -rn TODO $(find . -name '*.py')",
    "echo $(git rev-parse HEAD)",
])
def test_the_lane_only_adds_a_verdict(command):
    """A read-only body leaves classification exactly where it found it."""
    assert detect_mutative_command(command).is_mutative is False


def test_source_lines_are_not_scanned_as_shell_substitutions():
    """`$(` in SOURCE is a language construct, not shell execution.

    A DOM/jQuery call reads as a substitution to a shell-shaped scan, so the
    lane is suppressed for source content. Nothing is lost: a shell command a
    source file really runs arrives through the exec-sink extractor, which
    re-dispatches it as the shell command it is.
    """
    line = "$('#target').remove();"
    assert detect_mutative_command(line, from_source_code=True).is_mutative is False
