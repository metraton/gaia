"""
Tests for bin/cli/history.py -- gaia history subcommand.
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli.history import (
    _find_project_root,
    _read_workflow_metrics,
    _format_time,
    _truncate,
    _format_tokens,
    _status_label,
    register,
    cmd_history,
)


def _write_index(claude_dir: Path, episodes: list):
    """Write a fake episodic-memory/index.json."""
    ep_dir = claude_dir / "project-context" / "episodic-memory"
    ep_dir.mkdir(parents=True, exist_ok=True)
    (ep_dir / "index.json").write_text(json.dumps({"episodes": episodes}))


def _write_metrics_jsonl(claude_dir: Path, entries: list):
    """Write a fake workflow-episodic-memory/metrics.jsonl."""
    wem_dir = claude_dir / "project-context" / "workflow-episodic-memory"
    wem_dir.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(json.dumps(e) for e in entries) + "\n"
    (wem_dir / "metrics.jsonl").write_text(lines)


class TestReadWorkflowMetrics(unittest.TestCase):
    def test_reads_from_index_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_dir = root / ".claude"
            claude_dir.mkdir()
            episodes = [
                {"agent": "developer", "timestamp": "2026-04-15T10:00:00Z", "plan_status": "COMPLETE"},
                {"agent": "gaia-operator", "timestamp": "2026-04-15T11:00:00Z", "plan_status": "BLOCKED"},
            ]
            _write_index(claude_dir, episodes)
            result = _read_workflow_metrics(root)
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0]["agent"], "developer")

    def test_skips_entries_without_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_dir = root / ".claude"
            claude_dir.mkdir()
            episodes = [
                {"timestamp": "2026-04-15T10:00:00Z"},  # no agent
                {"agent": "developer", "timestamp": "2026-04-15T11:00:00Z"},
            ]
            _write_index(claude_dir, episodes)
            result = _read_workflow_metrics(root)
            self.assertEqual(len(result), 1)

    def test_falls_back_to_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_dir = root / ".claude"
            claude_dir.mkdir()
            # No index.json, only metrics.jsonl
            entries = [
                {"agent": "developer", "timestamp": "2026-04-15T10:00:00Z"},
            ]
            _write_metrics_jsonl(claude_dir, entries)
            result = _read_workflow_metrics(root)
            self.assertEqual(len(result), 1)

    def test_empty_when_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            result = _read_workflow_metrics(root)
            self.assertEqual(result, [])


class TestFormatHelpers(unittest.TestCase):
    def test_truncate_short_string(self):
        self.assertEqual(_truncate("hello", 20), "hello")

    def test_truncate_long_string(self):
        result = _truncate("a" * 50, 20)
        self.assertTrue(len(result) <= 20)
        self.assertTrue(result.endswith("..."))

    def test_truncate_collapses_whitespace(self):
        result = _truncate("hello   world", 20)
        self.assertEqual(result, "hello world")

    def test_format_tokens_large(self):
        result = _format_tokens(1500)
        self.assertIn("1.5k", result)

    def test_format_tokens_none(self):
        result = _format_tokens(None)
        self.assertIn("n/a", result)

    def test_status_label_complete(self):
        result = _status_label("COMPLETE")
        self.assertIn("COMPLETE", result)

    def test_status_label_empty(self):
        result = _status_label("")
        self.assertIn("n/a", result)

    def test_format_time_today(self):
        now = datetime.now(timezone.utc)
        iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        result = _format_time(iso)
        # Should be HH:MM only (no date prefix for today)
        self.assertRegex(result, r"^\d{2}:\d{2}$")

    def test_format_time_past(self):
        result = _format_time("2026-01-01T10:00:00Z")
        # Should include MM-DD prefix
        self.assertIn("01-01", result)


class TestRegisterSubcommand(unittest.TestCase):
    def test_register_creates_parser(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["history", "--limit", "5"])
        self.assertEqual(args.limit, 5)

    def test_today_flag(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["history", "--today"])
        self.assertTrue(args.today)

    def test_blocked_flag(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["history", "--blocked"])
        self.assertTrue(args.blocked)

    def test_agent_flag(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["history", "--agent", "developer"])
        self.assertEqual(args.agent, "developer")

    def test_json_flag(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["history", "--json"])
        self.assertTrue(args.json)

    def test_short_flags(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["history", "-t", "-b", "-n", "5"])
        self.assertTrue(args.today)
        self.assertTrue(args.blocked)
        self.assertEqual(args.limit, 5)


class TestCmdHistory(unittest.TestCase):
    def _make_args(self, today=False, blocked=False, agent=None, limit=20, as_json=False):
        import argparse
        ns = argparse.Namespace()
        ns.today = today
        ns.blocked = blocked
        ns.agent = agent
        ns.limit = limit
        ns.json = as_json
        return ns

    def _make_project(self, episodes: list) -> Path:
        tmp = tempfile.mkdtemp()
        root = Path(tmp)
        claude_dir = root / ".claude"
        claude_dir.mkdir()
        _write_index(claude_dir, episodes)
        return root

    def test_no_claude_dir_returns_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = self._make_args()
            with patch("cli.history._find_project_root", return_value=root):
                import io
                from contextlib import redirect_stdout
                with redirect_stdout(io.StringIO()):
                    rc = cmd_history(args)
            self.assertEqual(rc, 1)

    def test_empty_history_returns_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            args = self._make_args()
            with patch("cli.history._find_project_root", return_value=root):
                import io
                from contextlib import redirect_stdout
                with redirect_stdout(io.StringIO()):
                    rc = cmd_history(args)
            self.assertEqual(rc, 0)

    def test_json_output(self):
        episodes = [
            {
                "agent": "developer",
                "timestamp": "2026-04-15T10:00:00Z",
                "plan_status": "COMPLETE",
                "prompt": "Fix the bug",
                "output_tokens_approx": 1000,
            }
        ]
        root = self._make_project(episodes)
        try:
            args = self._make_args(as_json=True)
            with patch("cli.history._find_project_root", return_value=root):
                import io
                from contextlib import redirect_stdout
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cmd_history(args)
                output = buf.getvalue()

            self.assertEqual(rc, 0)
            data = json.loads(output)
            self.assertIsInstance(data, list)
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["agent"], "developer")
        finally:
            import shutil
            shutil.rmtree(root)

    def test_filter_by_today(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        episodes = [
            {"agent": "developer", "timestamp": today, "plan_status": "COMPLETE"},
            {"agent": "developer", "timestamp": "2026-01-01T10:00:00Z", "plan_status": "COMPLETE"},
        ]
        root = self._make_project(episodes)
        try:
            args = self._make_args(today=True, as_json=True)
            with patch("cli.history._find_project_root", return_value=root):
                import io
                from contextlib import redirect_stdout
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cmd_history(args)
                data = json.loads(buf.getvalue())

            self.assertEqual(rc, 0)
            self.assertIsInstance(data, list)
            self.assertEqual(len(data), 1)
        finally:
            import shutil
            shutil.rmtree(root)

    def test_filter_blocked(self):
        episodes = [
            {"agent": "developer", "timestamp": "2026-04-15T10:00:00Z", "plan_status": "COMPLETE"},
            {"agent": "developer", "timestamp": "2026-04-15T11:00:00Z", "plan_status": "BLOCKED"},
        ]
        root = self._make_project(episodes)
        try:
            args = self._make_args(blocked=True, as_json=True)
            with patch("cli.history._find_project_root", return_value=root):
                import io
                from contextlib import redirect_stdout
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cmd_history(args)
                data = json.loads(buf.getvalue())

            self.assertEqual(rc, 0)
            self.assertIsInstance(data, list)
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["plan_status"], "BLOCKED")
        finally:
            import shutil
            shutil.rmtree(root)

    def test_filter_by_agent(self):
        episodes = [
            {"agent": "developer", "timestamp": "2026-04-15T10:00:00Z", "plan_status": "COMPLETE"},
            {"agent": "gaia-operator", "timestamp": "2026-04-15T11:00:00Z", "plan_status": "COMPLETE"},
        ]
        root = self._make_project(episodes)
        try:
            args = self._make_args(agent="developer", as_json=True)
            with patch("cli.history._find_project_root", return_value=root):
                import io
                from contextlib import redirect_stdout
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cmd_history(args)
                data = json.loads(buf.getvalue())

            self.assertEqual(rc, 0)
            self.assertIsInstance(data, list)
            self.assertEqual(len(data), 1)
        finally:
            import shutil
            shutil.rmtree(root)

    def test_limit_applied(self):
        episodes = [
            {"agent": "developer", "timestamp": f"2026-04-15T{h:02d}:00:00Z", "plan_status": "COMPLETE"}
            for h in range(10)
        ]
        root = self._make_project(episodes)
        try:
            args = self._make_args(limit=3, as_json=True)
            with patch("cli.history._find_project_root", return_value=root):
                import io
                from contextlib import redirect_stdout
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cmd_history(args)
                data = json.loads(buf.getvalue())

            self.assertEqual(rc, 0)
            self.assertIsInstance(data, list)
            self.assertEqual(len(data), 3)
        finally:
            import shutil
            shutil.rmtree(root)

    def test_table_output_contains_agent(self):
        episodes = [
            {"agent": "developer", "timestamp": "2026-04-15T10:00:00Z", "plan_status": "COMPLETE", "prompt": "Do work"},
        ]
        root = self._make_project(episodes)
        try:
            args = self._make_args()
            with patch("cli.history._find_project_root", return_value=root):
                import io
                from contextlib import redirect_stdout
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cmd_history(args)
                output = buf.getvalue()

            self.assertEqual(rc, 0)
            self.assertIn("developer", output)
            self.assertIn("COMPLETE", output)
        finally:
            import shutil
            shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()
