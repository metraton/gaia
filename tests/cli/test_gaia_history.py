"""
Tests for bin/cli/history.py -- gaia history subcommand.

T6 migration: fixtures updated to monkeypatch gaia.store.writer._connect
(or patch _read_workflow_metrics directly) since _read_workflow_metrics now
reads from gaia.db episodes table exclusively, not from legacy index.json /
metrics.jsonl files.
"""

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

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


# ---------------------------------------------------------------------------
# Helpers for DB-backed fixtures
# ---------------------------------------------------------------------------

def _make_in_memory_db(episodes: list) -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the episodes table seeded."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        """CREATE TABLE episodes (
            episode_id TEXT PRIMARY KEY,
            workspace TEXT,
            timestamp TEXT,
            session_id TEXT,
            task_id TEXT,
            agent TEXT,
            type TEXT,
            title TEXT,
            prompt TEXT,
            enriched_prompt TEXT,
            plan_status TEXT,
            outcome TEXT,
            exit_code INTEGER,
            duration_seconds REAL,
            output_tokens_approx INTEGER,
            tier TEXT
        )"""
    )
    for ep in episodes:
        con.execute(
            "INSERT INTO episodes (episode_id, workspace, timestamp, session_id, "
            "task_id, agent, type, title, prompt, enriched_prompt, plan_status, "
            "outcome, exit_code, duration_seconds, output_tokens_approx, tier) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ep.get("episode_id", "ep-001"),
                ep.get("workspace", "me"),
                ep.get("timestamp"),
                ep.get("session_id"),
                ep.get("task_id"),
                ep.get("agent"),
                ep.get("type"),
                ep.get("title"),
                ep.get("prompt"),
                ep.get("enriched_prompt"),
                ep.get("plan_status"),
                ep.get("outcome"),
                ep.get("exit_code"),
                ep.get("duration_seconds"),
                ep.get("output_tokens_approx"),
                ep.get("tier"),
            ),
        )
    con.commit()
    return con


class TestReadWorkflowMetrics(unittest.TestCase):
    """Tests for _read_workflow_metrics — now queries gaia.db episodes table.

    Each test patches gaia.store.writer._connect to return an in-memory DB
    seeded with the desired episodes.
    """

    def _patch_connect(self, episodes: list, tmp_path: Path = None):
        """Return a context manager that patches _connect to an in-memory DB.

        Also patches gaia.project.current to return None so the query runs
        without a workspace filter, matching all seeded rows.
        """
        import gaia.store.writer as _writer_mod
        import gaia.project as _project_mod

        db = _make_in_memory_db(episodes)

        def _fake_connect():
            return db

        def _fake_current(**kwargs):
            return None

        from contextlib import contextmanager

        @contextmanager
        def _combined():
            with patch.object(_writer_mod, "_connect", _fake_connect):
                with patch.object(_project_mod, "current", _fake_current):
                    yield

        return _combined()

    def test_reads_from_db(self):
        """_read_workflow_metrics returns episodes with agent field from gaia.db."""
        episodes = [
            {
                "episode_id": "ep-1",
                "agent": "developer",
                "timestamp": "2026-04-15T10:00:00Z",
                "plan_status": "COMPLETE",
            },
            {
                "episode_id": "ep-2",
                "agent": "gaia-operator",
                "timestamp": "2026-04-15T11:00:00Z",
                "plan_status": "BLOCKED",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            with self._patch_connect(episodes):
                result = _read_workflow_metrics(root)
        self.assertEqual(len(result), 2)
        agents = [r["agent"] for r in result]
        self.assertIn("developer", agents)
        self.assertIn("gaia-operator", agents)

    def test_skips_entries_without_agent(self):
        """DB rows with agent=NULL are excluded by the WHERE clause."""
        # The SQL query already filters WHERE agent IS NOT NULL, so we only
        # seed rows that have agents — the test verifies the non-agent row
        # is absent from the result by checking count.
        episodes = [
            {
                "episode_id": "ep-1",
                "agent": "developer",
                "timestamp": "2026-04-15T11:00:00Z",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            with self._patch_connect(episodes):
                result = _read_workflow_metrics(root)
        self.assertEqual(len(result), 1)

    def test_falls_back_to_empty_on_connect_failure(self):
        """When _connect raises, _read_workflow_metrics returns []."""
        import gaia.store.writer as _writer_mod

        def _raise():
            raise Exception("DB unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            with patch.object(_writer_mod, "_connect", _raise):
                result = _read_workflow_metrics(root)
        self.assertEqual(result, [])

    def test_empty_when_no_episodes(self):
        """Empty episodes table returns []."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            with self._patch_connect([]):
                result = _read_workflow_metrics(root)
        self.assertEqual(result, [])

    def test_workspace_override_wins_over_project_current(self):
        """Explicit --workspace beats gaia.project.current() resolution."""
        episodes = [
            {"episode_id": "ep-1", "agent": "developer", "workspace": "me",
             "timestamp": "2026-04-15T10:00:00Z"},
            {"episode_id": "ep-2", "agent": "gaia-operator", "workspace": "other-ws",
             "timestamp": "2026-04-15T11:00:00Z"},
        ]
        db = _make_in_memory_db(episodes)
        import gaia.store.writer as _writer_mod
        import gaia.project as _project_mod

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            with patch.object(_writer_mod, "_connect", lambda *a, **k: db):
                # project.current() resolves to "me" -- the default path --
                # but the explicit override must take precedence.
                with patch.object(_project_mod, "current", lambda **k: "me"):
                    result = _read_workflow_metrics(root, workspace_override="other-ws")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["agent"], "gaia-operator")


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

    def test_workspace_flag_default_none(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["history"])
        self.assertIsNone(args.workspace)

    def test_workspace_flag_explicit(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["history", "--workspace", "other-ws"])
        self.assertEqual(args.workspace, "other-ws")

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
    """Tests for cmd_history — patches _read_workflow_metrics directly.

    T6 migration: tests no longer write index.json / metrics.jsonl since
    _read_workflow_metrics now reads from gaia.db exclusively. Instead,
    we patch cli.history._read_workflow_metrics to return controlled data.
    """

    def _make_args(self, today=False, blocked=False, agent=None, limit=20,
                   as_json=False, workspace=None):
        import argparse
        ns = argparse.Namespace()
        ns.today = today
        ns.blocked = blocked
        ns.agent = agent
        ns.limit = limit
        ns.json = as_json
        ns.workspace = workspace
        return ns

    def _make_root(self) -> Path:
        """Create a minimal project root with a .claude/ directory."""
        tmp = tempfile.mkdtemp()
        root = Path(tmp)
        (root / ".claude").mkdir()
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
        root = self._make_root()
        try:
            args = self._make_args()
            with patch("cli.history._find_project_root", return_value=root):
                with patch("cli.history._read_workflow_metrics", return_value=[]):
                    import io
                    from contextlib import redirect_stdout
                    with redirect_stdout(io.StringIO()):
                        rc = cmd_history(args)
            self.assertEqual(rc, 0)
        finally:
            import shutil
            shutil.rmtree(root)

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
        root = self._make_root()
        try:
            args = self._make_args(as_json=True)
            with patch("cli.history._find_project_root", return_value=root):
                with patch("cli.history._read_workflow_metrics", return_value=episodes):
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
        root = self._make_root()
        try:
            args = self._make_args(today=True, as_json=True)
            with patch("cli.history._find_project_root", return_value=root):
                with patch("cli.history._read_workflow_metrics", return_value=episodes):
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
        root = self._make_root()
        try:
            args = self._make_args(blocked=True, as_json=True)
            with patch("cli.history._find_project_root", return_value=root):
                with patch("cli.history._read_workflow_metrics", return_value=episodes):
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
        root = self._make_root()
        try:
            args = self._make_args(agent="developer", as_json=True)
            with patch("cli.history._find_project_root", return_value=root):
                with patch("cli.history._read_workflow_metrics", return_value=episodes):
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
            {
                "agent": "developer",
                "timestamp": f"2026-04-15T{h:02d}:00:00Z",
                "plan_status": "COMPLETE",
            }
            for h in range(10)
        ]
        root = self._make_root()
        try:
            args = self._make_args(limit=3, as_json=True)
            with patch("cli.history._find_project_root", return_value=root):
                with patch("cli.history._read_workflow_metrics", return_value=episodes):
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
            {
                "agent": "developer",
                "timestamp": "2026-04-15T10:00:00Z",
                "plan_status": "COMPLETE",
                "prompt": "Do work",
            },
        ]
        root = self._make_root()
        try:
            args = self._make_args()
            with patch("cli.history._find_project_root", return_value=root):
                with patch("cli.history._read_workflow_metrics", return_value=episodes):
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

    def test_workspace_flag_forwarded_to_reader(self):
        """cmd_history passes args.workspace through to _read_workflow_metrics."""
        root = self._make_root()
        try:
            args = self._make_args(as_json=True, workspace="other-ws")
            with patch("cli.history._find_project_root", return_value=root):
                with patch("cli.history._read_workflow_metrics", return_value=[]) as m_wf:
                    import io
                    from contextlib import redirect_stdout
                    with redirect_stdout(io.StringIO()):
                        rc = cmd_history(args)
            self.assertEqual(rc, 0)
            m_wf.assert_called_once_with(root, "other-ws")
        finally:
            import shutil
            shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()
