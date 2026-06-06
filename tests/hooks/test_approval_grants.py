#!/usr/bin/env python3
"""Tests for COMMAND_SET approval grants (M3 / D4 / D10).

Covers:
1. Byte-for-byte match (positive path)
2. Wrapped command (cd prefix, redirect, flag) requires fresh approval (negative)
3. Single-use: second match fails after consumption
4. TTL expiry: expired grant does not match
5. Revocation: revoked grant does not match on retry
"""

import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
for _p in [str(HOOKS_DIR), str(PLUGIN_ROOT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path):
    """Return a fresh SQLite DB path with the approval_grants table."""
    db = tmp_path / "test.db"
    import sqlite3
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


@pytest.fixture()
def future_expires():
    from datetime import datetime, timezone, timedelta
    return (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def past_expires():
    from datetime import datetime, timezone, timedelta
    return (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Helper: insert a grant row directly
# ---------------------------------------------------------------------------

def _insert_grant(db, approval_id, command_set, session_id="sess-1",
                  expires_at=None, status="PENDING"):
    import sqlite3
    con = sqlite3.connect(str(db))
    con.execute(
        "INSERT INTO approval_grants "
        "(approval_id, session_id, command_set_json, expires_at, status, consumed_indexes_json) "
        "VALUES (?, ?, ?, ?, ?, '[]')",
        (approval_id, session_id, json.dumps(command_set), expires_at, status),
    )
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# Test 1: Byte-for-byte positive match
# ---------------------------------------------------------------------------

class TestCommandSetPositiveMatch:
    def test_exact_command_matches(self, tmp_db, future_expires):
        from modules.security.approval_grants import (
            create_command_set_grant,
            match_command_set_grant,
        )
        cmd = "git push origin main"
        ok = create_command_set_grant(
            [{"command": cmd, "rationale": "deploy"}],
            "approval-1",
            session_id="sess-1",
            db_path=tmp_db,
        )
        assert ok is True

        result = match_command_set_grant(cmd, db_path=tmp_db)
        assert result is not None
        approval_id, idx = result
        assert approval_id == "approval-1"
        assert idx == 0

    def test_multiple_commands_in_set(self, tmp_db, future_expires):
        from modules.security.approval_grants import (
            create_command_set_grant,
            match_command_set_grant,
        )
        cmds = ["git add -A", "git commit -m 'fix'", "git push origin main"]
        create_command_set_grant(
            [{"command": c, "rationale": "r"} for c in cmds],
            "approval-multi",
            session_id="sess-2",
            db_path=tmp_db,
        )
        for i, cmd in enumerate(cmds):
            res = match_command_set_grant(cmd, db_path=tmp_db)
            assert res is not None
            assert res[1] == i


# ---------------------------------------------------------------------------
# Test 2: Wrapped commands require fresh approval (negative)
# ---------------------------------------------------------------------------

class TestCommandSetNegativeMatch:
    def test_cd_prefix_does_not_match(self, tmp_db):
        from modules.security.approval_grants import (
            create_command_set_grant,
            match_command_set_grant,
        )
        cmd = "git push origin main"
        create_command_set_grant(
            [{"command": cmd, "rationale": "r"}],
            "approval-cd",
            session_id="sess-3",
            db_path=tmp_db,
        )
        wrapped = f"cd /repo && {cmd}"
        assert match_command_set_grant(wrapped, db_path=tmp_db) is None

    def test_redirect_does_not_match(self, tmp_db):
        from modules.security.approval_grants import (
            create_command_set_grant,
            match_command_set_grant,
        )
        cmd = "terraform apply -auto-approve"
        create_command_set_grant(
            [{"command": cmd, "rationale": "r"}],
            "approval-redir",
            session_id="sess-4",
            db_path=tmp_db,
        )
        assert match_command_set_grant(
            cmd + " > output.txt", db_path=tmp_db
        ) is None

    def test_extra_flag_does_not_match(self, tmp_db):
        from modules.security.approval_grants import (
            create_command_set_grant,
            match_command_set_grant,
        )
        cmd = "kubectl delete pod foo"
        create_command_set_grant(
            [{"command": cmd, "rationale": "r"}],
            "approval-flag",
            session_id="sess-5",
            db_path=tmp_db,
        )
        assert match_command_set_grant(
            cmd + " --force", db_path=tmp_db
        ) is None


# ---------------------------------------------------------------------------
# Test 3: Single-use consumption
# ---------------------------------------------------------------------------

class TestSingleUseConsumption:
    def test_second_match_fails_after_mark_consumed(self, tmp_db):
        from gaia.store.writer import (
            insert_approval_grant,
            mark_command_set_item_consumed,
        )
        from modules.security.approval_grants import match_command_set_grant

        cmd = "helm upgrade my-app ./chart"
        insert_approval_grant(
            "approval-singleuse",
            [{"command": cmd, "rationale": "upgrade"}],
            session_id="sess-6",
            scope="COMMAND_SET",
            db_path=tmp_db,
        )

        res = match_command_set_grant(cmd, db_path=tmp_db)
        assert res is not None
        mark_command_set_item_consumed("approval-singleuse", 0, db_path=tmp_db)

        # Second attempt should fail -- item already consumed
        res2 = match_command_set_grant(cmd, db_path=tmp_db)
        assert res2 is None


# ---------------------------------------------------------------------------
# Test 4: TTL expiry
# ---------------------------------------------------------------------------

class TestExpiry:
    def test_expired_grant_does_not_match(self, tmp_db, past_expires):
        from modules.security.approval_grants import (
            create_command_set_grant,
            match_command_set_grant,
        )
        # Insert with a past expires_at directly via helper
        _insert_grant(
            tmp_db,
            "approval-expired",
            [{"command": "git push origin main", "rationale": "r"}],
            session_id="sess-7",
            expires_at=past_expires,
        )
        result = match_command_set_grant(
            "git push origin main", db_path=tmp_db
        )
        assert result is None


# ---------------------------------------------------------------------------
# Test 5: Revocation
# ---------------------------------------------------------------------------

class TestRevocation:
    def test_revoked_grant_does_not_match(self, tmp_db):
        from gaia.store.writer import insert_approval_grant, revoke_approval_grant
        from modules.security.approval_grants import match_command_set_grant

        cmd = "aws s3 sync . s3://bucket"
        insert_approval_grant(
            "approval-revoke",
            [{"command": cmd, "rationale": "sync"}],
            session_id="sess-8",
            scope="COMMAND_SET",
            db_path=tmp_db,
        )

        # Verify match before revoke
        assert match_command_set_grant(cmd, db_path=tmp_db) is not None

        revoke_approval_grant("approval-revoke", db_path=tmp_db)

        # After revocation, should not match
        assert match_command_set_grant(cmd, db_path=tmp_db) is None
