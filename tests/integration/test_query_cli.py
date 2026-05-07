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

def _ensure_project(db_path: Path, workspace: str = "me") -> None:
    # Use writer._connect which bootstraps schema on first connection.
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        con.execute(
            "INSERT OR IGNORE INTO projects (name, identity) VALUES (?, ?)",
            (workspace, workspace),
        )
        con.commit()
    finally:
        con.close()


def _seed_memory(db_path: Path, name: str, type_: str, body: str,
                 description: str | None = None,
                 updated_at: str = "2026-05-07T10:00:00Z",
                 workspace: str = "me") -> None:
    _ensure_project(db_path, workspace)
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO memory (project, name, type, description, body, "
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
                  workspace: str = "me") -> None:
    _ensure_project(db_path, workspace)
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO episodes (episode_id, project, timestamp, agent, "
            "                      type, title, plan_status, outcome) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (episode_id, workspace, timestamp, agent, type_, title,
             plan_status, outcome),
        )
        con.commit()
    finally:
        con.close()


def _seed_harness_event(db_path: Path, *, type_: str, ts: str,
                        agent: str = "", result: str = "ok",
                        severity: str = "info",
                        payload: str = "{}",
                        workspace: str = "me") -> None:
    _ensure_project(db_path, workspace)
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO harness_events (project, ts, type, source, agent, "
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
    # Examples present in epilog
    assert "Examples:" in help_text
