"""
test_project_context_db_write_guard.py -- AC-5 verification.

Tests the gaia_db_write_guard hook (B3 M6) against:
- Direct sqlite3 writes to gaia.db -- rejected with exact message
- Read-only SELECT queries -- allowed
- bash -c wrapped writes -- rejected
- Heredoc-style writes -- rejected
"""

from __future__ import annotations

from hooks.modules.security.gaia_db_write_guard import (
    REJECTION_MESSAGE,
    check,
    is_db_write_attempt,
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
    cmd = "ls -la /home/jorge"
    allowed, reason = check(cmd)
    assert allowed is True


def test_absolute_gaia_db_path_rejected():
    """Absolute path to .gaia DB is also caught."""
    cmd = 'sqlite3 /home/jorge/.gaia/gaia.db "ALTER TABLE apps ADD COLUMN x TEXT"'
    allowed, reason = check(cmd)
    assert allowed is False


def test_rejection_message_constant():
    """The exact message string is exposed for downstream consumers."""
    msg = rejection_message()
    assert "Direct SQL writes to gaia.db are not allowed" in msg
    assert "Use `gaia context` CLI or emit CONTEXT_UPDATE" in msg
    assert "Raw SQL bypasses agent_permissions enforcement" in msg
