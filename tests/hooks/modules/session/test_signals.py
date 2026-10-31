"""Signals reach a session through prompt_context once, and never its own writes, on an isolated database."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[4]
_GAIA = _REPO / "bin" / "gaia"
sys.path.insert(0, str(_REPO / "hooks"))

from modules.session import session_lifecycle
from modules.session.session_lifecycle import prompt_context

LINE = "~ changed: user preference ×1 — gaia signals"
SLUG = "user_pref_signal_probe"


def _gaia(session, *argv):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
    if session:
        env["CLAUDE_CODE_SESSION_ID"] = session
    result = subprocess.run(
        [sys.executable, str(_GAIA), *argv], env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _db():
    from gaia.paths import db_path
    return db_path()


def _scalar(sql, *params):
    con = sqlite3.connect(str(_db()))
    try:
        return con.execute(sql, params).fetchone()[0]
    finally:
        con.close()


@pytest.fixture
def two_sessions():
    """Both sessions hold a cursor before anything changes, as SessionStart leaves them."""
    assert prompt_context("A", None) == ""
    assert prompt_context("B", None) == ""


def _write_user_preference(session):
    _gaia(session, "memory", "add", "--name", SLUG, "--type", "user",
          "--kind", "preference", "--description", "Preference: probe.", "--body", "probe")


def test_another_sessions_write_is_one_line_once_and_never_the_writers(two_sessions):
    _write_user_preference("A")

    assert prompt_context("B", None) == LINE
    assert prompt_context("B", None) == ""
    assert prompt_context("A", None) == ""


def test_a_read_of_the_row_raises_no_signal(two_sessions):
    _write_user_preference("A")
    assert prompt_context("B", None) == LINE
    reads_before = _scalar("SELECT deliberate_count FROM memory WHERE name = ?", SLUG)

    _gaia("C", "memory", "show", SLUG, "--workspace", "_gaia_user")
    _gaia(None, "memory", "show", SLUG, "--workspace", "_gaia_user")

    assert _scalar("SELECT deliberate_count FROM memory WHERE name = ?", SLUG) > reads_before
    assert _scalar("SELECT COUNT(*) FROM signals") == 1
    assert prompt_context("A", None) == ""
    assert prompt_context("B", None) == ""


def test_with_nothing_changed_the_prompt_context_is_the_notifications_line_alone(
    two_sessions, monkeypatch,
):
    bell = session_lifecycle.notifications_counter
    monkeypatch.setattr("gaia.store.reader.count_unread_notifications", lambda ws: 2)

    assert prompt_context("B", None) == bell(None)
    assert bell(None) == "\U0001F514 2 notifications due (`gaia notifications list --unread` to see them)"


def test_the_bell_line_and_the_signal_line_share_one_context(two_sessions, monkeypatch):
    _write_user_preference("A")
    monkeypatch.setattr("gaia.store.reader.count_unread_notifications", lambda ws: 1)

    assert prompt_context("B", None) == session_lifecycle.notifications_counter(None) + "\n" + LINE


def test_gaia_signals_lists_the_row_with_the_command_that_opens_it(two_sessions):
    _write_user_preference("A")
    assert prompt_context("B", None) == LINE

    listed = _gaia("B", "signals")
    assert f"user preference insert {SLUG} -> gaia memory show {SLUG} --workspace _gaia_user" in listed
    rows = json.loads(_gaia("B", "signals", "--json"))
    assert [(r["subject"], r["ref"], r["session_id"]) for r in rows] == [("user", SLUG, "A")]
    assert _gaia("A", "signals") == "No signals from other sessions.\n"


@pytest.fixture
def workspace_me():
    from gaia.store.writer import _connect

    con = _connect()
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        con.commit()
    finally:
        con.close()


def test_project_memory_is_named_by_its_initiative_and_unkeyed_rows_are_silent(
    two_sessions, workspace_me,
):
    from gaia.store.writer import upsert_memory

    upsert_memory("me", "atom_keyed", type="atom", body="b", initiative="gaia")
    upsert_memory("me", "atom_unkeyed", type="atom", body="b")

    assert prompt_context("B", None) == "~ changed: project memory gaia ×1 — gaia signals"


def test_a_workspace_only_move_raises_no_signal(two_sessions, workspace_me):
    from gaia.store.writer import _connect, upsert_memory

    upsert_memory("me", "atom_moved", type="atom", body="b", initiative="gaia")
    assert prompt_context("B", None).startswith("~ changed: project memory gaia")
    con = _connect()
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('ws')")
        con.execute("UPDATE memory SET workspace = 'ws' WHERE name = 'atom_moved'")
        con.commit()
    finally:
        con.close()

    assert _scalar("SELECT workspace FROM memory WHERE name = 'atom_moved'") == "ws"
    assert _scalar("SELECT COUNT(*) FROM signals") == 1
    assert prompt_context("B", None) == ""


def test_an_unattributed_write_reaches_every_session(two_sessions, workspace_me):
    from gaia.store.writer import upsert_memory

    upsert_memory("me", "atom_terminal", type="atom", body="b", initiative="gaia")

    assert prompt_context("A", None).startswith("~ changed: project memory gaia")
    assert prompt_context("B", None).startswith("~ changed: project memory gaia")


def test_an_opencode_child_sessions_write_is_its_parents_own(two_sessions, workspace_me, monkeypatch):
    from gaia.store.writer import _connect, upsert_memory

    con = _connect()
    try:
        con.execute(
            "INSERT INTO agent_contract_handoffs "
            "(agent_id, session_id, workspace, agent_state, raw_handoff_json, harness_agent_id) "
            "VALUES ('a0123456789abcdef', 'A', 'me', 'DISPATCHED', '{}', 'child-of-A')"
        )
        con.commit()
    finally:
        con.close()
    monkeypatch.setenv("GAIA_HOST_SESSION_ID", "child-of-A")

    upsert_memory("me", "atom_from_child", type="atom", body="b", initiative="gaia")

    assert prompt_context("A", None) == ""
    assert prompt_context("B", None) == "~ changed: project memory gaia ×1 — gaia signals"


def test_an_accepted_memory_proposal_signals_the_other_session_only(
    two_sessions, workspace_me, monkeypatch,
):
    from gaia.store.writer import finalize_agent_contract_handoff, insert_dispatched_handoff

    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    agent = "a" + "2" * 16
    contract_id = f"{agent}.000000000001"
    create = {"slug": SLUG, "type": "user", "kind": "preference",
              "description": "Probe: run it.", "body": "probe"}
    envelope = {
        "agent_status": {"agent_state": "COMPLETE", "agent_id": agent,
                         "pending_steps": [], "next_action": "done"},
        "memory_delta": {"version": 1, "proposals": [{"operation": "create", "create": create}]},
    }
    insert_dispatched_handoff(contract_id, agent, "me")
    finalize_agent_contract_handoff(contract_id=contract_id, agent_id=agent, workspace="me",
                                    agent_state="COMPLETE", raw_handoff_json=json.dumps(envelope))
    assert prompt_context("B", None) == ""
    proposal_id = _scalar("SELECT id FROM proposals WHERE status = 'pending'")

    _gaia("A", "contract", "proposals", "accept", str(proposal_id))

    assert _scalar("SELECT COUNT(*) FROM memory WHERE name = ?", SLUG) == 1
    assert prompt_context("B", None) == LINE
    assert prompt_context("A", None) == ""


def test_the_session_end_drops_the_cursor(two_sessions):
    from gaia.signals import read_cursor

    session_lifecycle.end_session("B")

    assert read_cursor("B") is None
    assert read_cursor("A") is not None
