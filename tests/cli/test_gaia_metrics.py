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

Dashboard v3: adds TimeWindow (--range/--since/--until, all funneled through
gaia.store.reader.parse_when), SQL-level window filtering on the three
DB-backed readers, native-agent-filtered runtime skills, and schema_version=2
with a 'window' key on to_dict().
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
    _calculate_runtime_skill_summary,
    _split_native_agents,
    _format_tokens,
    _format_chars,
    _format_duration_ms,
    _make_bar,
    _resolve_time_window,
    _display_width,
    _box_top,
    _box_row,
    _box_divider,
    _box_bottom,
    TimeWindow,
    register,
    cmd_metrics,
    MetricsSnapshot,
    SCHEMA_VERSION,
    NATIVE_AGENT_NAMES,
    render_console,
)


def _write_audit_jsonl(logs_dir: Path, entries: list, filename: str = "audit-2026-04-15.jsonl"):
    logs_dir.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(json.dumps(e) for e in entries) + "\n"
    (logs_dir / filename).write_text(lines)


def _seed_schema_and_rows(con: sqlite3.Connection, episodes: list, anomalies: list = None) -> None:
    """Create the episodes + episode_anomalies schema on ``con`` and seed rows.

    Shared by ``_make_in_memory_db`` (single-reader tests) and any test that
    needs a file-backed DB that survives a reader's ``con.close()`` (see
    TestCmdMetrics.test_range_today_excludes_old_episode_from_snapshot).
    """
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


def _make_in_memory_db(episodes: list, anomalies: list = None) -> sqlite3.Connection:
    """In-memory SQLite DB with episodes + episode_anomalies seeded.

    ``episodes`` accepts dicts with optional ``context_metrics`` (a dict, which
    is JSON-serialized to match the real column). ``anomalies`` accepts dicts
    with episode_id/workspace/timestamp/type.
    """
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    _seed_schema_and_rows(con, episodes, anomalies)
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

    def test_no_tier_column(self):
        # P1 fix #5: tier/t3count is gone from this calculator -- it
        # duplicated Security Tier Usage. Only label/count/percentage/
        # avg_duration_ms remain.
        logs = [{"command": "git push", "tier": "T3"}]
        result = _calculate_top_commands(logs)
        self.assertNotIn("tier", result[0])
        self.assertNotIn("t3count", result[0])
        self.assertIn("percentage", result[0])
        self.assertIn("avg_duration_ms", result[0])

    def test_percentage_is_share_of_total(self):
        logs = [
            {"command": "git status"},
            {"command": "git status"},
            {"command": "kubectl get pods"},
        ]
        result = _calculate_top_commands(logs)
        git_status = next(r for r in result if r["label"] == "git status")
        self.assertAlmostEqual(git_status["percentage"], 2 / 3 * 100)

    def test_avg_duration_from_duration_ms(self):
        # P1 fix #5: duration_ms (logger.py) was recorded but unused.
        logs = [
            {"command": "git status", "duration_ms": 100},
            {"command": "git status", "duration_ms": 300},
        ]
        result = _calculate_top_commands(logs)
        git_status = next(r for r in result if r["label"] == "git status")
        self.assertEqual(git_status["avg_duration_ms"], 200)

    def test_avg_duration_none_when_no_timed_entries(self):
        logs = [{"command": "git status"}]
        result = _calculate_top_commands(logs)
        self.assertIsNone(result[0]["avg_duration_ms"])


class TestFormatDuration(unittest.TestCase):
    def test_none_is_na(self):
        self.assertEqual(_format_duration_ms(None), "n/a")

    def test_sub_second_in_ms(self):
        self.assertEqual(_format_duration_ms(250), "250ms")

    def test_over_second_in_s(self):
        self.assertEqual(_format_duration_ms(1500), "1.5s")


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

    def test_prefers_real_over_approx(self):
        # P1 fix #4: output_tokens_real wins over the chars/4 approximation.
        metrics = [
            {"agent": "developer", "output_tokens_approx": 100, "output_tokens_real": 250},
        ]
        result = _calculate_token_usage(metrics)
        self.assertEqual(result["grand_total"], 250)
        self.assertEqual(result["real_count"], 1)
        self.assertEqual(result["approx_count"], 0)
        self.assertEqual(result["agents"][0]["source"], "real")

    def test_degrades_to_approx_when_real_absent(self):
        metrics = [{"agent": "developer", "output_tokens_approx": 100}]
        result = _calculate_token_usage(metrics)
        self.assertEqual(result["grand_total"], 100)
        self.assertEqual(result["real_count"], 0)
        self.assertEqual(result["agents"][0]["source"], "approx")

    def test_surfaces_input_and_cache_when_present(self):
        metrics = [
            {"agent": "developer", "output_tokens_real": 250,
             "input_tokens": 1000, "cache_creation_tokens": 40, "cache_read_tokens": 900},
        ]
        result = _calculate_token_usage(metrics)
        self.assertEqual(result["input_tokens"], 1000)
        self.assertEqual(result["cache_read_tokens"], 900)

    def test_input_cache_none_when_no_transcript_data(self):
        metrics = [{"agent": "developer", "output_tokens_approx": 100}]
        result = _calculate_token_usage(metrics)
        self.assertIsNone(result["input_tokens"])


class TestSplitNativeAgents(unittest.TestCase):
    def test_segregates_native_agents(self):
        # P1 fix #3: Explore et al. are separated from Gaia specialists.
        metrics = [
            {"agent": "developer"},
            {"agent": "Explore"},
            {"agent": "gaia-operator"},
            {"agent": "Plan"},
        ]
        gaia, native = _split_native_agents(metrics)
        self.assertEqual({r["agent"] for r in gaia}, {"developer", "gaia-operator"})
        self.assertEqual({r["agent"] for r in native}, {"Explore", "Plan"})

    def test_native_names_present(self):
        self.assertIn("Explore", NATIVE_AGENT_NAMES)
        self.assertIn("claude-code-guide", NATIVE_AGENT_NAMES)


class TestRuntimeSkillsExcludesNative(unittest.TestCase):
    """P0 fix #1: runtime skills previously computed over UNFILTERED
    run_snapshots, unlike invocations/outcomes/tokens (which already used
    gaia_metrics). MetricsSnapshot.build() now segregates run_snapshots the
    same way before calling _calculate_runtime_skill_summary.
    """

    def test_calculator_still_accepts_raw_input(self):
        # The calculator itself has no opinion on native agents -- filtering
        # is the caller's job (MetricsSnapshot.build). Direct calls with an
        # already-mixed list still produce a profile per agent seen.
        run_snapshots = [
            {"agent": "Explore", "timestamp": "t1",
             "default_skills_snapshot": {"model": "inherit", "skills": ["a"], "skills_count": 1, "tools": []}},
            {"agent": "developer", "timestamp": "t1",
             "default_skills_snapshot": {"model": "inherit", "skills": ["b"], "skills_count": 1, "tools": []}},
        ]
        result = _calculate_runtime_skill_summary([], run_snapshots)
        agents = {p["agent"] for p in result["latest_profiles"]}
        self.assertEqual(agents, {"Explore", "developer"})

    def test_snapshot_build_excludes_native_agents(self):
        # End-to-end via MetricsSnapshot.build(): Explore must NOT appear in
        # runtime_skills even though it's present in run_snapshots.
        run_snapshots = [
            {"agent": "Explore", "timestamp": "t1",
             "default_skills_snapshot": {"model": "inherit", "skills": ["explore-only"], "skills_count": 1, "tools": []}},
            {"agent": "developer", "timestamp": "t1",
             "default_skills_snapshot": {"model": "inherit", "skills": ["agent-protocol"], "skills_count": 1, "tools": []}},
        ]
        snap = MetricsSnapshot.build(
            workspace=None, audit_logs=[], workflow_metrics=[],
            run_snapshots=run_snapshots, skill_snapshots=[], anomaly_entries=[],
        )
        agents = {p["agent"] for p in snap.runtime_skills["latest_profiles"]}
        self.assertIn("developer", agents)
        self.assertNotIn("Explore", agents)

    def test_sorted_by_skills_count_desc(self):
        # P3 fix #10: busiest profile first, not alphabetical.
        run_snapshots = [
            {"agent": "aaa-agent", "timestamp": "t1",
             "default_skills_snapshot": {"model": "inherit", "skills": ["a"], "skills_count": 1, "tools": []}},
            {"agent": "zzz-agent", "timestamp": "t1",
             "default_skills_snapshot": {"model": "inherit", "skills": ["a", "b", "c"], "skills_count": 3, "tools": []}},
        ]
        result = _calculate_runtime_skill_summary([], run_snapshots)
        names = [p["agent"] for p in result["latest_profiles"]]
        self.assertEqual(names[0], "zzz-agent")


class TestTimeWindow(unittest.TestCase):
    """Dashboard v3: TimeWindow / _resolve_time_window, built on top of
    gaia.store.reader.parse_when() (no new parser)."""

    def test_default_range_is_30d(self):
        window = _resolve_time_window(None, None, None)
        self.assertEqual(window.label, "30d")
        self.assertIsNotNone(window.since_iso)
        self.assertIsNone(window.until_iso)
        self.assertFalse(window.capped_by_retention)

    def test_range_all_has_no_bounds(self):
        window = _resolve_time_window("all", None, None)
        self.assertEqual(window.label, "all")
        self.assertIsNone(window.since_iso)
        self.assertIsNone(window.until_iso)

    def test_range_today_bounds_to_utc_midnight(self):
        window = _resolve_time_window("today", None, None)
        self.assertEqual(window.label, "today")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
        self.assertEqual(window.since_iso, today)

    def test_range_7d_delegates_to_parse_when(self):
        window = _resolve_time_window("7d", None, None)
        self.assertEqual(window.label, "7d")
        self.assertIsNotNone(window.since_iso)

    def test_explicit_since_until(self):
        window = _resolve_time_window(None, "2026-01-01", "2026-02-01")
        self.assertEqual(window.since_iso, "2026-01-01T00:00:00Z")
        self.assertEqual(window.until_iso, "2026-02-01T00:00:00Z")

    def test_invalid_since_raises(self):
        with self.assertRaises(ValueError):
            _resolve_time_window(None, "not-a-date", None)

    def test_to_dict_shape(self):
        window = TimeWindow(label="7d", since_iso="2026-01-01T00:00:00Z", until_iso=None)
        d = window.to_dict()
        self.assertEqual(
            set(d.keys()), {"label", "since_iso", "until_iso", "capped_by_retention"}
        )
        self.assertFalse(d["capped_by_retention"])


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

    def test_no_internal_age_cutoff(self):
        # Dashboard v3: age filtering moved to the reader layer (SQL, bound
        # by the caller's TimeWindow) so --range=all isn't silently reclamped
        # to 30 days by the calculator. An "old" entry passed directly is
        # counted -- the calculator no longer applies its own cutoff.
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        entries = [
            {
                "timestamp": old,
                "anomalies": [{"type": "contract_missing"}],
                "metrics": {"agent": "developer"},
            }
        ]
        result = _calculate_anomaly_summary(entries)
        self.assertIsNotNone(result)
        self.assertEqual(result["total"], 1)

    def test_empty_entries_returns_none(self):
        self.assertIsNone(_calculate_anomaly_summary([]))

    def test_severity_sorts_above_volume(self):
        # P2 fix #6: a rare critical must sort above a high-volume warning.
        recent = datetime.now(timezone.utc).isoformat()
        entries = [
            {
                "timestamp": recent,
                "metrics": {"agent": "developer"},
                "anomalies": (
                    [{"type": "pipe_retroactive", "severity": "warning"}] * 20
                    + [{"type": "response_contract_violation", "severity": "critical"}]
                ),
            }
        ]
        result = _calculate_anomaly_summary(entries)
        self.assertIsNotNone(result)
        # critical entry appears first despite being far less frequent
        self.assertEqual(result["by_type"][0]["type"], "response_contract_violation")
        self.assertEqual(result["by_type"][0]["severity"], "critical")
        self.assertEqual(result["by_type"][1]["type"], "pipe_retroactive")
        # by_severity aggregate is present
        crit = next(s for s in result["by_severity"] if s["severity"] == "critical")
        self.assertEqual(crit["count"], 1)


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

    # Dashboard v2: _make_bar now returns a FIXED-width string of filled (█)
    # + empty (░) cells, not just the filled prefix.
    def test_make_bar_full(self):
        result = _make_bar(100, 10)
        self.assertEqual(len(result), 10)
        self.assertEqual(result.count("█"), 10)

    def test_make_bar_empty(self):
        result = _make_bar(0, 10)
        self.assertEqual(len(result), 10)
        self.assertEqual(result.count("█"), 0)
        self.assertEqual(result.count("░"), 10)

    def test_make_bar_half(self):
        result = _make_bar(50, 10)
        self.assertEqual(len(result), 10)
        self.assertEqual(result.count("█"), 5)


class TestBoxDisplayWidth(unittest.TestCase):
    """Dashboard v3 fix: box rows/dividers/top must align on terminal
    display width, not len(). Full-width glyphs (CJK, most emoji, and the
    T3 warning sign already used in Security Tier Usage rows) occupy 2
    terminal cells but len() only counts 1 -- the desync this guards.
    """

    def test_display_width_ascii_matches_len(self):
        self.assertEqual(_display_width("hello world"), len("hello world"))

    def test_display_width_cjk_is_double(self):
        # Each CJK ideograph is 2 terminal cells wide, but len() counts 1.
        self.assertEqual(_display_width("中文"), 4)
        self.assertEqual(len("中文"), 2)

    def test_display_width_emoji_is_double(self):
        self.assertEqual(_display_width("\U0001f680"), 2)  # rocket

    def test_display_width_warning_sign(self):
        # The exact glyph the live Security Tier Usage row uses (line
        # `warn = " ⚠ " if tier == "T3" else "   "`).
        self.assertGreaterEqual(_display_width("⚠"), 1)

    def test_box_row_ascii_and_cjk_rows_stay_aligned(self):
        # Root-cause regression: before the fix, a row containing
        # full-width glyphs came out narrower on-screen than its ASCII
        # sibling even though both pass the same _BOX_W to ljust().
        ascii_row = _box_row("T0     read-only     40    plain ascii row")
        cjk_row = _box_row("CJK label 日本語のテスト  full-width test 中文测试")
        emoji_row = _box_row("emoji row \U0001f680\U0001f525 rocket fire")
        # All three must render to the exact same terminal column count,
        # and all three end on the closing border character.
        self.assertEqual(_display_width(ascii_row), _display_width(cjk_row))
        self.assertEqual(_display_width(ascii_row), _display_width(emoji_row))
        for row in (ascii_row, cjk_row, emoji_row):
            self.assertTrue(row.startswith("│"))
            self.assertTrue(row.endswith("│"))

    def test_box_row_with_warning_sign_stays_aligned(self):
        # Exact production content from the Security Tier Usage section.
        row_with_warn = _box_row("T3  ⚠ mutating       12    count 4  33.3%")
        row_without_warn = _box_row("T0     read-only     40    count 9  66.7%")
        self.assertEqual(_display_width(row_with_warn), _display_width(row_without_warn))

    def test_box_row_truncates_by_display_width_not_char_count(self):
        # A row of wide glyphs long enough to need truncation must still
        # close on the right border at the same column as any other row.
        long_cjk = "中" * 60
        row = _box_row(long_cjk)
        plain_row = _box_row("short row")
        self.assertEqual(_display_width(row), _display_width(plain_row))
        self.assertTrue(row.endswith("…│"))

    def test_box_top_and_divider_align_with_wide_title(self):
        top = _box_top("SECURITY TIER USAGE", right_label="52 ops")
        cjk_top = _box_top("中文标题", right_label="52 ops")
        self.assertEqual(_display_width(top), _display_width(cjk_top))
        divider = _box_divider("legend")
        cjk_divider = _box_divider("图例")
        self.assertEqual(_display_width(divider), _display_width(cjk_divider))

    def test_box_bottom_matches_top_width(self):
        top = _box_top("TITLE")
        bottom = _box_bottom()
        self.assertEqual(_display_width(top), _display_width(bottom))


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

    def test_workflow_metrics_since_iso_filters_older_episodes(self):
        # Dashboard v3: --range now filters episodes in SQL.
        episodes = [
            {"episode_id": "ep-old", "agent": "developer", "timestamp": "2020-01-01T00:00:00Z"},
            {"episode_id": "ep-new", "agent": "developer", "timestamp": "2026-04-15T10:00:00Z"},
        ]
        con = _make_in_memory_db(episodes)
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(Path(tmp))
            with _patch_store(con):
                result = _read_workflow_metrics(root, since_iso="2026-01-01T00:00:00Z")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["episode_id"], "ep-new")

    def test_anomaly_entries_since_iso_filters_older_rows(self):
        episodes = [
            {"episode_id": "ep-old", "agent": "developer", "timestamp": "2020-01-01T00:00:00Z"},
            {"episode_id": "ep-new", "agent": "developer", "timestamp": "2026-04-15T10:00:00Z"},
        ]
        anomalies = [
            {"episode_id": "ep-old", "timestamp": "2020-01-01T00:00:00Z", "type": "pipe_retroactive"},
            {"episode_id": "ep-new", "timestamp": "2026-04-15T10:00:00Z", "type": "pipe_retroactive"},
        ]
        con = _make_in_memory_db(episodes, anomalies)
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(Path(tmp))
            with _patch_store(con):
                result = _read_anomaly_entries(root, since_iso="2026-01-01T00:00:00Z")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["timestamp"], "2026-04-15T10:00:00Z")

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


class TestMetricsSnapshot(unittest.TestCase):
    """Dashboard v2: one snapshot feeds both --json and render_console."""

    def _build(self, **kw):
        base = dict(
            workspace=None, audit_logs=[], workflow_metrics=[],
            run_snapshots=[], skill_snapshots=[], anomaly_entries=[],
        )
        base.update(kw)
        return MetricsSnapshot.build(**base)

    def test_to_dict_carries_schema_version(self):
        snap = self._build()
        d = snap.to_dict()
        self.assertEqual(d["schema_version"], SCHEMA_VERSION)
        self.assertIn("generated_at", d)
        # canonical single key -- no divergent "tiers" vs "security_tiers"
        self.assertIn("security_tiers", d)
        self.assertNotIn("tiers", d)

    def test_empty_snapshot_has_schema_version(self):
        d = MetricsSnapshot.empty().to_dict()
        self.assertEqual(d["schema_version"], SCHEMA_VERSION)
        self.assertEqual(d["security_tiers"]["total"], 0)
        self.assertEqual(d["agent_invocations"]["total"], 0)

    def test_schema_version_is_2(self):
        # Dashboard v3 bump: to_dict() gains 'window' + 'window_support'.
        self.assertEqual(SCHEMA_VERSION, "2")

    def test_to_dict_carries_window(self):
        window = TimeWindow(label="7d", since_iso="2026-01-01T00:00:00Z", until_iso=None)
        snap = self._build(window=window)
        d = snap.to_dict()
        self.assertEqual(d["window"]["label"], "7d")
        self.assertEqual(d["window"]["since_iso"], "2026-01-01T00:00:00Z")
        self.assertIn("capped_by_retention", d["window"])

    def test_to_dict_carries_window_support(self):
        d = self._build().to_dict()
        self.assertIn("window_support", d)
        self.assertIn("security_tiers", d["window_support"]["audit_log_backed"])
        self.assertIn("agent_invocations", d["window_support"]["episodes_backed"])
        self.assertEqual(d["window_support"]["cap_days"], 30)

    def test_default_window_when_not_provided(self):
        d = self._build().to_dict()
        self.assertEqual(d["window"]["label"], "all")

    def test_native_agents_segregated_in_snapshot(self):
        metrics = [
            {"agent": "developer", "timestamp": "2026-04-15T10:00:00Z", "exit_code": 0, "output_length": 100},
            {"agent": "Explore", "timestamp": "2026-04-15T10:00:00Z", "exit_code": 0, "output_length": 100},
        ]
        snap = self._build(workflow_metrics=metrics)
        gaia_agents = {a["name"] for a in snap.agent_invocations["agents"]}
        native_agents = {a["name"] for a in snap.native_agent_activity["agents"]}
        self.assertIn("developer", gaia_agents)
        self.assertNotIn("Explore", gaia_agents)
        self.assertIn("Explore", native_agents)

    def test_header_today_distinct_from_all_time(self):
        # P0 fix #1: today count and all-time count are separate keys, not
        # the same accumulated number.
        old = "2020-01-01T10:00:00Z"
        metrics = [
            {"agent": "developer", "timestamp": old, "exit_code": 0, "output_length": 10},
            {"agent": "developer", "timestamp": old, "exit_code": 0, "output_length": 10},
        ]
        snap = self._build(workflow_metrics=metrics)
        self.assertEqual(snap.agent_invocations["total"], 2)
        self.assertEqual(snap.agent_invocations["today_count"], 0)

    def test_render_console_does_not_raise(self):
        now = datetime.now(timezone.utc).isoformat()
        audit = [{"tier": "T0", "command": "git status", "timestamp": now},
                 {"tier": "T3", "command": "git push", "timestamp": now}]
        metrics = [{"agent": "developer", "timestamp": now, "exit_code": 0,
                    "output_length": 500, "output_tokens_approx": 120,
                    "plan_status": "COMPLETE"}]
        snap = self._build(audit_logs=audit, workflow_metrics=metrics)
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            render_console(snap)
        out = buf.getvalue()
        self.assertIn("SECURITY TIER USAGE", out)
        self.assertIn("┌", out)  # box-drawing present
        self.assertIn("█", out)  # bar glyph present
        self.assertIn("schema_version=", out)


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
            self.assertEqual(data["schema_version"], SCHEMA_VERSION)
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
            self.assertEqual(data["schema_version"], SCHEMA_VERSION)
            self.assertIn("security_tiers", data)
            self.assertIn("cmd_types", data)
            self.assertIn("agent_invocations", data)
            self.assertIn("native_agent_activity", data)
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
            self.assertIn("SECURITY TIER USAGE", output)
            self.assertIn("COMMAND TYPE BREAKDOWN", output)
            # Dashboard v3: Activity Today is a compact strip under the
            # header, not its own box (P2 #7) -- T3-today lives in Security
            # Tier Usage instead of being repeated in a separate section.
            self.assertIn("Today (UTC):", output)

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
        """cmd_metrics passes args.workspace + the resolved window through to
        every DB-backed reader."""
        fixed_window = TimeWindow(label="7d", since_iso="2026-04-08T00:00:00Z", until_iso=None)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            args = self._make_args(as_json=True, workspace="other-ws")
            with patch("cli.metrics._find_project_root", return_value=root), \
                    patch("cli.metrics._resolve_time_window", return_value=fixed_window), \
                    patch("cli.metrics._read_workflow_metrics", return_value=[]) as m_wf, \
                    patch("cli.metrics._read_run_snapshots", return_value=[]) as m_rs, \
                    patch("cli.metrics._read_anomaly_entries", return_value=[]) as m_ae:
                import io
                from contextlib import redirect_stdout
                with redirect_stdout(io.StringIO()):
                    rc = cmd_metrics(args)
            self.assertEqual(rc, 0)
            m_wf.assert_called_once_with(
                root, "other-ws", since_iso="2026-04-08T00:00:00Z", until_iso=None
            )
            m_rs.assert_called_once_with(
                root, "other-ws", since_iso="2026-04-08T00:00:00Z", until_iso=None
            )
            m_ae.assert_called_once_with(
                root, "other-ws", since_iso="2026-04-08T00:00:00Z", until_iso=None
            )

    def _make_range_args(self, agent=None, as_json=False, workspace=None, range=None, since=None, until=None):
        import argparse
        ns = argparse.Namespace()
        ns.agent = agent
        ns.json = as_json
        ns.workspace = workspace
        ns.range = range
        ns.since = since
        ns.until = until
        return ns

    def test_range_and_since_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            args = self._make_range_args(range="7d", since="24h")
            with patch("cli.metrics._find_project_root", return_value=root):
                import io
                from contextlib import redirect_stdout
                with redirect_stdout(io.StringIO()):
                    rc = cmd_metrics(args)
            self.assertEqual(rc, 2)

    def test_invalid_since_returns_error_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            args = self._make_range_args(since="not-a-date")
            with patch("cli.metrics._find_project_root", return_value=root):
                import io
                from contextlib import redirect_stdout
                with redirect_stdout(io.StringIO()):
                    rc = cmd_metrics(args)
            self.assertEqual(rc, 2)

    def test_range_today_excludes_old_episode_from_snapshot(self):
        # End-to-end: --range=today must exclude an episode from 2020 while
        # --range=all includes it, proving the window reaches SQL filtering.
        # setUp() globally patches _open_store to (None, None) -- swap it out
        # for a real (file-backed, reopenable) store connection for the span
        # of this test. A single in-memory connection can't be reused here:
        # one cmd_metrics call opens/closes it across three DB readers
        # (_read_workflow_metrics, _read_run_snapshots, _read_anomaly_entries),
        # and closing an in-memory sqlite3 connection destroys its data --
        # exactly the limitation _patch_store's docstring calls out for
        # single-reader tests. A file-backed DB survives close+reopen, like
        # production's gaia.db.
        old_ts = "2020-01-01T00:00:00Z"
        episodes = [{"episode_id": "ep-old", "agent": "developer", "timestamp": old_ts}]

        self._open_patch.stop()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / ".claude").mkdir()
                db_path = Path(tmp) / "gaia.db"
                seed_con = sqlite3.connect(str(db_path))
                seed_con.row_factory = sqlite3.Row
                _seed_schema_and_rows(seed_con, episodes)
                seed_con.close()

                import gaia.store.writer as _writer_mod
                import gaia.project as _project_mod

                def _fresh_connect(*a, **k):
                    con = sqlite3.connect(str(db_path))
                    con.row_factory = sqlite3.Row
                    return con

                with patch("cli.metrics._find_project_root", return_value=root), \
                        patch.object(_writer_mod, "_connect", _fresh_connect), \
                        patch.object(_project_mod, "current", lambda **k: None):
                    import io
                    from contextlib import redirect_stdout

                    args_today = self._make_range_args(as_json=True, range="today")
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        cmd_metrics(args_today)
                    data_today = json.loads(buf.getvalue())

                    args_all = self._make_range_args(as_json=True, range="all")
                    buf2 = io.StringIO()
                    with redirect_stdout(buf2):
                        cmd_metrics(args_all)
                    data_all = json.loads(buf2.getvalue())
        finally:
            self._open_patch.start()

        self.assertEqual(data_today["agent_invocations"]["total"], 0)
        self.assertEqual(data_all["agent_invocations"]["total"], 1)


class TestRegisterRangeFlags(unittest.TestCase):
    def test_range_choices_enforced(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["metrics", "--range", "7d"])
        self.assertEqual(args.range, "7d")
        with self.assertRaises(SystemExit):
            parser.parse_args(["metrics", "--range", "bogus"])

    def test_since_until_default_none(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["metrics"])
        self.assertIsNone(args.range)
        self.assertIsNone(args.since)
        self.assertIsNone(args.until)


if __name__ == "__main__":
    unittest.main()
