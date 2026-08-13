#!/usr/bin/env python3
"""Unit coverage for the executing-substitution extractor.

The behavioural consequences live in the classifier truth table, its
no-overcorrection census, and the protected-path guard's own suite. What is
pinned HERE is the property none of those can show directly: the extractor
answers a QUOTING question, and it must answer it the way bash does.

That matters because the whole family this module gates -- a substitution in
the middle of a chain -- is one character away from the false-positive class
this repository already carries. `$(rm -rf /)` in single quotes is text a
report is written in; the same characters in double quotes are a delete that
runs before the outer command starts. A suite that only asked "are the
dangerous forms gated?" would pass just as happily with a rule that gated
every mention of them.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.shell_substitution import extract_substitutions


@pytest.mark.parametrize("command,expected", [
    # The three spellings the shell actually executes.
    ("echo $(rm -rf /)", ["rm -rf /"]),
    ("echo `rm -rf /`", ["rm -rf /"]),
    ("diff /tmp/a <(rm -rf /)", ["rm -rf /"]),
    ("tee >(rm -rf /)", ["rm -rf /"]),
    # Double quotes do NOT suspend command substitution.
    ('echo "result: $(rm -rf /)"', ["rm -rf /"]),
    ('echo "$(cp p .claude/hooks/x.py)"', ["cp p .claude/hooks/x.py"]),
    # Position is irrelevant -- the shell evaluates it before the outer command.
    ("ls $(dd if=/dev/zero of=/dev/sda)", ["dd if=/dev/zero of=/dev/sda"]),
    ("X=$(rm -rf /)", ["rm -rf /"]),
    # Parameter expansion is not execution, but one nested inside it is: the
    # scan is linear and reaches the ``$(`` on its own terms.
    ("echo ${FOO:-$(rm -rf /)}", ["rm -rf /"]),
    # A nested body is reported alongside its parent, not hidden inside it.
    ("echo $(echo $(rm -rf /))", ["echo $(rm -rf /)", "rm -rf /"]),
    # A quoted paren inside the body does not close the substitution early.
    ('echo $(grep -rn "a)b" /tmp)', ['grep -rn "a)b" /tmp']),
    # An operator split upstream can cut the closer off; the remainder is still
    # a command that would run, so it is still reported.
    ("echo $(rm -rf /", ["rm -rf /"]),
    # More than one substitution in one line.
    ("diff <(sort a) <(sort b)", ["sort a", "sort b"]),
])
def test_executing_substitutions_are_extracted(command, expected):
    assert extract_substitutions(command) == expected


@pytest.mark.parametrize("command", [
    # SINGLE quotes suspend every expansion: this is a mention, not a use, and
    # each of these is a shape an agent genuinely types when reporting a
    # finding about the very family this module gates.
    "grep -rn 'echo $(rm -rf /)' hooks/",
    "gaia contract add evidence_report.open_gaps 'echo $(rm -rf /) was free'",
    "gaia memory search 'echo $(mkfs.ext4 /dev/sda1)'",
    "git commit -m 'fix: gate echo $(dd if=/dev/zero of=/dev/sda)'",
    "echo 'literal $(cp payload.py .claude/hooks/pre_tool_use.py)'",
    "gaia memory search '`rm -rf /`'",
    "echo '<(rm -rf /)'",
    "echo '$(rm -rf /)' | wc -c",
    # A backslash makes the next character literal outside single quotes.
    r'echo "\$(rm -rf /)"',
    r'echo "\`rm -rf /\`"',
    # A comment is not run. Only at a word boundary -- `foo#bar` is one word.
    "ls -la # $(mkfs.ext4 /dev/sda1)",
    # Parens with no dollar are text in either quoting style.
    "echo '(rm -rf /)'",
    'echo "(rm -rf /)"',
    # Process substitution, unlike $(), is NOT expanded inside double quotes.
    'echo "a<(rm -rf /)b"',
    # Nothing substitution-shaped at all.
    "kubectl get pods -o json",
    "echo ${HOME}",
    "",
])
def test_mentions_and_non_executing_forms_yield_nothing(command):
    assert extract_substitutions(command) == []


def test_comment_marker_inside_a_word_is_not_a_comment():
    """A URL fragment or a `foo#bar` token must not blind the rest of the line.

    Treating every `#` as a comment would silently stop the scan mid-command,
    which is a false negative rather than a false positive -- the failure
    direction that costs the most here.
    """
    assert extract_substitutions("curl http://x/#frag $(rm -rf /)") == ["rm -rf /"]


def test_comment_ends_at_the_newline():
    """A comment suspends the scan for its own line only, not for the rest."""
    assert extract_substitutions("ls # $(pwd)\necho $(rm -rf /)") == ["rm -rf /"]


def test_arithmetic_expansion_needs_no_special_case():
    """`$((...))` yields a body whose first token matches nothing, by design.

    Arithmetic runs no command, so the right answer is "nothing dangerous".
    The uniform `$(` handler reaches it without a second code path, while
    `$( (cmd) )` -- a real substitution wrapping a subshell -- still surrenders
    its inner command to the caller.
    """
    assert extract_substitutions("echo $((2 + 2))") == ["(2 + 2)"]
    assert extract_substitutions("echo $( (rm -rf /) )") == ["(rm -rf /)"]


def test_extraction_is_bounded_on_pathological_nesting():
    """A degenerate string terminates instead of recursing without end."""
    command = "echo " + "$(" * 200 + "rm -rf /" + ")" * 200
    assert len(extract_substitutions(command)) <= 64
