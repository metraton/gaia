#!/usr/bin/env python3
"""Scratch: craft a regex-blocked command whose first vs last suggestion
prefix differ, to kill the L625 break->continue mutant."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path("hooks")))
import modules.security.blocked_commands as bc  # noqa: E402

keys = list(bc.BLOCKED_COMMAND_SUGGESTIONS.keys())
# "dd" sorts early in the dict; "drop table" later. A command containing both
# substrings, blocked by the drop-table regex.
c = " ".join(["drop", "table", "users", ";", "dd"])
print("blocked:", bc.is_blocked_command(c).is_blocked,
      "semantic:", bc._match_semantic_block_rule(c) is not None)
matched = [(i, k) for i, k in enumerate(keys) if k in c.lower()]
print("matched (dict-order):", matched)
print("first:", matched[0][1] if matched else None,
      "last:", matched[-1][1] if matched else None)
r = bc.is_blocked_command(c)
print("orig suggestion:", repr(r.suggestion))
