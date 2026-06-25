"""
Tests for bin/cli/approvals.py -- gaia approvals subcommand.

All approval_grants module functions are mocked so tests run without a
live .claude/cache/approvals/ directory.
"""

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup -- ensure bin/ and hooks/ are importable
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / "bin"
HOOKS_DIR = REPO_ROOT / "hooks"

for _p in [str(BIN_DIR), str(HOOKS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Import the module under test
import cli.approvals as approvals_mod


# ---------------------------------------------------------------------------
# Sample data factories
# ---------------------------------------------------------------------------

def _make_pending(
    nonce="abcd1234ef567890abcd1234ef567890",
    command="git push origin main",
    verb="push",
    category="GIT_PUSH",
    session_id="test-session-aaa",
    age_offset=0,
    context=None,
):
    """Return a minimal pending approval dict as stored on disk."""
    return {
        "nonce": nonce,
        "session_id": session_id,
        "command": command,
        "danger_verb": verb,
        "danger_category": category,
        "scope_type": "semantic_signature",
        "scope_signature": {},
        "timestamp": time.time() - age_offset,
        "ttl_minutes": 1440,
        "context": context or {
            "source": "developer-agent",
            "description": f"Push branch to remote",
            "risk": "medium",
            "rollback": "git revert HEAD",
        },
        "environment": {"git_branch": "feature/x"},
        "cwd": "/home/user/project",
    }


def _make_args(**kwargs):
    """Build a SimpleNamespace mimicking parsed argparse args."""
    defaults = {
        "json": False,
        "session": None,
        "dry_run": False,
        "reason": None,
        "orphans_only": False,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# DB-backed test fixture (Task E: all pending approvals live in gaia.db)
# ---------------------------------------------------------------------------

def _sha256(value):
    import hashlib
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _make_v12_schema(con):
    """Apply the approvals + approval_events schema to a file-backed DB."""
    con.execute("PRAGMA foreign_keys = ON")
    con.create_function("gaia_sha256", 1, lambda v: _sha256(v), deterministic=True)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS approvals (
            id           TEXT PRIMARY KEY,
            agent_id     TEXT,
            session_id   TEXT,
            status       TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','approved','rejected','revoked','expired')),
            fingerprint  TEXT,
            payload_json TEXT,
            created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            decided_at   TEXT
        );
        CREATE TABLE IF NOT EXISTS approval_events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            approval_id   TEXT NOT NULL,
            event_type    TEXT NOT NULL CHECK (event_type IN (
                              'REQUESTED','SHOWN','APPROVED','REJECTED',
                              'EXECUTED','FAILED','NOOP','REVOKED','REVERTED'
                          )),
            agent_id      TEXT,
            session_id    TEXT,
            payload_json  TEXT,
            fingerprint   TEXT,
            prev_hash     TEXT,
            this_hash     TEXT,
            metadata_json TEXT,
            created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            FOREIGN KEY (approval_id) REFERENCES approvals(id)
        );
        CREATE TRIGGER IF NOT EXISTS bu_approval_events_immutable
        BEFORE UPDATE ON approval_events
        BEGIN SELECT RAISE(ABORT, 'approval_events is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS bd_approval_events_immutable
        BEFORE DELETE ON approval_events
        BEGIN SELECT RAISE(ABORT, 'approval_events is append-only'); END;
    """)


def _sealed_payload(command, verb="push", category="GIT_PUSH", scope=None):
    """Build a sealed_payload dict that scan_pending_db / _scan_pending_shared parse."""
    return {
        "operation": f"{category} command intercepted: {verb}",
        "exact_content": command,
        "scope": scope or (command.split()[0] if command.strip() else "unknown"),
        "risk_level": "medium",
        "rollback_hint": "git revert HEAD",
        "rationale": f"{verb} requires approval",
        "commands": [command],
    }


@pytest.fixture()
def db_store(tmp_path, monkeypatch):
    """File-backed DB with gaia.approvals.store patched to use it.

    Yields (store_module, insert_pending) where insert_pending(command, ...,
    approval_id=...) inserts a pending row and returns its approval_id. This is
    how Task E tests seed pending approvals -- the DB is the sole store.
    """
    import sqlite3
    db_path = tmp_path / "cli_approvals.db"
    con = sqlite3.connect(str(db_path))
    _make_v12_schema(con)
    con.commit()
    con.close()

    monkeypatch.setattr(
        "gaia.approvals.store._open_db",
        lambda: sqlite3.connect(str(db_path)),
    )

    import gaia.approvals.store as store

    def insert_pending(command, *, verb="push", category="GIT_PUSH",
                       session_id="test-session-aaa", scope=None, approval_id=None):
        payload = _sealed_payload(command, verb=verb, category=category, scope=scope)
        return store.insert_requested(
            payload,
            agent_id="test-agent",
            session_id=session_id,
            approval_id=approval_id,
        )

    # Also stub the DB-backed grant writer so cmd_list's grant listing returns [].
    stub_writer = MagicMock()
    stub_writer.list_approval_grants = MagicMock(return_value=[])
    monkeypatch.setattr(approvals_mod, "_import_writer", lambda: stub_writer)

    yield store, insert_pending


# ---------------------------------------------------------------------------
# Tests: cmd_list
# ---------------------------------------------------------------------------

class TestCmdList:
    """Tests for cmd_list (DB-backed since Task E).

    All pending approvals are stored in gaia.db.  cmd_list reads the
    pending portion via _scan_pending_shared() (which now queries
    gaia.approvals.store) and the grant portion via _import_writer
    (stubbed to []).  --orphans-only filters out pendings whose
    owning session is currently alive in session_registry.
    """

    def test_list_empty_returns_0(self, capsys, db_store):
        rc = approvals_mod.cmd_list(_make_args())
        assert rc == 0
        captured = capsys.readouterr()
        assert "No active grants or pending approvals." in captured.out

    def test_list_with_items_shows_table(self, capsys, db_store):
        _store, insert_pending = db_store
        insert_pending("git push origin main", approval_id="P-abcd1234ef567890abcd1234ef567890")
        rc = approvals_mod.cmd_list(_make_args())
        assert rc == 0
        captured = capsys.readouterr()
        assert "P-abcd1234" in captured.out
        assert "push" in captured.out
        assert "git push origin m" in captured.out

    def test_list_json_output(self, capsys, db_store):
        _store, insert_pending = db_store
        insert_pending("git push origin main", approval_id="P-abcd1234ef567890abcd1234ef567890")
        rc = approvals_mod.cmd_list(_make_args(json=True))
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["count"] == 1
        assert len(data["pending_fs"]) == 1
        assert data["pending_fs"][0]["approval_id"] == "P-abcd1234"

    def test_list_json_empty(self, capsys, db_store):
        rc = approvals_mod.cmd_list(_make_args(json=True))
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["count"] == 0
        assert data["pending_fs"] == []

    def test_list_passes_session_filter(self, db_store):
        # With --session, cmd_list delegates to get_pending_approvals_for_session
        # (legacy filesystem-session path, retained for explicit session queries).
        with patch.object(approvals_mod, "_import_approval_grants") as mock_ag:
            mock_fn = MagicMock(return_value=[])
            mock_ag.return_value = {
                "get_pending_approvals_for_session": mock_fn,
                "load_pending_by_nonce_prefix": MagicMock(),
            }
            approvals_mod.cmd_list(_make_args(session="sess-xyz"))
        mock_fn.assert_called_once_with("sess-xyz")

    # ----- --orphans-only --------------------------------------------------

    def test_list_orphans_only_filters_live_sessions(self, capsys, db_store):
        """With --orphans-only, pendings from live sessions are hidden."""
        _store, insert_pending = db_store
        insert_pending("live cmd", session_id="session-alive",
                       approval_id="P-aaaa1111bbbb2222aaaa1111bbbb2222")
        insert_pending("orphan cmd", session_id="session-dead",
                       approval_id="P-cccc3333dddd4444cccc3333dddd4444")

        with patch(
            "modules.session.session_registry.get_live_sessions",
            return_value={"session-alive"},
        ):
            rc = approvals_mod.cmd_list(_make_args(orphans_only=True, json=True))

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["count"] == 1, (
            "--orphans-only must hide pendings whose session is alive."
        )
        assert data["pending_fs"][0]["approval_id"] == "P-cccc3333"

    def test_list_orphans_only_empty_when_all_alive(self, capsys, db_store):
        """If every pending's session is alive, --orphans-only returns none."""
        _store, insert_pending = db_store
        insert_pending("live cmd", session_id="session-alive",
                       approval_id="P-aaaa1111bbbb2222aaaa1111bbbb2222")

        with patch(
            "modules.session.session_registry.get_live_sessions",
            return_value={"session-alive"},
        ):
            rc = approvals_mod.cmd_list(_make_args(orphans_only=True, json=True))

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["count"] == 0

    def test_list_without_orphans_only_shows_all(self, capsys, db_store):
        """Default behavior (no flag): both live and dead session pendings listed."""
        _store, insert_pending = db_store
        insert_pending("cmd alive", session_id="session-alive",
                       approval_id="P-aaaa1111bbbb2222aaaa1111bbbb2222")
        insert_pending("cmd dead", session_id="session-dead",
                       approval_id="P-cccc3333dddd4444cccc3333dddd4444")

        with patch(
            "modules.session.session_registry.get_live_sessions",
            return_value={"session-alive"},
        ):
            rc = approvals_mod.cmd_list(_make_args(json=True))

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["count"] == 2, (
            "Without --orphans-only, both live and dead session pendings are listed."
        )


# ---------------------------------------------------------------------------
# Tests: cmd_show
# ---------------------------------------------------------------------------

class TestCmdShow:
    def test_show_found(self, capsys):
        pending = _make_pending()
        with patch.object(approvals_mod, "_import_approval_grants") as mock_ag:
            mock_ag.return_value = {
                "get_pending_approvals_for_session": MagicMock(),
                "load_pending_by_nonce_prefix": MagicMock(return_value=pending),
            }
            args = _make_args()
            args.approval_id = "abcd1234"
            rc = approvals_mod.cmd_show(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "P-abcd1234" in captured.out
        assert "git push origin main" in captured.out

    def test_show_not_found_returns_1(self, capsys):
        with patch.object(approvals_mod, "_import_approval_grants") as mock_ag:
            mock_ag.return_value = {
                "get_pending_approvals_for_session": MagicMock(),
                "load_pending_by_nonce_prefix": MagicMock(return_value=None),
            }
            args = _make_args()
            args.approval_id = "deadbeef"
            rc = approvals_mod.cmd_show(args)
        assert rc == 1

    def test_show_json_output(self, capsys):
        pending = _make_pending()
        with patch.object(approvals_mod, "_import_approval_grants") as mock_ag:
            mock_ag.return_value = {
                "get_pending_approvals_for_session": MagicMock(),
                "load_pending_by_nonce_prefix": MagicMock(return_value=pending),
            }
            args = _make_args(json=True)
            args.approval_id = "abcd1234"
            rc = approvals_mod.cmd_show(args)
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["approval_id"] == "P-abcd1234"
        assert data["command"] == "git push origin main"
        assert "environment" in data

    def test_show_strips_P_prefix(self, capsys):
        pending = _make_pending()
        with patch.object(approvals_mod, "_import_approval_grants") as mock_ag:
            mock_fn = MagicMock(return_value=pending)
            mock_ag.return_value = {
                "get_pending_approvals_for_session": MagicMock(),
                "load_pending_by_nonce_prefix": mock_fn,
            }
            args = _make_args()
            args.approval_id = "P-abcd1234"
            approvals_mod.cmd_show(args)
        # Should have called with just the hex prefix, not "P-abcd1234"
        call_arg = mock_fn.call_args[0][0]
        assert not call_arg.upper().startswith("P-")


# ---------------------------------------------------------------------------
# Tests: cmd_reject
# ---------------------------------------------------------------------------

class TestCmdReject:
    """cmd_reject single-reject path (DB-backed since Task E).

    Single reject finds the pending DB row by nonce prefix and revokes it
    via store.revoke() (pending -> revoked, append-only chain).
    """

    def test_reject_success(self, capsys, db_store):
        _store, insert_pending = db_store
        insert_pending("git push origin main", approval_id="P-abcd1234ef567890abcd1234ef567890")
        args = _make_args()
        args.nonce = "abcd1234"
        rc = approvals_mod.cmd_reject(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Rejected P-abcd1234" in captured.out

    def test_reject_not_found_returns_1(self, capsys, db_store):
        args = _make_args()
        args.nonce = "deadbeef"
        rc = approvals_mod.cmd_reject(args)
        assert rc == 1

    def test_reject_strips_P_prefix(self, db_store):
        _store, insert_pending = db_store
        insert_pending("git push origin main", approval_id="P-abcd1234ef567890abcd1234ef567890")
        args = _make_args()
        args.nonce = "P-abcd1234"
        rc = approvals_mod.cmd_reject(args)
        # P- prefix must be stripped before matching; the row is found and revoked.
        assert rc == 0

    def test_reject_json_output(self, capsys, db_store):
        _store, insert_pending = db_store
        insert_pending("git push origin main", approval_id="P-abcd1234ef567890abcd1234ef567890")
        args = _make_args(json=True, reason="not needed")
        args.nonce = "abcd1234"
        rc = approvals_mod.cmd_reject(args)
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "rejected"
        assert data["nonce_prefix"] == "abcd1234"
        assert data["reason"] == "not needed"

    def test_reject_with_reason(self, capsys, db_store):
        _store, insert_pending = db_store
        insert_pending("git push origin main", approval_id="P-abcd1234ef567890abcd1234ef567890")
        args = _make_args(reason="risky operation")
        args.nonce = "abcd1234"
        rc = approvals_mod.cmd_reject(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "risky operation" in captured.out

    def test_reject_no_nonce_no_all_returns_1(self, capsys):
        """Without --all and without a nonce, reject should return exit code 1."""
        args = _make_args()
        args.nonce = None
        rc = approvals_mod.cmd_reject(args)
        assert rc == 1


# ---------------------------------------------------------------------------
# Tests: cmd_reject --all (bulk reject)
# ---------------------------------------------------------------------------

class TestCmdRejectAll:
    def _make_reject_all_args(self, **kwargs):
        """Build args with all=True and nonce=None."""
        base = _make_args(**kwargs)
        base.all = True
        base.nonce = None
        return base

    def test_reject_all_empty_queue(self, capsys, db_store):
        """When queue is empty, exit 0 with informational message."""
        rc = approvals_mod.cmd_reject(self._make_reject_all_args())
        assert rc == 0
        captured = capsys.readouterr()
        assert "No pending approvals to reject" in captured.out

    def test_reject_all_empty_queue_json(self, capsys, db_store):
        rc = approvals_mod.cmd_reject(self._make_reject_all_args(json=True))
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["rejected"] == 0
        assert data["ids"] == []

    def test_reject_all_rejects_all_pending(self, capsys, db_store):
        """With two pending approvals, both should be rejected."""
        _store, insert_pending = db_store
        insert_pending("git push origin main", approval_id="P-aaaa1111bbbb2222aaaa1111bbbb2222")
        insert_pending("kubectl delete pod x", verb="delete",
                       approval_id="P-cccc3333dddd4444cccc3333dddd4444")
        rc = approvals_mod.cmd_reject(self._make_reject_all_args())
        assert rc == 0
        captured = capsys.readouterr()
        assert "Rejected 2 approval(s)" in captured.out
        assert "P-aaaa1111" in captured.out or "P-cccc3333" in captured.out

    def test_reject_all_json_output(self, capsys, db_store):
        _store, insert_pending = db_store
        insert_pending("git push origin main", approval_id="P-aaaa1111bbbb2222aaaa1111bbbb2222")
        rc = approvals_mod.cmd_reject(self._make_reject_all_args(json=True))
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "ok"
        assert data["rejected"] == 1
        assert len(data["ids"]) == 1
        assert data["ids"][0] == "P-aaaa1111"

    def test_reject_all_with_reason(self, capsys, db_store):
        _store, insert_pending = db_store
        insert_pending("git push origin main", approval_id="P-aaaa1111bbbb2222aaaa1111bbbb2222")
        rc = approvals_mod.cmd_reject(self._make_reject_all_args(reason="bulk-test"))
        assert rc == 0
        captured = capsys.readouterr()
        assert "bulk-test" in captured.out

    def test_reject_all_reason_in_json(self, capsys, db_store):
        _store, insert_pending = db_store
        insert_pending("git push origin main", approval_id="P-aaaa1111bbbb2222aaaa1111bbbb2222")
        rc = approvals_mod.cmd_reject(self._make_reject_all_args(json=True, reason="bulk-test"))
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["reason"] == "bulk-test"

    def test_reject_all_partial_failure(self, capsys, db_store, monkeypatch):
        """When one revoke call fails, exit 1 and report partial status."""
        _store, insert_pending = db_store
        insert_pending("git push origin main", approval_id="P-aaaa1111bbbb2222aaaa1111bbbb2222")
        insert_pending("kubectl delete pod x", verb="delete",
                       approval_id="P-cccc3333dddd4444cccc3333dddd4444")

        # Make the second revoke raise.
        orig_revoke = _store.revoke
        calls = {"n": 0}

        def flaky_revoke(approval_id, session_id, **kw):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated revoke failure")
            return orig_revoke(approval_id, session_id, **kw)

        monkeypatch.setattr(_store, "revoke", flaky_revoke)

        rc = approvals_mod.cmd_reject(self._make_reject_all_args(json=True))
        assert rc == 1
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "partial"
        assert data["rejected"] == 1
        assert len(data["failed"]) == 1

    def test_reject_all_parser_flag(self):
        """Verify the --all flag parses correctly from registered parser."""
        import argparse
        root = argparse.ArgumentParser()
        subparsers = root.add_subparsers(dest="command")
        approvals_mod.register(subparsers)
        args = root.parse_args(["approvals", "reject", "--all", "--reason", "bulk-test"])
        assert args.all is True
        assert args.nonce is None
        assert args.reason == "bulk-test"

    def test_reject_all_standalone_parser(self):
        parser = approvals_mod._build_standalone_parser()
        args = parser.parse_args(["reject", "--all", "--reason", "cleanup"])
        assert args.all is True
        assert args.nonce is None
        assert args.reason == "cleanup"


# ---------------------------------------------------------------------------
# Tests: cmd_reject_all (reject-all subcommand)
# ---------------------------------------------------------------------------

class TestCmdRejectAllSubcommand:
    """Tests for the ``reject-all`` subcommand (cmd_reject_all).

    This is the first-class subcommand surface documented in the
    pending-approvals skill.  Functionality mirrors ``reject --all`` but with
    additional ``--dry-run`` and ``--workspace`` flags.
    """

    def _make_args(self, dry_run=False, workspace=None):
        return SimpleNamespace(dry_run=dry_run, workspace=workspace)

    # ------------------------------------------------------------------
    # 0 pendings
    # ------------------------------------------------------------------

    def test_reject_all_empty_queue_prints_nothing_to_reject(self, capsys, db_store):
        """With no active pendings, exit 0 and print the 'nothing to reject' message."""
        rc = approvals_mod.cmd_reject_all(self._make_args())
        assert rc == 0
        captured = capsys.readouterr()
        assert "No active pendings" in captured.out

    # ------------------------------------------------------------------
    # 3 pendings -- all rejected
    # ------------------------------------------------------------------

    def test_reject_all_rejects_three_pendings(self, capsys, db_store):
        """With 3 pending approvals, all 3 are revoked, count is 3."""
        _store, insert_pending = db_store
        insert_pending("cmd-aaaa", approval_id="P-aaaa1111bbbb2222aaaa1111bbbb2222")
        insert_pending("cmd-cccc", approval_id="P-cccc3333dddd4444cccc3333dddd4444")
        insert_pending("cmd-eeee", approval_id="P-eeee5555ffff6666eeee5555ffff6666")

        rc = approvals_mod.cmd_reject_all(self._make_args())

        assert rc == 0
        captured = capsys.readouterr()
        assert "3 pending(s) rejected" in captured.out
        assert "P-aaaa1111" in captured.out
        assert "P-cccc3333" in captured.out
        assert "P-eeee5555" in captured.out

    def test_reject_all_marks_pendings_revoked_in_db(self, db_store):
        """reject-all must transition DB rows pending -> revoked (not delete them)."""
        _store, insert_pending = db_store
        aid = insert_pending("git push origin main",
                             approval_id="P-aaaa1111bbbb2222aaaa1111bbbb2222")

        approvals_mod.cmd_reject_all(self._make_args())

        # The row still exists but is now revoked (append-only audit preserved).
        row = _store.get_by_id(aid)
        assert row is not None, "reject-all must not delete the DB row"
        assert row["status"] == "revoked", (
            "reject-all transitions pending -> revoked via store.revoke()"
        )

    def test_reject_all_does_not_touch_already_decided(self, capsys, db_store):
        """Already-revoked pendings are not in the pending queue, so not re-counted."""
        _store, insert_pending = db_store
        aid_done = insert_pending("git push origin main",
                                  approval_id="P-aaaa1111bbbb2222aaaa1111bbbb2222")
        # Pre-revoke this one so it is no longer pending.
        _store.revoke(aid_done, "pretest")
        insert_pending("kubectl delete pod x", verb="delete",
                       approval_id="P-cccc3333dddd4444cccc3333dddd4444")

        rc = approvals_mod.cmd_reject_all(self._make_args())

        assert rc == 0
        captured = capsys.readouterr()
        # Only 1 pending was active -- only 1 rejection counted.
        assert "1 pending(s) rejected" in captured.out
        assert "P-cccc3333" in captured.out
        assert "P-aaaa1111" not in captured.out

    # ------------------------------------------------------------------
    # --dry-run
    # ------------------------------------------------------------------

    def test_reject_all_dry_run_no_state_changes(self, db_store):
        """--dry-run must not revoke any DB row."""
        _store, insert_pending = db_store
        aid = insert_pending("git push origin main",
                             approval_id="P-aaaa1111bbbb2222aaaa1111bbbb2222")

        rc = approvals_mod.cmd_reject_all(self._make_args(dry_run=True))

        assert rc == 0
        # Row is still pending -- dry-run did not revoke it.
        row = _store.get_by_id(aid)
        assert row["status"] == "pending"

    def test_reject_all_dry_run_prints_list(self, capsys, db_store):
        """--dry-run output shows '[dry-run] would reject:' and each P-id + command."""
        _store, insert_pending = db_store
        insert_pending("git push origin main",
                       approval_id="P-aaaa1111bbbb2222aaaa1111bbbb2222")

        rc = approvals_mod.cmd_reject_all(self._make_args(dry_run=True))

        assert rc == 0
        captured = capsys.readouterr()
        assert "[dry-run] would reject:" in captured.out
        assert "P-aaaa1111" in captured.out
        assert "git push origin main" in captured.out

    def test_reject_all_dry_run_zero_pendings(self, capsys, db_store):
        """--dry-run with empty queue still prints 'nothing to reject'."""
        rc = approvals_mod.cmd_reject_all(self._make_args(dry_run=True))
        assert rc == 0
        captured = capsys.readouterr()
        assert "No active pendings" in captured.out

    # ------------------------------------------------------------------
    # --workspace (informational only since Task E: DB is per-machine)
    # ------------------------------------------------------------------

    def test_reject_all_workspace_is_informational(self, capsys, db_store):
        """--workspace no longer scopes to an FS dir; the DB is per-machine.

        The flag is accepted and an informational note is printed, but the
        operation still acts on the machine-wide DB pending queue.
        """
        _store, insert_pending = db_store
        aid = insert_pending("terraform apply", verb="apply",
                             approval_id="P-bbbb2222cccc3333bbbb2222cccc3333")

        rc = approvals_mod.cmd_reject_all(
            self._make_args(workspace="/some/other-ws")
        )

        assert rc == 0
        captured = capsys.readouterr()
        # The DB pending is revoked regardless of --workspace.
        assert "1 pending(s) rejected" in captured.out
        assert _store.get_by_id(aid)["status"] == "revoked"
        # Informational note about --workspace being ignored is on stderr.
        assert "workspace" in captured.err.lower()

    # ------------------------------------------------------------------
    # Subcommand parser registration
    # ------------------------------------------------------------------

    def test_reject_all_registered_in_parser(self):
        """'reject-all' must be parseable as a subcommand of 'gaia approvals'."""
        import argparse
        root = argparse.ArgumentParser()
        subparsers = root.add_subparsers(dest="command")
        approvals_mod.register(subparsers)
        args = root.parse_args(["approvals", "reject-all"])
        assert args.func == approvals_mod.cmd_reject_all
        assert args.dry_run is False
        assert args.workspace is None

    def test_reject_all_dry_run_flag_parses(self):
        """--dry-run flag must parse correctly from the subcommand."""
        import argparse
        root = argparse.ArgumentParser()
        subparsers = root.add_subparsers(dest="command")
        approvals_mod.register(subparsers)
        args = root.parse_args(["approvals", "reject-all", "--dry-run"])
        assert args.dry_run is True

    def test_reject_all_workspace_flag_parses(self):
        """--workspace flag must parse correctly from the subcommand."""
        import argparse
        root = argparse.ArgumentParser()
        subparsers = root.add_subparsers(dest="command")
        approvals_mod.register(subparsers)
        args = root.parse_args(["approvals", "reject-all", "--workspace", "/some/path"])
        assert args.workspace == "/some/path"

    def test_reject_all_standalone_parser(self):
        """reject-all must be parseable via the standalone parser as well."""
        parser = approvals_mod._build_standalone_parser()
        args = parser.parse_args(["reject-all", "--dry-run"])
        assert args.dry_run is True
        assert args.func == approvals_mod.cmd_reject_all


# ---------------------------------------------------------------------------
# Tests: cmd_clean
# ---------------------------------------------------------------------------

class TestCmdClean:
    """cmd_clean (DB-only since FS retirement).

    Expired DB pending rows (older than 24h) are revoked.  Expired
    approval_grants rows (past expires_at) are transitioned to EXPIRED.
    No filesystem grant files are swept.
    """

    def test_clean_dry_run(self, capsys, db_store):
        rc = approvals_mod.cmd_clean(_make_args(dry_run=True))
        assert rc == 0
        captured = capsys.readouterr()
        assert "Dry run" in captured.out

    def test_clean_dry_run_counts_expired(self, capsys, db_store):
        _store, insert_pending = db_store
        import sqlite3
        # Insert a pending and backdate its created_at past the 24h window.
        aid = insert_pending("kubectl delete pod", verb="delete",
                             approval_id="P-aabb1122ccdd3344aabb1122ccdd3344")
        con = _store._open_db()
        con.execute(
            "UPDATE approvals SET created_at = '2020-01-01T00:00:00Z' WHERE id = ?",
            (aid,),
        )
        con.commit()
        con.close()

        rc = approvals_mod.cmd_clean(_make_args(dry_run=True))
        assert rc == 0
        captured = capsys.readouterr()
        assert "1" in captured.out

    def test_clean_dry_run_json(self, capsys, db_store):
        rc = approvals_mod.cmd_clean(_make_args(dry_run=True, json=True))
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["dry_run"] is True
        assert "would_remove" in data

    def test_clean_live_revokes_expired_db_pending(self, capsys, db_store):
        """Live clean revokes DB pending rows older than 24h."""
        _store, insert_pending = db_store
        aid = insert_pending("kubectl delete pod", verb="delete",
                             approval_id="P-aabb1122ccdd3344aabb1122ccdd3344")
        con = _store._open_db()
        con.execute(
            "UPDATE approvals SET created_at = '2020-01-01T00:00:00Z' WHERE id = ?",
            (aid,),
        )
        con.commit()
        con.close()

        rc = approvals_mod.cmd_clean(_make_args(dry_run=False))
        assert rc == 0
        # The expired row is now revoked.
        assert _store.get_by_id(aid)["status"] == "revoked"

    def test_clean_live_keeps_fresh_db_pending(self, capsys, db_store):
        """Live clean must NOT revoke a fresh (< 24h) pending."""
        _store, insert_pending = db_store
        aid = insert_pending("git push origin main",
                             approval_id="P-ffff0000ffff0000ffff0000ffff0000")
        rc = approvals_mod.cmd_clean(_make_args(dry_run=False))
        assert rc == 0
        assert _store.get_by_id(aid)["status"] == "pending"


# ---------------------------------------------------------------------------
# Tests: cmd_stats
# ---------------------------------------------------------------------------

class TestCmdStats:
    """cmd_stats (DB-backed since Task E): counts derived from the approvals table."""

    def test_stats_empty(self, capsys, db_store):
        rc = approvals_mod.cmd_stats(_make_args())
        assert rc == 0
        captured = capsys.readouterr()
        assert "Stats" in captured.out

    def test_stats_json(self, capsys, db_store):
        _store, insert_pending = db_store
        insert_pending("git push origin main",
                       approval_id="P-abcd1234ef567890abcd1234ef567890")
        rc = approvals_mod.cmd_stats(_make_args(json=True))
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "pending_all_sessions" in data
        assert "approved" in data
        assert "rejected" in data
        assert "revoked" in data
        assert "active_db_grants" in data
        assert "verb_breakdown" in data
        assert data["pending_all_sessions"] == 1

    def test_stats_counts_revoked(self, capsys, db_store):
        _store, insert_pending = db_store
        aid = insert_pending("git push origin main",
                             approval_id="P-aaaa1111bbbb2222aaaa1111bbbb2222")
        _store.revoke(aid, "test")
        rc = approvals_mod.cmd_stats(_make_args(json=True))
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["revoked"] == 1
        assert data["pending_all_sessions"] == 0


# ---------------------------------------------------------------------------
# Tests: _format_age helper
# ---------------------------------------------------------------------------

class TestFormatAge:
    def test_seconds(self):
        assert approvals_mod._format_age(30) == "30s"

    def test_minutes(self):
        assert approvals_mod._format_age(90) == "1m"

    def test_hours(self):
        assert approvals_mod._format_age(7200) == "2h"

    def test_days(self):
        assert approvals_mod._format_age(86400 * 3) == "3d"


# ---------------------------------------------------------------------------
# Tests: parser registration
# ---------------------------------------------------------------------------

class TestRegister:
    def test_register_adds_approvals_subcommand(self):
        import argparse
        root = argparse.ArgumentParser()
        subparsers = root.add_subparsers(dest="command")
        approvals_mod.register(subparsers)
        # Should be able to parse --help without error (check subcommand exists)
        with pytest.raises(SystemExit) as exc:
            root.parse_args(["approvals", "--help"])
        assert exc.value.code == 0

    def test_register_list_subcommand_parses(self):
        import argparse
        root = argparse.ArgumentParser()
        subparsers = root.add_subparsers(dest="command")
        approvals_mod.register(subparsers)
        args = root.parse_args(["approvals", "list", "--json"])
        assert args.json is True

    def test_register_list_orphans_only_parses(self):
        """--orphans-only must be exposed on `gaia approvals list`."""
        import argparse
        root = argparse.ArgumentParser()
        subparsers = root.add_subparsers(dest="command")
        approvals_mod.register(subparsers)
        args = root.parse_args(["approvals", "list", "--orphans-only"])
        assert args.orphans_only is True
        # Default must be False so existing consumers are unaffected.
        args2 = root.parse_args(["approvals", "list"])
        assert args2.orphans_only is False

    def test_register_reject_subcommand_parses(self):
        import argparse
        root = argparse.ArgumentParser()
        subparsers = root.add_subparsers(dest="command")
        approvals_mod.register(subparsers)
        args = root.parse_args(["approvals", "reject", "abcd1234", "--reason", "no"])
        assert args.nonce == "abcd1234"
        assert args.reason == "no"

    def test_register_clean_dry_run_parses(self):
        import argparse
        root = argparse.ArgumentParser()
        subparsers = root.add_subparsers(dest="command")
        approvals_mod.register(subparsers)
        args = root.parse_args(["approvals", "clean", "--dry-run"])
        assert args.dry_run is True


# ---------------------------------------------------------------------------
# Tests: standalone shim (if __name__ == "__main__")
# ---------------------------------------------------------------------------

class TestStandaloneParser:
    def test_standalone_parser_list(self):
        parser = approvals_mod._build_standalone_parser()
        args = parser.parse_args(["list", "--json"])
        assert args.json is True
        assert args.func == approvals_mod.cmd_list

    def test_standalone_parser_list_orphans_only(self):
        parser = approvals_mod._build_standalone_parser()
        args = parser.parse_args(["list", "--orphans-only"])
        assert args.orphans_only is True

    def test_standalone_parser_show(self):
        parser = approvals_mod._build_standalone_parser()
        args = parser.parse_args(["show", "abcd1234"])
        assert args.approval_id == "abcd1234"
        assert args.func == approvals_mod.cmd_show_v2

    def test_standalone_parser_clean_dry_run(self):
        parser = approvals_mod._build_standalone_parser()
        args = parser.parse_args(["clean", "--dry-run"])
        assert args.dry_run is True
        assert args.func == approvals_mod.cmd_clean
