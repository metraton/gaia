"""
Tests for bin/cli/cleanup.py -- gaia cleanup subcommand.

Tests use a temporary directory as a fake project root to avoid touching
real installation files.
"""

import json
import os
import sys
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure bin/ is on the path so the plugin can be imported
_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli.cleanup import (
    _find_project_root,
    _matches_pattern,
    _apply_retention_policy,
    _remove_claude_md,
    _remove_settings_json,
    _remove_symlinks,
    register,
    cmd_cleanup,
    RETENTION_POLICY,
)


def _make_project(tmp_path: Path) -> Path:
    """Create a minimal fake project with .claude/ structure."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "logs").mkdir()
    (claude_dir / "metrics").mkdir()
    (claude_dir / "project-context" / "episodic-memory" / "episodes").mkdir(parents=True)
    (claude_dir / "project-context" / "workflow-episodic-memory" / "signals").mkdir(parents=True)
    (claude_dir / "session" / "active" / "response-contract").mkdir(parents=True)
    return tmp_path


class TestMatchesPattern(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(_matches_pattern("audit-2026-01-01.jsonl", "audit-*.jsonl"))

    def test_no_match(self):
        self.assertFalse(_matches_pattern("metrics-2026.jsonl", "audit-*.jsonl"))

    def test_simple_wildcard(self):
        self.assertTrue(_matches_pattern("hooks-20260101.log", "hooks-*.log"))

    def test_json_pattern(self):
        self.assertTrue(_matches_pattern("episode-001.json", "*.json"))


class TestFindProjectRoot(unittest.TestCase):
    def test_finds_claude_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            with patch("os.getcwd", return_value=str(root)):
                result = _find_project_root()
            self.assertEqual(result, root)

    def test_walks_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            subdir = root / "src" / "deep"
            subdir.mkdir(parents=True)
            with patch("os.getcwd", return_value=str(subdir)):
                result = _find_project_root()
            self.assertEqual(result, root)


class TestRemoveClaudeMd(unittest.TestCase):
    def test_missing_returns_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = _remove_claude_md(root, dry_run=False)
            self.assertFalse(result["found"])

    def test_removes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CLAUDE.md").write_text("identity\n")
            result = _remove_claude_md(root, dry_run=False)
            self.assertTrue(result["found"])
            self.assertTrue(result["removed"])
            self.assertFalse((root / "CLAUDE.md").exists())

    def test_dry_run_does_not_remove(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CLAUDE.md").write_text("identity\n")
            result = _remove_claude_md(root, dry_run=True)
            self.assertTrue(result["found"])
            self.assertTrue((root / "CLAUDE.md").exists())


class TestRemoveSettingsJson(unittest.TestCase):
    def test_missing_returns_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            result = _remove_settings_json(root, dry_run=False)
            self.assertFalse(result["found"])

    def test_removes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_dir = root / ".claude"
            claude_dir.mkdir()
            (claude_dir / "settings.json").write_text("{}\n")
            result = _remove_settings_json(root, dry_run=False)
            self.assertTrue(result["found"])
            self.assertFalse((claude_dir / "settings.json").exists())

    def test_dry_run_does_not_remove(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_dir = root / ".claude"
            claude_dir.mkdir()
            (claude_dir / "settings.json").write_text("{}\n")
            _remove_settings_json(root, dry_run=True)
            self.assertTrue((claude_dir / "settings.json").exists())


class TestRemoveSymlinks(unittest.TestCase):
    def test_no_symlinks_nothing_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            result = _remove_symlinks(root, dry_run=False)
            self.assertEqual(result["removed"], [])

    def test_removes_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_dir = root / ".claude"
            claude_dir.mkdir()
            # Create a real dir and point a symlink at it
            target = root / "real_agents"
            target.mkdir()
            link = claude_dir / "agents"
            link.symlink_to(target)
            result = _remove_symlinks(root, dry_run=False)
            self.assertIn(".claude/agents", result["removed"])
            self.assertFalse(link.exists())

    def test_dry_run_does_not_remove_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_dir = root / ".claude"
            claude_dir.mkdir()
            target = root / "real_agents"
            target.mkdir()
            link = claude_dir / "agents"
            link.symlink_to(target)
            _remove_symlinks(root, dry_run=True)
            self.assertTrue(link.exists())


class TestApplyRetentionPolicy(unittest.TestCase):
    def _old_mtime(self, path: Path, days: int = 100):
        """Set file mtime to be older than given days."""
        old_time = time.time() - days * 86400
        os.utime(path, (old_time, old_time))

    def test_prunes_old_audit_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / ".claude" / "logs"
            logs_dir.mkdir(parents=True)
            old_log = logs_dir / "audit-2020-01-01.jsonl"
            old_log.write_text('{"timestamp":"2020-01-01T00:00:00Z"}\n')
            self._old_mtime(old_log, days=100)

            actions = _apply_retention_policy(root, dry_run=False)
            deleted = [a for a in actions if a["action"] == "delete-file"]
            self.assertTrue(len(deleted) >= 1)
            self.assertFalse(old_log.exists())

    def test_dry_run_does_not_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / ".claude" / "logs"
            logs_dir.mkdir(parents=True)
            old_log = logs_dir / "audit-2020-01-01.jsonl"
            old_log.write_text('{"timestamp":"2020-01-01T00:00:00Z"}\n')
            self._old_mtime(old_log, days=100)

            actions = _apply_retention_policy(root, dry_run=True)
            deleted = [a for a in actions if a["action"] == "delete-file"]
            self.assertTrue(len(deleted) >= 1)
            self.assertTrue(old_log.exists())  # Not deleted in dry-run

    def test_retains_recent_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / ".claude" / "logs"
            logs_dir.mkdir(parents=True)
            recent_log = logs_dir / "audit-2026-04-15.jsonl"
            recent_log.write_text('{"timestamp":"2026-04-15T12:00:00Z"}\n')
            # mtime is now (default) -- within retention

            actions = _apply_retention_policy(root, dry_run=False)
            deleted = [a for a in actions if "audit-2026-04-15" in a.get("path", "")]
            self.assertEqual(deleted, [])
            self.assertTrue(recent_log.exists())

    def test_prunes_legacy_logs_regardless_of_age(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / ".claude" / "logs"
            logs_dir.mkdir(parents=True)
            legacy_log = logs_dir / "pre_tool_use_v2-2026-04-15.log"
            legacy_log.write_text("legacy content\n")
            # NOT old -- but should still be removed as legacy

            actions = _apply_retention_policy(root, dry_run=False)
            legacy_deleted = [a for a in actions if a["action"] == "delete-legacy"]
            self.assertTrue(len(legacy_deleted) >= 1)
            self.assertFalse(legacy_log.exists())

    def test_truncates_jsonl_by_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wem_dir = root / ".claude" / "project-context" / "workflow-episodic-memory"
            wem_dir.mkdir(parents=True)
            metrics_file = wem_dir / "metrics.jsonl"
            # One old entry, one recent
            old_entry = json.dumps({"timestamp": "2020-01-01T00:00:00Z", "data": "old"})
            new_entry = json.dumps({"timestamp": "2026-04-15T00:00:00Z", "data": "new"})
            metrics_file.write_text(f"{old_entry}\n{new_entry}\n")

            actions = _apply_retention_policy(root, dry_run=False)
            truncate_actions = [a for a in actions if a["action"] == "truncate-jsonl"]
            self.assertTrue(len(truncate_actions) >= 1)

            remaining = metrics_file.read_text()
            self.assertIn("new", remaining)
            self.assertNotIn("old", remaining)


class TestRegisterSubcommand(unittest.TestCase):
    def test_register_creates_parser(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        # Verify subcommand is registered and parses without flags
        args = parser.parse_args(["cleanup"])
        self.assertEqual(args.subcommand, "cleanup")

    def test_dry_run_flag(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["cleanup", "--dry-run"])
        self.assertTrue(args.dry_run)

    def test_prune_flag(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["cleanup", "--prune"])
        self.assertTrue(args.prune)

    def test_json_flag(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["cleanup", "--json"])
        self.assertTrue(args.json)


class TestCmdCleanup(unittest.TestCase):
    def _make_args(self, prune=False, retain=False, dry_run=False, as_json=False):
        import argparse
        ns = argparse.Namespace()
        ns.prune = prune
        ns.retain = retain
        ns.dry_run = dry_run
        ns.json = as_json
        return ns

    def test_prune_dry_run_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            args = self._make_args(prune=True, dry_run=True, as_json=True)
            with patch("cli.cleanup._find_project_root", return_value=root):
                import io
                from contextlib import redirect_stdout
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cmd_cleanup(args)
                output = buf.getvalue()

            self.assertEqual(rc, 0)
            data = json.loads(output)
            self.assertTrue(data["dry_run"])
            self.assertTrue(data["prune_only"])

    def test_full_cleanup_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            (root / "CLAUDE.md").write_text("identity\n")
            args = self._make_args(as_json=True)
            with patch("cli.cleanup._find_project_root", return_value=root):
                import io
                from contextlib import redirect_stdout
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cmd_cleanup(args)
                output = buf.getvalue()

            self.assertEqual(rc, 0)
            data = json.loads(output)
            self.assertIn("claude_md", data)
            self.assertTrue(data["claude_md"]["found"])

    def test_returns_zero_on_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            args = self._make_args()
            with patch("cli.cleanup._find_project_root", return_value=root):
                import io
                from contextlib import redirect_stdout
                with redirect_stdout(io.StringIO()):
                    rc = cmd_cleanup(args)
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
