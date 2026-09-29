"""
gaia_db_write_guard.py -- B3 M6 security hook.

PreToolUse Bash hook that rejects commands writing directly to ~/.gaia/gaia.db
bypassing the store API.

Patterns detected:
    sqlite3\\s+.*(gaia\\.db|~/\\.gaia/.*\\.db).*(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE)
    case-insensitive, inside quotes / heredocs; and any shell writer whose
    DESTINATION is the gaia.db file or a side file -- cp/mv/install/ln/rsync,
    redirect, tee, dd of=, truncate, or a mutative inline interpreter naming it
    -- behind any wrapper the classifier peels.

Read-only SQL (SELECT) is allowed. The write block is categorical and NOT
approvable -- like blocked_commands.py, it denies with SecurityTier.T3_BLOCKED
and no approval_id, so there is no T3 grant that lifts it. Legitimate cases
go through the sanctioned paths instead:
    - DDL / migrations: scripts/bootstrap_database.sh and tools/migration/*
    - data writes: the `gaia context` CLI / update_contracts, backed by the
      typed API in gaia/store/writer.py (which enforces _is_authorized())

Public API:
    is_db_write_attempt(command: str) -> bool
    writes_db_file(command: str) -> bool
    rejection_message() -> str
    check(command: str) -> tuple[bool, str | None]   -- main entrypoint
"""

from __future__ import annotations

import os
import re
from typing import Optional, Tuple

from .mutative_verbs import _peel_leading_command_wrappers, detect_mutative_command
from .shell_write_guard import _split_components, _tokenize, _writer_targets

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
    "Use `gaia context` CLI or emit update_contracts. "
    "Raw SQL bypasses agent_permissions enforcement."
)

FILE_WRITE_REJECTION_MESSAGE = (
    "Writing the gaia.db file from the shell (copy, move, redirect, truncate, "
    "or an interpreter) is not allowed: it replaces or corrupts the store "
    "without agent_permissions enforcement. Use the `gaia` CLI."
)

# The store and its SQLite side files; a write to any of them corrupts the store.
_GAIA_DB_FILE_NAMES = frozenset({
    "gaia.db", "gaia.db-wal", "gaia.db-shm", "gaia.db-journal",
})
_GAIA_DB_NAME_RE = re.compile(r"gaia\.db\b")
# The last operand is the destination.
_COPY_WRITERS = frozenset({"cp", "mv", "install", "ln", "rsync"})
_TRUNCATORS = frozenset({"truncate"})
_INTERPRETERS = frozenset({"python", "node", "perl", "ruby", "php"})


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


def _is_gaia_db_file(token: str) -> bool:
    return os.path.basename(token.strip("'\"")) in _GAIA_DB_FILE_NAMES


def _component_writes_db(component: str) -> bool:
    """Report whether one command component writes the Gaia DB file by any shell writer.

    Keyed on the DESTINATION: a component that only reads the file (``ls``,
    ``cp gaia.db /tmp/x``) or only names it in quoted text is not a write.
    """
    peeled, _ = _peel_leading_command_wrappers(component)
    if any(_is_gaia_db_file(t) for t in _writer_targets(peeled)):
        return True

    tokens = _tokenize(peeled)
    if not tokens:
        return False
    base = os.path.basename(tokens[0])
    operands = [t for t in tokens[1:] if not t.startswith("-")]
    if base in _COPY_WRITERS:
        return bool(operands) and _is_gaia_db_file(operands[-1])
    if base in _TRUNCATORS:
        return any(_is_gaia_db_file(t) for t in operands)
    if base.rstrip("0123456789.") in _INTERPRETERS and _GAIA_DB_NAME_RE.search(peeled):
        return detect_mutative_command(peeled).is_mutative
    return False


def writes_db_file(command: str) -> bool:
    """Return True iff some component of *command* writes the Gaia DB file through the shell."""
    return any(
        _component_writes_db(component.strip())
        for component in _split_components(command or "")
        if component.strip()
    )


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
        - (False, msg)  if command writes gaia.db outside the store API --
          SQL through the SQL shell, or any shell writer over the file
    """
    if is_db_write_attempt(command):
        return False, REJECTION_MESSAGE
    if writes_db_file(command):
        return False, FILE_WRITE_REJECTION_MESSAGE
    return True, None
