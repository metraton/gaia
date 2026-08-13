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


# Bodies whose classification DEPENDS on a descent the bound can cut off. A
# read-only body is useless at the boundary -- free is both the correct answer
# and the answer a released bound produces, so the assertion cannot fail and the
# test is decoration. Each of these is decided by a lane that must OPEN
# something (a script file, a package entry, a stdin payload); when the bound
# stops that lane, retaining and releasing give opposite verdicts, which is what
# makes the assertion able to bite.
#
# Membership is decided by ONE question: does this body's verdict change when
# the retaining verdict is removed? A row that answers no is not evidence for
# the bound however plausible it looks in a list, and presenting it beside rows
# that are is worse than omitting it -- it inflates the apparent coverage of the
# thing the test exists to prove. `python3 -` was in this list and answered no:
# a stdin payload is resolved by its own handler BEFORE the budget guard is
# consulted, so free was never the released answer for it. It is pinned below,
# separately, as the placement case it actually is.
BODIES_THAT_SPEND_DESCENT = [
    ("shell-script", "bash /nonexistent/deploy.sh"),
    ("python-script", "python3 /nonexistent/migrate.py"),
    ("npm-entry", "npm run build"),
    ("executor-payload", 'bash -c "kubectl delete deployment web -n prod"'),
]


@pytest.mark.parametrize(
    "family,body", BODIES_THAT_SPEND_DESCENT,
    ids=[f for f, _ in BODIES_THAT_SPEND_DESCENT],
)
@pytest.mark.parametrize("offset", [-1, 0, 1], ids=["below", "at", "above"])
def test_the_bound_holds_at_its_own_value_for_every_descending_family(
    family, body, offset,
):
    """The bound must not open ON the value it is set to.

    This is the case the previous edge test could not see, because it varied
    only DEPTH and held the innermost body read-only. The axis that matters is
    the FAMILY of that body: a family that spends descent budget is decided by
    a lane whose exhaustion used to resolve the opposite way from the
    substitution guard's, so the two met at the bound and the level that spent
    the last unit was released -- free at exactly this value, gated one level on
    either side of it.
    """
    depth = _MAX_SUBSTITUTION_RECURSION_DEPTH + offset
    result = detect_mutative_command(nest(depth, body))
    assert result.is_mutative is True, (
        f"{family} at depth {depth} (bound{offset:+d}) classified free: "
        f"verb={result.verb!r} -- a nesting level that spends the last unit of "
        f"descent budget must be retained, not released"
    )


@pytest.mark.parametrize("offset", [-1, 0, 1], ids=["below", "at", "above"])
def test_a_stdin_payload_is_gated_by_lane_placement_not_by_the_bound(offset):
    """Held apart from the rows above because it proves a DIFFERENT half.

    A stdin payload has no file to open, so its own handler answers before the
    budget guard is reached. It is gated at every depth by where that guard sits
    relative to the lane's shape check -- not by the retaining verdict -- and
    grouping it with the rows that do exercise the retaining verdict would let
    one green row stand in for evidence it cannot give.
    """
    depth = _MAX_SUBSTITUTION_RECURSION_DEPTH + offset
    result = detect_mutative_command(nest(depth, "python3 -"))
    assert result.is_mutative is True


def test_a_read_body_at_the_bound_is_still_free():
    """The other direction, so retaining does not silently become blanket-gating.

    Nothing needs to be opened to classify a read, so the bound does not apply
    to it and the answer must stay free at the same depth where a script body
    is retained.
    """
    assert detect_mutative_command(
        nest(_MAX_SUBSTITUTION_RECURSION_DEPTH, "pwd"),
    ).is_mutative is False


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
