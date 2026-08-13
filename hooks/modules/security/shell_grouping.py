"""
shell_grouping.py -- strip shell GROUPING and SUBSTITUTION wrappers so the
first token of a command is the command.

Three independent layers of the classifier identify a command by its FIRST
WORD -- ``command_semantics.analyze_command`` (``base_cmd``),
``blocked_commands`` (``^``-anchored permanent-deny regexes) and
``protected_path_guard`` (``os.path.basename(tokens[0])``). A grouping
character glued to that word makes it match nothing in any table, and the
command passes as safe by elimination:

    (rm -rf /)          -> first token "(rm"  -> no alias, no regex, T0
    $(cp x .claude/...) -> first token "$(cp" -> the categorical .claude/
                           boundary never fires

The three layers each extract that token themselves, so the repair has to be
ONE normalization they all consume -- patching any single layer leaves the
other two open. In particular, teaching ``bash_validator._has_operators``
about parentheses repairs the ``rm`` case and leaves the protected-path
breach standing, because that guard does its own split and its own
extraction.

WHY ONLY THE FIRST AND LAST POSITION. A grouping character in the MIDDLE of a
command is not a hole: a mutative verb in a middle token is already gated
(``kubectl delete pod nginx``, ``terraform apply -auto-approve``), and the
gaia.db guard survives every wrapper form because it matches by PATH rather
than by first token. Widening the normalization past the ends would buy no
coverage and would cost the thing that matters more here than the hole --
MENTION vs USE. A dangerous command QUOTED inside another command's argument
is being written down, not run, and it is never at position 0; a
normalization that reached into the middle of a command would turn every such
mention into a use, which is the measured false-positive class this
repository already carries (a ``grep -rn "SessionStart" ...`` blocked as T3
because the quoted text was read as syntax).

WHY THE OPENER IS UNCONDITIONAL AND THE CLOSER IS NOT. Nothing legitimately
starts a command with ``(``, ``{``, ``$(`` or a backtick except a group or a
substitution, so a leading opener is always a wrapper. A TRAILING closer is
different: it may be closing a wrapper whose opener was cut off by an
operator split (``(cd /tmp && rm -rf /)`` splits into ``(cd /tmp`` and
``rm -rf /)``, and the surviving ``)`` is what defeats the end-anchored
``rm_critical`` regex), or it may be the honest end of a substitution used as
an argument (``ls -la $(pwd)``). The two are told apart by BALANCE: a closer
is stripped only when the string carries more of that closer than of its
opener, so the orphan remnant goes and the balanced argument stays.

Public API:
    strip_grouping_wrappers(command: str) -> str
"""

from __future__ import annotations

# Grouping / substitution OPENERS, longest first so ``$((`` is consumed as one
# unit rather than as ``$(`` plus a stray ``(``.
_OPENERS = ("$((", "$(", "((", "(", "{", "`")

# Their CLOSERS, longest first for the same reason.
_CLOSERS = ("))", ")", "}", "`")

# Each closer, reduced to the single characters balance is counted on: the
# closing character and the opening character it pairs with. ``))`` counts as
# two ``)`` against two ``(``, so it shares the parenthesis entry. The backtick
# is its own pair (opener is None), so it balances on parity -- an odd count
# means one is unmatched -- rather than against a distinct opening character.
_CLOSER_UNITS = {
    "))": (")", "("),
    ")": (")", "("),
    "}": ("}", "{"),
    "`": ("`", None),
}

# A command is not nested more than a handful of wrappers deep before the
# obfuscation-depth limit in bash_validator has its own say; this bound only
# keeps the peel from looping on pathological input.
_MAX_PEEL_ROUNDS = 8


def _has_orphan_closer(text: str, closer: str) -> bool:
    """Return True when *closer* appears in *text* without a matching opener.

    Counting is deliberately quote-unaware. Quote tracking would make the
    answer depend on quoting that the operator split has already broken, and
    the cost of the two errors is not symmetric: an undercount leaves a
    wrapper remnant in place (the hole this module closes), while an overcount
    only trims a trailing character from a command whose first token -- the
    thing every consumer keys on -- is unchanged.
    """
    closing_char, opening_char = _CLOSER_UNITS[closer]
    if opening_char is None:
        return text.count(closing_char) % 2 == 1
    return text.count(closing_char) > text.count(opening_char)


def strip_grouping_wrappers(command: str) -> str:
    """Return *command* with leading grouping/substitution wrappers removed.

    A leading ``(``, ``((``, ``{``, ``$(``, ``$((`` or backtick is stripped
    unconditionally; a trailing ``)``, ``))``, ``}`` or backtick is stripped
    only when it has no matching opener left in the string. Peeling repeats so
    a nested wrapper (``((rm -rf /))``) resolves, bounded by
    ``_MAX_PEEL_ROUNDS``.

    The result is an ANALYSIS form only -- it is never the string that gets
    executed. Callers pass it to first-token extraction and to the deny-pattern
    regexes; the command the user typed is untouched.

    Returns the input unchanged (modulo surrounding whitespace) when it carries
    no wrapper, so a caller can compare the two to decide whether a second,
    strictly-additive classification pass is worth running.
    """
    if not command:
        return command

    current = command.strip()
    for _ in range(_MAX_PEEL_ROUNDS):
        peeled = current

        for opener in _OPENERS:
            if peeled.startswith(opener):
                peeled = peeled[len(opener):].lstrip()
                break

        for closer in _CLOSERS:
            if peeled.endswith(closer) and _has_orphan_closer(peeled, closer):
                peeled = peeled[: len(peeled) - len(closer)].rstrip()
                break

        if peeled == current:
            return current
        current = peeled

    return current
