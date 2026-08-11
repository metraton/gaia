"""Tests for the scheduled-task desired-state registry.

Covers the store writers/readers (against a bootstrapped v30 DB), the neutral
schedule_spec validation + cron translation, the cron-string parser used by the
`gaia schedule register --cron` path, and marker/adoption parsing. It does NOT
exercise crontab install/remove (those mutate the real user crontab and are T3);
that surface is covered by the tier-classification tests and left to manual
verification.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture()
def db(tmp_path, bootstrapped_db_template):
    """Fresh DB (applies the v30 migration), copied from the session-scoped
    ``bootstrapped_db_template`` instead of re-running
    ``scripts/bootstrap_database.sh`` per test. Each test still gets its own
    independent, mutable DB file -- isolation is unchanged.
    """
    from tests.conftest import copy_bootstrapped_db

    db_path = tmp_path / "gaia.db"
    copy_bootstrapped_db(bootstrapped_db_template, db_path)
    return db_path


# ---------------------------------------------------------------------------
# schema / migration
# ---------------------------------------------------------------------------

def test_v30_tables_exist(db):
    import sqlite3
    con = sqlite3.connect(str(db))
    try:
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    finally:
        con.close()
    assert {"scheduled_tasks", "scheduled_task_machines", "scheduled_task_state"} <= names


def test_schema_version_reaches_at_least_30(db):
    # Floor check (scripts/migrations/README.md section 2): the ledger must
    # reach AT LEAST v30 (the version that introduced scheduled_tasks). A
    # `== 30` point check breaks on every later migration (it did on v31).
    import sqlite3
    con = sqlite3.connect(str(db))
    try:
        ver = con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    finally:
        con.close()
    assert ver >= 30


# ---------------------------------------------------------------------------
# store writers / readers
# ---------------------------------------------------------------------------

def test_upsert_and_get(db):
    from gaia.store import writer, reader
    tid = writer.upsert_scheduled_task(
        name="gmail-triage",
        schedule_spec={"kind": "calendar", "minute": 20, "hour": [9, 13, 17, 21]},
        schedule_hint="20 9,13,17,21",
        prompt_body="do the triage",
        project_dir="/home/jorge/ws/me",
        workspace="me",
        db_path=db,
    )
    assert tid > 0
    row = reader.get_scheduled_task("gmail-triage", workspace="me", db_path=db)
    assert row is not None
    assert row["name"] == "gmail-triage"
    assert row["spec"]["kind"] == "calendar"
    assert row["spec"]["hour"] == [9, 13, 17, 21]
    assert row["enabled"] == 1
    assert row["machine_scope"] == "all"


def test_upsert_is_update_on_same_name(db):
    from gaia.store import writer, reader
    a = writer.upsert_scheduled_task(
        name="t", schedule_spec={"kind": "interval", "every_seconds": 3600},
        workspace="me", db_path=db,
    )
    b = writer.upsert_scheduled_task(
        name="t", schedule_spec={"kind": "interval", "every_seconds": 7200},
        workspace="me", db_path=db,
    )
    assert a == b  # same row updated, not duplicated
    rows = reader.list_scheduled_tasks(workspace="me", db_path=db)
    assert len([r for r in rows if r["name"] == "t"]) == 1
    assert reader.get_scheduled_task("t", workspace="me", db_path=db)["spec"]["every_seconds"] == 7200


def test_enable_disable(db):
    from gaia.store import writer, reader
    writer.upsert_scheduled_task(
        name="t", schedule_spec={"kind": "interval", "every_seconds": 3600},
        workspace="me", db_path=db,
    )
    writer.set_scheduled_task_enabled("t", False, workspace="me", db_path=db)
    assert reader.get_scheduled_task("t", workspace="me", db_path=db)["enabled"] == 0
    # disabled task is excluded from the per-machine desired set
    got = reader.scheduled_tasks_for_machine("anyhost", workspace="me", db_path=db)
    assert all(r["name"] != "t" for r in got)
    writer.set_scheduled_task_enabled("t", True, workspace="me", db_path=db)
    assert reader.get_scheduled_task("t", workspace="me", db_path=db)["enabled"] == 1


def test_delete(db):
    from gaia.store import writer, reader
    writer.upsert_scheduled_task(
        name="t", schedule_spec={"kind": "interval", "every_seconds": 3600},
        workspace="me", db_path=db,
    )
    res = writer.delete_scheduled_task("t", workspace="me", db_path=db)
    assert res["status"] == "ok"
    assert reader.get_scheduled_task("t", workspace="me", db_path=db) is None
    assert writer.delete_scheduled_task("t", workspace="me", db_path=db)["status"] == "not_found"


def test_named_machine_scope(db):
    from gaia.store import writer, reader
    writer.upsert_scheduled_task(
        name="host-only",
        schedule_spec={"kind": "calendar", "hour": 7, "minute": 0},
        machine_scope="named", machines=["laptop", "desktop"],
        workspace="me", db_path=db,
    )
    row = reader.get_scheduled_task("host-only", workspace="me", db_path=db)
    assert row["machine_scope"] == "named"
    assert set(row["machines"]) == {"laptop", "desktop"}
    assert any(r["name"] == "host-only"
               for r in reader.scheduled_tasks_for_machine("laptop", workspace="me", db_path=db))
    assert all(r["name"] != "host-only"
               for r in reader.scheduled_tasks_for_machine("server", workspace="me", db_path=db))


def test_mark_state(db):
    from gaia.store import writer, reader
    tid = writer.upsert_scheduled_task(
        name="t", schedule_spec={"kind": "interval", "every_seconds": 3600},
        workspace="me", db_path=db,
    )
    writer.mark_scheduled_task_state(tid, "myhost", backend="cron", installed=True, db_path=db)
    st = reader.get_scheduled_task_state(tid, "myhost", db_path=db)
    assert st["installed"] == 1
    assert st["backend"] == "cron"
    assert st["last_synced_at"]


def test_upsert_rejects_bad_spec_json(db):
    from gaia.store import writer
    with pytest.raises(ValueError):
        writer.upsert_scheduled_task(
            name="t", schedule_spec="{not json", workspace="me", db_path=db,
        )


# ---------------------------------------------------------------------------
# neutral spec validation + cron translation (pure, no crontab I/O)
# ---------------------------------------------------------------------------

def test_validate_spec_ok():
    from gaia.schedulers import validate_spec
    validate_spec({"kind": "calendar", "minute": 20, "hour": [9, 13]})
    validate_spec({"kind": "interval", "every_seconds": 21600})


def test_validate_spec_rejects():
    from gaia.schedulers import validate_spec, SpecError
    with pytest.raises(SpecError):
        validate_spec({"kind": "calendar"})  # pins nothing
    with pytest.raises(SpecError):
        validate_spec({"kind": "bogus"})
    with pytest.raises(SpecError):
        validate_spec({"kind": "calendar", "hour": 99})
    with pytest.raises(SpecError):
        validate_spec({"kind": "interval", "every_seconds": 0})


def test_cron_translation_calendar():
    from gaia.schedulers.cron import CronBackend
    b = CronBackend()
    expr = b.translate({"spec": {"kind": "calendar", "minute": 20,
                                 "hour": [9, 13, 17, 21]}})
    assert expr == "20 9,13,17,21 * * *"


def test_cron_translation_interval():
    from gaia.schedulers.cron import CronBackend
    b = CronBackend()
    assert b.translate({"spec": {"kind": "interval", "every_seconds": 21600}}) == "0 */6 * * *"
    assert b.translate({"spec": {"kind": "interval", "every_seconds": 1800}}) == "*/30 * * * *"


def test_cron_translation_interval_unexpressible():
    from gaia.schedulers.cron import CronBackend
    from gaia.schedulers import SpecError
    b = CronBackend()
    with pytest.raises(SpecError):
        b.translate({"spec": {"kind": "interval", "every_seconds": 90}})  # 90s not minute-aligned


# ---------------------------------------------------------------------------
# CLI cron-string parser round-trip + marker/adoption parsing (pure)
# ---------------------------------------------------------------------------

def _load_schedule_cli():
    import importlib.util
    path = _REPO_ROOT / "bin" / "cli" / "schedule.py"
    spec = importlib.util.spec_from_file_location("cli.schedule", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cron_string_roundtrip_gmail_triage():
    from gaia.schedulers.cron import CronBackend
    cli = _load_schedule_cli()
    spec = cli._cron_to_spec("20 9,13,17,21 * * *")
    assert spec == {"kind": "calendar", "minute": 20, "hour": [9, 13, 17, 21],
                    "day_of_month": None, "month": None, "day_of_week": None}
    # translate back to the same cron line
    assert CronBackend().translate({"spec": spec}) == "20 9,13,17,21 * * *"


def test_every_parser():
    cli = _load_schedule_cli()
    assert cli._every_to_spec("6h") == {"kind": "interval", "every_seconds": 21600}
    assert cli._every_to_spec("30m") == {"kind": "interval", "every_seconds": 1800}
    assert cli._every_to_spec("2d") == {"kind": "interval", "every_seconds": 172800}


def test_cli_dispatch_register_list_show(db, monkeypatch, capsys):
    """End-to-end CLI handlers (write path), invoked in-process so the live
    installed hook -- which has not yet been rebuilt with the schedule tier
    exception -- does not gate the register call."""
    import argparse
    monkeypatch.setenv("GAIA_DB", str(db))
    cli = _load_schedule_cli()

    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="subcommand")
    cli.register(subs)

    args = parser.parse_args([
        "schedule", "register", "--name", "gmail-triage",
        "--cron", "20 9,13,17,21 * * *", "--prompt", "body",
        "--project-dir", "/home/jorge/ws/me", "--workspace", "me",
    ])
    assert cli.cmd_schedule(args) == 0

    args = parser.parse_args(["schedule", "list", "--workspace", "me", "--json"])
    assert cli.cmd_schedule(args) == 0
    out = capsys.readouterr().out
    assert "gmail-triage" in out

    args = parser.parse_args(["schedule", "show", "gmail-triage", "--workspace", "me", "--json"])
    assert cli.cmd_schedule(args) == 0
    out = capsys.readouterr().out
    assert '"native"' in out and "20 9,13,17,21 * * *" in out


def test_marker_and_adoption_parsing():
    from gaia.schedulers.cron import CronBackend
    b = CronBackend()
    managed = "20 9,13,17,21 * * * env TASK_NAME=x foo.sh # gaia-schedule:gmail-triage"
    assert b._marker_name(managed) == "gmail-triage"
    unmarked = "20 9,13,17,21 * * * /home/j/scheduled-tasks/gmail-triage.sh >> log 2>&1"
    assert b._marker_name(unmarked) is None
    # adoption heuristic: unmarked legacy line matching the task name + a .sh sig
    assert b._looks_adopted(unmarked, "gmail-triage") is True
    # a marked line is never treated as adoptable
    assert b._looks_adopted(managed, "gmail-triage") is False
    # an unrelated line is not dropped
    assert b._looks_adopted("0 0 * * * /usr/bin/backup.sh", "gmail-triage") is False


def test_adopt_skips_comment_line_above_entry(monkeypatch):
    """Regression: the wrapper writes a `#`-comment ABOVE the gmail-triage
    entry. That comment contains the substring "gmail-triage" and splits into
    >=6 tokens, so before the fix it slipped past the `len(toks) < 6` guard and
    fed cron="# gmail-triage headless..." into _parse_cron_field -> int("#") ->
    ValueError. Adoption must skip comment/blank lines and pick the real entry.
    """
    from gaia.schedulers.cron import CronBackend
    cli = _load_schedule_cli()

    sample = [
        "# gmail-triage headless scheduled task -- runs at 09:20, 13:20, 17:20, 21:20",
        "20 9,13,17,21 * * * env TASK_NAME=gmail-triage "
        "PROJECT_DIR=/home/jorge/ws/me "
        "PROMPT_FILE=/home/jorge/.gaia/scheduled-tasks/gmail-triage.prompt "
        "/home/jorge/.gaia/scheduled-tasks/run-scheduled-task.sh "
        ">> /home/jorge/.gaia/scheduled-tasks/logs/gmail-triage.log 2>&1",
        "",
    ]
    monkeypatch.setattr(CronBackend, "_read_crontab", staticmethod(lambda: sample))

    found = cli._adopt_from_crontab("gmail-triage")
    assert found is not None
    cron, project_dir, prompt_file = found
    assert cron == "20 9,13,17,21 * * *"
    assert project_dir == "/home/jorge/ws/me"
    assert prompt_file == "/home/jorge/.gaia/scheduled-tasks/gmail-triage.prompt"

    # the adopted cron string converts to the neutral spec without blowing up
    spec = cli._cron_to_spec(cron)
    assert spec == {"kind": "calendar", "minute": 20, "hour": [9, 13, 17, 21],
                    "day_of_month": None, "month": None, "day_of_week": None}


def test_adopt_returns_none_when_only_a_matching_comment(monkeypatch):
    """A crontab with ONLY a matching comment (no real entry) must yield None,
    not a crash and not a bogus '#'-led cron expression."""
    from gaia.schedulers.cron import CronBackend
    cli = _load_schedule_cli()

    sample = [
        "# gmail-triage headless scheduled task -- runs at 09:20, 13:20, 17:20, 21:20",
        "",
    ]
    monkeypatch.setattr(CronBackend, "_read_crontab", staticmethod(lambda: sample))
    assert cli._adopt_from_crontab("gmail-triage") is None


# ---------------------------------------------------------------------------
# suspension: schema, scopes, read-time expiry, disabled-vs-suspended
# ---------------------------------------------------------------------------
#
# The two ways a task is switched off must never be confusable:
#   enabled = 0                -- permanent, no deadline.
#   schedule_suspensions row   -- has a deadline that reactivates on its own.
# Every test below either pins that distinction or pins the read-time expiry
# that makes the deadline work without a daemon.

_SPEC = {"kind": "calendar", "minute": 20, "hour": [9, 13, 17, 21]}


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _past(**kw):
    from datetime import datetime, timedelta, timezone
    return _iso(datetime.now(tz=timezone.utc) - timedelta(**kw))


def _future(**kw):
    from datetime import datetime, timedelta, timezone
    return _iso(datetime.now(tz=timezone.utc) + timedelta(**kw))


def _register(db, name="gmail-triage", **kw):
    from gaia.store import writer
    return writer.upsert_scheduled_task(
        name=name, schedule_spec=_SPEC, schedule_hint="20 9,13,17,21",
        project_dir="/home/jorge/ws/me", workspace="me", db_path=db, **kw
    )


def test_v47_suspension_table_and_indexes_exist(db):
    """The v46->v47 objects the bootstrap must have produced."""
    import sqlite3
    con = sqlite3.connect(str(db))
    try:
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        indexes = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        ver = con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        cols = {r[1] for r in con.execute(
            "PRAGMA table_info('schedule_suspensions')").fetchall()}
    finally:
        con.close()
    assert "schedule_suspensions" in tables
    assert {"idx_schedule_suspensions_global", "idx_schedule_suspensions_task"} <= indexes
    assert cols == {"id", "workspace", "task_id", "suspended_at", "until", "reason"}
    assert ver >= 47


def test_task_scope_suspension_removes_it_from_the_desired_set(db):
    from gaia.store import writer, reader
    _register(db)
    writer.suspend_scheduled_tasks(name="gmail-triage", until=_future(hours=8),
                                   workspace="me", db_path=db)
    row = reader.get_scheduled_task("gmail-triage", workspace="me", db_path=db)
    assert row["effective_state"] == "suspended"
    assert row["enabled"] == 1, "suspension must NOT flip the permanent switch"
    assert row["suspension"]["scope"] == "task"
    assert row["suspension"]["live"] is True
    # desired state a sync would install no longer contains it
    got = reader.scheduled_tasks_for_machine("anyhost", workspace="me", db_path=db)
    assert all(r["name"] != "gmail-triage" for r in got)


def test_global_scope_suspension_covers_every_task_at_once(db):
    from gaia.store import writer, reader
    _register(db, name="a")
    _register(db, name="b")
    writer.suspend_scheduled_tasks(name=None, until=_future(hours=8),
                                   workspace="me", db_path=db)
    rows = reader.list_scheduled_tasks(workspace="me", db_path=db)
    assert {r["name"] for r in rows} == {"a", "b"}
    for r in rows:
        assert r["effective_state"] == "suspended"
        assert r["suspension"]["scope"] == "global"
    assert reader.scheduled_tasks_for_machine("anyhost", workspace="me", db_path=db) == []


def test_expiry_reactivates_on_read_with_no_write(db):
    """The core property: a lapsed deadline restores the task by being READ.

    Nothing rewrites the row -- no daemon, no cron entry. The suspension row is
    still there (it is the record of the lapse) but it no longer applies.
    """
    from gaia.store import writer, reader
    _register(db)
    writer.suspend_scheduled_tasks(name="gmail-triage", until=_past(minutes=5),
                                   workspace="me", db_path=db)

    row = reader.get_scheduled_task("gmail-triage", workspace="me", db_path=db)
    assert row["effective_state"] == "active", "a lapsed suspension must not hold"
    assert row["suspension"]["expired"] is True
    assert row["suspension"]["live"] is False
    assert row["suspension"]["lapsed_ago"] == "5m"
    # back in the set a sync would install, without anything having been written
    got = reader.scheduled_tasks_for_machine("anyhost", workspace="me", db_path=db)
    assert any(r["name"] == "gmail-triage" for r in got)
    # and the row survives as the record to announce / acknowledge
    susp = reader.list_schedule_suspensions(workspace="me", db_path=db)
    assert len(susp) == 1 and susp[0]["expired"] is True
    assert susp[0]["resumed_names"] == ["gmail-triage"]


def test_indefinite_suspension_never_expires(db):
    from gaia.store import writer, reader
    _register(db)
    writer.suspend_scheduled_tasks(name="gmail-triage", until=None,
                                   workspace="me", db_path=db)
    row = reader.get_scheduled_task("gmail-triage", workspace="me", db_path=db)
    assert row["effective_state"] == "suspended"
    assert row["suspension"]["indefinite"] is True
    assert row["suspension"]["expired"] is False
    assert row["suspension"]["remaining"] is None


def test_disabled_and_suspended_are_distinct_states(db):
    """`disable` and `suspend` must be readable apart, for a human AND a parser."""
    from gaia.store import writer, reader
    _register(db, name="perm")
    _register(db, name="temp")
    writer.set_scheduled_task_enabled("perm", False, workspace="me", db_path=db)
    writer.suspend_scheduled_tasks(name="temp", until=_future(hours=8),
                                   workspace="me", db_path=db)

    rows = {r["name"]: r for r in reader.list_scheduled_tasks(workspace="me", db_path=db)}
    assert rows["perm"]["effective_state"] == "disabled"
    assert rows["perm"]["enabled"] == 0
    assert rows["perm"]["suspension"] is None, "disabled carries no deadline"

    assert rows["temp"]["effective_state"] == "suspended"
    assert rows["temp"]["enabled"] == 1, "suspended is not disabled"
    assert rows["temp"]["suspension"]["until"] is not None, "suspended carries a deadline"


def test_disabled_dominates_a_lapsing_suspension(db):
    """A lapse must not claim to have resumed a task that is also disabled."""
    from gaia.store import writer, reader
    _register(db, name="off")
    _register(db, name="on")
    writer.set_scheduled_task_enabled("off", False, workspace="me", db_path=db)
    writer.suspend_scheduled_tasks(name=None, until=_past(minutes=1),
                                   workspace="me", db_path=db)

    rows = {r["name"]: r for r in reader.list_scheduled_tasks(workspace="me", db_path=db)}
    assert rows["off"]["effective_state"] == "disabled"
    assert rows["on"]["effective_state"] == "active"
    susp = reader.list_schedule_suspensions(workspace="me", db_path=db)[0]
    assert sorted(susp["covered_names"]) == ["off", "on"]
    assert susp["resumed_names"] == ["on"], "a disabled task did not come back"


def test_a_lapse_does_not_claim_a_task_another_suspension_still_holds(db):
    """"Active again" must be true, not merely "this deadline passed"."""
    from gaia.store import writer, reader
    _register(db, name="held")
    writer.suspend_scheduled_tasks(name="held", until=_past(minutes=2),
                                   workspace="me", db_path=db)
    writer.suspend_scheduled_tasks(name=None, until=None,
                                   workspace="me", db_path=db)

    row = reader.get_scheduled_task("held", workspace="me", db_path=db)
    assert row["effective_state"] == "suspended", "the global switch still holds it"

    lapsed = [s for s in reader.list_schedule_suspensions(workspace="me", db_path=db)
              if s["expired"]]
    assert len(lapsed) == 1
    assert lapsed[0]["covered_names"] == ["held"]
    assert lapsed[0]["resumed_names"] == [], (
        "the lapse must not announce a task that another live suspension holds"
    )


def test_task_scope_takes_precedence_over_global(db):
    from gaia.store import writer, reader
    _register(db)
    writer.suspend_scheduled_tasks(name=None, until=_future(hours=1),
                                   workspace="me", db_path=db)
    writer.suspend_scheduled_tasks(name="gmail-triage", until=_future(days=3),
                                   reason="mine", workspace="me", db_path=db)
    row = reader.get_scheduled_task("gmail-triage", workspace="me", db_path=db)
    assert row["suspension"]["scope"] == "task"
    assert row["suspension"]["reason"] == "mine"


def test_suspend_replaces_rather_than_stacks(db):
    from gaia.store import writer, reader
    _register(db)
    writer.suspend_scheduled_tasks(name="gmail-triage", until=_future(hours=1),
                                   workspace="me", db_path=db)
    writer.suspend_scheduled_tasks(name="gmail-triage", until=_future(days=2),
                                   workspace="me", db_path=db)
    rows = reader.list_schedule_suspensions(workspace="me", db_path=db)
    assert len(rows) == 1, "re-suspending extends the deadline, it does not stack rows"


def test_resume_lifts_live_and_acknowledges_lapsed(db):
    from gaia.store import writer, reader
    _register(db)

    writer.suspend_scheduled_tasks(name="gmail-triage", until=_future(hours=8),
                                   workspace="me", db_path=db)
    res = writer.resume_scheduled_tasks(name="gmail-triage", workspace="me", db_path=db)
    assert res["status"] == "ok" and res["was_expired"] is False
    assert reader.list_schedule_suspensions(workspace="me", db_path=db) == []

    writer.suspend_scheduled_tasks(name="gmail-triage", until=_past(hours=2),
                                   workspace="me", db_path=db)
    res = writer.resume_scheduled_tasks(name="gmail-triage", workspace="me", db_path=db)
    assert res["status"] == "ok" and res["was_expired"] is True, (
        "resuming a lapsed suspension is an acknowledgement, and must say so"
    )
    assert reader.list_schedule_suspensions(workspace="me", db_path=db) == []


def test_resume_distinguishes_no_suspension_from_no_task(db):
    from gaia.store import writer
    _register(db)
    assert writer.resume_scheduled_tasks(
        name="gmail-triage", workspace="me", db_path=db)["status"] == "not_suspended"
    assert writer.resume_scheduled_tasks(
        name="nope", workspace="me", db_path=db)["status"] == "not_found"


def test_suspend_unknown_task_is_not_found(db):
    from gaia.store import writer
    assert writer.suspend_scheduled_tasks(
        name="nope", until=None, workspace="me", db_path=db)["status"] == "not_found"


def test_remove_cascades_the_suspension_row(db):
    from gaia.store import writer, reader
    _register(db)
    writer.suspend_scheduled_tasks(name="gmail-triage", until=_future(hours=8),
                                   workspace="me", db_path=db)
    writer.delete_scheduled_task("gmail-triage", workspace="me", db_path=db)
    assert reader.list_schedule_suspensions(workspace="me", db_path=db) == []


def test_global_suspension_is_scoped_to_its_workspace(db):
    from gaia.store import writer, reader
    from gaia.store.writer import upsert_scheduled_task
    _register(db, name="mine")
    upsert_scheduled_task(name="theirs", schedule_spec=_SPEC,
                          workspace="other", db_path=db)
    writer.suspend_scheduled_tasks(name=None, until=_future(hours=8),
                                   workspace="me", db_path=db)
    rows = {r["name"]: r for r in reader.list_scheduled_tasks(db_path=db)}
    assert rows["mine"]["effective_state"] == "suspended"
    assert rows["theirs"]["effective_state"] == "active", (
        "one workspace's global switch must not reach another's tasks"
    )


# ---------------------------------------------------------------------------
# forward deadline parsing
# ---------------------------------------------------------------------------

def test_parse_deadline_reads_forward_where_parse_when_reads_back():
    from gaia.store.reader import parse_deadline, parse_when
    now = _iso(__import__("datetime").datetime.now(
        tz=__import__("datetime").timezone.utc))
    assert parse_deadline("8h") > now, "--for must point into the future"
    assert parse_when("8h") < now, "--since must point into the past"


def test_parse_deadline_accepts_durations_and_dates():
    from gaia.store.reader import parse_deadline, is_duration
    assert parse_deadline("2026-09-01") == "2026-09-01T00:00:00Z"
    assert parse_deadline("2026-09-01T18:00:00") == "2026-09-01T18:00:00Z"
    assert is_duration("90m") is True
    assert is_duration("2026-09-01") is False
    for bad in ("", "tomorrow", "8 fortnights"):
        with pytest.raises(ValueError):
            parse_deadline(bad)


def test_humanize_seconds_is_compact_and_two_units_max():
    from gaia.store.reader import humanize_seconds
    assert humanize_seconds(45) == "45s"
    assert humanize_seconds(3600) == "1h"
    assert humanize_seconds(3600 * 27 + 60 * 5) == "1d 3h"
    assert humanize_seconds(-10) == "0s"


# ---------------------------------------------------------------------------
# reconcile plan: suspended-but-installed is its own bucket
# ---------------------------------------------------------------------------

def test_reconcile_separates_suspended_from_disabled(db, monkeypatch):
    """`status` must say WHETHER a still-installed entry returns on its own."""
    from gaia.schedulers import reconcile
    from gaia.schedulers.cron import CronBackend
    from gaia.store import writer

    monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
    _register(db, name="perm")
    _register(db, name="temp")
    writer.set_scheduled_task_enabled("perm", False, workspace="me", db_path=db)
    writer.suspend_scheduled_tasks(name="temp", until=_future(hours=8),
                                   workspace="me", db_path=db)

    monkeypatch.setattr(CronBackend, "available", lambda self: True)
    monkeypatch.setattr(CronBackend, "managed_entries",
                        lambda self: {"perm": "20 9,13,17,21 * * *",
                                      "temp": "20 9,13,17,21 * * *"})
    monkeypatch.setattr(CronBackend, "ensure_daemon", lambda self: None)

    plan = reconcile.compute_plan(workspace="me")
    assert plan.disabled_present == ["perm"]
    assert [s["name"] for s in plan.suspended_present] == ["temp"]
    assert plan.suspended_present[0]["remaining"] is not None
    assert plan.in_sync is False


# ---------------------------------------------------------------------------
# SessionStart announcement: detect-only, lapse louder than live
# ---------------------------------------------------------------------------

def _manifest(monkeypatch, db):
    import importlib
    monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
    mod = importlib.import_module("hooks.modules.session.session_manifest")
    monkeypatch.setattr(mod, "_read_workspace_identity", lambda: "me")
    return mod


def test_session_block_is_silent_when_nothing_is_suspended(db, monkeypatch):
    mod = _manifest(monkeypatch, db)
    _register(db)
    assert mod.build_schedule_suspension_block() == ""


def test_session_block_announces_a_live_suspension_with_time_left(db, monkeypatch):
    from gaia.store import writer
    mod = _manifest(monkeypatch, db)
    _register(db)
    writer.suspend_scheduled_tasks(name=None, until=_future(hours=8),
                                   reason="debugging", workspace="me", db_path=db)
    out = mod.build_schedule_suspension_block()
    assert "## Scheduled Tasks (suspended)" in out
    assert "all tasks" in out
    assert "7h 59m more" in out, f"must state the time left. got:\n{out}"
    assert "debugging" in out
    assert "LAPSED" not in out


def test_session_block_announces_a_lapse_more_prominently(db, monkeypatch):
    """A lapse means something is running again -- it leads, and it is marked."""
    from gaia.store import writer
    mod = _manifest(monkeypatch, db)
    _register(db, name="a")
    _register(db, name="b")
    writer.suspend_scheduled_tasks(name="a", until=_past(minutes=30),
                                   workspace="me", db_path=db)
    writer.suspend_scheduled_tasks(name="b", until=_future(hours=5),
                                   workspace="me", db_path=db)
    out = mod.build_schedule_suspension_block()

    lapsed_at = out.index("SUSPENSION LAPSED")
    live_at = out.index("## Scheduled Tasks (suspended)")
    assert lapsed_at < live_at, f"the lapse must lead. got:\n{out}"
    assert "- ! a — suspension expired 30m ago" in out
    assert "active again: a" in out
    assert "gaia schedule resume" in out


def test_session_block_never_touches_the_scheduler(db, monkeypatch):
    """The hard constraint: detect and advise, never install/reactivate/sync."""
    from gaia.schedulers.cron import CronBackend
    from gaia.store import writer
    mod = _manifest(monkeypatch, db)
    _register(db)
    writer.suspend_scheduled_tasks(name="gmail-triage", until=_past(minutes=1),
                                   workspace="me", db_path=db)

    calls = []
    monkeypatch.setattr(CronBackend, "install",
                        lambda self, tasks: calls.append("install") or [])
    monkeypatch.setattr(CronBackend, "_write_crontab",
                        lambda *a, **k: calls.append("write"))

    out = mod.build_schedule_suspension_block()
    assert "LAPSED" in out
    assert calls == [], f"the hook must not write the scheduler, called: {calls}"


# ---------------------------------------------------------------------------
# The resume hint must match the suspension's SCOPE -- a task-scope
# suspension clears only by name, a global one only by `--all`. Printing the
# wrong form is not merely cosmetic: `resume <name>` on a global suspension
# returns `not_suspended` and leaves the notice standing (measured live).
# ---------------------------------------------------------------------------

def test_session_block_lapse_hint_matches_task_scope(db, monkeypatch):
    from gaia.store import writer
    mod = _manifest(monkeypatch, db)
    _register(db, name="a")
    writer.suspend_scheduled_tasks(name="a", until=_past(minutes=5),
                                   workspace="me", db_path=db)
    out = mod.build_schedule_suspension_block()
    assert "acknowledge: `gaia schedule resume a` (T0)" in out
    assert "gaia schedule resume --all" not in out


def test_session_block_lapse_hint_matches_global_scope(db, monkeypatch):
    from gaia.store import writer
    mod = _manifest(monkeypatch, db)
    _register(db, name="a")
    writer.suspend_scheduled_tasks(name=None, until=_past(minutes=5),
                                   workspace="me", db_path=db)
    out = mod.build_schedule_suspension_block()
    assert "acknowledge: `gaia schedule resume --all` (T0)" in out
    assert "gaia schedule resume a`" not in out


def test_session_block_live_suspension_hint_matches_scope(db, monkeypatch):
    """The not-yet-lapsed footer ("lift early") must be scope-correct too."""
    from gaia.store import writer
    mod = _manifest(monkeypatch, db)
    _register(db, name="a")
    writer.suspend_scheduled_tasks(name="a", until=_future(hours=1),
                                   workspace="me", db_path=db)
    out = mod.build_schedule_suspension_block()
    assert "lift early: `gaia schedule resume a` (T0)" in out
    assert "gaia schedule resume --all" not in out


def test_session_block_global_lapse_hint_actually_clears_it(db, monkeypatch, capsys):
    """The printed hint must work verbatim: it is the only channel that
    acknowledges a lapse, which does not self-clear."""
    from gaia.store import writer
    mod = _manifest(monkeypatch, db)
    _register(db, name="a")
    writer.suspend_scheduled_tasks(name=None, until=_past(minutes=5),
                                   workspace="me", db_path=db)
    assert "gaia schedule resume --all" in mod.build_schedule_suspension_block()

    monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
    cli, parser = _parser()
    args = parser.parse_args(["schedule", "resume", "--all",
                              "--workspace", "me", "--json"])
    assert cli.cmd_schedule(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"

    assert mod.build_schedule_suspension_block() == ""


# ---------------------------------------------------------------------------
# CLI surface for the two new verbs
# ---------------------------------------------------------------------------

def _parser():
    import argparse
    cli = _load_schedule_cli()
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="subcommand")
    cli.register(subs)
    return cli, parser


def test_cli_suspend_and_resume_roundtrip(db, monkeypatch, capsys):
    monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
    _register(db)
    cli, parser = _parser()

    args = parser.parse_args(["schedule", "suspend", "--all", "--for", "8h",
                              "--workspace", "me", "--json"])
    assert cli.cmd_schedule(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["scope"] == "global" and out["indefinite"] is False

    args = parser.parse_args(["schedule", "list", "--workspace", "me"])
    assert cli.cmd_schedule(args) == 0
    assert "suspended" in capsys.readouterr().out

    args = parser.parse_args(["schedule", "resume", "--all",
                              "--workspace", "me", "--json"])
    assert cli.cmd_schedule(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_cli_suspend_requires_an_explicit_scope(db, monkeypatch, capsys):
    """A bare `suspend` must not silently mean "everything"."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
    _register(db)
    cli, parser = _parser()

    args = parser.parse_args(["schedule", "suspend", "--for", "8h",
                              "--workspace", "me", "--json"])
    assert cli.cmd_schedule(args) == 1
    assert "--all" in capsys.readouterr().out

    args = parser.parse_args(["schedule", "suspend", "gmail-triage", "--all",
                              "--for", "8h", "--workspace", "me", "--json"])
    assert cli.cmd_schedule(args) == 1
    assert "not both" in capsys.readouterr().out


def test_cli_suspend_requires_exactly_one_deadline_form(db, monkeypatch, capsys):
    monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
    _register(db)
    cli, parser = _parser()

    args = parser.parse_args(["schedule", "suspend", "--all",
                              "--workspace", "me", "--json"])
    assert cli.cmd_schedule(args) == 1
    assert "exactly one" in capsys.readouterr().out

    # --for is durations, --until is dates; each rejects the other's shape so a
    # mistyped date cannot silently become a duration or vice versa.
    args = parser.parse_args(["schedule", "suspend", "--all", "--for", "2026-09-01",
                              "--workspace", "me", "--json"])
    assert cli.cmd_schedule(args) == 1
    assert "--until" in capsys.readouterr().out

    args = parser.parse_args(["schedule", "suspend", "--all", "--until", "8h",
                              "--workspace", "me", "--json"])
    assert cli.cmd_schedule(args) == 1
    assert "--for" in capsys.readouterr().out


def test_cli_suspend_rejects_a_past_until(db, monkeypatch, capsys):
    """The exact user-path regression: `--until` in the past must not suspend.

    Accepting it left the task ACTIVE (the opposite of what was asked) plus a
    lapsed-suspension notice at every session start -- a false alarm nobody
    could tell apart from a real one. The task must stay untouched by the
    rejected attempt.
    """
    monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
    _register(db)
    cli, parser = _parser()

    args = parser.parse_args(["schedule", "suspend", "gmail-triage", "--until", "2020-01-01",
                              "--workspace", "me", "--json"])
    assert cli.cmd_schedule(args) == 1
    out = capsys.readouterr().out
    assert "already passed" in out
    assert "--indefinitely" in out
    assert "disable" in out

    from gaia.store import reader
    row = reader.get_scheduled_task("gmail-triage", workspace="me", db_path=db)
    assert row["effective_state"] == "active"
    assert row["suspension"] is None, "a rejected suspend must write nothing"


def test_cli_suspend_past_until_alternatives_actually_work(db, monkeypatch, capsys):
    """The two alternatives the rejection message names must be reachable,
    not merely mentioned -- a routing message to a broken command is worse
    than no message."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
    _register(db)
    cli, parser = _parser()

    args = parser.parse_args(["schedule", "suspend", "gmail-triage", "--indefinitely",
                              "--workspace", "me", "--json"])
    assert cli.cmd_schedule(args) == 0
    capsys.readouterr()

    args = parser.parse_args(["schedule", "disable", "gmail-triage", "--workspace", "me"])
    assert cli.cmd_schedule(args) == 0
    capsys.readouterr()

    from gaia.store import reader
    row = reader.get_scheduled_task("gmail-triage", workspace="me", db_path=db)
    assert row["effective_state"] == "disabled"
    assert row["enabled"] == 0


def test_cli_suspend_for_already_rejects_a_negative_duration(db, monkeypatch, capsys):
    """`--for` always points forward: the duration parser only matches
    unsigned digits, so a negative duration falls through to the same
    wrong-shape rejection as any other malformed --for value -- no separate
    fix needed, but the guarantee is worth pinning down."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
    _register(db)
    cli, parser = _parser()

    args = parser.parse_args(["schedule", "suspend", "gmail-triage", "--for=-8h",
                              "--workspace", "me", "--json"])
    assert cli.cmd_schedule(args) == 1
    assert "--for takes a duration" in capsys.readouterr().out

    from gaia.store import reader
    row = reader.get_scheduled_task("gmail-triage", workspace="me", db_path=db)
    assert row["effective_state"] == "active"
    assert row["suspension"] is None


def test_cli_show_prints_state_and_suspension_apart(db, monkeypatch, capsys):
    from gaia.store import writer
    monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
    _register(db)
    writer.suspend_scheduled_tasks(name="gmail-triage", until=None,
                                   workspace="me", db_path=db)
    cli, parser = _parser()
    args = parser.parse_args(["schedule", "show", "gmail-triage", "--workspace", "me"])
    assert cli.cmd_schedule(args) == 0
    out = capsys.readouterr().out
    assert "state:         suspended" in out
    assert "enabled:       True" in out, "the permanent switch is untouched"
    assert "indefinitely" in out


# ---------------------------------------------------------------------------
# `list`/`show` (_describe_state) and `status` (_render_suspensions) print a
# resume hint for a lapsed suspension; it must match the suspension's scope --
# see the SessionStart-block tests above for why the wrong form is a real bug,
# not a style nit.
# ---------------------------------------------------------------------------

def test_cli_list_lapse_hint_matches_task_scope(db, monkeypatch, capsys):
    from gaia.store import writer
    monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
    _register(db, name="task-only")
    writer.suspend_scheduled_tasks(name="task-only", until=_past(minutes=1),
                                   workspace="me", db_path=db)
    cli, parser = _parser()
    args = parser.parse_args(["schedule", "list", "--workspace", "me"])
    assert cli.cmd_schedule(args) == 0
    out = capsys.readouterr().out
    assert "clear with `gaia schedule resume task-only`" in out


def test_cli_list_lapse_hint_matches_global_scope(db, monkeypatch, capsys):
    from gaia.store import writer
    monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
    _register(db, name="task-only")
    writer.suspend_scheduled_tasks(name=None, until=_past(minutes=1),
                                   workspace="me", db_path=db)
    cli, parser = _parser()
    args = parser.parse_args(["schedule", "list", "--workspace", "me"])
    assert cli.cmd_schedule(args) == 0
    out = capsys.readouterr().out
    assert "clear with `gaia schedule resume --all`" in out
    assert "clear with `gaia schedule resume task-only`" not in out


def test_cli_status_lapse_hint_matches_task_scope(db, monkeypatch, capsys):
    from gaia.store import writer
    monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
    _register(db, name="task-only")
    writer.suspend_scheduled_tasks(name="task-only", until=_past(minutes=1),
                                   workspace="me", db_path=db)
    cli, parser = _parser()
    args = parser.parse_args(["schedule", "status", "--workspace", "me"])
    assert cli.cmd_schedule(args) == 0
    out = capsys.readouterr().out
    assert "acknowledge: `gaia schedule resume task-only` (T0)" in out


def test_cli_status_lapse_hint_matches_global_scope(db, monkeypatch, capsys):
    from gaia.store import writer
    monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
    _register(db, name="task-only")
    writer.suspend_scheduled_tasks(name=None, until=_past(minutes=1),
                                   workspace="me", db_path=db)
    cli, parser = _parser()
    args = parser.parse_args(["schedule", "status", "--workspace", "me"])
    assert cli.cmd_schedule(args) == 0
    out = capsys.readouterr().out
    assert "acknowledge: `gaia schedule resume --all` (T0)" in out
    assert "acknowledge: `gaia schedule resume task-only` (T0)" not in out


# ---------------------------------------------------------------------------
# the [id] `list` prints must resolve as an identifier in every verb that
# operates on one task, and a workspace-scoped diagnosis must never claim a
# task does not exist when it exists in a sibling workspace
# ---------------------------------------------------------------------------

def _register_in(db, name, workspace):
    """Register a task under an explicit workspace, for the cross-workspace
    tests below -- `_register()` above hardcodes workspace='me', so a second
    workspace is registered through the writer directly instead."""
    from gaia.store import writer
    return writer.upsert_scheduled_task(
        name=name, schedule_spec=_SPEC, schedule_hint="20 9,13,17,21",
        project_dir="/home/jorge/ws/me", workspace=workspace, db_path=db,
    )


def test_cli_show_resolves_the_bracketed_id_across_workspaces(db, monkeypatch, capsys):
    """The exact user-path regression: `list` prints `[id]`, but every verb
    used to resolve by NAME only -- `show <id>` (and `remove <id>`) failed with
    "no scheduled task named '<id>'" even though the task existed. Ids are a
    single global sequence, so resolution must not be limited to the caller's
    own --workspace."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
    _register_in(db, "task-in-me", "me")
    task_id = _register_in(db, "task-in-other", "other")
    cli, parser = _parser()

    args = parser.parse_args(["schedule", "show", str(task_id), "--workspace", "me"])
    assert cli.cmd_schedule(args) == 0
    out = capsys.readouterr().out
    assert f"resolved id {task_id} to task 'task-in-other' in workspace 'other'" in out
    assert "# Scheduled task #" in out and "task-in-other" in out


def test_cli_not_found_names_the_sibling_workspace_instead_of_claiming_nonexistence(
        db, monkeypatch, capsys):
    """The second aggravating factor from the measured incident: a bare
    "not found" gave no signal that the task lived in a workspace the caller
    simply was not looking at, which read as "it no longer exists"."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
    _register_in(db, "task-in-other", "other")
    cli, parser = _parser()

    args = parser.parse_args(["schedule", "show", "task-in-other", "--workspace", "me", "--json"])
    assert cli.cmd_schedule(args) == 1
    out = capsys.readouterr().out
    assert "in workspace 'me'" in out
    assert "it exists in: 'other'" in out
    assert "--workspace=<workspace>" in out


def test_cli_not_found_numeric_ref_gets_its_own_wording(db, monkeypatch, capsys):
    """A purely numeric ref that matches no real id is a different mistake
    than a mistyped name, and the message says so plus how to check."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
    _register(db)
    cli, parser = _parser()

    args = parser.parse_args(["schedule", "show", "999", "--workspace", "me", "--json"])
    assert cli.cmd_schedule(args) == 1
    out = capsys.readouterr().out
    assert "no scheduled task with id 999" in out
    assert "checked every workspace" in out
    assert "gaia schedule list --all-workspaces" in out


def test_cli_disable_resolves_id_across_workspaces_and_actually_mutates(db, monkeypatch, capsys):
    """The fix is not read-only: a mutating T0 verb (`disable`) must resolve
    and act on the task the id names, not on the caller's own workspace."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
    task_id = _register_in(db, "task-in-other", "other")
    cli, parser = _parser()

    args = parser.parse_args(["schedule", "disable", str(task_id), "--workspace", "me"])
    assert cli.cmd_schedule(args) == 0

    from gaia.store import reader
    row = reader.get_scheduled_task("task-in-other", workspace="other", db_path=db)
    assert row["enabled"] == 0, "disable must have reached the resolved task, not workspace 'me'"


def test_cli_remove_resolves_the_bracketed_id_across_workspaces(db, monkeypatch, capsys):
    """`remove` is the exact verb from the live incident report. Exercised
    in-process (calling the CLI dispatcher directly, the same convention every
    other CLI test in this file uses, including `test_delete`'s direct writer
    call for this same T3 verb) rather than as a live subprocess, which is
    where the tier gate actually lives."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
    task_id = _register_in(db, "task-in-other", "other")
    cli, parser = _parser()

    args = parser.parse_args(["schedule", "remove", str(task_id), "--workspace", "me"])
    assert cli.cmd_schedule(args) == 0
    out = capsys.readouterr().out
    assert "task-in-other" in out

    from gaia.store import reader
    assert reader.get_scheduled_task("task-in-other", workspace="other", db_path=db) is None


def test_cli_list_flags_tasks_hidden_in_other_workspaces(db, monkeypatch, capsys):
    """A workspace-scoped `list` must not stay silent about tasks it is not
    showing -- that silence is what read as "there is nothing else" and
    produced a false not-found diagnosis downstream."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
    _register_in(db, "task-in-me", "me")
    _register_in(db, "task-in-other", "other")
    cli, parser = _parser()

    args = parser.parse_args(["schedule", "list", "--workspace", "me"])
    assert cli.cmd_schedule(args) == 0
    out = capsys.readouterr().out
    assert "task-in-me" in out
    assert "task-in-other" not in out, "still workspace-scoped by default"
    assert "also exist in other workspaces" in out
    assert "gaia schedule list --all-workspaces" in out

    args = parser.parse_args(["schedule", "list", "--all-workspaces"])
    assert cli.cmd_schedule(args) == 0
    out = capsys.readouterr().out
    assert "task-in-me" in out and "task-in-other" in out
    assert "also exist in other workspaces" not in out, "nothing is hidden once --all-workspaces is used"


def test_cli_list_json_stays_a_bare_array(db, monkeypatch, capsys):
    """The hidden-elsewhere notice is for the human-readable path only --
    `--json` keeps the bare-array shape every other Gaia `list --json` uses
    (notifications, evidence, task, plan, brief)."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
    _register_in(db, "task-in-me", "me")
    _register_in(db, "task-in-other", "other")
    cli, parser = _parser()

    args = parser.parse_args(["schedule", "list", "--workspace", "me", "--json"])
    assert cli.cmd_schedule(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert isinstance(out, list)
    assert [r["name"] for r in out] == ["task-in-me"]


# ---------------------------------------------------------------------------
# migration: an already-registered task survives v46 -> v47
# ---------------------------------------------------------------------------

def test_v46_to_v47_preserves_an_existing_registered_task(db, tmp_path):
    """Drive the REAL bootstrap over a DB rolled back to v46 with a live row.

    This is the migration guarantee stated as a test rather than as reasoning:
    a task registered and DISABLED before the suspension feature existed must
    still be valid and legible afterwards, with its schedule, its prompt paths
    and its disabled state unchanged.
    """
    import sqlite3
    import subprocess
    from gaia.store import writer, reader

    _register(db)
    writer.set_scheduled_task_enabled("gmail-triage", False, workspace="me", db_path=db)
    before = reader.get_scheduled_task("gmail-triage", workspace="me", db_path=db)

    # Roll the DB back to the pre-feature shape.
    con = sqlite3.connect(str(db))
    try:
        con.execute("DROP INDEX IF EXISTS idx_schedule_suspensions_global")
        con.execute("DROP INDEX IF EXISTS idx_schedule_suspensions_task")
        con.execute("DROP TABLE IF EXISTS schedule_suspensions")
        con.execute("DELETE FROM schema_version WHERE version > 46")
        con.commit()
        assert con.execute(
            "SELECT MAX(version) FROM schema_version").fetchone()[0] == 46
    finally:
        con.close()

    env = dict(os.environ, GAIA_DB=str(db), WORKSPACE=str(tmp_path))
    proc = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / "bootstrap_database.py")],
        env=env, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, f"bootstrap failed:\n{proc.stdout}\n{proc.stderr}"

    con = sqlite3.connect(str(db))
    try:
        assert con.execute(
            "SELECT MAX(version) FROM schema_version").fetchone()[0] >= 47
        assert con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='schedule_suspensions'").fetchone()
    finally:
        con.close()

    after = reader.get_scheduled_task("gmail-triage", workspace="me", db_path=db)
    assert after is not None, "the registered task must survive the migration"
    for field in ("id", "name", "workspace", "schedule_spec", "schedule_hint",
                  "enabled", "machine_scope", "project_dir", "created_at"):
        assert after[field] == before[field], f"{field} changed across the migration"
    assert after["spec"] == _SPEC
    assert after["effective_state"] == "disabled"
    assert after["suspension"] is None
    assert reader.list_schedule_suspensions(workspace="me", db_path=db) == []


def test_looks_adopted_skips_comment_lines():
    """A comment is never a crontab entry, even one that mentions the task name
    plus a .sh / claude / scheduled-task signature -- so it is never dropped as
    an 'adopted legacy line'."""
    from gaia.schedulers.cron import CronBackend
    b = CronBackend()
    assert b._looks_adopted(
        "# gmail-triage headless scheduled task -- runs at 09:20, 13:20", "gmail-triage"
    ) is False
    assert b._looks_adopted(
        "# runs claude for gmail-triage via run-scheduled-task.sh", "gmail-triage"
    ) is False
    assert b._looks_adopted("", "gmail-triage") is False
