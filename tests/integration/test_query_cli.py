"""
Integration tests for ``gaia query`` (cross-surface analytical query).

Each test routes the substrate DB into ``tmp_path`` via ``GAIA_DATA_DIR``,
seeds rows into ``memory`` / ``episodes`` / ``harness_events``, and exercises
the ``cmd_query`` dispatch via an ``argparse.Namespace`` (the same path
``gaia query`` takes at runtime).

Covers:
  * unfiltered query mixes all three surfaces
  * --surface=harness_events --failed selects only error events
  * --since=<duration> + --agent=... combine correctly
  * --command-like uses SQL LIKE against harness_events.result
  * --format=count emits a single integer
  * --since=<garbage> raises a clear error and exits non-zero
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pytest

# Ensure repo root and bin/ are importable
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Route the substrate DB into ``tmp_path``."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


# ---------------------------------------------------------------------------
# Seeders
# ---------------------------------------------------------------------------

def _ensure_workspace(db_path: Path, workspace: str = "me") -> None:
    # Use writer._connect which bootstraps schema on first connection.
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        con.execute(
            "INSERT OR IGNORE INTO workspaces (name, identity) VALUES (?, ?)",
            (workspace, workspace),
        )
        con.commit()
    finally:
        con.close()


def _seed_memory(db_path: Path, name: str, type_: str, body: str,
                 description: str | None = None,
                 updated_at: str = "2026-05-07T10:00:00Z",
                 workspace: str = "me") -> None:
    _ensure_workspace(db_path, workspace)
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO memory (workspace, name, type, description, body, "
            "                    updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (workspace, name, type_, description, body, updated_at),
        )
        con.commit()
    finally:
        con.close()


def _seed_episode(db_path: Path, episode_id: str, *, agent: str,
                  type_: str = "task", title: str = "ep title",
                  plan_status: str | None = "COMPLETE",
                  outcome: str = "success",
                  timestamp: str = "2026-05-07T11:00:00Z",
                  context_metrics: dict | None = None,
                  workspace: str = "me") -> None:
    _ensure_workspace(db_path, workspace)
    cm = json.dumps(context_metrics) if context_metrics is not None else None
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO episodes (episode_id, workspace, timestamp, agent, "
            "                      type, title, plan_status, outcome, "
            "                      context_metrics) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (episode_id, workspace, timestamp, agent, type_, title,
             plan_status, outcome, cm),
        )
        con.commit()
    finally:
        con.close()


def _metrics_blob(*, compliance_total, grade, input_tokens, output_real,
                  cache_read=0, cache_creation=0, duration_ms=1000,
                  tool_calls=5, api_calls=8, model="claude-sonnet-5") -> dict:
    """Build a context_metrics blob matching the workflow recorder's shape."""
    return {"metrics": {
        "compliance_score": {"total": compliance_total, "grade": grade,
                             "deductions": []},
        "input_tokens": input_tokens,
        "output_tokens_real": output_real,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_creation,
        "duration_ms": duration_ms,
        "tool_call_count": tool_calls,
        "api_call_count": api_calls,
        "model_used": model,
    }}


def _seed_harness_event(db_path: Path, *, type_: str, ts: str,
                        agent: str = "", result: str = "ok",
                        severity: str = "info",
                        payload: str = "{}",
                        workspace: str = "me") -> None:
    _ensure_workspace(db_path, workspace)
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO harness_events (workspace, ts, type, source, agent, "
            "                            result, severity, payload) "
            "VALUES (?, ?, ?, 'hook', ?, ?, ?, ?)",
            (workspace, ts, type_, agent, result, severity, payload),
        )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def _make_args(**overrides) -> argparse.Namespace:
    """Construct a Namespace pre-populated with the defaults of the parser."""
    base = dict(
        surface="all",
        workspace="me",
        since=None,
        until=None,
        last=20,
        agent=None,
        type=None,
        command_like=None,
        failed=False,
        format="table",
        json=False,
        group_by=None,
        count=False,
        snippets=False,
        metrics=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_query_unfiltered_mixes_all_surfaces(tmp_db, tmp_path,
                                             monkeypatch, capsys):
    """No filters -> rows from all three surfaces appear in the output."""
    from cli.query import cmd_query

    monkeypatch.chdir(tmp_path)
    _seed_memory(tmp_db, "project_x", "project", "memory body",
                 description="memory desc",
                 updated_at="2026-05-07T05:00:00Z")
    _seed_episode(tmp_db, "ep_001", agent="developer",
                  timestamp="2026-05-07T06:00:00Z")
    _seed_harness_event(tmp_db, type_="command.executed",
                        ts="2026-05-07T07:00:00Z",
                        result="ok: ls -la")

    args = _make_args(format="json")
    rc = cmd_query(args)
    assert rc == 0, capsys.readouterr()

    out = json.loads(capsys.readouterr().out)
    surfaces = sorted({r["surface"] for r in out})
    assert surfaces == ["episodes", "harness_events", "memory"]


def test_query_failed_harness_events_only(tmp_db, tmp_path,
                                          monkeypatch, capsys):
    """--surface=harness_events --failed picks rows with severity=error."""
    from cli.query import cmd_query

    monkeypatch.chdir(tmp_path)
    _seed_harness_event(tmp_db, type_="command.executed",
                        ts="2026-05-07T05:00:00Z",
                        result="ok: ls", severity="info")
    _seed_harness_event(tmp_db, type_="command.executed",
                        ts="2026-05-07T06:00:00Z",
                        result="error: command died",
                        severity="error")

    args = _make_args(surface="harness_events", failed=True, format="json")
    rc = cmd_query(args)
    assert rc == 0, capsys.readouterr()

    out = json.loads(capsys.readouterr().out)
    assert len(out) == 1
    assert out[0]["surface"] == "harness_events"
    assert "error" in out[0]["summary"].lower()


def test_query_since_and_agent_filter_combine(tmp_db, tmp_path,
                                              monkeypatch, capsys):
    """--since=24h + --agent=developer narrows the result set correctly."""
    from cli.query import cmd_query
    from datetime import datetime, timezone, timedelta

    monkeypatch.chdir(tmp_path)
    now = datetime.now(tz=timezone.utc)
    recent = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old = (now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

    _seed_episode(tmp_db, "ep_recent_dev", agent="developer",
                  timestamp=recent)
    _seed_episode(tmp_db, "ep_recent_other", agent="orchestrator",
                  timestamp=recent)
    _seed_episode(tmp_db, "ep_old_dev", agent="developer",
                  timestamp=old)

    args = _make_args(surface="episodes", since="24h",
                      agent="developer", format="json")
    rc = cmd_query(args)
    assert rc == 0, capsys.readouterr()

    out = json.loads(capsys.readouterr().out)
    ids = sorted(r["raw"]["episode_id"] for r in out)
    assert ids == ["ep_recent_dev"]


def test_query_command_like_filter(tmp_db, tmp_path, monkeypatch, capsys):
    """--command-like='%git push%' matches harness_events.result via LIKE."""
    from cli.query import cmd_query

    monkeypatch.chdir(tmp_path)
    _seed_harness_event(tmp_db, type_="command.executed",
                        ts="2026-05-07T01:00:00Z",
                        result="ok: git push origin main")
    _seed_harness_event(tmp_db, type_="command.executed",
                        ts="2026-05-07T02:00:00Z",
                        result="ok: ls")

    args = _make_args(surface="harness_events",
                      command_like="%git push%", format="json")
    rc = cmd_query(args)
    assert rc == 0, capsys.readouterr()

    out = json.loads(capsys.readouterr().out)
    assert len(out) == 1
    assert "git push" in out[0]["summary"]


def test_query_format_count(tmp_db, tmp_path, monkeypatch, capsys):
    """--format=count emits an integer with no other output."""
    from cli.query import cmd_query

    monkeypatch.chdir(tmp_path)
    for i in range(3):
        _seed_episode(tmp_db, f"ep_count_{i}", agent="developer",
                      timestamp=f"2026-05-07T0{i}:00:00Z")

    args = _make_args(surface="episodes", format="count")
    rc = cmd_query(args)
    assert rc == 0, capsys.readouterr()
    out = capsys.readouterr().out.strip()
    assert out == "3"


def test_query_since_invalid_format_returns_error(tmp_db, tmp_path,
                                                  monkeypatch, capsys):
    """Garbage --since value raises a clear ValueError + exit 1."""
    from cli.query import cmd_query

    monkeypatch.chdir(tmp_path)
    args = _make_args(since="garbage-not-a-date")
    rc = cmd_query(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "could not parse" in err.lower()


def test_query_group_by_surface_counts(tmp_db, tmp_path,
                                       monkeypatch, capsys):
    """--group-by=surface buckets results across the three surfaces."""
    from cli.query import cmd_query

    monkeypatch.chdir(tmp_path)
    _seed_memory(tmp_db, "m1", "project", "body",
                 updated_at="2026-05-07T01:00:00Z")
    _seed_episode(tmp_db, "ep_g1", agent="developer",
                  timestamp="2026-05-07T02:00:00Z")
    _seed_episode(tmp_db, "ep_g2", agent="orchestrator",
                  timestamp="2026-05-07T03:00:00Z")
    _seed_harness_event(tmp_db, type_="command.executed",
                        ts="2026-05-07T04:00:00Z", result="ok: ls")

    args = _make_args(group_by="surface", format="json")
    rc = cmd_query(args)
    assert rc == 0, capsys.readouterr()

    out = json.loads(capsys.readouterr().out)
    by_surface = {r["surface"]: r["count"] for r in out}
    assert by_surface == {"memory": 1, "episodes": 2, "harness_events": 1}


def test_query_count_without_group_by_returns_total(tmp_db, tmp_path,
                                                    monkeypatch, capsys):
    """--count alone emits a single integer total across the merged result."""
    from cli.query import cmd_query

    monkeypatch.chdir(tmp_path)
    for i in range(3):
        _seed_episode(tmp_db, f"ep_c{i}", agent="developer",
                      timestamp=f"2026-05-07T0{i}:00:00Z")

    args = _make_args(surface="episodes", count=True, format="json")
    rc = cmd_query(args)
    assert rc == 0, capsys.readouterr()
    out = json.loads(capsys.readouterr().out)
    assert out == [{"count": 3}]


def test_query_group_by_day_truncates_timestamp(tmp_db, tmp_path,
                                                monkeypatch, capsys):
    """--group-by=day buckets by YYYY-MM-DD prefix of timestamp."""
    from cli.query import cmd_query

    monkeypatch.chdir(tmp_path)
    _seed_episode(tmp_db, "ep_d1", agent="developer",
                  timestamp="2026-05-07T01:00:00Z")
    _seed_episode(tmp_db, "ep_d2", agent="developer",
                  timestamp="2026-05-07T22:00:00Z")
    _seed_episode(tmp_db, "ep_d3", agent="developer",
                  timestamp="2026-05-06T22:00:00Z")

    args = _make_args(surface="episodes", group_by="day", format="json")
    rc = cmd_query(args)
    assert rc == 0, capsys.readouterr()
    out = json.loads(capsys.readouterr().out)
    by_day = {r["day"]: r["count"] for r in out}
    assert by_day == {"2026-05-07": 2, "2026-05-06": 1}


def test_query_snippets_highlights_command_like(tmp_db, tmp_path,
                                                monkeypatch, capsys):
    """--snippets wraps the matched needle with [..] brackets in the summary."""
    from cli.query import cmd_query

    monkeypatch.chdir(tmp_path)
    _seed_harness_event(tmp_db, type_="command.executed",
                        ts="2026-05-07T01:00:00Z",
                        result=("preface text " * 10
                                + "ok: git push origin main "
                                + "trailing context " * 5))

    args = _make_args(surface="harness_events",
                      command_like="%git push%",
                      snippets=True, format="json")
    rc = cmd_query(args)
    assert rc == 0, capsys.readouterr()
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 1
    assert "[git push]" in out[0]["summary"]


def test_query_snippets_noop_without_textual_filter(tmp_db, tmp_path,
                                                    monkeypatch, capsys):
    """--snippets without textual filter leaves the summary intact."""
    from cli.query import cmd_query

    monkeypatch.chdir(tmp_path)
    _seed_episode(tmp_db, "ep_plain", agent="developer",
                  title="plain title",
                  timestamp="2026-05-07T01:00:00Z")

    args = _make_args(surface="episodes", snippets=True, format="json")
    rc = cmd_query(args)
    assert rc == 0, capsys.readouterr()
    baseline = json.loads(capsys.readouterr().out)

    # Now run the same query without --snippets
    args2 = _make_args(surface="episodes", snippets=False, format="json")
    rc = cmd_query(args2)
    assert rc == 0
    plain = json.loads(capsys.readouterr().out)

    # No needle => summary identical to non-snippet rendering
    assert baseline[0]["summary"] == plain[0]["summary"]


def test_query_group_by_agent_buckets(tmp_db, tmp_path, monkeypatch, capsys):
    """--group-by=agent buckets by agent across surfaces that carry agent."""
    from cli.query import cmd_query

    monkeypatch.chdir(tmp_path)
    _seed_episode(tmp_db, "ep_a1", agent="developer",
                  timestamp="2026-05-07T01:00:00Z")
    _seed_episode(tmp_db, "ep_a2", agent="developer",
                  timestamp="2026-05-07T02:00:00Z")
    _seed_episode(tmp_db, "ep_a3", agent="orchestrator",
                  timestamp="2026-05-07T03:00:00Z")

    args = _make_args(surface="episodes", group_by="agent", format="json")
    rc = cmd_query(args)
    assert rc == 0, capsys.readouterr()
    out = json.loads(capsys.readouterr().out)
    by_agent = {r["agent"]: r["count"] for r in out}
    assert by_agent == {"developer": 2, "orchestrator": 1}


def test_query_metrics_projects_context_metrics_fields(tmp_db, tmp_path,
                                                       monkeypatch, capsys):
    """--metrics projects context_metrics telemetry into each row's raw."""
    from cli.query import cmd_query

    monkeypatch.chdir(tmp_path)
    _seed_episode(
        tmp_db, "ep_m1", agent="developer",
        timestamp="2026-05-07T01:00:00Z",
        context_metrics=_metrics_blob(compliance_total=85, grade="B",
                                      input_tokens=4826, output_real=2759,
                                      cache_read=120000),
    )

    args = _make_args(metrics=True, format="json")
    rc = cmd_query(args)
    assert rc == 0, capsys.readouterr()
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 1
    raw = out[0]["raw"]
    assert raw["compliance_score"] == 85
    assert raw["compliance_grade"] == "B"
    assert raw["input_tokens"] == 4826
    assert raw["output_tokens_real"] == 2759
    assert raw["model_used"] == "claude-sonnet-5"
    # summary carries the projected metrics
    assert "compliance=85/B" in out[0]["summary"]


def test_query_metrics_forces_episodes_surface(tmp_db, tmp_path,
                                               monkeypatch, capsys):
    """--metrics scopes to episodes even when other surfaces have rows."""
    from cli.query import cmd_query

    monkeypatch.chdir(tmp_path)
    _seed_memory(tmp_db, "m1", "project", "body",
                 updated_at="2026-05-07T01:00:00Z")
    _seed_harness_event(tmp_db, type_="command.executed",
                        ts="2026-05-07T02:00:00Z", result="ok: ls")
    _seed_episode(tmp_db, "ep_only", agent="developer",
                  timestamp="2026-05-07T03:00:00Z",
                  context_metrics=_metrics_blob(compliance_total=70, grade="C",
                                                input_tokens=100, output_real=50))

    # surface left at default "all" -- --metrics must override to episodes.
    args = _make_args(metrics=True, format="json")
    rc = cmd_query(args)
    assert rc == 0, capsys.readouterr()
    out = json.loads(capsys.readouterr().out)
    surfaces = {r["surface"] for r in out}
    assert surfaces == {"episodes"}


def test_query_metrics_excludes_rows_without_blob(tmp_db, tmp_path,
                                                  monkeypatch, capsys):
    """Episodes with NULL context_metrics are excluded from --metrics."""
    from cli.query import cmd_query

    monkeypatch.chdir(tmp_path)
    _seed_episode(tmp_db, "ep_with", agent="developer",
                  timestamp="2026-05-07T02:00:00Z",
                  context_metrics=_metrics_blob(compliance_total=60, grade="C",
                                                input_tokens=10, output_real=5))
    _seed_episode(tmp_db, "ep_without", agent="developer",
                  timestamp="2026-05-07T01:00:00Z")  # no context_metrics

    args = _make_args(metrics=True, format="json")
    rc = cmd_query(args)
    assert rc == 0, capsys.readouterr()
    out = json.loads(capsys.readouterr().out)
    ids = {r["raw"]["episode_id"] for r in out}
    assert ids == {"ep_with"}


def test_query_metrics_group_by_agent_aggregates(tmp_db, tmp_path,
                                                 monkeypatch, capsys):
    """--metrics --group-by=agent yields avg compliance + token sums."""
    from cli.query import cmd_query

    monkeypatch.chdir(tmp_path)
    _seed_episode(tmp_db, "ep_d1", agent="developer",
                  timestamp="2026-05-07T01:00:00Z",
                  context_metrics=_metrics_blob(compliance_total=80, grade="B",
                                                input_tokens=1000, output_real=500,
                                                cache_read=10000))
    _seed_episode(tmp_db, "ep_d2", agent="developer",
                  timestamp="2026-05-07T02:00:00Z",
                  context_metrics=_metrics_blob(compliance_total=60, grade="C",
                                                input_tokens=2000, output_real=1000,
                                                cache_read=20000))
    _seed_episode(tmp_db, "ep_o1", agent="orchestrator",
                  timestamp="2026-05-07T03:00:00Z",
                  context_metrics=_metrics_blob(compliance_total=90, grade="A",
                                                input_tokens=500, output_real=250,
                                                cache_read=5000))

    args = _make_args(metrics=True, group_by="agent", format="json")
    rc = cmd_query(args)
    assert rc == 0, capsys.readouterr()
    out = json.loads(capsys.readouterr().out)
    by_agent = {r["agent"]: r for r in out}
    assert by_agent["developer"]["count"] == 2
    assert by_agent["developer"]["avg_compliance_score"] == 70.0  # (80+60)/2
    assert by_agent["developer"]["input_tokens"] == 3000  # summed
    assert by_agent["developer"]["output_tokens_real"] == 1500
    assert by_agent["developer"]["cache_read_tokens"] == 30000
    assert by_agent["orchestrator"]["avg_compliance_score"] == 90.0


def test_query_metrics_count_single_bucket(tmp_db, tmp_path,
                                           monkeypatch, capsys):
    """--metrics --count (no group-by) yields one (all) aggregate bucket."""
    from cli.query import cmd_query

    monkeypatch.chdir(tmp_path)
    for i in range(2):
        _seed_episode(tmp_db, f"ep_{i}", agent="developer",
                      timestamp=f"2026-05-07T0{i}:00:00Z",
                      context_metrics=_metrics_blob(compliance_total=50, grade="F",
                                                    input_tokens=100, output_real=50))

    args = _make_args(metrics=True, count=True, format="json")
    rc = cmd_query(args)
    assert rc == 0, capsys.readouterr()
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 1
    assert out[0]["count"] == 2
    assert out[0]["avg_compliance_score"] == 50.0
    assert out[0]["input_tokens"] == 200


def test_query_metrics_table_render(tmp_db, tmp_path, monkeypatch, capsys):
    """--metrics default table renders compliance + token columns."""
    from cli.query import cmd_query

    monkeypatch.chdir(tmp_path)
    _seed_episode(tmp_db, "ep_t1", agent="developer",
                  timestamp="2026-05-07T01:00:00Z",
                  context_metrics=_metrics_blob(compliance_total=82, grade="B",
                                                input_tokens=4826, output_real=2759))

    args = _make_args(metrics=True, format="table")
    rc = cmd_query(args)
    assert rc == 0, capsys.readouterr()
    out = capsys.readouterr().out
    assert "COMPL" in out
    assert "MODEL" in out
    assert "developer" in out
    assert "82" in out


def test_query_registers_subcommand_choice():
    """``gaia query`` is wired into the argparse tree."""
    import cli.query as query_mod

    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="subcommand")
    query_mod.register(subs)

    assert "query" in subs.choices
    # Help renders without raising
    help_text = subs.choices["query"].format_help()
    assert "--surface" in help_text
    assert "--since" in help_text
    assert "--command-like" in help_text
    assert "--failed" in help_text
    assert "--group-by" in help_text
    assert "--count" in help_text
    assert "--snippets" in help_text
    assert "--metrics" in help_text
    # Examples present in epilog
    assert "Examples:" in help_text
