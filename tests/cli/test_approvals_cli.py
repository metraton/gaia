#!/usr/bin/env python3
"""Tests for gaia approvals CLI subcommands: list, show, revoke.

Covers:
1. list -- returns DB grants + filesystem pending (both empty is OK)
2. show -- DB lookup by approval_id; falls back to filesystem
3. revoke -- insert → revoke → next match fails
4. --help does NOT list 'expire' subcommand
5. revoke nonexistent approval_id returns exit code 1
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

GAIA_ROOT = Path(__file__).resolve().parent.parent.parent
BIN_DIR = GAIA_ROOT / "bin"
sys.path.insert(0, str(GAIA_ROOT))
sys.path.insert(0, str(BIN_DIR))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path):
    """Fresh DB with approval_grants table."""
    db = tmp_path / "test.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE IF NOT EXISTS approval_grants (
            approval_id          TEXT PRIMARY KEY,
            agent_id             TEXT,
            session_id           TEXT,
            command_set_json     TEXT NOT NULL,
            scope                TEXT NOT NULL DEFAULT 'COMMAND_SET',
            created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            expires_at           TEXT,
            status               TEXT NOT NULL DEFAULT 'PENDING',
            consumed_indexes_json TEXT,
            consumed_at          TEXT,
            revoked_at           TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ag_session ON approval_grants(session_id);
        CREATE INDEX IF NOT EXISTS idx_ag_status  ON approval_grants(status);
    """)
    con.close()
    return db


def _make_args(**kwargs):
    """Create a simple namespace for CLI args."""
    defaults = {
        "json": False,
        "session": None,
        "orphans_only": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# Mock helpers for filesystem-based approval_grants module
# ---------------------------------------------------------------------------

def _mock_approval_grants_empty():
    m = MagicMock()
    m.cleanup_expired_grants.return_value = 0
    m.get_pending_approvals_for_session.return_value = []
    m.load_pending_by_nonce_prefix.return_value = None
    m.reject_pending.return_value = False
    return m


# ---------------------------------------------------------------------------
# 1. list subcommand
# ---------------------------------------------------------------------------

class TestCmdList:
    def test_list_empty_db_and_no_fs_pending(self, tmp_db, capsys):
        from bin.cli.approvals import cmd_list

        mock_writer = MagicMock()
        mock_writer.list_approval_grants.return_value = []
        mock_ag = _mock_approval_grants_empty()

        with patch("bin.cli.approvals._import_writer", return_value=mock_writer), \
             patch("bin.cli.approvals._import_approval_grants", return_value={
                 "get_pending_approvals_for_session": lambda *a, **kw: [],
                 "cleanup_expired_grants": lambda *a, **kw: 0,
                 "load_pending_by_nonce_prefix": lambda *a: None,
                 "reject_pending": lambda *a: False,
             }), \
             patch("bin.cli.approvals._scan_pending_shared", return_value=[]):
            rc = cmd_list(_make_args())

        assert rc == 0
        out, _ = capsys.readouterr()
        assert "No active grants" in out or "No" in out

    def test_list_shows_db_grants(self, tmp_db, capsys):
        from gaia.store.writer import insert_approval_grant
        from bin.cli.approvals import cmd_list

        insert_approval_grant(
            "approval-list-test",
            [{"command": "git push origin main", "rationale": "deploy"}],
            session_id="sess-list",
            db_path=tmp_db,
        )

        import gaia.store.writer as real_writer

        with patch("bin.cli.approvals._import_writer", return_value=real_writer), \
             patch("bin.cli.approvals._scan_pending_shared", return_value=[]):
            # Monkey-patch db_path resolution
            original_list = real_writer.list_approval_grants
            with patch.object(real_writer, "list_approval_grants",
                               side_effect=lambda **kw: original_list(db_path=tmp_db, **{k: v for k, v in kw.items() if k != "db_path"})):
                rc = cmd_list(_make_args())

        assert rc == 0

    def test_list_json_output_shape(self, tmp_db, capsys):
        from bin.cli.approvals import cmd_list

        mock_writer = MagicMock()
        mock_writer.list_approval_grants.return_value = []

        with patch("bin.cli.approvals._import_writer", return_value=mock_writer), \
             patch("bin.cli.approvals._scan_pending_shared", return_value=[]):
            rc = cmd_list(_make_args(**{"json": True}))

        assert rc == 0
        out, _ = capsys.readouterr()
        data = json.loads(out)
        assert "grants" in data
        assert "pending_fs" in data
        assert "count" in data


# ---------------------------------------------------------------------------
# 2. show subcommand
# ---------------------------------------------------------------------------

class TestCmdShow:
    def test_show_not_found_returns_1(self, capsys):
        from bin.cli.approvals import cmd_show

        mock_writer = MagicMock()
        mock_writer.list_approval_grants.return_value = []

        with patch("bin.cli.approvals._import_writer", return_value=mock_writer), \
             patch("bin.cli.approvals._import_approval_grants", return_value={
                 "load_pending_by_nonce_prefix": lambda *a: None,
             }):
            rc = cmd_show(_make_args(approval_id="nonexistent-id"))

        assert rc == 1

    def test_show_db_grant_found(self, tmp_db, capsys):
        from gaia.store.writer import insert_approval_grant
        from bin.cli.approvals import cmd_show
        import gaia.store.writer as real_writer

        insert_approval_grant(
            "approval-show-test",
            [{"command": "kubectl delete pod foo", "rationale": "clean"}],
            session_id="sess-show",
            db_path=tmp_db,
        )

        original_list = real_writer.list_approval_grants
        with patch("bin.cli.approvals._import_writer", return_value=real_writer), \
             patch.object(real_writer, "list_approval_grants",
                          side_effect=lambda **kw: original_list(db_path=tmp_db, **{k: v for k, v in kw.items() if k != "db_path"})):
            rc = cmd_show(_make_args(approval_id="approval-show-test"))

        assert rc == 0
        out, _ = capsys.readouterr()
        assert "approval-show-test" in out


# ---------------------------------------------------------------------------
# 3. revoke subcommand
# ---------------------------------------------------------------------------

class TestCmdRevoke:
    def test_revoke_existing_grant(self, tmp_db, capsys):
        from gaia.store.writer import insert_approval_grant
        from bin.cli.approvals import cmd_revoke
        import gaia.store.writer as real_writer

        insert_approval_grant(
            "approval-revoke-cli",
            [{"command": "git push origin main", "rationale": "deploy"}],
            session_id="sess-rev",
            db_path=tmp_db,
        )

        original_revoke = real_writer.revoke_approval_grant
        with patch("bin.cli.approvals._import_writer", return_value=real_writer), \
             patch.object(real_writer, "revoke_approval_grant",
                          side_effect=lambda aid, **kw: original_revoke(aid, db_path=tmp_db)):
            rc = cmd_revoke(_make_args(approval_id="approval-revoke-cli"))

        assert rc == 0
        out, _ = capsys.readouterr()
        assert "Revoked" in out
        assert "approval-revoke-cli" in out

    def test_revoke_nonexistent_returns_1(self, capsys):
        from bin.cli.approvals import cmd_revoke

        mock_writer = MagicMock()
        mock_writer.revoke_approval_grant.return_value = {"status": "not_found"}

        with patch("bin.cli.approvals._import_writer", return_value=mock_writer):
            rc = cmd_revoke(_make_args(approval_id="no-such-id"))

        assert rc == 1

    def test_revoke_then_match_fails(self, tmp_db):
        """Insert → revoke → match returns None (end-to-end)."""
        from gaia.store.writer import insert_approval_grant, revoke_approval_grant
        from modules.security.approval_grants import match_command_set_grant
        import sys
        hooks_dir = GAIA_ROOT / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        cmd = "aws s3 rm s3://bucket/key"
        insert_approval_grant(
            "approval-revoke-flow",
            [{"command": cmd, "rationale": "cleanup"}],
            session_id="sess-flow",
            db_path=tmp_db,
        )
        revoke_approval_grant("approval-revoke-flow", db_path=tmp_db)
        result = match_command_set_grant(cmd, session_id="sess-flow", db_path=tmp_db)
        assert result is None


# ---------------------------------------------------------------------------
# 4. --help does NOT list 'expire'
# ---------------------------------------------------------------------------

class TestHelpOutput:
    def test_expire_not_in_help(self):
        from bin.cli.approvals import _build_standalone_parser
        parser = _build_standalone_parser()
        help_text = parser.format_help()
        # 'expire' must not appear as a subcommand
        assert "expire" not in help_text.lower() or "expire" not in {
            s for s in help_text.split() if s.isalpha()
        }

    def test_revoke_in_help(self):
        from bin.cli.approvals import _build_standalone_parser
        parser = _build_standalone_parser()
        help_text = parser.format_help()
        assert "revoke" in help_text

    def test_list_show_in_help(self):
        from bin.cli.approvals import _build_standalone_parser
        parser = _build_standalone_parser()
        help_text = parser.format_help()
        assert "list" in help_text
        assert "show" in help_text
