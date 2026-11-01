"""The session ledger, the hand note, `gaia session snapshot` and the compaction refresh on both hosts, on an isolated database."""

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
STALE = "2026-01-01T00:00:00Z"


def _isolate(tmp_path, monkeypatch, template):
    data = tmp_path / "data"
    path = copy_bootstrapped_db(template, data / "gaia.db")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(path))
    for name in ("GAIA_HOST_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return path


@pytest.fixture
def db(tmp_path, monkeypatch, bootstrapped_db_template):
    path = _isolate(tmp_path, monkeypatch, bootstrapped_db_template)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO workspaces (name) VALUES ('me')")
    con.execute("INSERT INTO briefs (workspace, name) VALUES ('me', 'brief-a')")
    con.execute("INSERT INTO plans (brief_id) VALUES (1)")
    con.execute("INSERT INTO tasks (plan_id, order_num, goal) VALUES (1, 20, 'build the snapshot')")
    _row(con, "aaaaaaaaaaaaaaaaa.tokena", SESSION_A, "IN_PROGRESS", plan_task_id=1)
    _row(con, "bbbbbbbbbbbbbbbbb.tokenb", SESSION_B, "IN_PROGRESS")
    _row(con, "ccccccccccccccccc.tokenc", SESSION_A, "COMPLETE")
    for approval, session, status in (("P-a", SESSION_A, "pending"), ("P-b", SESSION_B, "pending"),
                                      ("P-done", SESSION_A, "approved")):
        con.execute("INSERT INTO approvals (id, agent_id, session_id, status) VALUES (?, 'a1', ?, ?)",
                    (approval, session, status))
    con.commit()
    con.close()
    return path


def _row(con, contract_id, session_id, state, created_at=None, **columns):
    cur = con.execute(
        "INSERT INTO agent_contract_handoffs "
        "(contract_id, agent_id, session_id, workspace, agent_state, raw_handoff_json, kind, "
        "plan_task_id, parent_handoff_id, continues_handoff_id, dispatch_description, created_at, claimed_at, "
        "cut_reason) "
        "VALUES (?, ?, ?, 'me', ?, ?, ?, ?, ?, ?, ?, COALESCE(?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now')), ?, ?)",
        (contract_id, contract_id.split(".")[0], session_id, state, json.dumps(columns.get("envelope", {})),
         columns.get("kind"), columns.get("plan_task_id"), columns.get("parent_handoff_id"),
         columns.get("continues_handoff_id"), columns.get("description"), created_at, columns.get("claimed_at"),
         columns.get("cut_reason")),
    )
    return cur.lastrowid


def _add_contracts(path, count, session_id, state="NEEDS_VERIFICATION", **columns):
    con = sqlite3.connect(path)
    for n in range(count):
        _row(con, f"{session_id}-{state}-{n}", session_id, state, **columns)
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


def _in_flight(session_id):
    from gaia.session_ledger import build_ledger

    return [c["contract_id"] for c in build_ledger(session_id)["in_flight"]]


def _gaia(*argv, env=None):
    return subprocess.run(
        [sys.executable, str(_ROOT / "bin" / "gaia"), *argv],
        capture_output=True, text=True, timeout=60, env={**os.environ, **(env or {})},
    )


def _claude_start_context(source, cwd, session_id=SESSION_A):
    payload = json.dumps({"hook_event_name": "SessionStart", "session_id": session_id, "source": source})
    proc = subprocess.run(
        [sys.executable, str(_ROOT / "hooks" / "session_start.py")],
        input=payload, capture_output=True, text=True, timeout=60, cwd=str(cwd), env=os.environ.copy(),
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]


def _resume(context):
    return context[context.index("## Resume point"):].split("\n\n", 1)[0]


def test_the_ledger_of_session_a_excludes_session_b_and_settled_or_used_work(db):
    from gaia.session_ledger import build_ledger

    ledger = build_ledger(SESSION_A)

    assert [c["contract_id"] for c in ledger["in_flight"]] == ["aaaaaaaaaaaaaaaaa.tokena"]
    assert [a["id"] for a in ledger["pending"]] == ["P-a"]


def test_a_pending_signature_past_its_ttl_is_not_in_the_ledger(db):
    from gaia.session_ledger import build_ledger

    con = sqlite3.connect(db)
    con.execute("INSERT INTO approvals (id, agent_id, session_id, status, created_at) "
                "VALUES ('P-old', 'a1', ?, 'pending', ?)", (SESSION_A, STALE))
    con.commit()
    con.close()

    assert [a["id"] for a in build_ledger(SESSION_A)["pending"]] == ["P-a"]


def test_a_long_lived_session_lists_only_its_running_and_waiting_rows_and_nothing_settled(
    tmp_path, monkeypatch, bootstrapped_db_template,
):
    path = _isolate(tmp_path, monkeypatch, bootstrapped_db_template)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO workspaces (name) VALUES ('me')")
    con.execute("INSERT INTO briefs (workspace, name) VALUES ('me', 'brief-a')")
    con.execute("INSERT INTO plans (brief_id) VALUES (1)")
    con.execute("INSERT INTO tasks (plan_id, order_num, goal) VALUES (1, 20, 'live')")
    con.execute("INSERT INTO tasks (plan_id, order_num, goal, status) VALUES (1, 19, 'settled', 'done')")
    done = _row(con, "a00000000000000d1.t", SESSION_A, "NEEDS_VERIFICATION", plan_task_id=2)
    _row(con, "a00000000000000d2.t", SESSION_A, "BLOCKED", kind="verifier", parent_handoff_id=done)
    _row(con, "a0000000000000a03.t", SESSION_A, "NEEDS_INPUT", created_at=STALE)
    _row(con, "a00000000000000d4.t", SESSION_A, "NEEDS_VERIFICATION", plan_task_id=1)
    _row(con, "a00000000000000d5.t", SESSION_A, "IN_PROGRESS", created_at=STALE)
    _row(con, "a0000000000000a04.t", SESSION_A, "IN_PROGRESS", created_at=STALE,
         claimed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    _row(con, "a0000000000000a01.t", SESSION_A, "DISPATCHED", plan_task_id=1)
    _row(con, "a0000000000000a02.t", SESSION_A, "NEEDS_INPUT")
    _row(con, "b0000000000000b01.t", SESSION_B, "DISPATCHED")
    con.commit()
    con.close()

    assert _in_flight(SESSION_A) == ["a0000000000000a02.t", "a0000000000000a01.t", "a0000000000000a04.t"]


def test_a_continued_chain_counts_once_and_a_complete_latest_link_closes_it(db):
    con = sqlite3.connect(db)
    first = con.execute("SELECT id FROM agent_contract_handoffs WHERE contract_id = 'bbbbbbbbbbbbbbbbb.tokenb'").fetchone()[0]
    con.close()
    _add_contracts(db, 1, SESSION_B, state="COMPLETE", continues_handoff_id=first)

    assert _in_flight(SESSION_B) == []


def test_a_later_verifier_pass_on_the_same_task_supersedes_the_producer_rows(db):
    _add_contracts(db, 2, "ses-v", plan_task_id=1)
    _add_contracts(db, 1, "ses-v", state="BLOCKED")
    _add_contracts(db, 1, "ses-v", state="COMPLETE", kind="verifier", plan_task_id=1)

    assert _in_flight("ses-v") == ["ses-v-BLOCKED-0"]


def test_a_chain_is_listed_once_by_its_latest_link_under_its_dispatch_title_never_its_kind(db):
    from gaia.session_ledger import build_ledger, render_resume_point

    con = sqlite3.connect(db)
    root = _row(con, "a00000000000000c1.t", "ses-chain", "NEEDS_INPUT", kind="investigation",
                description="Fix the resume point")
    middle = _row(con, "a00000000000000c2.t", "ses-chain", "NEEDS_INPUT", kind="investigation",
                  continues_handoff_id=root)
    _row(con, "a00000000000000c3.t", "ses-chain", "NEEDS_INPUT", kind="investigation", continues_handoff_id=middle)
    _row(con, "a00000000000000n1.t", "ses-chain", "NEEDS_VERIFICATION", kind="investigation")
    _row(con, "a00000000000000r1.t", "ses-chain", "APPROVAL_REQUEST", kind="investigation", cut_reason="reaped",
         description="Reaped while asking")
    con.commit()
    con.close()

    listed = [line for line in render_resume_point(build_ledger("ses-chain")).splitlines()
              if line.startswith("in_flight: ")]

    assert listed == ['in_flight: "Fix the resume point" state=NEEDS_INPUT ref=contract:a00000000000000c3.t']


def test_pending_keeps_a_presented_or_awaited_request_and_drops_a_stale_one_nobody_presented(db, monkeypatch):
    from gaia.store.writer import _connect
    from gaia.session_ledger import build_ledger

    monkeypatch.setattr("gaia.approvals.reading.live_sessions", lambda: {SESSION_A})
    two_hours_ago = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 7200))
    con = _connect(db)
    for approval in ("P-shown", "P-awaited", "P-orphan"):
        con.execute("INSERT INTO approvals (id, agent_id, session_id, status, created_at) "
                    "VALUES (?, 'a1', ?, 'pending', ?)", (approval, SESSION_A, two_hours_ago))
    con.execute("INSERT INTO approval_events (approval_id, event_type, session_id, created_at) "
                "VALUES ('P-shown', 'SHOWN', ?, ?)", (SESSION_A, two_hours_ago))
    _row(con, "a00000000000000w1.t", SESSION_A, "APPROVAL_REQUEST",
         envelope={"approval_request": {"approval_id": "P-awaited"}})
    con.commit()
    con.close()

    assert [a["id"] for a in build_ledger(SESSION_A)["pending"]] == ["P-a", "P-awaited", "P-shown"]


def test_a_long_session_lists_the_eight_newest_contracts_clipped_with_the_remainder(db):
    from gaia.session_ledger import build_ledger, render_resume_point

    _add_contracts(db, 40, "ses-long", state="NEEDS_INPUT",
                   description="Carry the ledger through every compaction " * 5)
    lines = render_resume_point(build_ledger("ses-long")).splitlines()
    listed = [line for line in lines if line.startswith("in_flight: ")]

    assert len(listed) == 8 and listed[-1].endswith("(+32)")
    assert "ref=contract:ses-long-NEEDS_INPUT-39" in listed[0]
    assert all(len(line.split('"')[1]) <= 80 for line in listed)


def test_an_empty_session_renders_only_its_session_line(db):
    from gaia.session_ledger import build_ledger, render_resume_point

    assert render_resume_point(build_ledger("ses-unknown")) == "## Resume point\nsession=ses-unknown compactions=0"


def test_a_failed_source_is_named_unavailable_not_rendered_empty(db, monkeypatch):
    import gaia.session_ledger as ledger_module

    def broken(con, session_id, since):
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(ledger_module, "_SOURCES", (("in_flight", broken), *ledger_module._SOURCES[1:]))
    rendered = ledger_module.render_resume_point(ledger_module.build_ledger(SESSION_A))

    assert "unavailable: in_flight" in rendered and "in_flight: " not in rendered
    assert "pending: P-a" in rendered


def test_a_new_resume_point_prunes_files_older_than_thirty_days(db):
    from gaia.session_snapshot import write_resume_point

    old = Path(write_resume_point("ses-old", "stale"))
    fresh = Path(write_resume_point("ses-fresh", "recent"))
    aged = time.time() - 31 * 24 * 3600
    os.utime(old, (aged, aged))

    write_resume_point(SESSION_A, "now")

    assert not old.exists() and fresh.exists()


def test_the_hand_note_is_session_scoped(db):
    from gaia.session_snapshot import read_resume_point, write_resume_point

    write_resume_point(SESSION_A, "finish the hooks, then run the gate")

    assert read_resume_point(SESSION_A) == "finish the hooks, then run the gate"
    assert read_resume_point(SESSION_B) is None


def test_session_snapshot_verb_prints_the_ledger_of_the_requested_session(db):
    from gaia.session_snapshot import RESUME_POINT_MAX_CHARS, write_resume_point

    note = ("step " * RESUME_POINT_MAX_CHARS)[:RESUME_POINT_MAX_CHARS]
    write_resume_point(SESSION_A, note)
    as_json = _gaia("session", "snapshot", "--session-id", SESSION_A, "--json")
    as_text = _gaia("session", "snapshot", "--session-id", SESSION_A)

    assert as_json.returncode == 0 and as_text.returncode == 0, as_json.stderr + as_text.stderr
    assert [a["id"] for a in json.loads(as_json.stdout)["pending"]] == ["P-a"]
    assert as_text.stdout.startswith(f"## Session ledger\nsession={SESSION_A} compactions=0\n")
    assert "## Resume point" not in as_text.stdout and note.strip() in as_text.stdout
    for foreign in ("bbbbbbbbbbbbbbbbb", "P-b", "brief-a", "Pending proposals"):
        assert foreign not in as_text.stdout


def test_resume_point_verb_writes_what_the_ledger_reads_and_refuses_another_sessions_note(db):
    from gaia.session_snapshot import read_resume_point

    own = _gaia("session", "resume-point", "set", "--session-id", SESSION_A, "--text", "resume at task 21",
                env={"CLAUDE_CODE_SESSION_ID": SESSION_A})
    foreign = _gaia("session", "resume-point", "set", "--session-id", SESSION_A, "--text", "planted",
                    env={"CLAUDE_CODE_SESSION_ID": SESSION_B})

    assert own.returncode == 0, own.stderr
    assert foreign.returncode == 1 and "cannot write the resume point" in foreign.stderr
    assert read_resume_point(SESSION_A) == "resume at task 21"


def test_session_start_compact_renders_the_ledger_once_with_this_sessions_work(db, tmp_path):
    from gaia.session_ledger import write_ledger
    from gaia.session_snapshot import write_resume_point

    _memory(db, SESSION_A, ["atom_from_before_compaction"])
    _memory(db, SESSION_B, ["atom_of_another_session"])
    write_resume_point(SESSION_A, "close task 20 after the verifier")
    write_ledger(SESSION_A)

    context = _claude_start_context("compact", tmp_path)
    resume = _resume(context)

    assert context.count("## Resume point") == 1
    assert resume.startswith(f"## Resume point\nsession={SESSION_A} compactions=1\n")
    assert "ref=contract:aaaaaaaaaaaaaaaaa.tokena" in resume and "pending: P-a" in resume
    assert "written: atom_from_before_compaction ref=memory:me/atom_from_before_compaction" in resume
    assert resume.endswith('note: "close task 20 after the verifier"')
    for foreign in ("bbbbbbbbbbbbbbbbb", "P-b", "P-done", "atom_of_another_session", "Pending proposals"):
        assert foreign not in context


def test_session_preview_is_a_startup_birth_with_no_resume_point(db):
    named = _gaia("session", "preview", "--session-id", SESSION_A)

    assert named.returncode == 0, named.stderr
    assert "## Resume point" not in named.stdout and "aaaaaaaaaaaaaaaaa.tokena" not in named.stdout


def test_a_full_ledger_from_real_rows_and_a_whole_note_fit_the_birth_budget(db):
    from gaia.session_ledger import write_ledger
    from gaia.session_snapshot import write_resume_point
    from modules.session import session_manifest

    _add_contracts(db, 40, "ses-press", state="BLOCKED")
    _memory(db, "ses-press", [f"atom_touched_under_pressure_{n:02d}" for n in range(30)])
    write_resume_point("ses-press", "n" * 2000)
    write_ledger("ses-press")

    birth = session_manifest.build_session_context(source="compact", session_id="ses-press")

    assert len(birth) <= session_manifest.BIRTH_BUDGET
    assert "(+32)" in _resume(birth) and "(+25)" in _resume(birth) and "n" * 2000 in birth


def _published_session_id(context):
    match = re.search(r"^session=(\S+)", context[context.index("## Environment"):], re.MULTILINE)
    assert match, context
    return match.group(1)


def test_both_hosts_publish_the_session_id_the_hand_note_is_written_under(db, tmp_path, monkeypatch):
    import bridge
    from gaia.session_snapshot import read_resume_point, write_resume_point

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
        assert read_resume_point(SESSION_A) == f"resume from {len(context)}"


def test_opencode_compacting_steers_the_summary_and_writes_the_ledger_the_next_message_renders(
    db, tmp_path, monkeypatch,
):
    import bridge
    from gaia.session_ledger import read_ledger, write_ledger
    from gaia.session_snapshot import write_resume_point
    from modules.context.compact_context_builder import build_summary_instructions

    write_resume_point(SESSION_A, "next: dispatch gaia-verifier to close task 890")
    write_ledger(SESSION_A)
    claude_context = _claude_start_context("compact", tmp_path)
    monkeypatch.setenv("GAIA_HOST", "opencode")
    response = bridge.handle({"event": "session.compacting", "sessionID": SESSION_A, "main": True})
    next_message = bridge.handle({"event": "chat.message", "sessionID": SESSION_A, "source": "compact"})
    opencode_context = next_message["additional_context"]

    assert response["updated_input"]["context"] == [build_summary_instructions()]
    assert "## Resume point" not in build_summary_instructions()
    assert read_ledger(SESSION_A)["compactions"] == 2
    assert f"session={SESSION_A} compactions=1" in claude_context
    assert f"session={SESSION_A} compactions=2" in opencode_context
    for context in (claude_context, opencode_context):
        assert context.count("## Resume point") == 1
        assert 'note: "next: dispatch gaia-verifier to close task 890"' in context
        assert "bbbbbbbbbbbbbbbbb" not in context and "P-b" not in context
