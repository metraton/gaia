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
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Tests: cmd_list
# ---------------------------------------------------------------------------

class TestCmdList:
    """Tests for cmd_list.

    When --session is not provided, cmd_list uses _list_all_pending() which
    reads directly from the grants directory via _import_grants_dir().  Tests
    for the no-session path therefore patch _import_grants_dir to return an
    empty tmp directory so they don't touch the real filesystem.

    When --session is provided, cmd_list delegates to
    get_pending_approvals_for_session() via _import_approval_grants(); those
    tests patch _import_approval_grants as before.
    """

    def test_list_empty_returns_0(self, capsys, tmp_path):
        grants_dir = tmp_path / "approvals"
        grants_dir.mkdir()
        with patch.object(approvals_mod, "_import_grants_dir", return_value=grants_dir):
            rc = approvals_mod.cmd_list(_make_args())
        assert rc == 0
        captured = capsys.readouterr()
        assert "No pending approvals" in captured.out

    def test_list_with_items_shows_table(self, capsys, tmp_path):
        pending = _make_pending()
        grants_dir = tmp_path / "approvals"
        grants_dir.mkdir()
        # Write the pending file to the fake grants dir
        pending_path = grants_dir / f"pending-{pending['nonce']}.json"
        pending_path.write_text(json.dumps(pending))
        with patch.object(approvals_mod, "_import_grants_dir", return_value=grants_dir):
            rc = approvals_mod.cmd_list(_make_args())
        assert rc == 0
        captured = capsys.readouterr()
        assert "P-abcd1234" in captured.out
        assert "push" in captured.out
        # Command is truncated in table to 40 chars; check the prefix
        assert "git push origin m" in captured.out

    def test_list_json_output(self, capsys, tmp_path):
        pending = _make_pending()
        grants_dir = tmp_path / "approvals"
        grants_dir.mkdir()
        pending_path = grants_dir / f"pending-{pending['nonce']}.json"
        pending_path.write_text(json.dumps(pending))
        with patch.object(approvals_mod, "_import_grants_dir", return_value=grants_dir):
            rc = approvals_mod.cmd_list(_make_args(json=True))
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["count"] == 1
        assert len(data["pending"]) == 1
        assert data["pending"][0]["approval_id"] == "P-abcd1234"

    def test_list_json_empty(self, capsys, tmp_path):
        grants_dir = tmp_path / "approvals"
        grants_dir.mkdir()
        with patch.object(approvals_mod, "_import_grants_dir", return_value=grants_dir):
            rc = approvals_mod.cmd_list(_make_args(json=True))
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["count"] == 0
        assert data["pending"] == []

    def test_list_import_error_returns_1(self, capsys):
        with patch.object(approvals_mod, "_import_grants_dir", side_effect=ImportError("no module")):
            rc = approvals_mod.cmd_list(_make_args())
        assert rc == 1

    def test_list_passes_session_filter(self):
        with patch.object(approvals_mod, "_import_approval_grants") as mock_ag:
            mock_fn = MagicMock(return_value=[])
            mock_ag.return_value = {
                "get_pending_approvals_for_session": mock_fn,
                "load_pending_by_nonce_prefix": MagicMock(),
                "reject_pending": MagicMock(),
                "cleanup_expired_grants": MagicMock(),
            }
            approvals_mod.cmd_list(_make_args(session="sess-xyz"))
        mock_fn.assert_called_once_with("sess-xyz")


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
                "reject_pending": MagicMock(),
                "cleanup_expired_grants": MagicMock(),
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
                "reject_pending": MagicMock(),
                "cleanup_expired_grants": MagicMock(),
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
                "reject_pending": MagicMock(),
                "cleanup_expired_grants": MagicMock(),
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
                "reject_pending": MagicMock(),
                "cleanup_expired_grants": MagicMock(),
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
    def test_reject_success(self, capsys):
        with patch.object(approvals_mod, "_import_approval_grants") as mock_ag:
            mock_ag.return_value = {
                "get_pending_approvals_for_session": MagicMock(),
                "load_pending_by_nonce_prefix": MagicMock(),
                "reject_pending": MagicMock(return_value=True),
                "cleanup_expired_grants": MagicMock(),
            }
            args = _make_args()
            args.nonce = "abcd1234"
            rc = approvals_mod.cmd_reject(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Rejected P-abcd1234" in captured.out

    def test_reject_not_found_returns_1(self, capsys):
        with patch.object(approvals_mod, "_import_approval_grants") as mock_ag:
            mock_ag.return_value = {
                "get_pending_approvals_for_session": MagicMock(),
                "load_pending_by_nonce_prefix": MagicMock(),
                "reject_pending": MagicMock(return_value=False),
                "cleanup_expired_grants": MagicMock(),
            }
            args = _make_args()
            args.nonce = "deadbeef"
            rc = approvals_mod.cmd_reject(args)
        assert rc == 1

    def test_reject_strips_P_prefix(self):
        with patch.object(approvals_mod, "_import_approval_grants") as mock_ag:
            mock_fn = MagicMock(return_value=True)
            mock_ag.return_value = {
                "get_pending_approvals_for_session": MagicMock(),
                "load_pending_by_nonce_prefix": MagicMock(),
                "reject_pending": mock_fn,
                "cleanup_expired_grants": MagicMock(),
            }
            args = _make_args()
            args.nonce = "P-abcd1234"
            approvals_mod.cmd_reject(args)
        call_arg = mock_fn.call_args[0][0]
        assert not call_arg.upper().startswith("P-")

    def test_reject_json_output(self, capsys):
        with patch.object(approvals_mod, "_import_approval_grants") as mock_ag:
            mock_ag.return_value = {
                "get_pending_approvals_for_session": MagicMock(),
                "load_pending_by_nonce_prefix": MagicMock(),
                "reject_pending": MagicMock(return_value=True),
                "cleanup_expired_grants": MagicMock(),
            }
            args = _make_args(json=True, reason="not needed")
            args.nonce = "abcd1234"
            rc = approvals_mod.cmd_reject(args)
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "rejected"
        assert data["nonce_prefix"] == "abcd1234"
        assert data["reason"] == "not needed"

    def test_reject_with_reason(self, capsys):
        with patch.object(approvals_mod, "_import_approval_grants") as mock_ag:
            mock_ag.return_value = {
                "get_pending_approvals_for_session": MagicMock(),
                "load_pending_by_nonce_prefix": MagicMock(),
                "reject_pending": MagicMock(return_value=True),
                "cleanup_expired_grants": MagicMock(),
            }
            args = _make_args(reason="risky operation")
            args.nonce = "abcd1234"
            rc = approvals_mod.cmd_reject(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "risky operation" in captured.out


# ---------------------------------------------------------------------------
# Tests: cmd_clean
# ---------------------------------------------------------------------------

class TestCmdClean:
    def test_clean_dry_run(self, capsys, tmp_path):
        grants_dir = tmp_path / "approvals"
        grants_dir.mkdir()

        with patch.object(approvals_mod, "_import_grants_dir", return_value=grants_dir):
            rc = approvals_mod.cmd_clean(_make_args(dry_run=True))
        assert rc == 0
        captured = capsys.readouterr()
        assert "Dry run" in captured.out

    def test_clean_dry_run_counts_expired(self, capsys, tmp_path):
        grants_dir = tmp_path / "approvals"
        grants_dir.mkdir()

        # Write one expired pending file
        expired = {
            "nonce": "aabb1122",
            "session_id": "s1",
            "command": "kubectl delete pod",
            "timestamp": time.time() - 3600,  # 1 hour ago
            "ttl_minutes": 5,
            "scope_signature": {},
        }
        (grants_dir / "pending-aabb1122.json").write_text(json.dumps(expired))

        with patch.object(approvals_mod, "_import_grants_dir", return_value=grants_dir):
            rc = approvals_mod.cmd_clean(_make_args(dry_run=True))
        assert rc == 0
        captured = capsys.readouterr()
        assert "1" in captured.out

    def test_clean_dry_run_json(self, capsys, tmp_path):
        grants_dir = tmp_path / "approvals"
        grants_dir.mkdir()

        with patch.object(approvals_mod, "_import_grants_dir", return_value=grants_dir):
            rc = approvals_mod.cmd_clean(_make_args(dry_run=True, json=True))
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["dry_run"] is True
        assert "would_remove" in data

    def test_clean_live_calls_cleanup(self, capsys):
        """Test that cmd_clean (live mode) calls cleanup_expired_grants."""
        mock_mod = MagicMock()
        mock_mod._last_cleanup_time = 0.0
        mock_mod.cleanup_expired_grants = MagicMock(return_value=3)

        with patch.object(approvals_mod, "_import_approval_grants_module", return_value=mock_mod):
            rc = approvals_mod.cmd_clean(_make_args(dry_run=False))

        assert rc == 0
        captured = capsys.readouterr()
        assert "3" in captured.out
        mock_mod.cleanup_expired_grants.assert_called_once()

    def test_clean_no_directory(self, capsys, tmp_path):
        nonexistent = tmp_path / "does_not_exist"
        with patch.object(approvals_mod, "_import_grants_dir", return_value=nonexistent):
            rc = approvals_mod.cmd_clean(_make_args(dry_run=True))
        assert rc == 0
        captured = capsys.readouterr()
        assert "does not exist" in captured.out or "Nothing" in captured.out


# ---------------------------------------------------------------------------
# Tests: cmd_stats
# ---------------------------------------------------------------------------

class TestCmdStats:
    def test_stats_empty(self, capsys, tmp_path):
        grants_dir = tmp_path / "approvals"
        grants_dir.mkdir()

        with patch.object(approvals_mod, "_import_approval_grants") as mock_ag:
            mock_ag.return_value = {
                "get_pending_approvals_for_session": MagicMock(return_value=[]),
                "load_pending_by_nonce_prefix": MagicMock(),
                "reject_pending": MagicMock(),
                "cleanup_expired_grants": MagicMock(),
            }
            with patch.object(approvals_mod, "_import_grants_dir", return_value=grants_dir):
                rc = approvals_mod.cmd_stats(_make_args())
        assert rc == 0
        captured = capsys.readouterr()
        assert "Stats" in captured.out

    def test_stats_json(self, capsys, tmp_path):
        grants_dir = tmp_path / "approvals"
        grants_dir.mkdir()

        # Write one live pending
        pending = _make_pending()
        (grants_dir / f"pending-{pending['nonce']}.json").write_text(json.dumps(pending))

        with patch.object(approvals_mod, "_import_approval_grants") as mock_ag:
            mock_ag.return_value = {
                "get_pending_approvals_for_session": MagicMock(return_value=[pending]),
                "load_pending_by_nonce_prefix": MagicMock(),
                "reject_pending": MagicMock(),
                "cleanup_expired_grants": MagicMock(),
            }
            with patch.object(approvals_mod, "_import_grants_dir", return_value=grants_dir):
                rc = approvals_mod.cmd_stats(_make_args(json=True))
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "pending_current_session" in data
        assert "pending_all_sessions" in data
        assert "active_grants" in data
        assert "verb_breakdown" in data

    def test_stats_counts_rejected(self, capsys, tmp_path):
        grants_dir = tmp_path / "approvals"
        grants_dir.mkdir()

        rejected = _make_pending(nonce="aaaa1111bbbb2222aaaa1111bbbb2222")
        rejected["status"] = "rejected"
        (grants_dir / f"pending-{rejected['nonce']}.json").write_text(json.dumps(rejected))

        with patch.object(approvals_mod, "_import_approval_grants") as mock_ag:
            mock_ag.return_value = {
                "get_pending_approvals_for_session": MagicMock(return_value=[]),
                "load_pending_by_nonce_prefix": MagicMock(),
                "reject_pending": MagicMock(),
                "cleanup_expired_grants": MagicMock(),
            }
            with patch.object(approvals_mod, "_import_grants_dir", return_value=grants_dir):
                rc = approvals_mod.cmd_stats(_make_args(json=True))
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["rejected"] == 1


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

    def test_standalone_parser_show(self):
        parser = approvals_mod._build_standalone_parser()
        args = parser.parse_args(["show", "abcd1234"])
        assert args.approval_id == "abcd1234"
        assert args.func == approvals_mod.cmd_show

    def test_standalone_parser_clean_dry_run(self):
        parser = approvals_mod._build_standalone_parser()
        args = parser.parse_args(["clean", "--dry-run"])
        assert args.dry_run is True
        assert args.func == approvals_mod.cmd_clean
