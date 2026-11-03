"""
test_project_context_db_write_guard.py -- AC-5 verification.

Tests the gaia_db_write_guard hook (B3 M6) against:
- Direct sqlite3 writes to gaia.db -- rejected with exact message
- Read-only SELECT queries -- allowed
- bash -c wrapped writes -- rejected
- Heredoc-style writes -- rejected
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# hooks/modules/core/state.py imports `adapters` as a top-level name.
_HOOKS_DIR = str(Path(__file__).resolve().parents[2] / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from hooks.modules.security.gaia_db_write_guard import (  # noqa: E402
    REJECTION_MESSAGE,
    check,
    rejection_message,
)


def test_raw_sqlite_update_rejected():
    """The canonical AC-5 case: sqlite3 + gaia.db + UPDATE -> rejected."""
    cmd = 'sqlite3 ~/.gaia/gaia.db "UPDATE apps SET status=\'active\' WHERE name=\'foo\'"'
    allowed, reason = check(cmd)
    assert allowed is False
    assert reason == REJECTION_MESSAGE
    assert "Direct SQL writes" in reason
    assert "bypasses agent_permissions enforcement" in reason


def test_raw_sqlite_insert_rejected():
    cmd = 'sqlite3 ~/.gaia/gaia.db "INSERT INTO apps (project, repo, name) VALUES (\'p\', \'r\', \'n\')"'
    allowed, reason = check(cmd)
    assert allowed is False
    assert reason == REJECTION_MESSAGE


def test_raw_sqlite_delete_rejected():
    cmd = 'sqlite3 ~/.gaia/gaia.db "DELETE FROM apps WHERE name=\'foo\'"'
    allowed, reason = check(cmd)
    assert allowed is False


def test_raw_sqlite_drop_rejected():
    cmd = "sqlite3 ~/.gaia/gaia.db 'DROP TABLE apps'"
    allowed, reason = check(cmd)
    assert allowed is False


def test_raw_sqlite_select_allowed():
    """Read-only SELECT is allowed."""
    cmd = 'sqlite3 ~/.gaia/gaia.db "SELECT * FROM apps"'
    allowed, reason = check(cmd)
    assert allowed is True
    assert reason is None


@pytest.mark.parametrize("sql", [
    "PRAGMA journal_mode=DELETE",
    "PRAGMA journal_mode=WAL",
    "pragma main.user_version = 7",
    "PRAGMA wal_checkpoint(TRUNCATE)",
    "PRAGMA optimize",
    "VACUUM",
    "ATTACH DATABASE '/tmp/x.db' AS x",
    "REINDEX",
])
def test_store_rewriting_statement_rejected(sql):
    """Statements that rewrite the store file are refused like any other direct write."""
    allowed, reason = check(f'sqlite3 ~/.gaia/gaia.db "{sql}"')
    assert allowed is False
    assert reason == REJECTION_MESSAGE


@pytest.mark.parametrize("sql", [
    "PRAGMA table_info(apps)",
    "PRAGMA journal_mode",
    "PRAGMA integrity_check",
])
def test_read_pragma_allowed(sql):
    assert check(f'sqlite3 ~/.gaia/gaia.db "{sql}"') == (True, None)


def test_bash_wrapped_write_rejected():
    """bash -c wrapping must still be detected."""
    cmd = "bash -c 'sqlite3 ~/.gaia/gaia.db \"INSERT INTO apps (name) VALUES (\\\"x\\\")\"'"
    allowed, reason = check(cmd)
    assert allowed is False


def test_heredoc_write_rejected():
    """Heredoc-style sqlite3 input is detected."""
    cmd = 'sqlite3 ~/.gaia/gaia.db <<EOF\nUPDATE apps SET status="x";\nEOF'
    allowed, reason = check(cmd)
    assert allowed is False


def test_non_gaia_db_allowed():
    """Writes to other DBs are not in scope."""
    cmd = 'sqlite3 /tmp/other.db "INSERT INTO foo VALUES (1)"'
    allowed, reason = check(cmd)
    assert allowed is True


def test_unrelated_command_allowed():
    cmd = "ls -la /home/user"
    allowed, reason = check(cmd)
    assert allowed is True


def test_absolute_gaia_db_path_rejected():
    """Absolute path to .gaia DB is also caught."""
    cmd = 'sqlite3 /home/user/.gaia/gaia.db "ALTER TABLE apps ADD COLUMN x TEXT"'
    allowed, reason = check(cmd)
    assert allowed is False


def test_rejection_message_constant():
    """The exact message string is exposed for downstream consumers."""
    msg = rejection_message()
    assert "Direct SQL writes to gaia.db are not allowed" in msg
    assert "Use `gaia context` CLI or emit update_contracts" in msg
    assert "Raw SQL bypasses agent_permissions enforcement" in msg
