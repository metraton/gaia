"""token_usage ingestion/reporting (gaia/usage.py) and brief_events (schema v58)."""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from gaia import usage

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "hooks"))

from modules.security.gaia_cli_only_guard import (  # noqa: E402
    ALLOWED_READ_PHRASES,
    ALLOWED_WRITE_PHRASES,
    match_allowed_phrase,
)

_SCHEMA = _ROOT / "gaia" / "store" / "schema.sql"
_MIGRATION = _ROOT / "scripts" / "migrations" / "v57_to_v58.sql"
_SESSION = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def con(tmp_path):
    db = sqlite3.connect(tmp_path / "gaia.db")
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(_SCHEMA.read_text())
    db.execute("INSERT INTO workspaces (name) VALUES ('century-inc'), ('me')")
    db.commit()
    yield db
    db.close()


def _line(message_id, output, *, agent=None, ts="2026-09-24T10:00:00.000Z", cache_read=100):
    entry = {
        "type": "assistant", "sessionId": _SESSION, "timestamp": ts,
        "message": {"id": message_id, "model": "m", "role": "assistant",
                    "usage": {"input_tokens": 2, "output_tokens": output,
                              "cache_creation_input_tokens": 10,
                              "cache_read_input_tokens": cache_read}},
    }
    if agent:
        entry["agentId"] = agent
    return json.dumps(entry)


def _write_session(projects, main_lines, subagents):
    project = projects / "-home-x"
    (project / _SESSION / "subagents").mkdir(parents=True)
    (project / f"{_SESSION}.jsonl").write_text("\n".join(main_lines) + "\n")
    for agent_id, (agent_type, lines) in subagents.items():
        base = project / _SESSION / "subagents" / f"agent-{agent_id}"
        base.with_suffix(".jsonl").write_text("\n".join(lines) + "\n")
        base.with_suffix(".meta.json").write_text(json.dumps({"agentType": agent_type}))


def _seed_plan(con):
    con.execute("INSERT INTO briefs (id, workspace, name) VALUES (1, 'century-inc', 'b')")
    con.execute("INSERT INTO plans (id, brief_id, status, created_at, updated_at) "
                "VALUES (77, 1, 'closed', '2026-09-24T00:00:00Z', '2026-09-25T00:00:00Z')")
    con.execute("INSERT INTO tasks (id, plan_id, order_num) VALUES (5, 77, 1)")
    rows = [(1, "abound", 5, None), (2, "averifier", None, 1), (3, "aother", None, None)]
    for row_id, harness, task, parent in rows:
        con.execute(
            "INSERT INTO agent_contract_handoffs (id, agent_id, workspace, agent_state, "
            "harness_agent_id, plan_task_id, parent_handoff_id, raw_handoff_json) "
            "VALUES (?, 'a0123456789abcdef', 'me', 'COMPLETE', ?, ?, ?, '{}')",
            (row_id, harness, task, parent))
    con.commit()


def test_ingest_counts_each_message_once_and_is_idempotent(con, tmp_path):
    projects = tmp_path / "projects"
    _write_session(
        projects,
        [_line("m1", 5), _line("m1", 300), _line("m2", 7)],
        {"abound": ("gitops-operator", [_line("s1", 40, agent="abound")])},
    )
    first = usage.ingest(con, usage.discover_transcripts(projects))
    second = usage.ingest(con, usage.discover_transcripts(projects))

    assert first == {"files": 2, "messages": 3, "new_rows": 3}
    assert second["new_rows"] == 0
    m1 = con.execute("SELECT output_tokens, cache_read_tokens FROM token_usage "
                     "WHERE message_id = 'm1'").fetchone()
    assert m1 == (300, 100)
    agent = con.execute("SELECT agent_type, harness_agent_id FROM token_usage "
                        "WHERE message_id = 's1'").fetchone()
    assert agent == ("gitops-operator", "abound")


def test_plan_report_splits_bound_unbound_and_main_inside_the_window(con, tmp_path):
    _seed_plan(con)
    projects = tmp_path / "projects"
    _write_session(
        projects,
        [_line("m1", 100), _line("late", 999, ts="2026-09-25T00:00:01.000Z")],
        {
            "abound": ("gitops-operator", [_line("s1", 40, agent="abound")]),
            "averifier": ("gaia-verifier", [_line("s2", 20, agent="averifier")]),
            "aother": ("gaia-planner", [_line("s3", 9, agent="aother")]),
        },
    )
    usage.ingest(con, usage.discover_transcripts(projects))

    report = usage.plan_report(con, 77)

    by_binding = {r["binding"]: r for r in report["by_session"]}
    assert by_binding["bound"]["output_tokens"] == 60
    assert by_binding["bound"]["subagents"] == 2
    assert by_binding["unbound"]["output_tokens"] == 9
    assert by_binding["main"]["output_tokens"] == 100
    assert report["total"]["calls"] == 4


def test_ac_edit_and_decision_are_recorded_and_bump_updated_at(con):
    con.execute("INSERT INTO briefs (id, workspace, name, updated_at) "
                "VALUES (1, 'century-inc', 'b', '2000-01-01T00:00:00Z')")
    con.execute("INSERT INTO acceptance_criteria (brief_id, ac_id, description) "
                "VALUES (1, 'AC-11', 'five instances')")
    con.execute("UPDATE acceptance_criteria SET description = 'eight instances' "
                "WHERE ac_id = 'AC-11'")
    con.execute("INSERT INTO brief_decisions (brief_id, decision) VALUES (1, 'eight')")
    con.commit()

    events = con.execute("SELECT kind, subject, before, after, source FROM brief_events "
                         "ORDER BY id").fetchall()
    assert [e[0] for e in events] == ["brief_created", "ac_added", "ac_edited", "decision_added"]
    edit = events[2]
    assert json.loads(edit[2])["description"] == "five instances"
    assert json.loads(edit[3])["description"] == "eight instances"
    assert {e[4] for e in events} == {"recorded"}
    updated = con.execute("SELECT updated_at FROM briefs WHERE id = 1").fetchone()[0]
    assert updated > "2000-01-01T00:00:00Z"


def test_migration_reconstructs_past_rows_once(con):
    con.execute("INSERT INTO briefs (id, workspace, name) VALUES (1, 'century-inc', 'b')")
    con.execute("INSERT INTO brief_decisions (id, brief_id, decision) VALUES (41, 1, 'x')")
    con.execute("DELETE FROM brief_events")
    con.commit()

    con.executescript(_MIGRATION.read_text())
    con.executescript(_MIGRATION.read_text())

    rows = con.execute("SELECT kind, subject, source FROM brief_events ORDER BY id").fetchall()
    assert rows == [("brief_created", "b", "reconstructed"),
                    ("decision_added", "D41", "reconstructed")]


def test_brief_delete_cascade_does_not_fail_on_its_own_history(con):
    con.execute("INSERT INTO briefs (id, workspace, name) VALUES (1, 'century-inc', 'b')")
    con.execute("INSERT INTO acceptance_criteria (brief_id, ac_id) VALUES (1, 'AC-1')")
    con.execute("DELETE FROM briefs WHERE id = 1")
    con.commit()
    assert con.execute("SELECT COUNT(*) FROM brief_events").fetchone()[0] == 0


def test_orchestrator_lane_reads_usage_and_history_but_not_ingest():
    assert match_allowed_phrase(("usage", "show", "--plan", "77"), ALLOWED_READ_PHRASES)
    assert match_allowed_phrase(("brief", "history", "b"), ALLOWED_READ_PHRASES)
    assert match_allowed_phrase(("usage", "ingest"),
                                ALLOWED_READ_PHRASES | ALLOWED_WRITE_PHRASES) is None
