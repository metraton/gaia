#!/usr/bin/env python3
"""
Tests for the Event Writer module (Brief 54 / Task 2.2 DB cutover).

As of Task 2.2, EventWriter.write_event writes to the ``harness_events`` table
in the Gaia SQLite substrate, NOT events.jsonl. There is no dual-write.

Validates:
1. EventWriter.write_event inserts a row into harness_events (DB, not JSONL)
2. write_harness_event (gaia.store.writer) column mapping
3. write_event NEVER writes events.jsonl (no-dual-write invariant)
4. write_event is silent-on-failure
5. read_events still reads the legacy JSONL file (retained read until 2.3)
6. Event type constants exist
"""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

# Add repo root so `gaia` package is importable in the test env.
REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from modules.events.event_writer import (
    AGENT_COMPLETE,
    AGENT_DISPATCH,
    COMMAND_EXECUTED,
    HEARTBEAT,
    SESSION_END,
    TRIGGER_SCHEDULED,
    USER_NOTE,
    EventWriter,
    read_events,
)

from gaia.store import writer as store_writer


def _read_harness_rows(db_path, **filters):
    """Read harness_events rows directly via the store connection."""
    con = store_writer._connect(db_path)
    try:
        rows = con.execute(
            "SELECT workspace, ts, type, source, agent, result, severity, payload "
            "FROM harness_events ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


class TestEventTypeConstants:
    """Test event type constants are defined correctly."""

    def test_agent_dispatch_constant(self):
        assert AGENT_DISPATCH == "agent.dispatch"

    def test_agent_complete_constant(self):
        assert AGENT_COMPLETE == "agent.complete"

    def test_command_executed_constant(self):
        assert COMMAND_EXECUTED == "command.executed"

    def test_session_end_constant(self):
        assert SESSION_END == "session.end"

    def test_trigger_scheduled_constant(self):
        assert TRIGGER_SCHEDULED == "trigger.scheduled"

    def test_heartbeat_constant(self):
        assert HEARTBEAT == "heartbeat"

    def test_user_note_constant(self):
        assert USER_NOTE == "user.note"


class TestWriteHarnessEvent:
    """Test gaia.store.writer.write_harness_event column mapping (DB INSERT)."""

    @pytest.fixture
    def db_path(self, tmp_path):
        return tmp_path / "gaia.db"

    def test_insert_maps_columns(self, db_path):
        """write_harness_event maps args to the harness_events columns."""
        row_id = store_writer.write_harness_event(
            event_type=AGENT_DISPATCH,
            source="hook",
            agent="platform-architect",
            result="dispatched for: plan staging",
            severity="info",
            meta={"k": "v"},
            workspace="me",
            db_path=db_path,
        )
        assert isinstance(row_id, int) and row_id > 0

        rows = _read_harness_rows(db_path)
        assert len(rows) == 1
        r = rows[0]
        assert r["type"] == "agent.dispatch"
        assert r["source"] == "hook"
        assert r["agent"] == "platform-architect"
        assert r["result"] == "dispatched for: plan staging"
        assert r["severity"] == "info"
        assert r["workspace"] == "me"
        assert json.loads(r["payload"]) == {"k": "v"}
        assert r["ts"]  # populated

    def test_meta_none_yields_null_payload(self, db_path):
        """Falsy meta -> NULL payload column."""
        store_writer.write_harness_event(
            event_type=SESSION_END,
            source="hook",
            agent="",
            result="session ended",
            db_path=db_path,
        )
        rows = _read_harness_rows(db_path)
        assert rows[0]["payload"] is None

    def test_workspace_none_is_allowed(self, db_path):
        """workspace=None is valid (nullable column, no FK)."""
        store_writer.write_harness_event(
            event_type=HEARTBEAT,
            source="system",
            result="ok",
            workspace=None,
            db_path=db_path,
        )
        rows = _read_harness_rows(db_path)
        assert rows[0]["workspace"] is None

    def test_multiple_inserts_append(self, db_path):
        """Multiple writes append rows."""
        store_writer.write_harness_event(event_type=AGENT_DISPATCH, db_path=db_path)
        store_writer.write_harness_event(event_type=AGENT_COMPLETE, db_path=db_path)
        store_writer.write_harness_event(event_type=SESSION_END, db_path=db_path)
        rows = _read_harness_rows(db_path)
        assert len(rows) == 3


class TestEventWriterDB:
    """EventWriter.write_event writes to harness_events, not events.jsonl."""

    @pytest.fixture
    def db_path(self, tmp_path, monkeypatch):
        # Point the store at a temp DB via GAIA_DATA_DIR (the prod resolution
        # path: gaia.paths.db_path() -> GAIA_DATA_DIR / gaia.db).
        data_dir = tmp_path / "gaia_data"
        data_dir.mkdir()
        monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
        return data_dir / "gaia.db"

    def test_write_event_inserts_db_row(self, db_path, monkeypatch):
        """write_event lands a row in harness_events via GAIA_DATA_DIR."""
        monkeypatch.setenv("GAIA_WORKSPACE", "me")
        EventWriter().write_event(
            AGENT_DISPATCH, "hook", "developer", "dispatched for: feature X",
        )
        rows = _read_harness_rows(db_path)
        assert len(rows) == 1
        assert rows[0]["type"] == "agent.dispatch"
        assert rows[0]["agent"] == "developer"
        assert rows[0]["workspace"] == "me"

    def test_write_event_meta_to_payload(self, db_path):
        """meta is serialized into the payload column."""
        EventWriter().write_event(
            AGENT_COMPLETE, "hook", "cloud-troubleshooter", "COMPLETE",
            meta={"episode_id": "ep_123", "summary": "found issue"},
        )
        rows = _read_harness_rows(db_path)
        payload = json.loads(rows[0]["payload"])
        assert payload["episode_id"] == "ep_123"
        assert payload["summary"] == "found issue"

    def test_write_event_custom_severity(self, db_path):
        EventWriter().write_event(
            COMMAND_EXECUTED, "hook", "", "error: kubectl apply failed",
            severity="warning",
        )
        rows = _read_harness_rows(db_path)
        assert rows[0]["severity"] == "warning"

    def test_write_event_gaia_workspace_env_wins(self, db_path, monkeypatch):
        """(a) With GAIA_WORKSPACE set, the event is attributed to that value."""
        monkeypatch.setenv("GAIA_WORKSPACE", "some-explicit-workspace")
        EventWriter().write_event(SESSION_END, "hook", "", "ended")
        rows = _read_harness_rows(db_path)
        assert rows[0]["workspace"] == "some-explicit-workspace"

    def test_write_event_derives_workspace_from_cwd_repo(
        self, db_path, tmp_path, monkeypatch
    ):
        """(b) With no env var but a resolvable git repo in cwd, workspace is
        derived from gaia.project.current() (the repo-root basename)."""
        monkeypatch.delenv("GAIA_WORKSPACE", raising=False)
        monkeypatch.delenv("GAIA_DISPATCH_WORKSPACE", raising=False)
        repo = tmp_path / "derived-workspace-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=str(repo), check=True)
        monkeypatch.chdir(repo)

        EventWriter().write_event(SESSION_END, "hook", "", "ended")

        rows = _read_harness_rows(db_path)
        assert rows[0]["workspace"] == "derived-workspace-repo"

    def test_write_event_workspace_falls_back_to_global_never_null(
        self, db_path, monkeypatch
    ):
        """(c) With nothing resolvable (no env, current() unresolvable), the
        workspace falls back to "global" -- and NEVER to NULL."""
        monkeypatch.delenv("GAIA_WORKSPACE", raising=False)
        monkeypatch.delenv("GAIA_DISPATCH_WORKSPACE", raising=False)
        # Force the path-based step to fail so the final "global" branch is hit.
        import gaia.project as project

        def _boom(cwd=None):
            raise RuntimeError("cannot resolve identity")

        monkeypatch.setattr(project, "current", _boom)

        EventWriter().write_event(SESSION_END, "hook", "", "ended")

        rows = _read_harness_rows(db_path)
        assert rows[0]["workspace"] == "global"
        assert rows[0]["workspace"] is not None

    def test_write_event_does_not_write_jsonl(self, db_path, tmp_path, monkeypatch):
        """NO-DUAL-WRITE invariant: write_event must NOT create events.jsonl.

        Force the legacy events dir to a known temp location and assert no
        events.jsonl ever appears there after a write.
        """
        events_dir = tmp_path / "legacy_events"
        events_dir.mkdir()
        EventWriter(events_dir=events_dir).write_event(
            AGENT_DISPATCH, "hook", "developer", "dispatched",
        )
        assert not (events_dir / "events.jsonl").exists()
        # And the DB row IS present (write went to DB).
        assert len(_read_harness_rows(db_path)) == 1

    def test_write_event_fails_silently(self, monkeypatch):
        """write_event must never raise, even when the DB write fails."""
        # Point GAIA_DATA_DIR at a path that cannot be a directory.
        bad = Path("/proc/nonexistent_gaia/cannot/exist")
        monkeypatch.setenv("GAIA_DATA_DIR", str(bad))
        # Should not raise.
        EventWriter().write_event(HEARTBEAT, "test", "", "ok")


class TestReadEventsLegacyJSONL:
    """read_events still reads the legacy JSONL file (retained until 2.3)."""

    @pytest.fixture
    def events_dir(self, tmp_path):
        edir = tmp_path / "events"
        edir.mkdir()
        return edir

    def _write_raw_event(self, events_dir, event_type, ts, result="ok", agent=""):
        events_file = events_dir / "events.jsonl"
        record = {
            "ts": ts.isoformat(),
            "type": event_type,
            "source": "test",
            "agent": agent,
            "result": result,
            "severity": "info",
        }
        with open(events_file, "a") as f:
            f.write(json.dumps(record) + "\n")

    def test_read_recent_events(self, events_dir):
        now = datetime.now(timezone.utc)
        self._write_raw_event(events_dir, AGENT_DISPATCH, now - timedelta(hours=1))
        self._write_raw_event(events_dir, AGENT_COMPLETE, now - timedelta(minutes=30))
        results = read_events(hours=24, events_dir=events_dir)
        assert len(results) == 2

    def test_read_events_filters_old(self, events_dir):
        now = datetime.now(timezone.utc)
        self._write_raw_event(events_dir, AGENT_DISPATCH, now - timedelta(hours=48))
        self._write_raw_event(events_dir, AGENT_COMPLETE, now - timedelta(minutes=30))
        results = read_events(hours=24, events_dir=events_dir)
        assert len(results) == 1
        assert results[0]["type"] == AGENT_COMPLETE

    def test_read_events_missing_file(self, events_dir):
        results = read_events(events_dir=events_dir)
        assert results == []
