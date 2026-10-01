"""Reminders and routines come due when the user touches Gaia, and only then.

Nothing wakes up to deliver them: a row is due when a read finds its due_at at
or before now, the read never writes, and ack/snooze/cancel are the only state
transitions. Every case runs in America/Santiago with an injected clock, so the
local wall time the user typed is the time they see it.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import sqlite3
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parents[1]
for _entry in (_REPO, _REPO / "bin", _REPO / "hooks"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from gaia import notifications_time  # noqa: E402
from gaia.store import reader, writer  # noqa: E402

_ZONE = "America/Santiago"


def _local(year, month, day, hour, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute).astimezone()


def _prompt_counter_module():
    spec = importlib.util.spec_from_file_location(
        "user_prompt_submit_under_test", _REPO / "hooks" / "user_prompt_submit.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prompt_notice() -> str:
    hook = _prompt_counter_module()
    from modules.session.session_lifecycle import notifications_counter

    return notifications_counter(hook._current_workspace())


@pytest.fixture
def gaia_env(tmp_path, monkeypatch):
    previous_tz = os.environ.get("TZ")
    os.environ["TZ"] = _ZONE
    time.tzset()
    data = tmp_path / "data"
    db = data / "gaia.db"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(db))
    monkeypatch.delenv("GAIA_WORKSPACE", raising=False)
    monkeypatch.delenv("GAIA_DISPATCH_WORKSPACE", raising=False)
    for name in ("me", "ws", "aaxis"):
        (tmp_path / name).mkdir()
    monkeypatch.chdir(tmp_path / "ws")
    writer._SCHEMA_VERSION_BY_DB.clear()
    writer._connect(db).close()

    clock = {"now": None}
    monkeypatch.setattr(notifications_time, "now_utc", lambda: clock["now"])

    def at(year, month, day, hour, minute=0):
        clock["now"] = _local(year, month, day, hour, minute).astimezone(timezone.utc)

    assert _local(2026, 10, 1, 16).utcoffset().total_seconds() == -3 * 3600, (
        "the America/Santiago zone must be installed for these cases to mean anything"
    )
    yield SimpleNamespace(db=db, at=at, tmp=tmp_path)
    writer._SCHEMA_VERSION_BY_DB.clear()
    if previous_tz is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = previous_tz
    time.tzset()


def gaia_notifications(*argv):
    from cli import notifications as cli

    parser = argparse.ArgumentParser()
    cli.register(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["notifications", *argv])
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.cmd_notifications(args)
    return rc, out.getvalue(), err.getvalue()


def add_json(*argv) -> dict:
    rc, out, err = gaia_notifications("add", *argv, "--json")
    assert rc == 0, out + err
    return json.loads(out)


def due_ids(workspace: str) -> list[int]:
    return [row["id"] for row in reader.list_unread_notifications(workspace=workspace)]


def row_count(db: Path) -> int:
    con = sqlite3.connect(str(db))
    try:
        return con.execute("SELECT COUNT(*) FROM task_notifications").fetchone()[0]
    finally:
        con.close()


def birth_block_line(workspace: str) -> str:
    from modules.session import session_manifest

    return session_manifest._recurring_work_line(workspace)


def due_at_local(notification_id: int) -> datetime:
    row = reader.get_notification(notification_id)
    return datetime.fromisoformat(row["due_at"].replace("Z", "+00:00")).astimezone()


def test_a_dated_reminder_is_due_once_from_its_local_hour_and_never_after_ack(gaia_env):
    gaia_env.at(2026, 10, 1, 10, 0)
    reminder = add_json(
        "--kind", "reminder", "--at", "2026-10-01T16:00", "--headline", "review-x-7f3"
    )["id"]
    counter = _prompt_notice

    gaia_env.at(2026, 10, 1, 15, 59)
    assert reminder not in due_ids("ws")
    assert counter() == ""
    assert "review-x-7f3" not in birth_block_line("ws")

    gaia_env.at(2026, 10, 1, 16, 0)
    for _ in range(3):
        assert "review-x-7f3" in birth_block_line("ws")
        assert counter()
    assert due_ids("ws") == [reminder]
    assert reader.count_unread_notifications(workspace="ws") == 1
    assert row_count(gaia_env.db) == 1

    rc, out, err = gaia_notifications("ack", str(reminder))
    assert rc == 0, out + err
    assert reminder not in due_ids("ws")
    assert counter() == ""

    gaia_env.at(2027, 10, 1, 16, 0)
    assert reminder not in due_ids("ws")
    assert reader.count_unread_notifications(workspace="ws") == 0


def test_a_friday_routine_acked_late_comes_back_once_on_the_next_friday(gaia_env):
    gaia_env.at(2026, 9, 30, 10, 0)
    routine = add_json("--kind", "routine", "--cron", "0 17 * * 5", "--headline", "Timesheet")["id"]
    assert due_at_local(routine) == _local(2026, 10, 2, 17, 0)

    gaia_env.at(2026, 10, 17, 18, 0)
    assert due_ids("ws") == [routine]
    rc, out, err = gaia_notifications("ack", str(routine))
    assert rc == 0, out + err

    assert due_at_local(routine) == _local(2026, 10, 23, 17, 0)
    assert routine not in due_ids("ws")
    assert row_count(gaia_env.db) == 1
    gaia_env.at(2026, 10, 23, 17, 0)
    assert due_ids("ws") == [routine]


def test_a_routine_keeps_its_local_hour_across_the_april_clock_change(gaia_env):
    gaia_env.at(2027, 3, 29, 9, 0)
    routine = add_json("--kind", "routine", "--cron", "0 17 * * 5", "--headline", "Timesheet")["id"]
    before_change = due_at_local(routine)
    assert before_change == _local(2027, 4, 2, 17, 0)

    gaia_env.at(2027, 4, 2, 17, 30)
    rc, out, err = gaia_notifications("ack", str(routine))
    assert rc == 0, out + err

    after_change = due_at_local(routine)
    assert after_change == _local(2027, 4, 9, 17, 0)
    assert (after_change.hour, after_change.minute) == (17, 0)
    assert after_change.utcoffset() != before_change.utcoffset()
    gaia_env.at(2027, 4, 9, 16, 59)
    assert routine not in due_ids("ws")
    gaia_env.at(2027, 4, 9, 17, 0)
    assert due_ids("ws") == [routine]


def test_snooze_hides_a_due_reminder_until_the_requested_hour(gaia_env):
    gaia_env.at(2026, 10, 1, 10, 0)
    reminder = add_json("--kind", "reminder", "--at", "2026-10-01T16:00", "--headline", "X")["id"]
    gaia_env.at(2026, 10, 1, 16, 0)
    assert due_ids("ws") == [reminder]

    rc, out, err = gaia_notifications("snooze", str(reminder), "--for", "2h")
    assert rc == 0, out + err
    gaia_env.at(2026, 10, 1, 17, 59)
    assert reminder not in due_ids("ws")
    gaia_env.at(2026, 10, 1, 18, 0)
    assert due_ids("ws") == [reminder]


def test_a_reminder_created_without_workspace_is_seen_from_every_workspace(gaia_env, monkeypatch):
    gaia_env.at(2026, 10, 1, 10, 0)
    monkeypatch.chdir(gaia_env.tmp / "me")
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    reminder = add_json("--kind", "reminder", "--at", "2026-10-01T16:00", "--headline", "X")["id"]
    monkeypatch.delenv("GAIA_WORKSPACE")
    gaia_env.at(2026, 10, 1, 16, 0)

    monkeypatch.chdir(gaia_env.tmp / "ws")
    assert reader.count_unread_notifications(workspace="ws") == 1
    assert _prompt_notice()

    rc, out, err = gaia_notifications("list", "--unread", "--workspace", "aaxis", "--json")
    assert rc == 0, out + err
    assert [row["id"] for row in json.loads(out)] == [reminder]


def test_reading_what_is_due_leaves_the_database_bytes_untouched(gaia_env):
    gaia_env.at(2026, 10, 1, 10, 0)
    add_json("--kind", "reminder", "--at", "2026-10-01T16:00", "--headline", "X")
    add_json("--kind", "routine", "--every", "6h", "--headline", "Y")
    gaia_env.at(2026, 10, 2, 9, 0)

    def snapshot():
        return {
            path.name: path.read_bytes()
            for path in (gaia_env.db, Path(f"{gaia_env.db}-wal"))
            if path.exists()
        }

    before = snapshot()
    assert birth_block_line("ws")
    assert _prompt_notice()
    rc, out, err = gaia_notifications("list", "--json")
    assert rc == 0, out + err
    rc, out, err = gaia_notifications("list", "--upcoming", "--json")
    assert rc == 0, out + err
    assert snapshot() == before


def test_add_reads_local_time_rejects_the_past_and_broken_pointers(gaia_env):
    gaia_env.at(2026, 10, 1, 10, 0)
    created = add_json("--kind", "reminder", "--at", "2026-10-01T16:00", "--headline", "X")
    assert created["due_at"] == "2026-10-01T19:00:00Z"
    assert row_count(gaia_env.db) == 1

    rejected = (
        ("--at", "2026-10-01T09:00"),
        ("--at", "2026-10-02T09:00", "--skill", "no-such-skill-anywhere"),
        ("--at", "2026-10-02T09:00", "--memory", "me:no_such_memory_slug"),
        ("--at", "2026-10-02T09:00", "--project", "me/no-such-project"),
    )
    for extra in rejected:
        rc, out, err = gaia_notifications("add", "--kind", "reminder", "--headline", "Z", *extra)
        assert rc != 0, extra
    assert row_count(gaia_env.db) == 1

    add_json("--kind", "reminder", "--at", "2026-10-01T12:00", "--headline", "Y",
             "--skill", "gmail-triage")
    gaia_env.at(2026, 10, 1, 12, 0)
    assert "gmail-triage" in birth_block_line("ws")
