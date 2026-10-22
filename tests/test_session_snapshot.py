"""The session snapshot is keyed by session and reaches both hosts' compaction.

Runs against a bootstrapped copy of the schema in a temporary HOME, GAIA_DATA_DIR and GAIA_DB.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
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


LONG_GOAL = (
    "Carry the session snapshot through every compaction so the orchestrator resumes "
    "exactly where it stopped, with its contracts, signatures and resume point intact "
    "and nothing borrowed from another session"
)
LONG_RESUME = "Rebase the snapshot branch, rerun the compaction gate, then request the push. " * 12
STALE = "2026-01-01T00:00:00Z"


def _row(con, contract_id, session_id, state, created_at=None, **columns):
    """Insert one handoff row and return its id."""
    cur = con.execute(
        "INSERT INTO agent_contract_handoffs "
        "(contract_id, agent_id, session_id, workspace, agent_state, raw_handoff_json, kind, "
        "plan_task_id, parent_handoff_id, created_at, claimed_at) "
        "VALUES (?, ?, ?, 'me', ?, '{}', ?, ?, ?, COALESCE(?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now')), ?)",
        (contract_id, contract_id.split(".")[0], session_id, state, columns.get("kind"),
         columns.get("plan_task_id"), columns.get("parent_handoff_id"), created_at,
         columns.get("claimed_at")),
    )
    return cur.lastrowid


@pytest.fixture
def two_sessions(tmp_path, monkeypatch, bootstrapped_db_template):
    """A long-lived session A whose settled rows must drop out, beside a session B it must not see."""
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
    con.execute("INSERT INTO briefs (workspace, name) VALUES ('me', 'brief-b')")
    con.execute("INSERT INTO plans (brief_id) VALUES (1)")
    con.execute("INSERT INTO plans (brief_id) VALUES (2)")
    con.execute("INSERT INTO tasks (plan_id, order_num, goal) VALUES (1, 20, ?)", (LONG_GOAL,))
    con.execute("INSERT INTO tasks (plan_id, order_num, goal, status) VALUES (1, 19, 'settled', 'done')")
    con.execute("INSERT INTO tasks (plan_id, order_num, goal) VALUES (2, 5, 'task of b')")
    done_producer = _row(con, "a00000000000000d1.t", SESSION_A, "NEEDS_VERIFICATION", plan_task_id=2)
    _row(con, "a00000000000000d2.t", SESSION_A, "BLOCKED", kind="verifier", parent_handoff_id=done_producer)
    _row(con, "a0000000000000a03.t", SESSION_A, "NEEDS_INPUT", created_at=STALE, kind="investigation")
    _row(con, "a00000000000000d4.t", SESSION_A, "NEEDS_VERIFICATION", plan_task_id=1)
    _row(con, "a00000000000000d5.t", SESSION_A, "IN_PROGRESS", created_at=STALE, kind="investigation")
    _row(con, "a0000000000000a04.t", SESSION_A, "IN_PROGRESS", created_at=STALE, kind="investigation",
         claimed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    _row(con, "a0000000000000a01.t", SESSION_A, "DISPATCHED", plan_task_id=1, kind="task_execution")
    _row(con, "a0000000000000a02.t", SESSION_A, "NEEDS_INPUT", kind="investigation")
    _row(con, "b0000000000000b01.t", SESSION_B, "DISPATCHED", plan_task_id=3, kind="task_execution")
    con.execute("INSERT INTO approvals (id, agent_id, session_id, status) VALUES ('P-a', 'a0000000000000a01', ?, 'pending')", (SESSION_A,))
    con.execute("INSERT INTO approvals (id, agent_id, session_id, status) VALUES ('P-done', 'a0000000000000a01', ?, 'approved')", (SESSION_A,))
    con.execute("INSERT INTO approvals (id, agent_id, session_id, status) VALUES ('P-b', 'b0000000000000b01', ?, 'pending')", (SESSION_B,))
    con.commit()
    con.close()
    from gaia.session_snapshot import write_resume_point

    write_resume_point(SESSION_A, LONG_RESUME)
    write_resume_point(SESSION_B, "resume b elsewhere")
    return path


def _assert_only_session_a(rendered):
    assert "a0000000000000a01.t" in rendered and "a0000000000000a02.t" in rendered
    assert "P-a" in rendered and "brief-a" in rendered and "plan 1" in rendered and "task 1 " in rendered
    assert "Rebase the snapshot branch" in rendered
    for foreign in ("b0000000000000b01", "P-b", "brief-b", "resume b elsewhere"):
        assert foreign not in rendered


def _assert_goal_cut_and_resume_whole(rendered):
    goal_line = next(line for line in rendered.splitlines() if line.startswith("Active:"))
    goal_part = goal_line.split(" -- ", 1)[1].removesuffix("...")
    assert LONG_GOAL.startswith(goal_part) and LONG_GOAL[len(goal_part)] == " "
    assert f"Resume point:\n{LONG_RESUME.strip()}\n\n" in rendered


def test_a_long_lived_session_carries_only_its_live_work_and_nothing_from_another(two_sessions):
    from gaia.session_snapshot import build_snapshot, render_snapshot

    snapshot = build_snapshot(SESSION_A)
    rendered = render_snapshot(snapshot)

    assert [c["contract_id"] for c in snapshot["open_contracts"]] == [
        "a0000000000000a02.t", "a0000000000000a01.t", "a0000000000000a04.t", "a0000000000000a03.t",
    ]
    assert [c["contract_id"] for c in snapshot["older_unbound"]] == ["a00000000000000d5.t"]
    assert f"gaia session snapshot --session-id {SESSION_A} --json" in rendered
    assert [a["id"] for a in snapshot["pending_signatures"]] == ["P-a"]
    assert (snapshot["active_task"]["brief"], snapshot["active_task"]["plan_id"], snapshot["active_task"]["task_id"]) == ("brief-a", 1, 1)
    _assert_only_session_a(rendered)
    assert "a0000000000000a03.t [NEEDS_INPUT]" in rendered
    for settled in ("a00000000000000d1", "a00000000000000d2", "a00000000000000d4", "a00000000000000d5"):
        assert settled not in rendered
    _assert_goal_cut_and_resume_whole(rendered)
    assert len(rendered) <= 1500 + len(LONG_RESUME)


def test_a_resume_point_at_the_setter_limit_reaches_the_snapshot_whole(db):
    from gaia.session_snapshot import RESUME_POINT_MAX_CHARS, build_snapshot, render_snapshot, write_resume_point

    tail = " Then dispatch gaia-verifier to close task 890."
    resume = ("step " * RESUME_POINT_MAX_CHARS)[: RESUME_POINT_MAX_CHARS - len(tail)] + tail
    write_resume_point("ses-full-resume", resume)

    rendered = render_snapshot(build_snapshot("ses-full-resume"))

    assert len(resume) == RESUME_POINT_MAX_CHARS
    assert f"Resume point:\n{resume}\n\n" in rendered
    assert "snapshot truncated" not in rendered


def test_every_section_is_present_when_empty():
    from gaia.session_snapshot import render_snapshot

    rendered = render_snapshot({
        "session_id": "s", "open_contracts": [], "older_unbound": [],
        "pending_signatures": [], "active_task": None, "resume_point": None,
    })

    for title in ("Active:", "Resume point:", "Pending signatures:", "Open contracts:"):
        assert title in rendered


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
    resume = ("resume here " * 100).strip()
    write_resume_point("ses-long", resume)

    snapshot = build_snapshot("ses-long")
    rendered = render_snapshot(snapshot)

    assert len(snapshot["open_contracts"]) == 40
    assert "ses-long-NEEDS_VERIFICATION-39" in rendered and "ses-long-NEEDS_VERIFICATION-30" in rendered
    assert "ses-long-NEEDS_VERIFICATION-29" not in rendered
    assert "+30 more" in rendered
    assert resume in rendered
    assert len(rendered) <= 1500 + len(resume)


def test_the_block_is_cut_on_a_whole_line_past_the_resume_point_when_every_section_is_full(db, monkeypatch):
    from gaia.session_snapshot import build_snapshot, render_snapshot, write_resume_point

    _add_contracts(db, 12, "ses-full", kind="task_execution-" + "x" * 200)
    write_resume_point("ses-full", "r" * 2000)

    snapshot = build_snapshot("ses-full")
    rendered = render_snapshot(snapshot)
    monkeypatch.setattr("gaia.session_snapshot._BLOCK_MAX_CHARS", 10**6)
    uncapped = render_snapshot(snapshot).splitlines()

    assert len(rendered) <= 1500 + 2000
    assert "r" * 2000 in rendered
    assert rendered.startswith("## Session Snapshot")
    *kept, marker = rendered.splitlines()
    assert marker.startswith("... (snapshot truncated")
    assert kept == uncapped[: len(kept)]


def test_a_clip_ends_on_a_whole_word():
    from gaia.session_snapshot import _clip

    assert _clip("alpha beta gamma", 13) == "alpha beta..."
    assert _clip("alpha beta gamma", 11) == "alpha beta..."
    assert _clip("alpha beta gamma", 16) == "alpha beta gamma"


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
    assert "Open contracts: none" in rendered


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
    assert "## Resume point" in context
    assert "aaaaaaaaaaaaaaaaa.tokena" in context and "P-a" in context
    assert "bbbbbbbbbbbbbbbbb" not in context and "P-b" not in context


def test_opencode_compacting_feeds_the_summarizer_the_steering_and_no_snapshot(db, monkeypatch):
    import bridge
    from modules.context.compact_context_builder import build_summary_instructions

    monkeypatch.setenv("GAIA_HOST", "opencode")
    response = bridge.handle({"event": "session.compacting", "sessionID": SESSION_A, "main": True})

    assert response["updated_input"]["context"] == [build_summary_instructions()]
    assert "## Session Snapshot" not in build_summary_instructions()


def _claude_start_context(source: str, cwd) -> str:
    payload = json.dumps({"hook_event_name": "SessionStart", "session_id": SESSION_A, "source": source})
    proc = subprocess.run(
        [sys.executable, str(_ROOT / "hooks" / "session_start.py")],
        input=payload, capture_output=True, text=True, timeout=60, cwd=str(cwd), env=os.environ.copy(),
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]


def _published_session_id(context: str) -> str:
    environment = context[context.index("## Environment"):]
    match = re.search(r"^- Session id: (\S+)", environment, re.MULTILINE)
    assert match, environment
    return match.group(1)


def test_both_hosts_publish_the_session_id_the_snapshot_reads_at_start_and_after_compaction(
    db, tmp_path, monkeypatch,
):
    import bridge
    from gaia.session_snapshot import build_snapshot, write_resume_point

    claude = [_claude_start_context(source, tmp_path) for source in ("startup", "compact")]
    monkeypatch.setenv("GAIA_HOST", "opencode")
    opencode = [
        bridge.handle({"event": "chat.message", "sessionID": SESSION_A})["additional_context"],
        bridge.handle({"event": "chat.message", "sessionID": SESSION_A, "source": "compact"})["additional_context"],
    ]

    for context in (*claude, *opencode):
        published = _published_session_id(context)
        assert published == SESSION_A
        write_resume_point(published, f"resume from {len(context)}")
        assert build_snapshot(SESSION_A)["resume_point"] == f"resume from {len(context)}"


def _proposals(path, pending, dismissed=0):
    con = sqlite3.connect(path)
    for n in range(pending + dismissed):
        con.execute(
            "INSERT INTO proposals (handoff_id, item_index, kind, payload, status) VALUES (1, ?, 'memory', '{}', ?)",
            (n, "pending" if n < pending else "dismissed"),
        )
    con.commit()
    con.close()


def _memory(path, session_id, names):
    con = sqlite3.connect(path)
    for name in names:
        con.execute(
            "INSERT INTO memory (workspace, name, type, body, origin_session_id, updated_at) "
            "VALUES ('me', ?, 'atom', 'body', ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))",
            (name, session_id),
        )
    con.commit()
    con.close()


def _aged_note(session_id, text, hours):
    from gaia.session_snapshot import write_resume_point

    path = Path(write_resume_point(session_id, text))
    aged = time.time() - hours * 3600
    os.utime(path, (aged, aged))


def _note_heading(section):
    return next(line for line in section.splitlines() if line.startswith("Hand note"))


def test_without_a_hand_note_the_resume_point_is_derived_from_the_database(db):
    from gaia.session_snapshot import build_snapshot, render_resume_section

    _memory(db, SESSION_A, ["atom_touched_here"])
    _memory(db, SESSION_B, ["atom_of_another_session"])

    section = render_resume_section(build_snapshot(SESSION_A))

    assert section.startswith("## Resume point\n")
    assert "No resume point recorded" not in section and "Hand note" not in section
    assert "brief brief-a (me), plan 1, task 1 (order 20)" in section
    assert "aaaaaaaaaaaaaaaaa.tokena" in section and "P-a" in section
    assert "Memory touched this session: atom_touched_here" in section
    assert "atom_of_another_session" not in section


def test_a_hand_note_older_than_the_newest_contract_is_shown_as_stale_with_its_age(db):
    from gaia.session_snapshot import build_snapshot, render_resume_section, write_resume_point

    _aged_note(SESSION_A, "dispatch task 20 next", hours=3)
    write_resume_point(SESSION_B, "verify the b contract")

    stale = _note_heading(render_resume_section(build_snapshot(SESSION_A)))
    current = _note_heading(render_resume_section(build_snapshot(SESSION_B)))

    assert "written 3h ago" in stale and "STALE" in stale
    assert "STALE" not in current and "ago" in current
    assert "dispatch task 20 next" in render_resume_section(build_snapshot(SESSION_A))


def test_a_fresh_startup_shows_the_pending_proposal_count_only_when_there_is_one(db, tmp_path):
    from gaia.session_snapshot import build_snapshot, render_resume_section

    assert render_resume_section(build_snapshot("ses-fresh")) == ""
    _proposals(db, pending=2, dismissed=1)

    section = render_resume_section(build_snapshot("ses-fresh"))
    payload = json.dumps({"hook_event_name": "SessionStart", "session_id": "ses-fresh", "source": "startup"})
    proc = subprocess.run(
        [sys.executable, str(_ROOT / "hooks" / "session_start.py")],
        input=payload, capture_output=True, text=True, timeout=60, cwd=str(tmp_path), env=os.environ.copy(),
    )

    assert section == "## Resume point\nPending proposals: 2 (`gaia contract proposals list`)"
    assert proc.returncode == 0, proc.stderr
    assert section in json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]


def test_an_active_plan_with_no_contract_of_the_session_is_named_with_its_next_task(db):
    from gaia.session_snapshot import build_snapshot, render_resume_section

    con = sqlite3.connect(db)
    con.execute("UPDATE plans SET status = 'active' WHERE id = 1")
    con.commit()
    con.close()

    unbound = render_resume_section(build_snapshot("ses-plan"))
    bound = render_resume_section(build_snapshot(SESSION_A))

    assert "plan 1" in unbound and "brief-a" in unbound
    assert "next pending task 1 (order 20) -- build the snapshot" in unbound
    assert [line for line in bound.splitlines() if line.startswith("Active")] == [
        "Active: brief brief-a (me), plan 1, task 1 (order 20) -- build the snapshot"
    ]


def test_a_compaction_birth_carries_the_derived_resume_point_once(db, tmp_path):
    _proposals(db, pending=1)
    _memory(db, SESSION_A, ["atom_from_before_compaction"])
    _aged_note(SESSION_A, "close task 20 after the verifier", hours=2)

    context = _claude_start_context("compact", tmp_path)

    assert context.count("## Resume point") == 1
    assert "Pending proposals: 1 (`gaia contract proposals list`)" in context
    assert "atom_from_before_compaction" in context and "aaaaaaaaaaaaaaaaa.tokena" in context
    assert "STALE" in _note_heading(context[context.index("## Resume point"):])


def test_under_budget_pressure_the_counts_survive_and_the_block_fits(db, monkeypatch):
    from modules.session import session_manifest

    _add_contracts(db, 40, "ses-press")
    _memory(db, "ses-press", [f"atom_touched_under_pressure_{n:02d}" for n in range(30)])
    _proposals(db, pending=5)
    _aged_note("ses-press", "n" * 2000, hours=1)

    full = session_manifest.build_session_context(source="compact", session_id="ses-press")
    monkeypatch.setattr(session_manifest, "BIRTH_BUDGET", len(full) - 300)
    pressed = session_manifest.build_session_context(source="compact", session_id="ses-press")

    assert len(full) <= session_manifest.CONTEXT_BUDGET
    assert len(pressed) <= len(full) - 300
    for context in (full, pressed):
        assert "Pending proposals: 5 (`gaia contract proposals list`)" in context
        assert "n" * 2000 in context
    assert "Memory touched this session: 30" in pressed and "Open contracts: 40" in pressed


def test_both_hosts_deliver_the_refresh_with_the_whole_resume_point_once_after_compaction(
    two_sessions, tmp_path, monkeypatch,
):
    import bridge
    from gaia.session_snapshot import RESUME_POINT_MAX_CHARS, build_snapshot, render_resume_section, write_resume_point
    from modules.session.session_manifest import build_environment_section

    tail = " Then dispatch gaia-verifier to close task 890."
    resume = ("next " * RESUME_POINT_MAX_CHARS)[: RESUME_POINT_MAX_CHARS - len(tail)] + tail
    write_resume_point(SESSION_A, resume)
    environment = build_environment_section(SESSION_A)
    expected = render_resume_section(build_snapshot(SESSION_A))
    payload = json.dumps({"hook_event_name": "SessionStart", "session_id": SESSION_A, "source": "compact"})
    proc = subprocess.run(
        [sys.executable, str(_ROOT / "hooks" / "session_start.py")],
        input=payload, capture_output=True, text=True, timeout=60, cwd=str(tmp_path),
        env=os.environ.copy(),
    )
    monkeypatch.setenv("GAIA_HOST", "opencode")
    summary_context = bridge.handle(
        {"event": "session.compacting", "sessionID": SESSION_A, "main": True}
    )["updated_input"]["context"]
    next_message = bridge.handle({"event": "chat.message", "sessionID": SESSION_A, "source": "compact"})

    assert proc.returncode == 0, proc.stderr
    assert "- gaia CLI: " in environment
    claude_context = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    opencode_context = "\n\n".join([*summary_context, next_message["additional_context"]])
    for context in (claude_context, opencode_context):
        assert expected in context
        assert resume in context
        assert context.count("## Resume point") == 1
        assert context.count(environment) == 1
        assert context.count("- gaia CLI: ") == 1
        for foreign in ("b0000000000000b01", "P-b", "resume b elsewhere"):
            assert foreign not in context
