"""Tests for the scheduled-task desired-state registry.

Covers the store writers/readers (against a bootstrapped v30 DB), the neutral
schedule_spec validation + cron translation, the cron-string parser used by the
`gaia schedule register --cron` path, and marker/adoption parsing. It does NOT
exercise crontab install/remove (those mutate the real user crontab and are T3);
that surface is covered by the tier-classification tests and left to manual
verification.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture()
def db(tmp_path):
    """Bootstrap a fresh DB (applies the v30 migration) and return its path."""
    bootstrap = _REPO_ROOT / "scripts" / "bootstrap_database.sh"
    db_path = tmp_path / "gaia.db"
    env = os.environ.copy()
    env["GAIA_DB"] = str(db_path)
    env["WORKSPACE"] = str(tmp_path)
    res = subprocess.run(
        ["bash", str(bootstrap)],
        env=env, capture_output=True, text=True, check=False, timeout=90,
    )
    assert res.returncode == 0, f"bootstrap failed:\n{res.stdout}\n{res.stderr}"
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
