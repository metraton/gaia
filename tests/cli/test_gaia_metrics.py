"""
Tests for bin/cli/metrics.py -- gaia metrics subcommand.

T6 migration (episodic-workflow-to-db): the workflow-metric readers
(_read_workflow_metrics, _read_run_snapshots, _read_agent_skill_snapshots,
_read_anomaly_entries) now query the gaia.db episodes + episode_anomalies
tables instead of the dead .claude/project-context/*.jsonl files, mirroring
bin/cli/history.py. Reader tests seed an in-memory DB and patch
gaia.store.writer._connect (which gaia.store.reader._connect delegates to)
plus gaia.project.current. The three audit-log sections (tier usage, command
breakdown, top commands) still read audit-*.jsonl and keep their filesystem
fixtures.
"""

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli.metrics import (
    _find_project_root,
    _read_audit_logs,
    _read_workflow_metrics,
    _read_run_snapshots,
    _read_agent_skill_snapshots,
    _read_anomaly_entries,
    _classify_command,
    _extract_command_label,
    _calculate_tier_usage,
    _calculate_command_type_breakdown,
    _calculate_top_commands,
    _calculate_agent_invocations,
    _calculate_agent_outcomes,
    _calculate_token_usage,
    _calculate_anomaly_summary,
    _format_tokens,
    _format_chars,
    _make_bar,
    register,
    cmd_metrics,
)


def _write_audit_jsonl(logs_dir: Path, entries: list, filename: str = "audit-2026-04-15.jsonl"):
    logs_dir.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(json.dumps(e) for e in entries) + "\n"
    (logs_dir / filename).write_text(lines)


def _make_in_memory_db(episodes: list, anomalies: list = None) -> sqlite3.Connection:
    """In-memory SQLite DB with episodes + episode_anomalies seeded.

    ``episodes`` accepts dicts with optional ``context_metrics`` (a dict, which
    is JSON-serialized to match the real column). ``anomalies`` accepts dicts
    with episode_id/workspace/timestamp/type.
    """
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
            plan_status TEXT,
            outcome TEXT,
            exit_code INTEGER,
            duration_seconds REAL,
            output_length INTEGER,
            output_tokens_approx INTEGER,
            tier TEXT,
            context_metrics TEXT
        )"""
    )
    con.execute(
        """CREATE TABLE episode_anomalies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            episode_id TEXT,
            workspace TEXT,
            timestamp TEXT,
            type TEXT,
            severity TEXT,
            message TEXT,
            payload TEXT
        )"""
    )
    for i, ep in enumerate(episodes):
        cm = ep.get("context_metrics")
        if isinstance(cm, (dict, list)):
            cm = json.dumps(cm)
        con.execute(
            "INSERT INTO episodes (episode_id, workspace, timestamp, session_id, "
            "task_id, agent, type, title, plan_status, outcome, exit_code, "
            "duration_seconds, output_length, output_tokens_approx, tier, "
            "context_metrics) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ep.get("episode_id", f"ep-{i}"),
                ep.get("workspace", "me"),
                ep.get("timestamp"),
                ep.get("session_id"),
                ep.get("task_id"),
                ep.get("agent"),
                ep.get("type"),
                ep.get("title"),
                ep.get("plan_status"),
                ep.get("outcome"),
                ep.get("exit_code"),
                ep.get("duration_seconds"),
                ep.get("output_length"),
                ep.get("output_tokens_approx"),
                ep.get("tier"),
                cm,
            ),
        )
    for an in (anomalies or []):
        con.execute(
            "INSERT INTO episode_anomalies (episode_id, workspace, timestamp, "
            "type, severity, message, payload) VALUES (?,?,?,?,?,?,?)",
            (
                an.get("episode_id", "ep-0"),
                an.get("workspace", "me"),
                an.get("timestamp"),
                an.get("type"),
                an.get("severity"),
                an.get("message"),
                an.get("payload"),
            ),
        )
    con.commit()
    return con


@contextmanager
def _patch_store(con):
    """Patch gaia.store.writer._connect + gaia.project.current for reader tests."""
    import gaia.store.writer as _writer_mod
    import gaia.project as _project_mod

    def _fake_connect(*a, **k):
        return con

    def _fake_current(**kwargs):
        return None

    with patch.object(_writer_mod, "_connect", _fake_connect):
        with patch.object(_project_mod, "current", _fake_current):
            yield


class TestClassifyCommand(unittest.TestCase):
    def test_terraform(self):
        self.assertEqual(_classify_command("terraform plan"), "terraform")
        self.assertEqual(_classify_command("terragrunt apply"), "terraform")

    def test_kubernetes(self):
        self.assertEqual(_classify_command("kubectl get pods"), "kubernetes")

    def test_git(self):
        self.assertEqual(_classify_command("git status"), "git")
        self.assertEqual(_classify_command("glab mr list"), "git")

    def test_gcp(self):
        self.assertEqual(_classify_command("gcloud compute instances list"), "gcp")

    def test_docker(self):
        self.assertEqual(_classify_command("docker ps"), "docker")

    def test_dev(self):
        self.assertEqual(_classify_command("npm install"), "dev")
        self.assertEqual(_classify_command("python3 script.py"), "dev")

    def test_general(self):
        self.assertEqual(_classify_command("ls -la"), "general")
        self.assertEqual(_classify_command(""), "general")


class TestExtractCommandLabel(unittest.TestCase):
    def test_simple_command(self):
        self.assertEqual(_extract_command_label("git status"), "git status")

    def test_strips_flags(self):
        result = _extract_command_label("git commit -m 'msg'")
        self.assertEqual(result, "git commit")

    def test_strips_timeout(self):
        result = _extract_command_label("timeout 30s kubectl get pods")
        self.assertIn("kubectl", result)

    def test_strips_env_vars(self):
        result = _extract_command_label("FOO=bar git status")
        self.assertIn("git", result)

    def test_truncates_at_32(self):
        result = _extract_command_label("a" * 100)
        self.assertLessEqual(len(result), 32)

    def test_empty_returns_unknown(self):
        self.assertEqual(_extract_command_label(""), "(unknown)")


class TestCalculateTierUsage(unittest.TestCase):
    def _make_logs(self, tiers):
        now = datetime.now(timezone.utc).isoformat()
        return [{"tier": t, "timestamp": now} for t in tiers]

    def test_counts_tiers(self):
        logs = self._make_logs(["T0", "T0", "T1", "T3"])
        result = _calculate_tier_usage(logs)
        self.assertEqual(result["total"], 4)
        t0 = next(d for d in result["distribution"] if d["tier"] == "T0")
        self.assertEqual(t0["count"], 2)

    def test_empty_logs(self):
        result = _calculate_tier_usage([])
        self.assertEqual(result["total"], 0)

    def test_today_stats(self):
        now = datetime.now(timezone.utc).isoformat()
        logs = [{"tier": "T3", "timestamp": now}]
        result = _calculate_tier_usage(logs)
        self.assertEqual(result["today_count"], 1)
        self.assertEqual(result["today_t3"], 1)


class TestCalculateCommandTypeBreakdown(unittest.TestCase):
    def test_breakdown(self):
        logs = [
            {"command": "git status"},
            {"command": "git log"},
            {"command": "kubectl get pods"},
        ]
        result = _calculate_command_type_breakdown(logs)
        git_item = next(b for b in result["breakdown"] if b["type"] == "git")
        self.assertEqual(git_item["count"], 2)

    def test_empty(self):
        result = _calculate_command_type_breakdown([])
        self.assertEqual(result["total"], 0)


class TestCalculateTopCommands(unittest.TestCase):
    def test_top_10(self):
        logs = [{"command": f"git command{i}", "tier": "T0"} for i in range(15)]
        result = _calculate_top_commands(logs)
        self.assertLessEqual(len(result), 10)

    def test_counts_correctly(self):
        logs = [
            {"command": "git status", "tier": "T0"},
            {"command": "git status", "tier": "T0"},
            {"command": "kubectl get pods", "tier": "T0"},
        ]
        result = _calculate_top_commands(logs)
        git_status = next((r for r in result if r["label"] == "git status"), None)
        self.assertIsNotNone(git_status)
        self.assertEqual(git_status["count"], 2)

    def test_tracks_t3(self):
        logs = [
            {"command": "git push", "tier": "T3"},
            {"command": "git push", "tier": "T3"},
        ]
        result = _calculate_top_commands(logs)
        push = next((r for r in result if "git" in r["label"]), None)
        self.assertIsNotNone(push)
        self.assertEqual(push["t3count"], 2)


class TestCalculateAgentInvocations(unittest.TestCase):
    def test_groups_by_agent(self):
        metrics = [
            {"agent": "developer", "exit_code": 0, "output_length": 1000, "timestamp": "2026-04-15T10:00:00Z"},
            {"agent": "developer", "exit_code": 0, "output_length": 2000, "timestamp": "2026-04-15T11:00:00Z"},
            {"agent": "gaia-operator", "exit_code": 1, "output_length": 500, "timestamp": "2026-04-15T12:00:00Z"},
        ]
        result = _calculate_agent_invocations(metrics)
        dev = next(a for a in result["agents"] if a["name"] == "developer")
        self.assertEqual(dev["count"], 2)
        self.assertEqual(dev["avg_output"], 1500)

    def test_today_count(self):
        today = datetime.now(timezone.utc).isoformat()
        metrics = [
            {"agent": "developer", "exit_code": 0, "output_length": 0, "timestamp": today},
        ]
        result = _calculate_agent_invocations(metrics)
        self.assertEqual(result["today_count"], 1)


class TestCalculateAgentOutcomes(unittest.TestCase):
    def test_counts_statuses(self):
        metrics = [
            {"agent": "developer", "plan_status": "COMPLETE"},
            {"agent": "developer", "plan_status": "COMPLETE"},
            {"agent": "developer", "plan_status": "BLOCKED"},
        ]
        result = _calculate_agent_outcomes(metrics)
        self.assertIsNotNone(result)
        complete = next(d for d in result["distribution"] if d["status"] == "COMPLETE")
        self.assertEqual(complete["count"], 2)

    def test_none_when_no_plan_status(self):
        metrics = [{"agent": "developer"}]
        result = _calculate_agent_outcomes(metrics)
        self.assertIsNone(result)


class TestCalculateTokenUsage(unittest.TestCase):
    def test_sums_tokens(self):
        metrics = [
            {"agent": "developer", "output_tokens_approx": 100},
            {"agent": "developer", "output_tokens_approx": 200},
        ]
        result = _calculate_token_usage(metrics)
        self.assertIsNotNone(result)
        self.assertEqual(result["grand_total"], 300)

    def test_none_when_no_tokens(self):
        result = _calculate_token_usage([{"agent": "developer"}])
        self.assertIsNone(result)


class TestCalculateAnomalySummary(unittest.TestCase):
    def test_recent_anomalies(self):
        recent = datetime.now(timezone.utc).isoformat()
        entries = [
            {
                "timestamp": recent,
                "anomalies": [{"type": "contract_missing"}, {"type": "contract_missing"}],
                "metrics": {"agent": "developer"},
            }
        ]
        result = _calculate_anomaly_summary(entries)
        self.assertIsNotNone(result)
        self.assertEqual(result["total"], 2)

    def test_old_anomalies_excluded(self):
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        entries = [
            {
                "timestamp": old,
                "anomalies": [{"type": "contract_missing"}],
                "metrics": {"agent": "developer"},
            }
        ]
        result = _calculate_anomaly_summary(entries)
        self.assertIsNone(result)


class TestFormatHelpers(unittest.TestCase):
    def test_format_tokens_millions(self):
        self.assertIn("1.0M", _format_tokens(1_000_000))

    def test_format_tokens_thousands(self):
        self.assertIn("1.5k", _format_tokens(1500))

    def test_format_tokens_small(self):
        self.assertEqual(_format_tokens(42), "42")

    def test_format_chars(self):
        self.assertIn("1.5k", _format_chars(1500))
        self.assertEqual(_format_chars(42), "42")

    def test_make_bar_full(self):
        result = _make_bar(100, 10)
        self.assertEqual(len(result), 10)

    def test_make_bar_empty(self):
        result = _make_bar(0, 10)
        self.assertEqual(len(result), 0)

    def test_make_bar_half(self):
        result = _make_bar(50, 10)
        self.assertEqual(len(result), 5)


class TestRegisterSubcommand(unittest.TestCase):
    def test_register_creates_parser(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["metrics"])
        self.assertIsNone(args.agent)

    def test_agent_flag(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["metrics", "--agent", "developer"])
        self.assertEqual(args.agent, "developer")

    def test_json_flag(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["metrics", "--json"])
        self.assertTrue(args.json)

    def test_workspace_flag_default_none(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["metrics"])
        self.assertIsNone(args.workspace)

    def test_workspace_flag_explicit(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["metrics", "--workspace", "other-ws"])
        self.assertEqual(args.workspace, "other-ws")


class TestDbBackedReaders(unittest.TestCase):
    """T6 migration: readers now query gaia.db, not .claude/*.jsonl files.

    Each test seeds a fresh in-memory DB and patches gaia.store.writer._connect
    (delegated to by gaia.store.reader._connect) + gaia.project.current(None).
    A reader closes its connection after use, so one reader is exercised per DB.
    """

    def _root(self, tmp: Path) -> Path:
        (tmp / ".claude").mkdir()
        return tmp

    def test_workflow_metrics_reads_episodes(self):
        episodes = [
            {"episode_id": "ep-1", "agent": "developer",
             "timestamp": "2026-04-15T10:00:00Z", "plan_status": "COMPLETE",
             "output_length": 1200, "output_tokens_approx": 300, "exit_code": 0},
            {"episode_id": "ep-2", "agent": "gaia-operator",
             "timestamp": "2026-04-15T11:00:00Z", "plan_status": "BLOCKED"},
        ]
        con = _make_in_memory_db(episodes)
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(Path(tmp))
            with _patch_store(con):
                result = _read_workflow_metrics(root)
        self.assertEqual(len(result), 2)
        agents = {r["agent"] for r in result}
        self.assertEqual(agents, {"developer", "gaia-operator"})
        dev = next(r for r in result if r["agent"] == "developer")
        self.assertEqual(dev["output_length"], 1200)
        self.assertEqual(dev["output_tokens_approx"], 300)

    def test_workflow_metrics_empty_on_connect_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(Path(tmp))
            with patch("cli.metrics._open_store", return_value=(None, None)):
                self.assertEqual(_read_workflow_metrics(root), [])

    def test_workflow_metrics_workspace_override_wins_over_project_current(self):
        """Explicit --workspace beats gaia.project.current() resolution."""
        episodes = [
            {"episode_id": "ep-1", "agent": "developer", "workspace": "me",
             "timestamp": "2026-04-15T10:00:00Z"},
            {"episode_id": "ep-2", "agent": "gaia-operator", "workspace": "other-ws",
             "timestamp": "2026-04-15T11:00:00Z"},
        ]
        con = _make_in_memory_db(episodes)
        import gaia.store.writer as _writer_mod
        import gaia.project as _project_mod

        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(Path(tmp))
            with patch.object(_writer_mod, "_connect", lambda *a, **k: con):
                # project.current() resolves to "me" -- the default path --
                # but the explicit override must take precedence.
                with patch.object(_project_mod, "current", lambda **k: "me"):
                    result = _read_workflow_metrics(root, workspace_override="other-ws")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["agent"], "gaia-operator")

    def test_run_snapshots_extract_metrics_blob(self):
        blob = {"metrics": {
            "agent": "developer",
            "timestamp": "2026-04-15T10:00:00Z",
            "default_skills_snapshot": {"model": "inherit",
                                        "skills": ["agent-protocol"],
                                        "skills_count": 1, "tools": ["Read"]},
            "context_updated": True,
            "context_sections_updated": ["application_services"],
            "context_rejected_sections": [],
            "context_snapshot": {},
        }}
        episodes = [{"episode_id": "ep-1", "agent": "developer",
                     "timestamp": "2026-04-15T10:00:00Z", "context_metrics": blob}]
        con = _make_in_memory_db(episodes)
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(Path(tmp))
            with _patch_store(con):
                snaps = _read_run_snapshots(root)
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0]["agent"], "developer")
        self.assertTrue(snaps[0]["context_updated"])
        self.assertEqual(snaps[0]["default_skills_snapshot"]["skills_count"], 1)

    def test_run_snapshots_handles_top_level_metrics_shape(self):
        # Older migrated rows may store the metrics dict at the top level.
        blob = {"agent": "developer", "timestamp": "2026-04-15T10:00:00Z",
                "context_updated": False}
        episodes = [{"episode_id": "ep-1", "agent": "developer",
                     "timestamp": "2026-04-15T10:00:00Z", "context_metrics": blob}]
        con = _make_in_memory_db(episodes)
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(Path(tmp))
            with _patch_store(con):
                snaps = _read_run_snapshots(root)
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0]["agent"], "developer")

    def test_agent_skill_snapshots_always_empty(self):
        # No gaia.db equivalent for explicit agent-skills.jsonl; returns [].
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(Path(tmp))
            self.assertEqual(_read_agent_skill_snapshots(root), [])

    def test_anomaly_entries_grouped_per_episode(self):
        episodes = [{"episode_id": "ep-1", "agent": "developer",
                     "timestamp": "2026-04-15T10:00:00Z"}]
        anomalies = [
            {"episode_id": "ep-1", "timestamp": "2026-04-15T10:00:00Z",
             "type": "investigation_skip"},
            {"episode_id": "ep-1", "timestamp": "2026-04-15T10:00:00Z",
             "type": "context_ignored"},
        ]
        con = _make_in_memory_db(episodes, anomalies)
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(Path(tmp))
            with _patch_store(con):
                entries = _read_anomaly_entries(root)
        # Two anomaly rows for one episode -> one grouped entry with 2 anomalies.
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["metrics"]["agent"], "developer")
        types = {a["type"] for a in entries[0]["anomalies"]}
        self.assertEqual(types, {"investigation_skip", "context_ignored"})

    def test_anomaly_entries_feed_summary_calculator(self):
        # End-to-end: grouped entries are the shape _calculate_anomaly_summary wants.
        now = datetime.now(timezone.utc).isoformat()
        episodes = [{"episode_id": "ep-1", "agent": "developer", "timestamp": now}]
        anomalies = [{"episode_id": "ep-1", "timestamp": now, "type": "pipe_retroactive"}]
        con = _make_in_memory_db(episodes, anomalies)
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(Path(tmp))
            with _patch_store(con):
                entries = _read_anomaly_entries(root)
        summary = _calculate_anomaly_summary(entries)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["session_count"], 1)


class TestCmdMetrics(unittest.TestCase):
    def setUp(self):
        # Isolate every cmd_metrics test from the real ~/.gaia/gaia.db: with
        # _open_store returning (None, None) the three DB-backed readers all
        # return []. Tests that need episode data override the specific reader.
        self._open_patch = patch("cli.metrics._open_store", return_value=(None, None))
        self._open_patch.start()
        self.addCleanup(self._open_patch.stop)

    def _make_args(self, agent=None, as_json=False, workspace=None):
        import argparse
        ns = argparse.Namespace()
        ns.agent = agent
        ns.json = as_json
        ns.workspace = workspace
        return ns

    def test_no_claude_dir_returns_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = self._make_args()
            with patch("cli.metrics._find_project_root", return_value=root):
                import io
                from contextlib import redirect_stdout
                with redirect_stdout(io.StringIO()):
                    rc = cmd_metrics(args)
            self.assertEqual(rc, 1)

    def test_no_data_returns_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            args = self._make_args()
            with patch("cli.metrics._find_project_root", return_value=root):
                import io
                from contextlib import redirect_stdout
                with redirect_stdout(io.StringIO()):
                    rc = cmd_metrics(args)
            self.assertEqual(rc, 0)

    def test_no_data_json_returns_full_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            args = self._make_args(as_json=True)
            with patch("cli.metrics._find_project_root", return_value=root):
                import io
                from contextlib import redirect_stdout
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cmd_metrics(args)
                output = buf.getvalue()
            self.assertEqual(rc, 0)
            data = json.loads(output)
            # Empty state must return full schema with zero values (not a "message" wrapper)
            self.assertIn("security_tiers", data)
            self.assertIn("cmd_types", data)
            self.assertIn("agent_invocations", data)
            self.assertEqual(data["security_tiers"]["total"], 0)
            self.assertEqual(data["agent_invocations"]["total"], 0)

    def test_json_output_with_audit_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_dir = root / ".claude"
            claude_dir.mkdir()
            now = datetime.now(timezone.utc).isoformat()
            logs = [
                {"tier": "T0", "command": "git status", "timestamp": now, "exit_code": 0},
                {"tier": "T3", "command": "git push", "timestamp": now, "exit_code": 0},
            ]
            _write_audit_jsonl(claude_dir / "logs", logs)

            args = self._make_args(as_json=True)
            with patch("cli.metrics._find_project_root", return_value=root):
                import io
                from contextlib import redirect_stdout
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cmd_metrics(args)
                output = buf.getvalue()

            self.assertEqual(rc, 0)
            data = json.loads(output)
            self.assertIn("security_tiers", data)
            self.assertIn("cmd_types", data)
            self.assertIn("agent_invocations", data)
            self.assertEqual(data["security_tiers"]["total"], 2)

    def test_dashboard_output_contains_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_dir = root / ".claude"
            claude_dir.mkdir()
            now = datetime.now(timezone.utc).isoformat()
            logs = [{"tier": "T0", "command": "git status", "timestamp": now}]
            _write_audit_jsonl(claude_dir / "logs", logs)

            args = self._make_args()
            with patch("cli.metrics._find_project_root", return_value=root):
                import io
                from contextlib import redirect_stdout
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cmd_metrics(args)
                output = buf.getvalue()

            self.assertEqual(rc, 0)
            self.assertIn("Security Tier", output)
            self.assertIn("Command Type", output)
            self.assertIn("Activity Today", output)

    def test_agent_detail_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_dir = root / ".claude"
            claude_dir.mkdir()
            now = datetime.now(timezone.utc).isoformat()
            episodes = [
                {
                    "agent": "developer",
                    "timestamp": now,
                    "plan_status": "COMPLETE",
                    "exit_code": 0,
                    "output_length": 1000,
                    "task_id": "task-001",
                }
            ]

            args = self._make_args(agent="developer")
            with patch("cli.metrics._find_project_root", return_value=root), \
                    patch("cli.metrics._read_workflow_metrics", return_value=episodes):
                import io
                from contextlib import redirect_stdout
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cmd_metrics(args)
                output = buf.getvalue()

            self.assertEqual(rc, 0)
            self.assertIn("developer", output)
            self.assertIn("Invocation History", output)

    def test_workspace_flag_forwarded_to_readers(self):
        """cmd_metrics passes args.workspace through to every DB-backed reader."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            args = self._make_args(as_json=True, workspace="other-ws")
            with patch("cli.metrics._find_project_root", return_value=root), \
                    patch("cli.metrics._read_workflow_metrics", return_value=[]) as m_wf, \
                    patch("cli.metrics._read_run_snapshots", return_value=[]) as m_rs, \
                    patch("cli.metrics._read_anomaly_entries", return_value=[]) as m_ae:
                import io
                from contextlib import redirect_stdout
                with redirect_stdout(io.StringIO()):
                    rc = cmd_metrics(args)
            self.assertEqual(rc, 0)
            m_wf.assert_called_once_with(root, "other-ws")
            m_rs.assert_called_once_with(root, "other-ws")
            m_ae.assert_called_once_with(root, "other-ws")


if __name__ == "__main__":
    unittest.main()
