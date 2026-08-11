"""The auditable event channel a manual task-close override writes through.

Matchable by ``pytest tests/ -k close_override_event_channel -q``.

Two surfaces under test, one channel:

  * ``gaia.state.task_closure_event`` -- the pure shape of the record (no DB,
    no env, no I/O).
  * ``gaia.store.writer.write_task_close_override_event`` -- the append that
    puts it in the substrate, exercised against a real, disposable sqlite DB
    (``GAIA_DATA_DIR`` -> ``tmp_path``, the convention in
    tests/cli/test_gate_status_write.py and tests/test_derived_closure_predicate.py).

The round trip is the centre of it: a record is written and then recovered
through the EXISTING readers -- ``cross_surface_query`` on the
``harness_events`` surface and ``read_defects`` -- with who, when and why intact.
Both readers are exercised, plus the actual ``gaia defects`` command handler, so
what is asserted is the operator-visible surface and not only the function
beneath it.

What is asserted goes past the cases the channel's contract enumerates, because
the properties it has to hold are stronger:

  * VISIBILITY is asserted against the reader's OWN inclusion criterion
    (``NON_DEFECT_EVENT_SEVERITIES``) rather than against the literal string
    'info', and the negative case is built too: the same record graded 'info'
    must vanish from the defects report while the real one stays. Without that
    falsifier a visibility assertion cannot fail for the reason it exists.
  * NO MIGRATION is asserted structurally, not just by reading a version number:
    every field the channel writes must already have a column in the live
    ``harness_events`` (checked against whatever schema is on disk, not a
    pinned version), the table set must be identical before and after an
    emission, and the sources this channel adds must contain no DDL.
  * THE RECORD CANNOT BE SUPPRESSED. A reason that states nothing appends
    nothing (and the substrate is checked, not just the exception); a failing
    append raises instead of passing silently; an unresolvable task still
    records.
  * THE PAYLOAD CANNOT BE SHADOWED. Caller-supplied context cannot overwrite the
    actor or the reason, which is why it is nested rather than merged.
  * NOTHING WITH A COLUMN IS DUPLICATED into the payload -- one fact, one place.
"""

from __future__ import annotations

import argparse
import inspect
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from gaia.state.task_closure_event import (  # noqa: E402
    DETAILS_PAYLOAD_KEY,
    HUMAN_ACTOR,
    MISSING_REASON_MESSAGE,
    TASK_CLOSE_OVERRIDE_EVENT,
    TASK_CLOSE_OVERRIDE_SEVERITY,
    TASK_CLOSE_OVERRIDE_SOURCE,
    build_override_event,
    normalize_reason,
    resolve_actor,
)
from gaia.store.reader import (  # noqa: E402
    NON_DEFECT_EVENT_SEVERITIES,
    cross_surface_query,
    read_defects,
)

_BRIEF = "close-override-channel-brief"
_WORKSPACE = "me"
_ORDER = 1
_REASON = "closed by hand: the gate's suite cannot run on this machine"


# ---------------------------------------------------------------------------
# Pure shape
# ---------------------------------------------------------------------------

def test_event_type_is_the_exact_string_consumers_filter_on():
    # Consumers name this string on the command line (`gaia query --type=...`,
    # `gaia defects --type=...`), so it is part of the substrate's vocabulary:
    # changing it orphans every record already written under the old name.
    assert TASK_CLOSE_OVERRIDE_EVENT == "task.close_override"
    assert build_override_event(
        brief_name=_BRIEF, task_order_num=_ORDER, reason=_REASON
    ).event_type == TASK_CLOSE_OVERRIDE_EVENT


def test_severity_is_above_info_by_the_readers_own_inclusion_criterion():
    # Asserted against the reader's constant, not against the literal 'info':
    # read_defects admits an orchestrator row by severity, so if that vocabulary
    # ever grows a new non-defect grade, this is where the channel's choice has
    # to be re-decided rather than silently going invisible.
    assert TASK_CLOSE_OVERRIDE_SEVERITY not in NON_DEFECT_EVENT_SEVERITIES

    event = build_override_event(
        brief_name=_BRIEF, task_order_num=_ORDER, reason=_REASON
    )
    assert event.severity == TASK_CLOSE_OVERRIDE_SEVERITY
    assert event.severity not in NON_DEFECT_EVENT_SEVERITIES


def test_source_distinguishes_this_record_from_hook_written_telemetry():
    event = build_override_event(
        brief_name=_BRIEF, task_order_num=_ORDER, reason=_REASON
    )
    assert event.source == TASK_CLOSE_OVERRIDE_SOURCE == "cli"


@pytest.mark.parametrize(
    "reason",
    [None, "", "   ", "\n", "\t  \n ", 42, 0, [], {}, b"why", object()],
)
def test_a_reason_that_states_nothing_is_rejected(reason):
    with pytest.raises(ValueError) as exc:
        normalize_reason(reason)
    assert str(exc.value) == MISSING_REASON_MESSAGE

    with pytest.raises(ValueError) as build_exc:
        build_override_event(
            brief_name=_BRIEF, task_order_num=_ORDER, reason=reason
        )
    assert str(build_exc.value) == MISSING_REASON_MESSAGE


def test_a_reason_is_recorded_stripped_but_otherwise_verbatim():
    reason = "  the gate's runner is offline; closing under protest  "

    assert normalize_reason(reason) == reason.strip()

    event = build_override_event(
        brief_name=_BRIEF, task_order_num=_ORDER, reason=reason
    )
    assert event.meta["reason"] == reason.strip()


@pytest.mark.parametrize(
    "raw", [None, "", "   ", "\n", 42, [], object()],
)
def test_an_absent_dispatch_identity_is_recorded_as_a_human_caller(raw):
    # Absent is not unknown: gaia.state.permissions reads an unset
    # GAIA_DISPATCH_AGENT as a human CLI caller, and a blank agent column would
    # read as "not recorded" for a fact that is in fact known.
    assert resolve_actor(raw) == HUMAN_ACTOR

    event = build_override_event(
        brief_name=_BRIEF, task_order_num=_ORDER, reason=_REASON, actor=raw
    )
    assert event.agent == HUMAN_ACTOR
    assert event.meta["actor"] == HUMAN_ACTOR


def test_a_dispatch_identity_is_recorded_as_the_actor():
    assert resolve_actor("  developer  ") == "developer"

    event = build_override_event(
        brief_name=_BRIEF, task_order_num=_ORDER, reason=_REASON,
        actor="gaia-system",
    )
    assert event.agent == "gaia-system"
    assert event.meta["actor"] == "gaia-system"


def test_the_actor_lands_in_the_agent_column_not_only_in_the_payload():
    # The agent column is what read_defects filters and renders; the payload is
    # opaque to SQL. An actor recorded only inside the JSON is unfilterable.
    event = build_override_event(
        brief_name=_BRIEF, task_order_num=_ORDER, reason=_REASON,
        actor="gaia-system",
    )
    assert event.as_write_kwargs()["agent"] == "gaia-system"


def test_caller_supplied_details_cannot_shadow_the_actor_or_the_reason():
    event = build_override_event(
        brief_name=_BRIEF,
        task_order_num=_ORDER,
        reason=_REASON,
        actor="gaia-system",
        details={"actor": "someone-else", "reason": "a different story",
                 "outstanding_gates": [129, 130]},
    )

    assert event.meta["actor"] == "gaia-system"
    assert event.meta["reason"] == _REASON
    assert event.meta[DETAILS_PAYLOAD_KEY] == {
        "actor": "someone-else",
        "reason": "a different story",
        "outstanding_gates": [129, 130],
    }


def test_details_are_omitted_entirely_when_the_caller_supplies_none():
    for details in (None, {}):
        event = build_override_event(
            brief_name=_BRIEF, task_order_num=_ORDER, reason=_REASON,
            details=details,
        )
        assert DETAILS_PAYLOAD_KEY not in event.meta


def test_the_payload_never_duplicates_a_value_that_has_its_own_column():
    # One fact, one place: ts / workspace / agent are columns, so a payload copy
    # would be a second value with nothing keeping the two equal.
    event = build_override_event(
        brief_name=_BRIEF, task_order_num=_ORDER, reason=_REASON,
        actor="gaia-system",
    )

    for column_name in ("ts", "timestamp", "when", "workspace", "severity",
                        "type", "source", "result"):
        assert column_name not in event.meta


def test_the_summary_line_leads_with_the_actor_and_carries_the_reason():
    # Both readers truncate this line, so what a triage listing shows first has
    # to be the two things it exists to say.
    event = build_override_event(
        brief_name=_BRIEF, task_order_num=_ORDER, reason=_REASON,
        actor="gaia-system",
    )

    assert event.result.startswith("manual close override by gaia-system")
    assert _REASON in event.result
    assert _BRIEF in event.result
    assert str(_ORDER) in event.result


def test_the_record_is_frozen_once_built():
    event = build_override_event(
        brief_name=_BRIEF, task_order_num=_ORDER, reason=_REASON
    )
    with pytest.raises(Exception):
        event.severity = "info"  # type: ignore[misc]


def test_write_kwargs_are_a_copy_so_the_record_cannot_be_edited_through_them():
    event = build_override_event(
        brief_name=_BRIEF, task_order_num=_ORDER, reason=_REASON
    )
    kwargs = event.as_write_kwargs()
    kwargs["meta"]["reason"] = "rewritten"

    assert event.meta["reason"] == _REASON


def test_the_record_shape_cannot_drift_from_the_writer_it_feeds():
    # A property, not a case: every key the record hands over must be a real
    # parameter of write_harness_event, so renaming one there fails here instead
    # of at the first override in production.
    from gaia.store.writer import write_harness_event

    accepted = set(inspect.signature(write_harness_event).parameters)
    produced = set(
        build_override_event(
            brief_name=_BRIEF, task_order_num=_ORDER, reason=_REASON
        ).as_write_kwargs()
    )

    assert produced <= accepted
    assert {"workspace", "db_path"} <= accepted - produced


# ---------------------------------------------------------------------------
# DB append seam -- isolated substrate per test
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Route the substrate DB into ``tmp_path``."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


def _seed_task(tmp_db: Path, brief: str = _BRIEF, order_num: int = _ORDER) -> None:
    """Seed workspace 'me' -> brief -> plan -> one pending task."""
    from gaia.briefs import upsert_brief
    from gaia.store.writer import add_task_to_plan, upsert_plan

    upsert_brief(_WORKSPACE, brief, {"status": "open", "title": brief},
                 db_path=tmp_db)
    upsert_plan(_WORKSPACE, brief, content="plan body", status="active",
                db_path=tmp_db)
    add_task_to_plan(_WORKSPACE, brief, order_num, "close this task by hand",
                     db_path=tmp_db)


def _table_names(db_path: Path) -> set[str]:
    con = sqlite3.connect(str(db_path))
    try:
        return {
            row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        con.close()


def _event_rows(db_path: Path) -> list[sqlite3.Row]:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT workspace, ts, type, source, agent, result, severity, payload "
            "FROM harness_events ORDER BY id"
        ).fetchall()
    finally:
        con.close()


def _task_statuses(db_path: Path) -> list[tuple]:
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(
            "SELECT id, order_num, status FROM tasks ORDER BY id"
        ).fetchall()
    finally:
        con.close()


def _emit(tmp_db: Path, **overrides):
    from gaia.store.writer import write_task_close_override_event

    kwargs = {
        "reason": _REASON,
        "db_path": tmp_db,
    }
    kwargs.update(overrides)
    return write_task_close_override_event(
        _WORKSPACE, _BRIEF, _ORDER, **kwargs
    )


# --- the round trip through the existing readers -----------------------------

def test_the_record_round_trips_through_the_existing_event_reader(tmp_db):
    _seed_task(tmp_db)

    row_id = _emit(tmp_db, actor="gaia-system")
    assert isinstance(row_id, int) and row_id > 0

    found = cross_surface_query(
        surface="harness_events",
        type=TASK_CLOSE_OVERRIDE_EVENT,
        workspace=_WORKSPACE,
        db_path=tmp_db,
    )

    assert len(found) == 1
    row = found[0]

    # WHO -- the agent column, so it is filterable and not merely present.
    assert row["agent"] == "gaia-system"
    # WHEN -- the ts column the append stamps, surfaced at the top level.
    assert row["timestamp"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?", row["timestamp"])
    # WHY -- legible in the payload.
    payload = json.loads(row["raw"]["payload"])
    assert payload["reason"] == _REASON
    assert payload["actor"] == "gaia-system"
    assert payload["brief_name"] == _BRIEF
    assert payload["task_order_num"] == _ORDER


def test_the_reader_can_isolate_this_record_by_type_among_other_events(tmp_db):
    _seed_task(tmp_db)
    from gaia.store.writer import write_harness_event

    write_harness_event(event_type="agent.dispatch", source="hook",
                        agent="developer", result="dispatched",
                        workspace=_WORKSPACE, db_path=tmp_db)
    _emit(tmp_db, actor="gaia-system")
    write_harness_event(event_type="session.end", source="hook", agent="",
                        result="session ended", workspace=_WORKSPACE,
                        db_path=tmp_db)

    found = cross_surface_query(
        surface="harness_events",
        type=TASK_CLOSE_OVERRIDE_EVENT,
        workspace=_WORKSPACE,
        db_path=tmp_db,
    )

    assert [r["type"] for r in found] == [TASK_CLOSE_OVERRIDE_EVENT]


def test_the_dispatch_identity_becomes_the_actor_when_no_actor_is_passed(
    tmp_db, monkeypatch
):
    _seed_task(tmp_db)
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")

    _emit(tmp_db)

    rows = _event_rows(tmp_db)
    assert [r["agent"] for r in rows] == ["developer"]
    assert json.loads(rows[0]["payload"])["actor"] == "developer"


def test_a_human_cli_caller_is_recorded_when_no_dispatch_identity_is_set(
    tmp_db, monkeypatch
):
    _seed_task(tmp_db)
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)

    _emit(tmp_db)

    rows = _event_rows(tmp_db)
    assert [r["agent"] for r in rows] == [HUMAN_ACTOR]


def test_an_explicit_actor_wins_over_the_ambient_dispatch_identity(
    tmp_db, monkeypatch
):
    _seed_task(tmp_db)
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "ambient-agent")

    _emit(tmp_db, actor="explicit-agent")

    assert [r["agent"] for r in _event_rows(tmp_db)] == ["explicit-agent"]


def test_the_workspace_column_is_set_so_a_scoped_read_finds_the_record(tmp_db):
    _seed_task(tmp_db)

    _emit(tmp_db)

    assert [r["workspace"] for r in _event_rows(tmp_db)] == [_WORKSPACE]
    assert cross_surface_query(
        surface="harness_events", type=TASK_CLOSE_OVERRIDE_EVENT,
        workspace=_WORKSPACE, db_path=tmp_db,
    )


# --- the channel is append-only and writes no task state ---------------------

def test_the_append_writes_no_task_state(tmp_db):
    _seed_task(tmp_db)
    before = _task_statuses(tmp_db)

    _emit(tmp_db)

    assert _task_statuses(tmp_db) == before
    assert [status for _, _, status in _task_statuses(tmp_db)] == ["pending"]


def test_two_overrides_on_the_same_task_both_persist(tmp_db):
    _seed_task(tmp_db)

    first = _emit(tmp_db, reason="first attempt: runner offline")
    second = _emit(tmp_db, reason="second attempt: runner still offline")

    assert first != second
    rows = _event_rows(tmp_db)
    assert len(rows) == 2
    assert [json.loads(r["payload"])["reason"] for r in rows] == [
        "first attempt: runner offline",
        "second attempt: runner still offline",
    ]


def test_a_rejected_reason_appends_nothing_at_all(tmp_db):
    _seed_task(tmp_db)
    # Force the substrate into existence first, so "no rows" is a real
    # observation about an existing table rather than an absent DB.
    _event_rows(tmp_db)

    with pytest.raises(ValueError) as exc:
        _emit(tmp_db, reason="   ")

    assert str(exc.value) == MISSING_REASON_MESSAGE
    assert _event_rows(tmp_db) == []


def test_a_failing_append_raises_instead_of_passing_silently(tmp_path):
    # The hook pipeline swallows event-write failures because telemetry must not
    # break a hook. This record is the justification for a state change, so a
    # failure to write it has to surface -- an override that quietly recorded
    # nothing is exactly the silence the channel exists to remove.
    from gaia.store.writer import write_task_close_override_event

    blocker = tmp_path / "not-a-directory"
    blocker.write_text("regular file", encoding="utf-8")

    with pytest.raises(OSError):
        write_task_close_override_event(
            _WORKSPACE, _BRIEF, _ORDER,
            reason=_REASON,
            db_path=blocker / "gaia.db",
        )


def test_the_record_survives_a_task_that_cannot_be_resolved(tmp_db):
    # Deliberate: harness_events holds no foreign key to tasks, and making the
    # append depend on a lookup would let a resolution failure suppress the
    # record of a mutation that already happened.
    from gaia.store.writer import write_task_close_override_event

    row_id = write_task_close_override_event(
        _WORKSPACE, "a-brief-that-was-never-created", 99,
        reason=_REASON, db_path=tmp_db,
    )

    assert row_id > 0
    payload = json.loads(_event_rows(tmp_db)[0]["payload"])
    assert payload["brief_name"] == "a-brief-that-was-never-created"
    assert payload["task_order_num"] == 99


def test_an_optional_task_id_is_recorded_when_the_caller_resolved_it(tmp_db):
    _seed_task(tmp_db)

    _emit(tmp_db, task_id=4242)

    assert json.loads(_event_rows(tmp_db)[0]["payload"])["task_id"] == 4242


def test_no_task_id_key_is_written_when_the_caller_has_none(tmp_db):
    _seed_task(tmp_db)

    _emit(tmp_db)

    assert "task_id" not in json.loads(_event_rows(tmp_db)[0]["payload"])


# --- visibility in the defects report ----------------------------------------

def test_the_record_appears_in_the_defects_report(tmp_db):
    _seed_task(tmp_db)

    _emit(tmp_db, actor="gaia-system")

    rows = read_defects(origin="orchestrator", workspace=_WORKSPACE,
                        db_path=tmp_db)

    assert len(rows) == 1
    row = rows[0]
    assert row["type"] == TASK_CLOSE_OVERRIDE_EVENT
    assert row["origin"] == "orchestrator"
    assert row["severity"] == TASK_CLOSE_OVERRIDE_SEVERITY
    assert row["severity"] not in NON_DEFECT_EVENT_SEVERITIES
    assert row["agent"] == "gaia-system"
    assert _REASON in row["message"]
    assert row["timestamp"]


def test_the_record_appears_in_the_defects_report_with_no_origin_filter(tmp_db):
    _seed_task(tmp_db)

    _emit(tmp_db)

    types = [
        r["type"] for r in read_defects(origin="all", workspace=_WORKSPACE,
                                        db_path=tmp_db)
    ]
    assert TASK_CLOSE_OVERRIDE_EVENT in types


def test_the_record_is_filterable_in_the_defects_report_by_type_and_agent(tmp_db):
    _seed_task(tmp_db)

    _emit(tmp_db, actor="gaia-system")

    by_type = read_defects(type=TASK_CLOSE_OVERRIDE_EVENT, workspace=_WORKSPACE,
                           db_path=tmp_db)
    by_agent = read_defects(agent="gaia-system", workspace=_WORKSPACE,
                            db_path=tmp_db)
    by_severity = read_defects(severity=TASK_CLOSE_OVERRIDE_SEVERITY,
                               workspace=_WORKSPACE, db_path=tmp_db)
    by_other_agent = read_defects(agent="somebody-else", workspace=_WORKSPACE,
                                  db_path=tmp_db)

    assert [r["type"] for r in by_type] == [TASK_CLOSE_OVERRIDE_EVENT]
    assert [r["type"] for r in by_agent] == [TASK_CLOSE_OVERRIDE_EVENT]
    assert [r["type"] for r in by_severity] == [TASK_CLOSE_OVERRIDE_EVENT]
    assert by_other_agent == []


def test_an_info_graded_record_would_not_reach_the_defects_report(tmp_db):
    # The falsifier for the visibility decision. The same record differing ONLY
    # in severity is invisible here while remaining findable by a type-filtered
    # query -- which is exactly why grading it 'info' would pass every other
    # assertion in this module and still defeat the channel's purpose.
    _seed_task(tmp_db)
    from gaia.store.writer import write_harness_event

    graded_info = build_override_event(
        brief_name=_BRIEF, task_order_num=_ORDER,
        reason="graded info on purpose", actor="gaia-system",
    ).as_write_kwargs()
    graded_info["severity"] = "info"
    write_harness_event(workspace=_WORKSPACE, db_path=tmp_db, **graded_info)

    _emit(tmp_db, actor="gaia-system", reason=_REASON)

    persisted = cross_surface_query(
        surface="harness_events", type=TASK_CLOSE_OVERRIDE_EVENT,
        workspace=_WORKSPACE, db_path=tmp_db,
    )
    defects = read_defects(origin="orchestrator", workspace=_WORKSPACE,
                           db_path=tmp_db)

    assert len(persisted) == 2
    assert len(defects) == 1
    assert _REASON in defects[0]["message"]
    assert "graded info on purpose" not in defects[0]["message"]


def test_the_record_appears_in_the_gaia_defects_command_output(tmp_db):
    # The command handler, not only the reader beneath it: this is the surface an
    # operator actually reads, and it resolves its own workspace and DB path.
    _seed_task(tmp_db)
    from cli.defects import cmd_defects

    _emit(tmp_db, actor="gaia-system")

    args = argparse.Namespace(
        origin="orchestrator", type=None, severity=None, agent=None,
        since=None, until=None, limit=20, workspace=_WORKSPACE,
        count=False, json=True,
    )
    import io
    import contextlib

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exit_code = cmd_defects(args)

    assert exit_code == 0
    rows = json.loads(buffer.getvalue())
    assert [r["type"] for r in rows] == [TASK_CLOSE_OVERRIDE_EVENT]
    assert rows[0]["severity"] == TASK_CLOSE_OVERRIDE_SEVERITY
    assert rows[0]["agent"] == "gaia-system"
    assert _REASON in rows[0]["message"]


# --- no new table, no migration ----------------------------------------------

def test_every_field_the_channel_writes_already_has_a_live_schema_column(tmp_db):
    # The structural content of "no migration needed": the record needs no
    # column that the live harness_events lacks. Checked against the actual
    # schema materialized in tmp_db (whatever version that is), not a pinned
    # number, so an unrelated schema bump elsewhere cannot make this stale. A
    # shape that grew a field with nowhere to land is what WOULD force a
    # migration, and this is what notices.
    _seed_task(tmp_db)
    con = sqlite3.connect(str(tmp_db))
    try:
        columns = {row[1] for row in con.execute(
            "PRAGMA table_info(harness_events)"
        ).fetchall()}
    finally:
        con.close()

    assert {"id", "workspace", "ts", "type", "source", "agent", "result",
            "severity", "payload"} <= columns


def test_an_emission_creates_no_table(tmp_db):
    _seed_task(tmp_db)
    before = _table_names(tmp_db)

    _emit(tmp_db)

    assert _table_names(tmp_db) == before
    assert "harness_events" in before


def test_the_channels_own_sources_declare_no_ddl():
    from gaia.state import task_closure_event
    from gaia.store.writer import write_task_close_override_event

    sources = [
        Path(task_closure_event.__file__).read_text(encoding="utf-8"),
        inspect.getsource(write_task_close_override_event),
    ]

    for source in sources:
        upper = source.upper()
        assert "CREATE TABLE" not in upper
        assert "ALTER TABLE" not in upper
        assert "CREATE INDEX" not in upper


def test_the_expected_schema_version_matches_the_channels_authored_baseline():
    # This channel was authored against v37 and needed no migration of its
    # own (asserted structurally above, against the live schema -- not this
    # number). v38 (plan_task_id index), v39 (cut_reason column), v40
    # (harness_agent_id column), and v41-v47 landed afterward for unrelated
    # work, and are the actual current floor -- tracked dynamically by
    # tests/cli/test_schema_version_lockstep.py, which is the real drift
    # guard. This assertion only pins the number this test module itself
    # depends on: it must be bumped in lockstep with any future migration,
    # the same as every other caller of EXPECTED_SCHEMA_VERSION.
    doctor_py = (_REPO_ROOT / "bin" / "cli" / "doctor.py").read_text(encoding="utf-8")
    match = re.search(r"^EXPECTED_SCHEMA_VERSION\s*=\s*(\d+)", doctor_py,
                      re.MULTILINE)

    assert match is not None
    assert int(match.group(1)) == 47


def test_no_migration_file_beyond_the_channels_authored_baseline_exists():
    # Same baseline as the test above, same reason it can go stale: v38
    # through v47 are real, unrelated migrations, not drift in this channel.
    # The actual lockstep invariant (EXPECTED_SCHEMA_VERSION == migration
    # floor) lives in tests/cli/test_schema_version_lockstep.py -- this only
    # pins what this module itself was written against.
    migrations = sorted(
        int(m.group(1))
        for path in (_REPO_ROOT / "scripts" / "migrations").glob("v*_to_v*.sql")
        if (m := re.fullmatch(r"v\d+_to_v(\d+)", path.stem))
    )

    assert migrations, "no migration files found -- the glob or layout changed"
    assert max(migrations) == 47
