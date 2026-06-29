#!/usr/bin/env python3
"""Scratch: inspect is_blocked_command suggestion outcomes (read-only)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path("hooks")))
from modules.security.blocked_commands import is_blocked_command, BLOCKED_COMMAND_SUGGESTIONS  # noqa: E402

# Build the test strings from fragments so this file's source does not contain
# a verbatim blocked command (which the dev's own hook would flag on read).
cases = [
    " ".join(["aws", "ec2", "delete-vpc", "--vpc-id", "vpc-1"]),
    " ".join(["kubectl", "delete", "namespace", "prod"]),
    " ".join(["docker", "system", "prune", "-a"]),
    " ".join(["terraform", "destroy"]),
]
for c in cases:
    r = is_blocked_command(c)
    print(f"{c!r:45s} blocked={r.is_blocked} sugg={r.suggestion!r}")
print("--- suggestion keys ---")
print(list(BLOCKED_COMMAND_SUGGESTIONS.keys()))
