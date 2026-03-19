"""
Tests for the gaia simulator testing module.

Tests cover:
1. LogExtractor: parsing hooks logs and audit JSONL
2. HookRunner: executing hooks and comparing results
3. ReplayReporter: formatting results
4. Regression detection: mocked hook returning different decisions
5. CLI: argument parsing and wiring

Run: python3 -m pytest tests/tools/test_gaia_simulator.py -v
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

# Add the tools directory to sys.path so 'gaia_simulator' package is importable
# Pattern: tests/tools/test_gaia_simulator.py -> tests/tools -> tests -> gaia-ops-plugin -> tools
TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
if TOOLS_DIR.is_symlink():
    TOOLS_DIR = TOOLS_DIR.resolve()
sys.path.insert(0, str(TOOLS_DIR))

# Module under test
from gaia_simulator.cli import main as gaia_simulator_cli_main  # noqa: E402
from gaia_simulator.extractor import LogExtractor, ReplayEvent  # noqa: E402
from gaia_simulator.runner import HookRunner, ReplayResult, _parse_decision_from_output, _classify_regression  # noqa: E402
from gaia_simulator.reporter import ReplayReporter  # noqa: E402


# ============================================================================
# Fixtures: sample log data
# ============================================================================

SAMPLE_HOOKS_LOG = textwrap.dedent("""\
    2026-03-11 10:00:01,000 [pre_tool_use] __main__ - INFO - Hook event: PreToolUse
    2026-03-11 10:00:01,000 [pre_tool_use] __main__ - INFO - Hook invoked: tool=Bash, params={"command": "kubectl get pods", "description": "List pods"}
    2026-03-11 10:00:01,001 [pre_tool_use] __main__ - INFO - ALLOWED: kubectl get pods - tier=T0
    2026-03-11 10:00:01,100 [post_tool_use] __main__ - INFO - Post-hook event: PostToolUse
    2026-03-11 10:00:02,000 [pre_tool_use] __main__ - INFO - Hook event: PreToolUse
    2026-03-11 10:00:02,000 [pre_tool_use] __main__ - INFO - Hook invoked: tool=Bash, params={"command": "ls /tmp/foo", "description": "List directory"}
    2026-03-11 10:00:02,001 [pre_tool_use] __main__ - INFO - ALLOWED: ls /tmp/foo - tier=T0
    2026-03-11 10:00:03,000 [pre_tool_use] __main__ - INFO - Hook event: PreToolUse
    2026-03-11 10:00:03,000 [pre_tool_use] __main__ - INFO - Hook invoked: tool=Bash, params={"command": "terraform apply", "description": "Apply changes"}
    2026-03-11 10:00:03,001 [pre_tool_use] __main__ - WARNING - BLOCKED: terraform apply - Dangerous mutative command: Mutative verb 'apply'
    2026-03-11 10:00:04,000 [pre_tool_use] __main__ - INFO - Hook event: PreToolUse
    2026-03-11 10:00:04,000 [pre_tool_use] __main__ - INFO - Hook invoked: tool=Agent, params={"description": "Investigate pods", "prompt": "Check pod status", "subagent_type": "cloud-troubleshooter"}
    2026-03-11 10:00:04,001 [pre_tool_use] __main__ - INFO - ALLOWED Task: cloud-troubleshooter
    2026-03-11 10:00:05,000 [pre_tool_use] __main__ - INFO - Hook event: PreToolUse
    2026-03-11 10:00:05,000 [pre_tool_use] __main__ - INFO - Hook invoked: tool=Agent, params={"description": "Unknown agent", "prompt": "Do something", "subagent_type": "general-purpose"}
    2026-03-11 10:00:05,001 [pre_tool_use] __main__ - WARNING - BLOCKED Task: general-purpose - Unknown agent: 'general-purpose'
    2026-03-11 10:00:06,000 [pre_tool_use] __main__ - INFO - Hook event: PreToolUse
    2026-03-11 10:00:06,000 [pre_tool_use] __main__ - INFO - Hook invoked: tool=Bash, params={"command": "git status", "description": "Check git status"}
    2026-03-11 10:00:06,001 [pre_tool_use] __main__ - INFO - ALLOWED: git status - tier=T0
    2026-03-11 10:00:07,000 [pre_tool_use] __main__ - INFO - Hook event: PreToolUse
    2026-03-11 10:00:07,000 [pre_tool_use] __main__ - INFO - Hook invoked: tool=Bash, params={"command": "echo hello", "description": "Say hello"}
    2026-03-11 10:00:07,001 [pre_tool_use] __main__ - INFO - ALLOWED: echo hello - tier=T0
    2026-03-11 10:00:08,000 [stop_hook] __main__ - INFO - Stop: reason=user_requested, quality_sufficient=True, score=1.00
""")

SAMPLE_AUDIT_JSONL = textwrap.dedent("""\
    {"timestamp": "2026-03-11T10:00:01.100000", "session_id": "default", "tool_name": "Bash", "command": "kubectl get pods", "parameters": {"command": "kubectl get pods", "description": "List pods"}, "duration_ms": 150.5, "exit_code": 0, "tier": "T0"}
    {"timestamp": "2026-03-11T10:00:02.100000", "session_id": "default", "tool_name": "Bash", "command": "ls /tmp/foo", "parameters": {"command": "ls /tmp/foo", "description": "List directory"}, "duration_ms": 50.0, "exit_code": 0, "tier": "T0"}
    {"timestamp": "2026-03-11T10:00:06.100000", "session_id": "default", "tool_name": "Bash", "command": "git status", "parameters": {"command": "git status", "description": "Check git status"}, "duration_ms": 200.0, "exit_code": 0, "tier": "T0"}
""")


@pytest.fixture
def logs_dir(tmp_path: Path) -> Path:
    """Create a temp directory with sample log files."""
    d = tmp_path / "logs"
    d.mkdir()
    (d / "hooks-2026-03-11.log").write_text(SAMPLE_HOOKS_LOG)
    (d / "audit-2026-03-11.jsonl").write_text(SAMPLE_AUDIT_JSONL)
    return d


@pytest.fixture
def hooks_dir() -> Path:
    """Return the real hooks directory."""
    return Path(__file__).resolve().parents[2] / "hooks"


# ============================================================================
# Test LogExtractor
# ============================================================================


class TestLogExtractorHooksLog:
    """Tests for parsing hooks-*.log files."""

    def test_extract_allowed_bash_events(self, logs_dir: Path):
        extractor = LogExtractor()
        events = extractor.extract_from_hooks_log(logs_dir / "hooks-2026-03-11.log")

        allowed_bash = [e for e in events if e.expected_decision == "ALLOW" and e.tool_name == "Bash"]
        assert len(allowed_bash) == 4  # kubectl, ls, git status, echo hello

    def test_extract_denied_bash_events(self, logs_dir: Path):
        """Mutative commands (e.g. terraform apply) are DENY (T3 nonce flow), not BLOCK."""
        extractor = LogExtractor()
        events = extractor.extract_from_hooks_log(logs_dir / "hooks-2026-03-11.log")

        denied = [e for e in events if e.expected_decision == "DENY" and e.tool_name == "Bash"]
        assert len(denied) == 1  # terraform apply

    def test_extract_allowed_agent_events(self, logs_dir: Path):
        extractor = LogExtractor()
        events = extractor.extract_from_hooks_log(logs_dir / "hooks-2026-03-11.log")

        allowed_agents = [e for e in events if e.expected_decision == "ALLOW" and e.tool_name == "Agent"]
        assert len(allowed_agents) == 1  # cloud-troubleshooter

    def test_extract_blocked_agent_events(self, logs_dir: Path):
        extractor = LogExtractor()
        events = extractor.extract_from_hooks_log(logs_dir / "hooks-2026-03-11.log")

        blocked_agents = [e for e in events if e.expected_decision == "BLOCK" and e.tool_name == "Agent"]
        assert len(blocked_agents) == 1  # general-purpose

    def test_event_has_correct_stdin_payload(self, logs_dir: Path):
        extractor = LogExtractor()
        events = extractor.extract_from_hooks_log(logs_dir / "hooks-2026-03-11.log")

        # First event: kubectl get pods
        ev = events[0]
        assert ev.stdin_payload["tool_name"] == "Bash"
        assert ev.stdin_payload["tool_input"]["command"] == "kubectl get pods"
        assert ev.stdin_payload["hook_event_name"] == "PreToolUse"

    def test_blocked_event_exit_code(self, logs_dir: Path):
        extractor = LogExtractor()
        events = extractor.extract_from_hooks_log(logs_dir / "hooks-2026-03-11.log")

        # Permanent blocks (exit 2): task blocks like unknown agent
        blocked = [e for e in events if e.expected_decision == "BLOCK"]
        for ev in blocked:
            assert ev.expected_exit_code == 2

        # Mutative denials (exit 0): T3 nonce approval flow
        denied = [e for e in events if e.expected_decision == "DENY"]
        for ev in denied:
            assert ev.expected_exit_code == 0

    def test_allowed_event_tier(self, logs_dir: Path):
        extractor = LogExtractor()
        events = extractor.extract_from_hooks_log(logs_dir / "hooks-2026-03-11.log")

        allowed_bash = [e for e in events if e.expected_decision == "ALLOW" and e.tool_name == "Bash"]
        for ev in allowed_bash:
            assert ev.expected_tier == "T0"

    def test_event_timestamp_format(self, logs_dir: Path):
        extractor = LogExtractor()
        events = extractor.extract_from_hooks_log(logs_dir / "hooks-2026-03-11.log")

        assert events[0].timestamp.startswith("2026-03-11 10:00:")

    def test_empty_file_returns_empty_list(self, tmp_path: Path):
        extractor = LogExtractor()
        empty_log = tmp_path / "hooks-2026-01-01.log"
        empty_log.write_text("")
        assert extractor.extract_from_hooks_log(empty_log) == []

    def test_nonexistent_file_returns_empty_list(self, tmp_path: Path):
        extractor = LogExtractor()
        assert extractor.extract_from_hooks_log(tmp_path / "nonexistent.log") == []

    def test_total_event_count(self, logs_dir: Path):
        extractor = LogExtractor()
        events = extractor.extract_from_hooks_log(logs_dir / "hooks-2026-03-11.log")
        # 4 allowed bash + 1 blocked bash + 1 allowed agent + 1 blocked agent + 1 stop = 8
        assert len(events) == 8

    def test_extract_stop_events(self, logs_dir: Path):
        extractor = LogExtractor()
        events = extractor.extract_from_hooks_log(logs_dir / "hooks-2026-03-11.log")

        stop_events = [e for e in events if e.hook_name == "stop_hook"]
        assert len(stop_events) == 1
        stop_event = stop_events[0]
        assert stop_event.expected_decision == "PASS"
        assert stop_event.expected_metadata["quality_sufficient"] is True
        assert stop_event.stdin_payload["stop_reason"] == "user_requested"


class TestLogExtractorAuditJsonl:
    """Tests for parsing audit-*.jsonl files."""

    def test_extract_audit_events(self, logs_dir: Path):
        extractor = LogExtractor()
        events = extractor.extract_from_audit_jsonl(logs_dir / "audit-2026-03-11.jsonl")

        assert len(events) == 3  # kubectl, ls, git status

    def test_audit_events_are_all_post_tool_use(self, logs_dir: Path):
        extractor = LogExtractor()
        events = extractor.extract_from_audit_jsonl(logs_dir / "audit-2026-03-11.jsonl")

        for ev in events:
            assert ev.expected_decision == "PASS"
            assert ev.expected_exit_code == 0
            assert ev.hook_name == "post_tool_use"

    def test_audit_event_has_tier(self, logs_dir: Path):
        extractor = LogExtractor()
        events = extractor.extract_from_audit_jsonl(logs_dir / "audit-2026-03-11.jsonl")

        for ev in events:
            assert ev.expected_tier == "T0"

    def test_audit_event_stdin_payload(self, logs_dir: Path):
        extractor = LogExtractor()
        events = extractor.extract_from_audit_jsonl(logs_dir / "audit-2026-03-11.jsonl")

        ev = events[0]
        assert ev.stdin_payload["tool_name"] == "Bash"
        assert ev.stdin_payload["tool_input"]["command"] == "kubectl get pods"
        assert ev.stdin_payload["hook_event_name"] == "PostToolUse"
        assert ev.stdin_payload["tool_result"]["exit_code"] == 0
        assert ev.compare_tier is True


class TestLogExtractorMerge:
    """Tests for extract_all which merges hooks log and audit JSONL."""

    def test_merge_across_sources(self, logs_dir: Path):
        extractor = LogExtractor()
        events = extractor.extract_all(logs_dir, date_filter="2026-03-11")

        # Hooks log has 8 events, audit has 3 events.
        # Timestamps differ between sources (invocation vs completion time),
        # so dedup by timestamp does not collapse them. That is correct --
        # the hooks log is the primary source for replay, audit adds coverage.
        assert len(events) == 11

        # Verify both sources are represented
        sources = {e.source_file for e in events}
        assert "hooks-2026-03-11.log" in sources
        assert "audit-2026-03-11.jsonl" in sources

    def test_date_filter(self, logs_dir: Path):
        extractor = LogExtractor()

        # Matching date
        events = extractor.extract_all(logs_dir, date_filter="2026-03-11")
        assert len(events) > 0

        # Non-matching date
        events = extractor.extract_all(logs_dir, date_filter="2026-01-01")
        assert len(events) == 0

    def test_hook_filter(self, logs_dir: Path):
        extractor = LogExtractor()
        events = extractor.extract_all(
            logs_dir, date_filter="2026-03-11", hook_filter="pre_tool_use"
        )
        for ev in events:
            assert ev.hook_name == "pre_tool_use"

    def test_sorted_by_timestamp(self, logs_dir: Path):
        extractor = LogExtractor()
        events = extractor.extract_all(logs_dir)

        timestamps = [e.timestamp for e in events]
        assert timestamps == sorted(timestamps)


# ============================================================================
# Test ReplayEvent (frozen dataclass)
# ============================================================================


class TestReplayEvent:
    """Tests for ReplayEvent immutability and structure."""

    def test_frozen(self):
        ev = ReplayEvent(
            timestamp="2026-03-11 10:00:00,000",
            hook_name="pre_tool_use",
            tool_name="Bash",
            stdin_payload={"tool_name": "Bash", "tool_input": {"command": "ls"}},
            expected_decision="ALLOW",
            expected_exit_code=0,
            expected_tier="T0",
            source_file="hooks-2026-03-11.log",
        )
        with pytest.raises(AttributeError):
            ev.expected_decision = "BLOCK"  # type: ignore[misc]


# ============================================================================
# Test decision parsing and regression classification
# ============================================================================


class TestDecisionParsing:
    """Tests for _parse_decision_from_output."""

    def test_exit_0_is_allow(self):
        decision, _ = _parse_decision_from_output(0, "")
        assert decision == "ALLOW"

    def test_exit_2_is_block(self):
        decision, _ = _parse_decision_from_output(2, "")
        assert decision == "BLOCK"

    def test_exit_1_is_error(self):
        decision, _ = _parse_decision_from_output(1, "")
        assert decision == "ERROR"

    def test_deny_from_json_output(self):
        stdout = json.dumps({
            "hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "Needs nonce"}
        })
        decision, _ = _parse_decision_from_output(0, stdout)
        assert decision == "DENY"


class TestRegressionClassification:
    """Tests for _classify_regression."""

    def test_no_regression(self):
        result = _classify_regression("ALLOW", "ALLOW", 0, 0, "T0", "T0")
        assert result is None

    def test_allow_to_block(self):
        result = _classify_regression("ALLOW", "BLOCK", 0, 2, "T0", "")
        assert result == "allow_to_block"

    def test_block_to_allow(self):
        result = _classify_regression("BLOCK", "ALLOW", 2, 0, "", "T0")
        assert result == "block_to_allow"

    def test_allow_to_t3(self):
        result = _classify_regression("ALLOW", "DENY", 0, 0, "T0", "")
        assert result == "allow_to_t3"

    def test_tier_change(self):
        result = _classify_regression("ALLOW", "ALLOW", 0, 0, "T0", "T1")
        assert result == "tier_change"

    def test_exit_code_change(self):
        result = _classify_regression("ALLOW", "ALLOW", 0, 1, "", "")
        assert result == "exit_code_change"

    def test_stop_quality_change(self):
        result = _classify_regression(
            "PASS",
            "PASS",
            0,
            0,
            "",
            "",
            expected_metadata={"quality_sufficient": True},
            actual_metadata={"quality_sufficient": False},
            compare_tier=False,
        )
        assert result == "quality_sufficient_change"


# ============================================================================
# Test HookRunner with real hooks
# ============================================================================


class TestHookRunner:
    """Tests for HookRunner using real hook scripts."""

    def test_run_allowed_bash_command(self, hooks_dir: Path, tmp_path: Path):
        runner = HookRunner(hooks_dir=hooks_dir, project_root=tmp_path)
        event = ReplayEvent(
            timestamp="2026-03-11 10:00:00,000",
            hook_name="pre_tool_use",
            tool_name="Bash",
            stdin_payload={
                "tool_name": "Bash",
                "tool_input": {"command": "ls /tmp", "description": "List tmp"},
                "hook_event_name": "PreToolUse",
                "session_id": "replay-test",
            },
            expected_decision="ALLOW",
            expected_exit_code=0,
            expected_tier="T0",
            source_file="test",
        )
        result = runner.run(event)
        assert result.actual_exit_code == 0
        assert result.actual_decision == "ALLOW"
        assert result.actual_tier == "T0"
        assert result.matched is True

    def test_run_blocked_command(self, hooks_dir: Path, tmp_path: Path):
        runner = HookRunner(hooks_dir=hooks_dir, project_root=tmp_path)
        # terraform destroy is permanently blocked
        cmd = " ".join(["terraform", "destroy"])
        event = ReplayEvent(
            timestamp="2026-03-11 10:00:00,000",
            hook_name="pre_tool_use",
            tool_name="Bash",
            stdin_payload={
                "tool_name": "Bash",
                "tool_input": {"command": cmd, "description": "Destroy infra"},
                "hook_event_name": "PreToolUse",
                "session_id": "replay-test",
            },
            expected_decision="BLOCK",
            expected_exit_code=2,
            expected_tier="",
            source_file="test",
        )
        result = runner.run(event)
        assert result.actual_exit_code == 2
        assert result.actual_decision == "BLOCK"
        assert result.matched is True

    def test_run_batch(self, hooks_dir: Path, tmp_path: Path):
        runner = HookRunner(hooks_dir=hooks_dir, project_root=tmp_path)
        events = [
            ReplayEvent(
                timestamp="2026-03-11 10:00:00,000",
                hook_name="pre_tool_use",
                tool_name="Bash",
                stdin_payload={
                    "tool_name": "Bash",
                    "tool_input": {"command": "ls /tmp", "description": "Safe cmd"},
                    "hook_event_name": "PreToolUse",
                    "session_id": "replay-test",
                },
                expected_decision="ALLOW",
                expected_exit_code=0,
                expected_tier="T0",
                source_file="test",
            ),
            ReplayEvent(
                timestamp="2026-03-11 10:00:01,000",
                hook_name="pre_tool_use",
                tool_name="Bash",
                stdin_payload={
                    "tool_name": "Bash",
                    "tool_input": {"command": "echo hello", "description": "Echo"},
                    "hook_event_name": "PreToolUse",
                    "session_id": "replay-test",
                },
                expected_decision="ALLOW",
                expected_exit_code=0,
                expected_tier="T0",
                source_file="test",
            ),
        ]
        results = runner.run_batch(events)
        assert len(results) == 2
        assert all(r.matched for r in results)

    def test_run_post_tool_use_event(self, hooks_dir: Path, tmp_path: Path):
        runner = HookRunner(hooks_dir=hooks_dir, project_root=tmp_path)
        event = ReplayEvent(
            timestamp="2026-03-11T10:00:01.100000",
            hook_name="post_tool_use",
            tool_name="Bash",
            stdin_payload={
                "tool_name": "Bash",
                "tool_input": {"command": "ls /tmp", "description": "List tmp"},
                "tool_result": {"output": "", "stdout": "", "exit_code": 0, "duration_ms": 50},
                "hook_event_name": "PostToolUse",
                "session_id": "replay-test",
            },
            expected_decision="PASS",
            expected_exit_code=0,
            expected_tier="T0",
            source_file="audit.jsonl",
            compare_tier=True,
        )
        result = runner.run(event)
        assert result.actual_exit_code == 0
        assert result.actual_decision == "PASS"
        assert result.actual_tier == "T0"
        assert result.matched is True

    def test_run_stop_hook_event(self, hooks_dir: Path, tmp_path: Path):
        runner = HookRunner(hooks_dir=hooks_dir, project_root=tmp_path)
        event = ReplayEvent(
            timestamp="2026-03-11 10:00:08,000",
            hook_name="stop_hook",
            tool_name="Stop",
            stdin_payload={
                "hook_event_name": "Stop",
                "session_id": "replay-test",
                "stop_reason": "user_requested",
            },
            expected_decision="PASS",
            expected_exit_code=0,
            expected_tier="",
            source_file="hooks.log",
            expected_metadata={"quality_sufficient": True, "score": 1.0},
        )
        result = runner.run(event)
        assert result.actual_exit_code == 0
        assert result.actual_decision == "PASS"
        assert result.actual_metadata["quality_sufficient"] is True
        assert result.matched is True

    def test_missing_hook_returns_error(self, tmp_path: Path):
        runner = HookRunner(hooks_dir=tmp_path / "nonexistent", project_root=tmp_path)
        event = ReplayEvent(
            timestamp="2026-03-11 10:00:00,000",
            hook_name="pre_tool_use",
            tool_name="Bash",
            stdin_payload={"tool_name": "Bash", "tool_input": {"command": "ls"}},
            expected_decision="ALLOW",
            expected_exit_code=0,
            expected_tier="T0",
            source_file="test",
        )
        result = runner.run(event)
        assert result.actual_decision == "ERROR"
        assert result.regression_type == "missing_hook"
        assert result.matched is False

    def test_progress_callback(self, hooks_dir: Path, tmp_path: Path):
        runner = HookRunner(hooks_dir=hooks_dir, project_root=tmp_path)
        events = [
            ReplayEvent(
                timestamp="2026-03-11 10:00:00,000",
                hook_name="pre_tool_use",
                tool_name="Bash",
                stdin_payload={
                    "tool_name": "Bash",
                    "tool_input": {"command": "echo hi", "description": "Echo"},
                    "hook_event_name": "PreToolUse",
                    "session_id": "replay-test",
                },
                expected_decision="ALLOW",
                expected_exit_code=0,
                expected_tier="T0",
                source_file="test",
            ),
        ]
        progress_calls = []
        runner.run_batch(events, progress_callback=lambda c, t: progress_calls.append((c, t)))
        assert progress_calls == [(1, 1)]


# ============================================================================
# Test ReplayReporter
# ============================================================================


def _make_result(
    matched: bool = True,
    expected_decision: str = "ALLOW",
    actual_decision: str = "ALLOW",
    regression_type: str | None = None,
    tool_name: str = "Bash",
    hook_name: str = "pre_tool_use",
    tier: str = "T0",
    command: str = "ls /tmp",
) -> ReplayResult:
    """Factory for test ReplayResult instances."""
    event = ReplayEvent(
        timestamp="2026-03-11 10:00:00,000",
        hook_name=hook_name,
        tool_name=tool_name,
        stdin_payload={
            "tool_name": tool_name,
            "tool_input": {"command": command, "description": "test"},
            "hook_event_name": "PreToolUse",
            "session_id": "test",
        },
        expected_decision=expected_decision,
        expected_exit_code=0 if expected_decision == "ALLOW" else 2,
        expected_tier=tier,
        source_file="test.log",
    )
    return ReplayResult(
        event=event,
        actual_exit_code=0 if actual_decision == "ALLOW" else 2,
        actual_stdout="",
        actual_stderr="",
        actual_decision=actual_decision,
        actual_tier=tier,
        matched=matched,
        regression_type=regression_type,
    )


class TestReplayReporter:
    """Tests for ReplayReporter formatting."""

    def test_summary_no_events(self):
        reporter = ReplayReporter()
        assert "No events" in reporter.summary([])

    def test_summary_all_matched(self):
        reporter = ReplayReporter()
        results = [_make_result(matched=True) for _ in range(5)]
        text = reporter.summary(results)
        assert "Total events:  5" in text
        assert "Matched:       5" in text
        assert "Regressions:   0" in text

    def test_summary_with_regressions(self):
        reporter = ReplayReporter()
        results = [
            _make_result(matched=True),
            _make_result(matched=True),
            _make_result(matched=False, expected_decision="ALLOW",
                         actual_decision="BLOCK", regression_type="allow_to_block"),
        ]
        text = reporter.summary(results)
        assert "Regressions:   1" in text
        assert "allow_to_block" in text

    def test_regressions_only_no_regressions(self):
        reporter = ReplayReporter()
        results = [_make_result(matched=True)]
        text = reporter.regressions_only(results)
        assert "No regressions" in text

    def test_regressions_only_shows_details(self):
        reporter = ReplayReporter()
        results = [
            _make_result(matched=False, expected_decision="ALLOW",
                         actual_decision="BLOCK", regression_type="allow_to_block",
                         command="terraform apply"),
        ]
        text = reporter.regressions_only(results)
        assert "REGRESSIONS FOUND: 1" in text
        assert "allow_to_block" in text
        assert "terraform apply" in text

    def test_full_report_includes_breakdowns(self):
        reporter = ReplayReporter()
        results = [
            _make_result(matched=True, tool_name="Bash", tier="T0"),
            _make_result(matched=True, tool_name="Agent", tier=""),
            _make_result(matched=False, regression_type="allow_to_block",
                         expected_decision="ALLOW", actual_decision="BLOCK"),
        ]
        text = reporter.full_report(results)
        assert "BREAKDOWN BY HOOK:" in text
        assert "BREAKDOWN BY TIER:" in text
        assert "BREAKDOWN BY TOOL:" in text
        assert "BREAKDOWN BY SOURCE:" in text

    def test_save_json(self, tmp_path: Path):
        reporter = ReplayReporter()
        results = [
            _make_result(matched=True),
            _make_result(matched=False, regression_type="allow_to_block",
                         expected_decision="ALLOW", actual_decision="BLOCK"),
        ]
        output_path = tmp_path / "results.json"
        reporter.save_json(results, output_path)

        assert output_path.exists()
        data = json.loads(output_path.read_text())
        assert len(data) == 2
        assert data[0]["matched"] is True
        assert data[1]["matched"] is False
        assert data[1]["regression_type"] == "allow_to_block"

    def test_save_json_has_expected_and_actual(self, tmp_path: Path):
        reporter = ReplayReporter()
        results = [_make_result(matched=True, tier="T0")]
        output_path = tmp_path / "out.json"
        reporter.save_json(results, output_path)

        data = json.loads(output_path.read_text())
        entry = data[0]
        assert "expected" in entry
        assert "actual" in entry
        assert entry["expected"]["decision"] == "ALLOW"
        assert entry["expected"]["tier"] == "T0"

    def test_results_payload_includes_metadata_and_limitations(self):
        reporter = ReplayReporter()
        result = _make_result(matched=True, tier="T0")
        result.event.limitations  # frozen dataclass default exists
        payload = reporter.results_payload([result])[0]
        assert payload["expected"]["metadata"] == {}
        assert payload["actual"]["metadata"] == {}
        assert payload["limitations"] == []


# ============================================================================
# Test ReplayResult (frozen dataclass)
# ============================================================================


class TestReplayResult:
    """Tests for ReplayResult immutability."""

    def test_frozen(self):
        r = _make_result(matched=True)
        with pytest.raises(AttributeError):
            r.matched = False  # type: ignore[misc]


# ============================================================================
# Integration: extract -> run -> report
# ============================================================================


class TestIntegration:
    """Integration tests using sample logs and real hooks."""

    def test_extract_and_report_sample(self, logs_dir: Path):
        """Extract from sample logs and verify reporter works with the events."""
        extractor = LogExtractor()
        events = extractor.extract_all(logs_dir)

        assert len(events) > 0

        # Reporter should handle events (as results with mock data)
        reporter = ReplayReporter()
        mock_results = [
            ReplayResult(
                event=ev,
                actual_exit_code=ev.expected_exit_code,
                actual_stdout="",
                actual_stderr="",
                actual_decision=ev.expected_decision,
                actual_tier=ev.expected_tier,
                matched=True,
                regression_type=None,
            )
            for ev in events
        ]

        text = reporter.summary(mock_results)
        assert "Total events:" in text
        assert "Regressions:   0" in text

    def test_full_pipeline_with_real_hooks(self, hooks_dir: Path, logs_dir: Path, tmp_path: Path):
        """Extract sample events, run against real hooks, report results.

        This is a lightweight integration test using only the safe commands
        from the sample data.
        """
        extractor = LogExtractor()
        events = extractor.extract_all(logs_dir)

        # Filter to only safe ALLOW bash commands for fast testing
        safe_events = [
            e for e in events
            if e.expected_decision in {"ALLOW", "PASS"}
            and e.tool_name == "Bash"
            and e.hook_name == "pre_tool_use"
            and e.stdin_payload.get("tool_input", {}).get("command", "").startswith(("ls ", "echo "))
        ]

        if not safe_events:
            pytest.skip("No safe bash events in sample data")

        runner = HookRunner(hooks_dir=hooks_dir, project_root=tmp_path)
        results = runner.run_batch(safe_events[:3])  # Limit for speed

        reporter = ReplayReporter()
        text = reporter.summary(results)
        assert "Total events:" in text

        # Safe commands should all be ALLOWED
        for r in results:
            assert r.actual_decision == "ALLOW", (
                f"Safe command unexpectedly {r.actual_decision}: "
                f"{r.event.stdin_payload.get('tool_input', {}).get('command', '')}"
            )


# ============================================================================
# CLI behavior
# ============================================================================


class TestGaiaSimulatorCli:
    """Tests for CLI output behavior."""

    def test_extract_only_json_keeps_stdout_machine_readable(self, logs_dir: Path, hooks_dir: Path, capsys):
        exit_code = gaia_simulator_cli_main([
            "--logs-dir", str(logs_dir),
            "--hooks-dir", str(hooks_dir),
            "--extract-only",
            "--report-format", "json",
            "--limit", "2",
        ])
        captured = capsys.readouterr()

        assert exit_code == 0
        parsed = json.loads(captured.out)
        assert len(parsed) == 2
        assert "Extracting events from:" in captured.err

    def test_json_report_writes_progress_to_stderr(self, logs_dir: Path, hooks_dir: Path, capsys):
        exit_code = gaia_simulator_cli_main([
            "--logs-dir", str(logs_dir),
            "--hooks-dir", str(hooks_dir),
            "--report-format", "json",
            "--hook", "pre_tool_use",
            "--limit", "1",
        ])
        captured = capsys.readouterr()

        assert exit_code == 0
        parsed = json.loads(captured.out)
        assert len(parsed) == 1
        assert parsed[0]["actual"]["decision"] == "ALLOW"
        assert "Replay complete:" in captured.err
