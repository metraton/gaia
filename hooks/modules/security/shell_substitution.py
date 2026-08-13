r"""
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

THE QUOTING FORMS THE SCANNER MODELS. Everything above rests on the quoting
state being RIGHT, so a form modelled wrong is a bypass of the whole module
rather than a rough edge. Four states are carried, and the list is exhaustive
by intent -- a reader adding a fifth quoting construct to bash would have to
add it here:

  ``'...'``      plain single quote. Suspends every expansion INCLUDING the
                 backslash, so it is resolved before the escape handling.
  ``$'...'``     ANSI-C quoting. Suspends expansion but HONOURS the backslash,
                 so it is resolved after the escape handling. The two single
                 quote forms differ on exactly this one rule, and collapsing
                 them was a measured false negative: ``$'it\'s'`` read as a
                 plain quote appears to close at the escaped quote, desyncs the
                 state, and hides every substitution to its right.
  ``"..."``      double quote. Does NOT suspend ``$()`` or backticks; does
                 suspend process substitution.
  ``$"..."``     locale translation. Identical to a double quote for expansion
                 purposes, which is what falls out of treating the ``$`` as an
                 ordinary character -- no state of its own.

WHAT IS DELIBERATELY OUT OF SCOPE:

  ``${ ... }``   parameter expansion is not execution, and is not treated as a
                 unit by the linear scan: a substitution NESTED inside one
                 (``${FOO:-$(rm -rf /)}``) really does execute, and it is found
                 because the ``$(`` inside the braces is reached on its own
                 terms. The paren counter is the one place that must know where
                 an expansion ENDS, since a ``)`` carried as data
                 (``${x//)/y}``) would otherwise close a substitution body
                 early and hand the caller a truncated command.
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

  ``<<EOF``      heredoc with an UNQUOTED delimiter. The body expands, so it is
                 scanned like ordinary text.
  ``<<'EOF'``    heredoc with a QUOTED delimiter (single, double, or a
                 backslash anywhere in the word). The body is literal and is
                 skipped entirely -- scanning it would gate text the shell only
                 ever prints.
  ``<<-EOF``     the same two, with leading TABS stripped from the terminator.
  ``<<<word``    a here-STRING, not a heredoc. It expands and has no body, so
                 it is deliberately NOT treated as an opener.

HEREDOCS WERE THE ONE CONSTRUCT LEFT UNMODELLED, on a stated argument that the
error could only cost a false positive and never a missed execution. That
argument was FALSE, and the way it failed is worth keeping: leaving the body as
ordinary text does not merely misread the body, it desynchronises the QUOTING
STATE for everything after it. One ordinary apostrophe in a heredoc body -- an
English contraction, the most mundane content there is -- opened a single-quote
state that never closed, and every character to the right of it, on every later
line, was then treated as quoted. A real substitution on a line AFTER the
heredoc became invisible, and a write into the protected hooks directory
classified as a read. The false positive was real too, in the other direction:
a QUOTED heredoc whose body merely names a substitution was permanently denied.
So one missing model cost an execution AND a spurious denial, and modelling it
is what closes both.

The result is an ANALYSIS form only. Nothing here executes, rewrites, or
returns a command to run; callers feed the extracted bodies back through their
own classifiers ADDITIVELY, so this can only ADD a verdict, never remove one.

Public API:
    extract_substitutions(command: str) -> list[str]
"""

from __future__ import annotations

from typing import List, Tuple

# Enough nesting depth for any honest command; past this the obfuscation-depth
# limit in bash_validator has its own say. The bound only keeps a pathological
# string from recursing without end.
#
# It must not sit BELOW the descent bound its consumers apply. A consumer that
# re-enters per level (the mutative lane) reaches bodies deeper than a consumer
# reading this flat list (the permanent-deny floor, the protected-path guard),
# and when the two disagree the deeper reader is the one with the SOFTER verdict
# -- a floor body nested between the two numbers was reported approvable rather
# than categorical, because only the lane that could still see it was the lane
# that cannot answer categorically. Measured at 8 against a bound of 12.
_MAX_NESTING_DEPTH = 12

# Upper bound on how many bodies one command may yield. A command long enough
# to exceed this is already past every other size guard in the pipeline; the cap
# exists so a degenerate string cannot turn classification into a long walk.
#
# Reaching it TRUNCATES the scan, so the bodies past it were never looked at --
# which is why ``extract_substitutions_truncated`` reports that fact instead of
# letting a short list pass for a complete one. Sixty-four read substitutions
# followed by a mutation returned the sixty-four reads and no mutation, and the
# command classified free.
_MAX_SUBSTITUTIONS = 64

_PROCESS_SUBSTITUTION_OPENERS = ("<(", ">(")


def _carries_an_opener(text: str) -> bool:
    """Return whether *text* could contain any substitution at all.

    A cheap necessary condition, not a parse: used to skip whole strings and to
    tell "nothing more to find" from "stopped looking", which are the same
    length of list and opposite verdicts.
    """
    return bool(text) and (
        "$(" in text or "`" in text or "<(" in text or ">(" in text
    )


def _read_heredoc_opener(text: str, i: int) -> "Tuple[str, bool, bool, int] | None":
    """Parse a heredoc redirection beginning at *i*, or return None.

    Args:
        text: The string being scanned.
        i: Index of the first ``<`` of a candidate ``<<``.

    Returns:
        ``(delimiter, expands, strip_tabs, next_index)`` where *expands* is
        True for an UNQUOTED delimiter (the body runs substitutions) and False
        when any quoting appears in the delimiter word (the body is literal);
        *next_index* is the index just past the delimiter word, so the rest of
        the LINE keeps being scanned as ordinary command text.

        ``None`` when this is not a heredoc: a here-string ``<<<``, or a ``<<``
        with no delimiter word after it.
    """
    if text.startswith("<<<", i):
        return None
    if not text.startswith("<<", i):
        return None

    cursor = i + 2
    strip_tabs = False
    if cursor < len(text) and text[cursor] == "-":
        strip_tabs = True
        cursor += 1
    while cursor < len(text) and text[cursor] in " \t":
        cursor += 1

    delimiter: List[str] = []
    expands = True
    while cursor < len(text):
        ch = text[cursor]
        if ch in " \t\n;&|<>()":
            break
        if ch == "\\":
            expands = False
            cursor += 1
            if cursor < len(text):
                delimiter.append(text[cursor])
                cursor += 1
            continue
        if ch in ("'", '"'):
            expands = False
            closing = text.find(ch, cursor + 1)
            if closing == -1:
                return None
            delimiter.append(text[cursor + 1:closing])
            cursor = closing + 1
            continue
        delimiter.append(ch)
        cursor += 1

    word = "".join(delimiter)
    if not word:
        return None
    return word, expands, strip_tabs, cursor


def _heredoc_body_span(
    text: str, start: int, delimiter: str, strip_tabs: bool,
) -> "Tuple[int, int, bool]":
    """Locate the body of a heredoc whose lines begin at *start*.

    Returns:
        ``(body_start, resume_index, terminated)``. *resume_index* is where
        scanning continues after the terminator line. *terminated* is False
        when no terminator line exists, which the caller must treat as the
        conservative case rather than the convenient one: a delimiter this
        scanner failed to recognise would otherwise let it skip the remainder
        of a real command line.
    """
    cursor = start
    length = len(text)
    while cursor < length:
        line_end = text.find("\n", cursor)
        if line_end == -1:
            line_end = length
        line = text[cursor:line_end]
        candidate = line.lstrip("\t") if strip_tabs else line
        if candidate.rstrip("\r") == delimiter:
            return start, min(line_end + 1, length), True
        if line_end >= length:
            break
        cursor = line_end + 1
    return start, length, False

# Quoting states. Three of the four are the obvious ones; the fourth exists
# because the shell has TWO single-quote forms whose escaping rules are
# opposite, and collapsing them is a false negative rather than a cosmetic
# simplification. In a plain ``'...'`` the backslash is an ordinary character;
# in ANSI-C ``$'...'`` it is an escape, so ``$'it\'s'`` contains a quote and
# does NOT end there. Reading the second as the first makes the scanner believe
# the string closed early, and every character to the right is then treated as
# quoted -- which silently hides a real substitution:
#
#     echo $'it\'s' $(rm -rf /)   -> the delete runs; a collapsed state
#                                    machine reports nothing at all
#
# The locale-translation form ``$"..."`` needs no state of its own: it expands
# exactly like a double quote, which is what falls out of treating the ``$`` as
# an ordinary character before it.
_Q_NONE = ""
_Q_SINGLE = "'"
_Q_DOUBLE = '"'
_Q_ANSI_C = "$'"


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
    A paren inside a PARAMETER EXPANSION does not count either: ``${x//)/y}``
    carries a close-paren as data, and counting it ends the body early, which
    truncates the command the caller is about to classify.

    The quoting rules here must match ``_collect``'s exactly -- the two walk the
    same grammar, and a divergence between them is a body that one finds and the
    other cuts in half.
    """
    depth = 1
    i = start
    length = len(text)
    quote = _Q_NONE
    while i < length:
        ch = text[i]
        if quote == _Q_SINGLE:
            if ch == "'":
                quote = _Q_NONE
            i += 1
            continue
        if ch == "\\":
            i += 2
            continue
        if quote == _Q_ANSI_C:
            if ch == "'":
                quote = _Q_NONE
            i += 1
            continue
        if quote == _Q_DOUBLE:
            if ch == '"':
                quote = _Q_NONE
            i += 1
            continue
        if text.startswith("$'", i):
            quote = _Q_ANSI_C
            i += 2
            continue
        if text.startswith("${", i):
            i = _skip_parameter_expansion(text, i)
            continue
        if text.startswith("<<<", i):
            # Here-string: consumed whole, so its second ``<`` is never read as
            # a heredoc opener -- see the same step in ``_collect``.
            i += 3
            continue
        if ch == "<" and text.startswith("<<", i):
            # A heredoc body is DATA. A paren inside it closes nothing, so the
            # body is skipped wholesale here regardless of whether it expands --
            # which is also what keeps this walker's idea of where a body ends
            # identical to ``_collect``'s.
            opener = _read_heredoc_opener(text, i)
            if opener is not None:
                delimiter, _expands, strip_tabs, after_word = opener
                newline = text.find("\n", after_word)
                if newline != -1:
                    _body, resume, terminated = _heredoc_body_span(
                        text, newline + 1, delimiter, strip_tabs,
                    )
                    if terminated:
                        i = resume
                        continue
                i = after_word
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


def _skip_parameter_expansion(text: str, start: int) -> int:
    """Return the index just past the ``${...}`` beginning at *start*.

    Only the paren counter needs this. ``_collect`` deliberately does NOT skip a
    parameter expansion, because a command substitution nested inside one
    (``${FOO:-$(rm -rf /)}``) really does execute and must still be found; there
    a stray ``}`` is inert anyway. Braces are counted so a nested expansion
    closes at the right place, and an unterminated one consumes the rest of the
    string rather than reopening the paren scan mid-expansion.
    """
    depth = 0
    i = start + 1  # the "$" is not part of the brace balance
    length = len(text)
    while i < length:
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return length


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


def _collect(
    command: str, depth: int, out: List[str], recurse: bool = True,
) -> bool:
    """Append every executing substitution body in *command* to *out*.

    Walks the string once, left to right, holding the shell's own quoting state
    so that what counts as execution here is what would execute there. Recurses
    into each body it finds, so a nested substitution is reported alongside its
    parent rather than hidden inside it.

    ``recurse=False`` stops at the OUTERMOST bodies. A caller that re-classifies
    each body through a classifier which itself calls back here does not want
    the flattened list: the nested body would then be judged twice, once with
    the context its parent establishes and once with the context of whoever
    holds the flat list, and those two differ the moment the parent begins with
    a ``cd``.

    Returns:
        True when the body-count cap stopped the walk with input still
        unexamined -- NOT merely when the cap was reached. A command carrying
        exactly the cap's worth of substitutions and nothing more has been read
        completely, and reporting that as truncated made a long-but-honest
        command demand consent for nothing.
    """
    if len(out) >= _MAX_SUBSTITUTIONS:
        # No room left. That only LOSES something if this text actually carries
        # an opener -- a body already counted, whose own content holds no
        # substitution, has nothing further to give up.
        return _carries_an_opener(command)
    if depth > _MAX_NESTING_DEPTH:
        return False
    truncated = False

    i = 0
    length = len(command)
    quote = _Q_NONE
    # A ``#`` starts a comment only at the beginning of a word. ``foo#bar`` and
    # ``http://x/#frag`` are not comments, and treating them as such would blind
    # the scan to whatever followed on the line.
    at_word_start = True
    # Heredocs declared on the current line, in declaration order. Their bodies
    # do not start where the ``<<`` appears -- they start after the newline, and
    # the rest of the declaring line is ordinary command text in between.
    pending_heredocs: List[Tuple[str, bool, bool]] = []

    while i < length and len(out) < _MAX_SUBSTITUTIONS:
        ch = command[i]

        if ch == "\n" and pending_heredocs and quote == _Q_NONE:
            cursor = i + 1
            for delimiter, expands, strip_tabs in pending_heredocs:
                body_start, resume, terminated = _heredoc_body_span(
                    command, cursor, delimiter, strip_tabs,
                )
                if not terminated:
                    # No terminator line: either the string was cut short by an
                    # upstream split, or this scanner misread the delimiter
                    # word. Skipping to the end on that guess is the one outcome
                    # that could hide an execution, so the remainder is left to
                    # ordinary scanning instead.
                    break
                if expands:
                    truncated = _collect(
                        command[body_start:resume], depth + 1, out, recurse,
                    ) or truncated
                cursor = resume
            else:
                pending_heredocs = []
                i = cursor
                at_word_start = True
                continue
            pending_heredocs = []
            i += 1
            at_word_start = True
            continue

        if quote == _Q_SINGLE:
            # A plain single quote suspends every expansion, the backslash
            # included -- so it is handled BEFORE the backslash branch below.
            if ch == "'":
                quote = _Q_NONE
            i += 1
            continue

        if ch == "\\":
            # Everywhere else -- unquoted, double-quoted, and ANSI-C -- a
            # backslash escapes the next character, so ``\$(`` and an escaped
            # backtick are literal text rather than execution.
            i += 2
            at_word_start = False
            continue

        if quote == _Q_ANSI_C:
            # Reached only past the backslash branch, which is the whole point:
            # ``$'it\'s'`` keeps going after the escaped quote, exactly as the
            # shell does, instead of closing there and desyncing the state.
            if ch == "'":
                quote = _Q_NONE
            i += 1
            at_word_start = False
            continue

        if quote == _Q_DOUBLE:
            if ch == '"':
                quote = _Q_NONE
                i += 1
                at_word_start = False
                continue
            # Fall through: ``$(`` and backticks DO expand inside double quotes.
        else:
            # ANSI-C quoting is a form of QUOTE, not an expansion, so it is
            # recognized here rather than beside ``$(``. Inside double quotes a
            # ``$'`` is an ordinary dollar followed by an ordinary quote, which
            # is why this sits in the unquoted branch only.
            if command.startswith("$'", i):
                quote = _Q_ANSI_C
                i += 2
                at_word_start = False
                continue
            if ch == "'":
                quote = _Q_SINGLE
                i += 1
                at_word_start = False
                continue
            if ch == '"':
                quote = _Q_DOUBLE
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
            if command.startswith("<<<", i):
                # A here-string, not a heredoc: its word EXPANDS and there is no
                # body. The whole operator is consumed in one step so its second
                # ``<`` is never reached as a start position -- read from there,
                # ``< "$(...)"`` parses as a heredoc whose delimiter is the
                # quoted word, which swallowed the substitution it should run.
                i += 3
                at_word_start = False
                continue
            if ch == "<" and command.startswith("<<", i):
                # Only the DECLARATION is here; the body begins after the
                # newline, handled at the top of this loop.
                opener = _read_heredoc_opener(command, i)
                if opener is not None:
                    delimiter, expands, strip_tabs, after_word = opener
                    pending_heredocs.append((delimiter, expands, strip_tabs))
                    i = after_word
                    at_word_start = False
                    continue

        if ch == "$" and command.startswith("$(", i):
            close = _find_matching_paren(command, i + 2)
            body = command[i + 2:] if close == -1 else command[i + 2:close]
            body = body.strip()
            if body:
                out.append(body)
                if recurse:
                    truncated = _collect(
                        body, depth + 1, out, recurse,
                    ) or truncated
            i = length if close == -1 else close + 1
            at_word_start = False
            continue

        if ch == "`":
            close = _find_closing_backtick(command, i + 1)
            body = command[i + 1:] if close == -1 else command[i + 1:close]
            body = body.strip()
            if body:
                out.append(body)
                if recurse:
                    truncated = _collect(
                        body, depth + 1, out, recurse,
                    ) or truncated
            i = length if close == -1 else close + 1
            at_word_start = False
            continue

        if quote == "" and command[i:i + 2] in _PROCESS_SUBSTITUTION_OPENERS:
            close = _find_matching_paren(command, i + 2)
            body = command[i + 2:] if close == -1 else command[i + 2:close]
            body = body.strip()
            if body:
                out.append(body)
                if recurse:
                    truncated = _collect(
                        body, depth + 1, out, recurse,
                    ) or truncated
            i = length if close == -1 else close + 1
            at_word_start = False
            continue

        at_word_start = ch.isspace() or ch in (";", "&", "|", "(", ")")
        i += 1

    # Exiting with input left means the cap ended the walk, not the string.
    return truncated or i < length


def extract_substitutions(
    command: str, top_level_only: bool = False,
) -> List[str]:
    """Return the body of every substitution *command* would execute.

    Args:
        command: A raw Bash command line, as the harness sent it -- not an
            operator-split component and not a pre-normalized form. Quoting
            decides the answer, and an upstream split can break the quoting.
        top_level_only: Return only the OUTERMOST bodies, leaving a nested
            substitution inside the body that contains it. The default flat
            list is right for a caller that classifies each body in isolation;
            this mode is for a caller whose classifier re-enters here on every
            body it is handed, so nesting is already covered by that re-entry
            and each level is judged in the context its own parent establishes
            rather than the outermost one's.

    Returns:
        The inner command of each ``$( )``, backtick and process substitution
        that the shell would actually run, outermost first, with nested ones
        following their parent. Bodies inside single quotes, behind a
        backslash, or in a comment are absent, because the shell does not run
        those either. An empty list means the string carries no execution
        beyond the command as written.
    """
    return extract_substitutions_truncated(command, top_level_only)[0]


def extract_substitutions_truncated(
    command: str, top_level_only: bool = False,
) -> Tuple[List[str], bool]:
    """Return the substitution bodies AND whether the scan ran out of room.

    Same extraction as ``extract_substitutions``; the second element is what
    that function cannot express. The body-count cap is applied as a scan
    condition, so hitting it does not merely shorten the list -- it stops the
    walk, and every body to the right of the cap goes unexamined. A caller that
    reads only the list cannot tell "no more substitutions" from "stopped
    looking", and the two demand opposite verdicts: the first is a complete
    answer, the second is an unfinished one.

    Returns:
        ``(bodies, truncated)``. ``truncated`` is True when the cap stopped the
        scan, meaning the list is a prefix of what the command really carries
        and a caller that gates on it must treat the remainder as unproven
        rather than absent.
    """
    if not _carries_an_opener(command):
        return [], False

    out: List[str] = []
    truncated = _collect(command, 0, out, not top_level_only)
    return out, truncated
