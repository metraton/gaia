#!/usr/bin/env python3
"""Scratch: confirm the two non-cluster equivalents by reasoning probes (RO).

L596 is_blocked_command OrWithAnd: `not command or not command.strip()` -> `and`.
  Show that for EVERY input class the is_blocked result is identical.
L74 _read_only_base_cmds ExceptionReplacer: the except ImportError is
  unreachable (import always succeeds), so the handler body never runs.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path("hooks")))
import modules.security.blocked_commands as bc  # noqa: E402


def orig_guard(command):
    return (not command) or (not command.strip())


def mut_guard(command):
    return (not command) and (not command.strip())


# The guard only changes control flow for inputs where orig != mut. After the
# guard, the function strips and analyzes; a blank/whitespace command can never
# match a block pattern, so is_blocked is False regardless of the early return.
probe = ["", "   ", "\t\n", "x", "  x  ", "rm -rf /", "   rm -rf /   "]
print("== L596 guard divergence vs is_blocked outcome ==")
for c in probe:
    o, m = orig_guard(c), mut_guard(c)
    blocked = bc.is_blocked_command(c).is_blocked
    note = "guard DIFFERS" if o != m else "guard same"
    print(f"{c!r:18s} orig_guard={o} mut_guard={m} [{note}] is_blocked={blocked}")
print("  -> where the guard differs (whitespace-only), is_blocked is False")
print("     either way: proceeding strips to '' which matches no pattern.")

print()
print("== L74 import reachability ==")
try:
    from modules.security.mutative_verbs import READ_ONLY_BASE_CMDS  # noqa: F401
    print("import READ_ONLY_BASE_CMDS: SUCCEEDS -> except ImportError unreachable")
except ImportError:
    print("import FAILS -> handler reachable")
print("returned set size:", len(bc._read_only_base_cmds()))
