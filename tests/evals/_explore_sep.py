#!/usr/bin/env python3
"""Scratch: brute-force inputs that distinguish each _has_unquoted_separator mutant.

Defines the original and parametric mutated variants, then searches a corpus of
candidate command strings for one that produces a DIFFERENT boolean -> that
input kills the mutant. Mutants where NO input in the corpus distinguishes are
candidate-equivalents (verify by reasoning).
"""

_SEP = ("&&", "||", ";", "|", "`", "$(")


def make(variant):
    def f(command):
        in_single = False
        in_double = False
        i = 0
        n = len(command)
        while (i < n) if variant != "705_lt_ne" else (i != n) if False else _loop705(variant, i, n):
            ch = command[i]
            # line 707 escape guard
            if _escape_guard(variant, ch, i, n):
                i = _escape_incr(variant, i)
                if variant != "709_break":
                    continue
                else:
                    break
            # line 710 single-quote toggle
            if _single_guard(variant, ch, in_double):
                in_single = _single_set(variant, in_single)
                i += 2 if variant == "712_num2" else 1
                continue
            # line 714 double-quote toggle
            if _double_guard(variant, ch, in_single):
                in_double = _double_set(variant, in_double)
                i += 2 if variant == "716_num2" else 1
                continue
            if _outside_guard(variant, in_single, in_double):
                for sep in _SEP:
                    if command.startswith(sep, i):
                        return True
            i += 1
        return False
    return f


def _loop705(variant, i, n):
    if variant == "705_lt_ne":
        return i != n
    if variant == "705_lt_isnot":
        return i is not n
    return i < n


def _escape_guard(variant, ch, i, n):
    is_bs = (ch is "\\") if variant == "707_eq_is" else (ch == "\\")
    if variant == "707_add_sub":
        rhs = i - 1
    elif variant == "707_add_mul":
        rhs = i * 1
    elif variant == "707_add_div":
        rhs = i / 1
    elif variant == "707_add_floordiv":
        rhs = i // 1
    elif variant == "707_add_mod":
        rhs = i % 1
    elif variant == "707_add_pow":
        rhs = i ** 1
    elif variant == "707_add_rshift":
        rhs = i >> 1
    elif variant == "707_add_lshift":
        rhs = i << 1
    elif variant == "707_add_bitor":
        rhs = i | 1
    elif variant == "707_add_bitand":
        rhs = i & 1
    elif variant == "707_add_bitxor":
        rhs = i ^ 1
    elif variant == "707_num_2":
        rhs = i + 2
    elif variant == "707_num_0":
        rhs = i + 0
    else:
        rhs = i + 1
    if variant == "707_lt_ne":
        cmp = rhs != n
    elif variant == "707_lt_lte":
        cmp = rhs <= n
    elif variant == "707_lt_isnot":
        cmp = rhs is not n
    else:
        cmp = rhs < n
    return is_bs and cmp


def _escape_incr(variant, i):
    if variant == "708_num":
        return i + 1  # mutated 2->1 (representative)
    return i + 2


def _single_guard(variant, ch, in_double):
    eq = (ch is "'") if variant == "710_eq_is" else (ch == "'")
    nd = (in_double) if variant == "710_del_not" else (not in_double)
    return eq and nd


def _single_set(variant, in_single):
    return in_single if variant == "711_del_not" else (not in_single)


def _double_guard(variant, ch, in_single):
    eq = (ch is '"') if variant == "714_eq_is" else (ch == '"')
    ns = (in_single) if variant == "714_del_not" else (not in_single)
    return eq and ns


def _double_set(variant, in_double):
    return in_double if variant == "715_del_not" else (not in_double)


def _outside_guard(variant, in_single, in_double):
    if variant == "718_and_or":
        return (not in_single) or (not in_double)
    return (not in_single) and (not in_double)


ORIG = make("orig")

MUTANTS = [
    "705_lt_ne", "705_lt_isnot",
    "707_eq_is", "707_add_sub", "707_add_mul", "707_add_div", "707_add_floordiv",
    "707_add_mod", "707_add_pow", "707_add_rshift", "707_add_lshift",
    "707_add_bitor", "707_add_bitand", "707_add_bitxor", "707_num_2", "707_num_0",
    "707_lt_ne", "707_lt_lte", "707_lt_isnot",
    "708_num", "709_break",
    "710_eq_is", "710_del_not", "711_del_not",
    "714_eq_is", "714_del_not", "715_del_not",
    "712_num2", "716_num2",
    "717_break_dummy",
    "718_and_or",
]

# Candidate corpus: separators at start/mid/end, inside/after quotes, escapes,
# trailing backslashes, nested.
CORPUS = [
    "", "a", "a|b", "|", "&&", ";", "`", "$(",
    "&& rm", "| rm", "; rm", "`id`", "$(id)",
    "\\|", "\\;", "\\&&", "a\\|b", "a\\|", "\\|b",
    "echo hello\\", "x\\\\|y", "\\\\|",
    "grep 'a|b' f", 'grep "a|b" f', "grep 'a' f && rm", 'grep "a" f ; rm',
    "echo \"it's | x\"", "echo 'say \" | x'",
    "echo \\\" | rm", "a\\x | b", "grep 'a | b' x | rm",
    "'", '"', "''", '""', "'|'", '"|"', "'\\''", "a'b|c", 'a"b|c',
    "x'|", 'x"|', "||x", ";;", "|;|", "grep -E 'a|b'",
    "\\` x", "$( |", "` | `",
    # backslash DEEP in the string (large i) to probe i<<1 / i**1 / i&1 / etc.
    "aaaa\\|", "aaaaaa\\|b", "abcdef\\;x", "xxxxxxxx\\&&y",
    "aa\\|", "aaa\\|", "aaaaa\\|", "a\\|", "aa\\'b'|c",
    # long inputs (> 256) to probe int-identity-sensitive `is` mutants
    "a" * 300 + " | rm",
    "a" * 300,
    "'" + "a" * 300 + "' | rm",
    "\\" + "a" * 300 + "|",
    "x" * 300 + "\\",
    # char right after an opening quote = a separator: probes i+=2 at the
    # quote toggle (line 712 single / line 716 double), which would skip it.
    "'|'a&&b", "'|x'", "'||'", "'|'",
    '"|"a&&b', '"|x"', '"||"', '"|"',
    "a'|'&&b", 'a"|"&&b',
    "'|' && rm", '"|" && rm',
    "x'|y'|z", 'x"|y"|z',
    "'&&'|x", '"&&"|x',
]


def main():
    for m in MUTANTS:
        if m.endswith("_dummy"):
            continue
        mut = make(m)

        def outcome(fn, c):
            try:
                return ("v", fn(c))
            except Exception as e:  # noqa: BLE001
                return ("exc", type(e).__name__)

        killers = [c for c in CORPUS if outcome(mut, c) != outcome(ORIG, c)]
        if killers:
            ex = killers[0]
            disp = ex if len(ex) <= 28 else ex[:12] + "..." + ex[-8:]
            print(f"KILL {m:16s} by {disp!r:30s} orig={outcome(ORIG, ex)} mut={outcome(mut, ex)}")
        else:
            print(f"EQUIV? {m:16s} -- no corpus input distinguishes")


if __name__ == "__main__":
    main()
