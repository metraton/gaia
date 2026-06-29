#!/usr/bin/env python3
"""Scratch: find a regex-branch command matching >=2 suggestion prefixes,
so first-match (break) vs last-match (continue) differ."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path("hooks")))
import modules.security.blocked_commands as bc  # noqa: E402

cands = [
    " ".join(["dd", "if=/dev/zero", "of=/dev/sda"]),
    " ".join(["mkfs.ext4", "/dev/sda1"]),
    " ".join(["drop", "database", "prod"]),
    " ".join(["drop", "table", "users"]),
    " ".join(["npm", "unpublish", "pkg"]),
]
keys = list(bc.BLOCKED_COMMAND_SUGGESTIONS.keys())
for c in cands:
    matched = [k for k in keys if k in c.lower()]
    if bc._match_semantic_block_rule(c) is None and bc.is_blocked_command(c).is_blocked:
        print(f"{c!r:38s} matched_prefixes={matched}")
