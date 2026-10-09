"""The session snapshot is keyed by session and reaches both hosts' compaction.

Runs against a bootstrapped copy of the schema in a temporary HOME, GAIA_DATA_DIR and GAIA_DB.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import copy_bootstrapped_db

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "hooks"), str(_ROOT / "opencode")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

SESSION_A = "ses-a"
SESSION_B = "ses-b"


@pytest.fixture
def db(tmp_path, monkeypatch, bootstrapped_db_template):
    data = tmp_path / "data"
    path = copy_bootstrapped_db(bootstrapped_db_template, data / "gaia.db")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(path))
    monkeypatch.chdir(tmp_path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO workspaces (name) VALUES ('me')")
    con.execute("INSERT INTO briefs (workspace, name) VALUES ('me', 'brief-a')")
    con.execute("INSERT INTO plans (brief_id) VALUES (1)")
    con.execute("INSERT INTO tasks (plan_id, order_num, goal) VALUES (1, 20, 'build the snapshot')")
    _contract(con, "aaaaaaaaaaaaaaaaa", "aaaaaaaaaaaaaaaaa.tokena", SESSION_A, "IN_PROGRESS", plan_task_id=1)
    _contract(con, "bbbbbbbbbbbbbbbbb", "bbbbbbbbbbbbbbbbb.tokenb", SESSION_B, "IN_PROGRESS")
    _contract(con, "ccccccccccccccccc", "ccccccccccccccccc.tokenc", SESSION_A, "COMPLETE")
    con.execute("INSERT INTO approvals (id, agent_id, session_id, status) VALUES ('P-a', 'aaaaaaaaaaaaaaaaa', ?, 'pending')", (SESSION_A,))
    con.execute("INSERT INTO approvals (id, agent_id, session_id, status) VALUES ('P-b', 'bbbbbbbbbbbbbbbbb', ?, 'pending')", (SESSION_B,))
    con.execute("INSERT INTO approvals (id, agent_id, session_id, status) VALUES ('P-done', 'aaaaaaaaaaaaaaaaa', ?, 'approved')", (SESSION_A,))
    con.commit()
    con.close()
    return path


def _contract(con, agent_id, contract_id, session_id, state, plan_task_id=None):
    con.execute(
        "INSERT INTO agent_contract_handoffs "
        "(contract_id, agent_id, session_id, workspace, agent_state, plan_task_id, raw_handoff_json) "
        "VALUES (?, ?, ?, 'me', ?, ?, '{}')",
        (contract_id, agent_id, session_id, state, plan_task_id),
    )


def test_snapshot_of_session_a_excludes_session_b(db):
    from gaia.session_snapshot import build_snapshot, render_snapshot

    snapshot = build_snapshot(SESSION_A)
    rendered = render_snapshot(snapshot)

    assert [c["contract_id"] for c in snapshot["open_contracts"]] == ["aaaaaaaaaaaaaaaaa.tokena"]
    assert [a["id"] for a in snapshot["pending_signatures"]] == ["P-a"]
    assert snapshot["active_task"]["brief"] == "brief-a"
    assert snapshot["active_task"]["task_order"] == 20
    assert "bbbbbbbbbbbbbbbbb" not in rendered and "P-b" not in rendered
    assert "ccccccccccccccccc" not in rendered and "P-done" not in rendered


def test_resume_point_is_session_scoped_and_rendered(db):
    from gaia.session_snapshot import build_snapshot, render_snapshot, write_resume_point

    write_resume_point(SESSION_A, "finish the hooks, then run the gate")

    assert "finish the hooks, then run the gate" in render_snapshot(build_snapshot(SESSION_A))
    assert build_snapshot(SESSION_B)["resume_point"] is None


def test_empty_session_yields_a_minimal_block_not_an_error(db):
    from gaia.session_snapshot import build_snapshot, render_snapshot

    rendered = render_snapshot(build_snapshot("ses-unknown"))

    assert rendered.startswith("## Session Snapshot")
    assert "No open contracts" in rendered


def test_session_snapshot_verb_prints_json_for_the_requested_session(db):
    proc = subprocess.run(
        [sys.executable, str(_ROOT / "bin" / "gaia"), "session", "snapshot", "--session-id", SESSION_A, "--json"],
        capture_output=True, text=True, timeout=60, env=os.environ.copy(),
    )

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["pending_signatures"][0]["id"] == "P-a"


def test_resume_point_verb_writes_what_the_snapshot_reads(db):
    proc = subprocess.run(
        [sys.executable, str(_ROOT / "bin" / "gaia"), "session", "resume-point", "set",
         "--session-id", SESSION_A, "--text", "resume at task 21"],
        capture_output=True, text=True, timeout=60, env=os.environ.copy(),
    )

    from gaia.session_snapshot import read_resume_point

    assert proc.returncode == 0, proc.stderr
    assert read_resume_point(SESSION_A) == "resume at task 21"


def test_session_start_compact_output_contains_the_snapshot(db, tmp_path):
    payload = json.dumps({"hook_event_name": "SessionStart", "session_id": SESSION_A, "source": "compact"})
    proc = subprocess.run(
        [sys.executable, str(_ROOT / "hooks" / "session_start.py")],
        input=payload, capture_output=True, text=True, timeout=60, cwd=str(tmp_path),
        env=os.environ.copy(),
    )

    assert proc.returncode == 0, proc.stderr
    context = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "## Session Snapshot" in context
    assert "aaaaaaaaaaaaaaaaa.tokena" in context and "P-a" in context
    assert "bbbbbbbbbbbbbbbbb" not in context and "P-b" not in context


def test_opencode_compacting_for_the_primary_session_receives_the_snapshot(db, monkeypatch):
    import bridge

    monkeypatch.setenv("GAIA_HOST", "opencode")
    response = bridge.handle({"event": "session.compacting", "sessionID": SESSION_A, "main": True})

    (context,) = response["updated_input"]["context"]
    assert "## Session Snapshot" in context
    assert "aaaaaaaaaaaaaaaaa.tokena" in context and "P-a" in context
    assert "bbbbbbbbbbbbbbbbb" not in context
