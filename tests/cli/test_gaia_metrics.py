"""
Tests for bin/cli/metrics.py -- gaia metrics subcommand.
"""

import json
import sys
import tempfile
import unittest
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


def _write_index(claude_dir: Path, episodes: list):
    ep_dir = claude_dir / "project-context" / "episodic-memory"
    ep_dir.mkdir(parents=True, exist_ok=True)
    (ep_dir / "index.json").write_text(json.dumps({"episodes": episodes}))


def _write_anomalies(claude_dir: Path, entries: list):
    wem_dir = claude_dir / "project-context" / "workflow-episodic-memory"
    wem_dir.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(json.dumps(e) for e in entries) + "\n"
    (wem_dir / "anomalies.jsonl").write_text(lines)


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


class TestCmdMetrics(unittest.TestCase):
    def _make_args(self, agent=None, as_json=False):
        import argparse
        ns = argparse.Namespace()
        ns.agent = agent
        ns.json = as_json
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
            episodes = [
                {"agent": "developer", "timestamp": now, "plan_status": "COMPLETE", "exit_code": 0},
            ]
            _write_index(claude_dir, episodes)

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
            _write_index(claude_dir, episodes)

            args = self._make_args(agent="developer")
            with patch("cli.metrics._find_project_root", return_value=root):
                import io
                from contextlib import redirect_stdout
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cmd_metrics(args)
                output = buf.getvalue()

            self.assertEqual(rc, 0)
            self.assertIn("developer", output)
            self.assertIn("Invocation History", output)


if __name__ == "__main__":
    unittest.main()
