#!/usr/bin/env python3
"""Scratch: find a command that hits the REGEX branch (not the semantic rule)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path("hooks")))
import modules.security.blocked_commands as bc  # noqa: E402

# Candidates likely regex-only (no semantic rule): disk ops, sql, npm unpublish.
cands = [
    " ".join(["dd", "if=/dev/zero", "of=/dev/sda"]),
    " ".join(["mkfs.ext4", "/dev/sda1"]),
    " ".join(["drop", "database", "prod"]),
    " ".join(["drop", "table", "users"]),
    " ".join(["npm", "unpublish", "pkg", "--force"]),
    " ".join(["gh", "repo", "delete", "org/repo"]),
]
for c in cands:
    sem = bc._match_semantic_block_rule(c)
    r = bc.is_blocked_command(c)
    via = "semantic" if sem is not None else ("regex" if r.is_blocked else "none")
    print(f"{c!r:42s} blocked={r.is_blocked} via={via} sugg={r.suggestion!r}")
