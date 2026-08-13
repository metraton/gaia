"""
shell_substitution.py -- extract the commands a shell will EXECUTE from inside
a command substitution, wherever in the string they sit.

``shell_grouping`` closed the case where a grouping character is glued to the
command at POSITION 0. One token to the right, the same boundary was still
open: a command substitution is evaluated by the shell BEFORE the outer command
ever runs, so it executes regardless of where it appears.

    echo $(rm -rf /)                          -- the delete runs; echo prints its output
    echo $(cp payload.py .claude/hooks/x.py)  -- the hook file is overwritten
    ls $(dd if=/dev/zero of=/dev/sda)         -- the device is written

Every one of those classified T0 by elimination, because each layer keys on the
first token of the string and the first token is ``echo`` or ``ls``.

WHY THIS IS NOT THE MENTION/USE TRADEOFF ``shell_grouping`` REFUSED TO MAKE.
That module declined to reach into the middle of a command because a dangerous
command QUOTED inside another command's argument is being written down, not
run, and a positional heuristic cannot tell the two apart. This module does not
use a positional heuristic. It runs the shell's own quoting rules, which draw
the line exactly where the shell draws it:

    grep -rn 'echo $(rm -rf /)' hooks/   SINGLE quotes -- literal text, nothing runs
    grep -rn "echo $(rm -rf /)" hooks/   DOUBLE quotes -- the delete RUNS, then grep
                                         searches for its output
    echo hello # $(rm -rf /)             a comment -- nothing runs
    echo "\$(rm -rf /)"                  escaped -- literal text
    echo '$(rm -rf /)' | wc -c           literal, and the pipe does not change that

So a mention stays a mention and a use is caught, and neither answer is a
guess: it is what bash itself would do with the same bytes.

WHAT IS IN SCOPE, and why each one:

  ``$( ... )``   command substitution. Executes in normal text AND inside
                 double quotes; NOT inside single quotes.
  `` ` ... ` ``  the legacy spelling of the same thing, with the same quoting
                 rules.
  ``<( ... )``   process substitution, and its ``>( ... )`` twin. Executes too,
                 but -- unlike ``$()`` -- it is NOT expanded inside double
                 quotes, so it is collected only from unquoted text. Getting
                 that asymmetry wrong in the permissive direction would miss a
                 real execution; getting it wrong in the strict direction would
                 gate the literal text ``"a<(b)"``, which is a mention.

WHAT IS DELIBERATELY OUT OF SCOPE:

  ``${ ... }``   parameter expansion is not execution. It is untouched here,
                 exactly as ``shell_grouping`` left it. Note that a
                 substitution NESTED inside one (``${FOO:-$(rm -rf /)}``) is
                 still found, because the scan is linear over the whole string
                 and does not skip the braces -- the ``$(`` inside them is
                 reached on its own terms.
  ``$(( ... ))`` arithmetic expansion does not run commands. It is not
                 special-cased: the uniform ``$(`` handler yields the body
                 ``(2 + 2)``, whose first token matches nothing in any table,
                 so the answer is right without a second code path -- while
                 ``$( (rm -rf /) )``, which IS a substitution wrapping a
                 subshell, still yields its inner command to the caller.
  indirection    a command assembled from a variable, read from a file, or
                 decoded at runtime is not literal text and is not visible to
                 any static scan. This is the same accepted ceiling every other
                 lane in this package carries.

HEREDOCS, stated because the answer is asymmetric. An unquoted heredoc body
expands substitutions and a quoted one (``<<'EOF'``) does not; the scanner does
not model heredoc delimiters, so a body is read as ordinary text. That is
correct for the unquoted form and over-strict for the quoted form -- the error
can only cost a false positive on a rare spelling, never a missed execution.

The result is an ANALYSIS form only. Nothing here executes, rewrites, or
returns a command to run; callers feed the extracted bodies back through their
own classifiers ADDITIVELY, so this can only ADD a verdict, never remove one.

Public API:
    extract_substitutions(command: str) -> list[str]
"""

from __future__ import annotations

from typing import List

# Enough nesting depth for any honest command; past this the obfuscation-depth
# limit in bash_validator has its own say. The bound only keeps a pathological
# string from recursing without end.
_MAX_NESTING_DEPTH = 8

# Upper bound on how many bodies one command may yield. A command long enough
# to exceed this is already past every other size guard in the pipeline; the cap
# exists so a degenerate string cannot turn classification into a long walk.
_MAX_SUBSTITUTIONS = 64

_PROCESS_SUBSTITUTION_OPENERS = ("<(", ">(")


def _find_matching_paren(text: str, start: int) -> int:
    """Return the index of the ``)`` that closes the ``(`` opened before *start*.

    Args:
        text: The full string being scanned.
        start: Index of the first character INSIDE the already-opened paren.

    Returns:
        Index of the matching ``)``, or ``-1`` when the string ends first --
        which happens legitimately when an operator split has already cut the
        closer off the component being classified.

    Nesting is counted only for parens that are not themselves quoted, so
    ``$(echo "a)b")`` closes on the last paren rather than on the quoted one.
    """
    depth = 1
    i = start
    length = len(text)
    quote = ""
    while i < length:
        ch = text[i]
        if quote == "'":
            if ch == "'":
                quote = ""
            i += 1
            continue
        if ch == "\\":
            i += 2
            continue
        if quote == '"':
            if ch == '"':
                quote = ""
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _find_closing_backtick(text: str, start: int) -> int:
    """Return the index of the backtick closing the one opened before *start*.

    Returns ``-1`` when none remains. A backslash-escaped backtick does not
    close the substitution -- that is how a nested one is spelled.
    """
    i = start
    length = len(text)
    while i < length:
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "`":
            return i
        i += 1
    return -1


def _collect(command: str, depth: int, out: List[str]) -> None:
    """Append every executing substitution body in *command* to *out*.

    Walks the string once, left to right, holding the shell's own quoting state
    so that what counts as execution here is what would execute there. Recurses
    into each body it finds, so a nested substitution is reported alongside its
    parent rather than hidden inside it.
    """
    if depth > _MAX_NESTING_DEPTH or len(out) >= _MAX_SUBSTITUTIONS:
        return

    i = 0
    length = len(command)
    quote = ""
    # A ``#`` starts a comment only at the beginning of a word. ``foo#bar`` and
    # ``http://x/#frag`` are not comments, and treating them as such would blind
    # the scan to whatever followed on the line.
    at_word_start = True

    while i < length and len(out) < _MAX_SUBSTITUTIONS:
        ch = command[i]

        if quote == "'":
            # Single quotes suspend every expansion, including the backslash.
            if ch == "'":
                quote = ""
            i += 1
            continue

        if ch == "\\":
            # Outside single quotes a backslash escapes the next character, so
            # ``\$(`` and an escaped backtick are literal text, not execution.
            i += 2
            at_word_start = False
            continue

        if quote == '"':
            if ch == '"':
                quote = ""
                i += 1
                at_word_start = False
                continue
            # Fall through: ``$(`` and backticks DO expand inside double quotes.
        else:
            if ch == "'":
                quote = "'"
                i += 1
                at_word_start = False
                continue
            if ch == '"':
                quote = '"'
                i += 1
                at_word_start = False
                continue
            if ch == "#" and at_word_start:
                newline = command.find("\n", i)
                if newline == -1:
                    return
                i = newline + 1
                at_word_start = True
                continue

        if ch == "$" and command.startswith("$(", i):
            close = _find_matching_paren(command, i + 2)
            body = command[i + 2:] if close == -1 else command[i + 2:close]
            body = body.strip()
            if body:
                out.append(body)
                _collect(body, depth + 1, out)
            i = length if close == -1 else close + 1
            at_word_start = False
            continue

        if ch == "`":
            close = _find_closing_backtick(command, i + 1)
            body = command[i + 1:] if close == -1 else command[i + 1:close]
            body = body.strip()
            if body:
                out.append(body)
                _collect(body, depth + 1, out)
            i = length if close == -1 else close + 1
            at_word_start = False
            continue

        if quote == "" and command[i:i + 2] in _PROCESS_SUBSTITUTION_OPENERS:
            close = _find_matching_paren(command, i + 2)
            body = command[i + 2:] if close == -1 else command[i + 2:close]
            body = body.strip()
            if body:
                out.append(body)
                _collect(body, depth + 1, out)
            i = length if close == -1 else close + 1
            at_word_start = False
            continue

        at_word_start = ch.isspace() or ch in (";", "&", "|", "(", ")")
        i += 1


def extract_substitutions(command: str) -> List[str]:
    """Return the body of every substitution *command* would execute.

    Args:
        command: A raw Bash command line, as the harness sent it -- not an
            operator-split component and not a pre-normalized form. Quoting
            decides the answer, and an upstream split can break the quoting.

    Returns:
        The inner command of each ``$( )``, backtick and process substitution
        that the shell would actually run, outermost first, with nested ones
        following their parent. Bodies inside single quotes, behind a
        backslash, or in a comment are absent, because the shell does not run
        those either. An empty list means the string carries no execution
        beyond the command as written.
    """
    if not command or ("$(" not in command and "`" not in command
                       and "<(" not in command and ">(" not in command):
        return []

    out: List[str] = []
    _collect(command, 0, out)
    return out
