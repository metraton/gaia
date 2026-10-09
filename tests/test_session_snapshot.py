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


def _add_contracts(path, count, session_id, state="NEEDS_VERIFICATION", **columns):
    con = sqlite3.connect(path)
    for n in range(count):
        con.execute(
            "INSERT INTO agent_contract_handoffs "
            "(contract_id, agent_id, session_id, workspace, agent_state, raw_handoff_json, kind, plan_task_id, continues_handoff_id) "
            "VALUES (?, ?, ?, 'me', ?, '{}', ?, ?, ?)",
            (f"{session_id}-{state}-{n}", f"{n:017x}", session_id, state,
             columns.get("kind"), columns.get("plan_task_id"), columns.get("continues_handoff_id")),
        )
    con.commit()
    con.close()


def test_a_long_session_lists_ten_newest_contracts_and_caps_the_block(db):
    from gaia.session_snapshot import build_snapshot, render_snapshot, write_resume_point

    _add_contracts(db, 40, "ses-long")
    write_resume_point("ses-long", "resume here " * 100)

    snapshot = build_snapshot("ses-long")
    rendered = render_snapshot(snapshot)

    assert len(snapshot["open_contracts"]) == 40
    assert "ses-long-NEEDS_VERIFICATION-39" in rendered and "ses-long-NEEDS_VERIFICATION-30" in rendered
    assert "ses-long-NEEDS_VERIFICATION-29" not in rendered
    assert "+30 more" in rendered
    assert "resume here" in rendered
    assert len(rendered) <= 1500


def test_the_block_is_cut_to_the_cap_even_when_every_section_is_full(db):
    from gaia.session_snapshot import build_snapshot, render_snapshot, write_resume_point

    _add_contracts(db, 12, "ses-full", kind="task_execution-" + "x" * 80)
    write_resume_point("ses-full", "r" * 2000)

    rendered = render_snapshot(build_snapshot("ses-full"))

    assert len(rendered) <= 1500
    assert rendered.startswith("## Session Snapshot")


def test_a_continued_chain_counts_once_and_a_complete_latest_link_closes_it(db):
    from gaia.session_snapshot import build_snapshot

    con = sqlite3.connect(db)
    first = con.execute("SELECT id FROM agent_contract_handoffs WHERE contract_id = 'bbbbbbbbbbbbbbbbb.tokenb'").fetchone()[0]
    con.close()
    _add_contracts(db, 1, SESSION_B, state="COMPLETE", continues_handoff_id=first)

    assert build_snapshot(SESSION_B)["open_contracts"] == []


def test_a_later_verifier_pass_on_the_same_task_supersedes_the_producer_rows(db):
    from gaia.session_snapshot import build_snapshot

    _add_contracts(db, 2, "ses-v", plan_task_id=1)
    _add_contracts(db, 1, "ses-v", state="BLOCKED")
    _add_contracts(db, 1, "ses-v", state="COMPLETE", kind="verifier", plan_task_id=1)

    open_ids = [c["contract_id"] for c in build_snapshot("ses-v")["open_contracts"]]

    assert open_ids == ["ses-v-BLOCKED-0"]


def test_a_new_resume_point_prunes_files_older_than_thirty_days(db):
    import os
    import time

    from gaia.session_snapshot import write_resume_point

    old = Path(write_resume_point("ses-old", "stale"))
    fresh = Path(write_resume_point("ses-fresh", "recent"))
    aged = time.time() - 31 * 24 * 3600
    os.utime(old, (aged, aged))

    write_resume_point(SESSION_A, "now")

    assert not old.exists()
    assert fresh.exists()


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

    context, _instructions = response["updated_input"]["context"]
    assert "## Session Snapshot" in context
    assert "aaaaaaaaaaaaaaaaa.tokena" in context and "P-a" in context
    assert "bbbbbbbbbbbbbbbbb" not in context
