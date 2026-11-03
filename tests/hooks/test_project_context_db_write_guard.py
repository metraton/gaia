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


STORE = "~/.gaia/gaia.db"


@pytest.mark.parametrize("statement", [
    "ANALYZE",
    "analyze main",
    ".restore /tmp/x.db",
    ".rest /tmp/x.db",
    ".import /tmp/a.csv apps",
    ".imp /tmp/a.csv apps",
])
def test_statement_that_changes_the_open_store_rejected(statement):
    assert check(f'sqlite3 {STORE} "{statement}"') == (False, REJECTION_MESSAGE)
    assert check(f"sqlite3 {STORE} <<EOF\n{statement}\nEOF") == (False, REJECTION_MESSAGE)


@pytest.mark.parametrize("dot_command", [
    ".backup main /home/user/.gaia/gaia.db",
    ".backup /home/user/.gaia/gaia.db",
    ".save ~/.gaia/gaia.db",
    ".sav ~/.gaia/gaia.db",
    ".clone /home/user/.gaia/gaia.db",
    ".output /home/user/.gaia/gaia.db",
    ".once -e ~/.gaia/gaia.db",
    ".backup main '/home/user/.gaia/gaia.db-wal'",
])
def test_dot_command_writing_the_store_file_rejected(dot_command):
    assert check(f'sqlite3 /tmp/other.db "{dot_command}"') == (False, REJECTION_MESSAGE)
    assert check(f"sqlite3 /tmp/other.db <<EOF\n{dot_command}\nEOF") == (False, REJECTION_MESSAGE)


@pytest.mark.parametrize("dot_command", [
    ".backup main /tmp/copy.db",
    ".save /tmp/copy.db",
    ".clone /tmp/copy.db",
    ".output /tmp/out.txt",
    ".once /tmp/out.txt",
    ".dump",
    ".tables",
])
def test_dot_command_writing_another_file_allowed(dot_command):
    assert check(f'sqlite3 {STORE} "{dot_command}"') == (True, None)
    assert check(f'sqlite3 {STORE} ".mode csv" "{dot_command}" "SELECT 1"') == (True, None)


def test_dot_command_after_a_dot_command_is_read_separately():
    cmd = f'sqlite3 {STORE} ".tables" ".restore /tmp/x.db"'
    assert check(cmd) == (False, REJECTION_MESSAGE)


def _script(tmp_path, body):
    path = tmp_path / "script.sql"
    path.write_text(body)
    return path


@pytest.mark.parametrize("body", [
    "UPDATE apps SET status = 'x';",
    "SELECT 1;\nPRAGMA user_version = 3;",
    "SELECT 1;\n.restore /tmp/x.db",
    "SELECT 1;\n.backup main /home/user/.gaia/gaia.db",
])
def test_read_of_a_script_that_writes_rejected(tmp_path, body):
    path = _script(tmp_path, body)
    assert check(f'sqlite3 {STORE} ".read {path}"') == (False, REJECTION_MESSAGE)
    assert check(f"sqlite3 {STORE} <<EOF\n.read {path}\nEOF") == (False, REJECTION_MESSAGE)


@pytest.mark.parametrize("body", [
    "SELECT * FROM apps;",
    ".tables\nPRAGMA table_info(apps);\n.backup main /tmp/copy.db",
    "",
])
def test_read_of_a_script_that_only_reads_allowed(tmp_path, body):
    path = _script(tmp_path, body)
    assert check(f'sqlite3 {STORE} ".read {path}"') == (True, None)


def test_read_follows_a_script_that_reads_another(tmp_path):
    inner = _script(tmp_path, "DELETE FROM apps;")
    outer = tmp_path / "outer.sql"
    outer.write_text(f".read {inner}\n")
    assert check(f'sqlite3 {STORE} ".read {outer}"') == (False, REJECTION_MESSAGE)


def test_read_resolves_a_relative_script_against_the_given_directory(tmp_path):
    _script(tmp_path, "SELECT 1;")
    assert check(f'sqlite3 {STORE} ".read script.sql"', cwd=str(tmp_path)) == (True, None)
    _script(tmp_path, "DELETE FROM apps;")
    assert check(f'sqlite3 {STORE} ".read script.sql"', cwd=str(tmp_path)) == (False, REJECTION_MESSAGE)


@pytest.mark.parametrize("command", [
    f'sqlite3 {STORE} ".read /nonexistent/script.sql"',
    f'sqlite3 {STORE} ".read script.sql"',
    f'sqlite3 {STORE} ".read $SCRIPT"',
    f'sqlite3 {STORE} ".read |cat /tmp/script.sql"',
    f'sqlite3 {STORE} ".read /dev/stdin"',
    f'sqlite3 {STORE} ".read /tmp"',
])
def test_read_of_a_script_that_cannot_be_read_rejected(command):
    allowed, reason = check(command)
    assert allowed is False
    assert "could not prove" in reason


def test_read_relative_after_a_cd_cannot_be_proven(tmp_path):
    _script(tmp_path, "SELECT 1;")
    allowed, reason = check(f'cd /tmp && sqlite3 {STORE} ".read script.sql"', cwd=str(tmp_path))
    assert allowed is False
    assert "could not prove" in reason


def test_validator_hands_the_shell_directory_to_a_relative_read(tmp_path):
    from hooks.modules.tools.bash_validator import BashValidator

    _script(tmp_path, "DELETE FROM apps;")
    result = BashValidator().validate(
        f'sqlite3 {STORE} ".read script.sql"',
        is_subagent=True, session_id="ses-db-guard", agent_type="gaia-system", cwd=str(tmp_path),
    )
    assert result.rule == "floor.gaia-db-write"
    assert result.reason.startswith(REJECTION_MESSAGE)


def test_read_against_another_database_is_not_judged():
    assert check('sqlite3 /tmp/other.db ".read /nonexistent/script.sql"') == (True, None)


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
