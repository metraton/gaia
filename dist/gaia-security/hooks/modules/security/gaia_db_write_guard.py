"""
gaia_db_write_guard.py -- B3 M6 security hook.

PreToolUse Bash hook that rejects commands writing directly to ~/.gaia/gaia.db
bypassing the store API.

Pattern detected:
    sqlite3\\s+.*(gaia\\.db|~/\\.gaia/.*\\.db).*(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE)
    case-insensitive, inside quotes / heredocs.

Read-only SQL (SELECT) is allowed. Bypass is possible via T3 approval grant
for legitimate cases (manual migrations, debug ops).

Public API:
    is_db_write_attempt(command: str) -> bool
    rejection_message() -> str
    check(command: str) -> tuple[bool, str | None]   -- main entrypoint
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Detection regex
# ---------------------------------------------------------------------------
# Matches:
#   sqlite3 ~/.gaia/gaia.db "UPDATE apps SET ..."
#   sqlite3 /home/x/.gaia/gaia.db <<EOF\nINSERT ...
#   bash -c 'sqlite3 ~/.gaia/gaia.db "DELETE FROM apps"'
#
# Two-pass approach to handle both shells and heredocs:
#   1. Find sqlite3 invocation with a gaia.db path
#   2. In the same command/heredoc, look for write verbs
_SQLITE_INVOCATION = re.compile(
    r"sqlite3\b[^&;|\n]*?(gaia\.db|~/\.gaia/[^\s'\"]*\.db|\.gaia/[^\s'\"]*\.db)",
    re.IGNORECASE,
)
_WRITE_VERBS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE)\b",
    re.IGNORECASE,
)

REJECTION_MESSAGE = (
    "Direct SQL writes to gaia.db are not allowed. "
    "Use `gaia context` CLI or emit CONTEXT_UPDATE. "
    "Raw SQL bypasses agent_permissions enforcement."
)


def is_db_write_attempt(command: str) -> bool:
    """Return True iff `command` matches the sqlite3-write-to-gaia.db pattern.

    Args:
        command: The Bash command line (full string, may include subshells,
            heredocs, quoted strings).

    Returns:
        True if the command contains a sqlite3 invocation against gaia.db
        AND a write verb (INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/REPLACE).
    """
    if not command:
        return False

    # Check for sqlite3 + gaia.db reference
    invocation_match = _SQLITE_INVOCATION.search(command)
    if invocation_match is None:
        return False

    # Look for write verb in the rest of the command (anywhere -- args, heredoc, etc.)
    verb_match = _WRITE_VERBS.search(command)
    if verb_match is None:
        return False

    return True


def rejection_message() -> str:
    """Return the canonical rejection message."""
    return REJECTION_MESSAGE


def check(command: str) -> Tuple[bool, Optional[str]]:
    """Main entrypoint for PreToolUse Bash hook integration.

    Args:
        command: The Bash command line.

    Returns:
        (allowed, reason)
        - (True, None)  if command is safe
        - (False, msg)  if command is a direct sqlite3 write to gaia.db
    """
    if is_db_write_attempt(command):
        return False, REJECTION_MESSAGE
    return True, None
