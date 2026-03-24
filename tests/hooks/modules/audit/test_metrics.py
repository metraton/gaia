#!/usr/bin/env python3
"""
Tests for metrics aggregation from audit logs.

Validates generate_summary() reads audit-*.jsonl and produces correct
aggregations: total_executions, avg_duration_ms, top_commands,
tier_distribution, command_type_distribution.
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.audit.metrics import generate_summary, _classify_command


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_audit_record(logs_dir: Path, record: dict, date_str: str = None):
    """Write a single audit record to the appropriate daily file."""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    audit_file = logs_dir / f"audit-{date_str}.jsonl"
    with open(audit_file, "a") as f:
        f.write(json.dumps(record) + "\n")


def _make_record(
    command: str = "kubectl get pods",
    tool_name: str = "Bash",
    duration_ms: float = 150.0,
    tier: str = "T0",
    timestamp: str = None,
    exit_code: int = 0,
):
    """Create a minimal audit log record."""
    if timestamp is None:
        timestamp = datetime.now().isoformat()
    return {
        "timestamp": timestamp,
        "session_id": "test-session",
        "tool_name": tool_name,
        "command": command,
        "parameters": {"command": command},
        "duration_ms": duration_ms,
        "exit_code": exit_code,
        "tier": tier,
    }


# ---------------------------------------------------------------------------
# _classify_command
# ---------------------------------------------------------------------------


class TestClassifyCommand:
    def test_terraform(self):
        assert _classify_command("terraform plan -out=plan.tfplan") == "terraform"

    def test_kubectl(self):
        assert _classify_command("kubectl get pods -n prod") == "kubernetes"

    def test_helm(self):
        assert _classify_command("helm list -A") == "helm"

    def test_gcloud(self):
        assert _classify_command("gcloud compute instances list") == "gcp"

    def test_aws(self):
        assert _classify_command("aws s3 ls") == "aws"

    def test_flux(self):
        assert _classify_command("flux get kustomizations") == "flux"

    def test_docker(self):
        assert _classify_command("docker ps") == "docker"

    def test_git(self):
        assert _classify_command("git status") == "git"

    def test_general_fallback(self):
        assert _classify_command("ls -la") == "general"

    def test_case_insensitive(self):
        assert _classify_command("KUBECTL get pods") == "kubernetes"


# ---------------------------------------------------------------------------
# generate_summary — empty
# ---------------------------------------------------------------------------


class TestGenerateSummaryEmpty:
    def test_returns_zeros_when_no_files(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        summary = generate_summary(days=7, logs_dir=logs_dir)

        assert summary["period_days"] == 7
        assert summary["total_executions"] == 0
        assert summary["avg_duration_ms"] == 0.0
        assert summary["top_commands"] == []
        assert summary["tier_distribution"] == {}
        assert summary["command_type_distribution"] == {}

    def test_returns_zeros_when_empty_file(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "audit-2026-03-24.jsonl").write_text("")
        summary = generate_summary(days=7, logs_dir=logs_dir)
        assert summary["total_executions"] == 0

    def test_no_success_rate_field(self, tmp_path):
        """success_rate was removed as unreliable."""
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        summary = generate_summary(days=7, logs_dir=logs_dir)
        assert "success_rate" not in summary


# ---------------------------------------------------------------------------
# generate_summary — with data
# ---------------------------------------------------------------------------


class TestGenerateSummaryWithData:
    def test_counts_records(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        now = datetime.now()
        for i in range(5):
            _write_audit_record(logs_dir, _make_record(
                command=f"kubectl get pods -n ns{i}",
                timestamp=now.isoformat(),
                duration_ms=100.0 + i * 10,
            ))

        summary = generate_summary(days=7, logs_dir=logs_dir)
        assert summary["total_executions"] == 5

    def test_avg_duration(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        now = datetime.now()
        _write_audit_record(logs_dir, _make_record(
            timestamp=now.isoformat(), duration_ms=100.0
        ))
        _write_audit_record(logs_dir, _make_record(
            timestamp=now.isoformat(), duration_ms=200.0
        ))

        summary = generate_summary(days=7, logs_dir=logs_dir)
        assert summary["avg_duration_ms"] == 150.0

    def test_command_type_distribution(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        now = datetime.now()
        _write_audit_record(logs_dir, _make_record(
            command="kubectl get pods", timestamp=now.isoformat()
        ))
        _write_audit_record(logs_dir, _make_record(
            command="terraform plan", timestamp=now.isoformat()
        ))
        _write_audit_record(logs_dir, _make_record(
            command="kubectl apply -f x.yaml", timestamp=now.isoformat()
        ))

        summary = generate_summary(days=7, logs_dir=logs_dir)
        assert summary["command_type_distribution"]["kubernetes"] == 2
        assert summary["command_type_distribution"]["terraform"] == 1

    def test_tier_distribution(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        now = datetime.now()
        _write_audit_record(logs_dir, _make_record(
            tier="T0", timestamp=now.isoformat()
        ))
        _write_audit_record(logs_dir, _make_record(
            tier="T0", timestamp=now.isoformat()
        ))
        _write_audit_record(logs_dir, _make_record(
            tier="T3", timestamp=now.isoformat()
        ))

        summary = generate_summary(days=7, logs_dir=logs_dir)
        assert summary["tier_distribution"]["T0"] == 2
        assert summary["tier_distribution"]["T3"] == 1

    def test_top_commands_sorted_by_count(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        now = datetime.now()
        # 3 git, 2 kubernetes, 1 terraform
        for _ in range(3):
            _write_audit_record(logs_dir, _make_record(
                command="git status", timestamp=now.isoformat()
            ))
        for _ in range(2):
            _write_audit_record(logs_dir, _make_record(
                command="kubectl get ns", timestamp=now.isoformat()
            ))
        _write_audit_record(logs_dir, _make_record(
            command="terraform plan", timestamp=now.isoformat()
        ))

        summary = generate_summary(days=7, logs_dir=logs_dir)
        top = summary["top_commands"]
        assert top[0]["type"] == "git"
        assert top[0]["count"] == 3
        assert top[1]["type"] == "kubernetes"
        assert top[1]["count"] == 2

    def test_has_generated_at_field(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        now = datetime.now()
        _write_audit_record(logs_dir, _make_record(timestamp=now.isoformat()))

        summary = generate_summary(days=7, logs_dir=logs_dir)
        assert "generated_at" in summary


# ---------------------------------------------------------------------------
# generate_summary — date filtering
# ---------------------------------------------------------------------------


class TestGenerateSummaryDateFiltering:
    def test_excludes_old_records(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        now = datetime.now()
        old = now - timedelta(days=10)

        _write_audit_record(
            logs_dir,
            _make_record(timestamp=now.isoformat()),
            date_str=now.strftime("%Y-%m-%d"),
        )
        _write_audit_record(
            logs_dir,
            _make_record(timestamp=old.isoformat()),
            date_str=old.strftime("%Y-%m-%d"),
        )

        summary = generate_summary(days=7, logs_dir=logs_dir)
        assert summary["total_executions"] == 1

    def test_custom_days_parameter(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        now = datetime.now()
        three_days_ago = now - timedelta(days=3)

        _write_audit_record(
            logs_dir,
            _make_record(timestamp=now.isoformat()),
            date_str=now.strftime("%Y-%m-%d"),
        )
        _write_audit_record(
            logs_dir,
            _make_record(timestamp=three_days_ago.isoformat()),
            date_str=three_days_ago.strftime("%Y-%m-%d"),
        )

        summary_1day = generate_summary(days=1, logs_dir=logs_dir)
        summary_7day = generate_summary(days=7, logs_dir=logs_dir)

        assert summary_1day["total_executions"] == 1
        assert summary_7day["total_executions"] == 2


# ---------------------------------------------------------------------------
# generate_summary — resilience
# ---------------------------------------------------------------------------


class TestGenerateSummaryResilience:
    def test_skips_malformed_json_lines(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        now = datetime.now()
        audit_file = logs_dir / f"audit-{now.strftime('%Y-%m-%d')}.jsonl"
        good_record = _make_record(timestamp=now.isoformat())
        with open(audit_file, "w") as f:
            f.write(json.dumps(good_record) + "\n")
            f.write("not valid json\n")
            f.write(json.dumps(good_record) + "\n")

        summary = generate_summary(days=7, logs_dir=logs_dir)
        assert summary["total_executions"] == 2

    def test_handles_missing_fields_gracefully(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        now = datetime.now()
        # Record with minimal fields
        minimal = {"timestamp": now.isoformat()}
        _write_audit_record(logs_dir, minimal)

        summary = generate_summary(days=7, logs_dir=logs_dir)
        assert summary["total_executions"] == 1
        assert summary["avg_duration_ms"] == 0.0
        assert summary["command_type_distribution"]["general"] == 1
        assert summary["tier_distribution"]["unknown"] == 1

    def test_non_bash_tools_classify_as_general(self, tmp_path):
        """Non-Bash tools have empty command field, classified as general."""
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        now = datetime.now()
        _write_audit_record(logs_dir, _make_record(
            tool_name="Read", command="", timestamp=now.isoformat()
        ))

        summary = generate_summary(days=7, logs_dir=logs_dir)
        assert summary["command_type_distribution"]["general"] == 1
