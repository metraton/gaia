"""Signals reach a session through prompt_context once, itemized, and never its own writes, on an isolated database."""

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

SLUG = "user_pref_signal_probe"
LINE = f'~ user memory changed:\n  added    kind=preference "probe."  ref=memory:user/{SLUG}'
USER = "_gaia_user"


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


def _execute(sql, *params):
    from gaia.store.writer import _connect

    con = _connect()
    try:
        con.execute(sql, params)
        con.commit()
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


def _remember(workspace, slug, kind, title, **placement):
    from gaia.store.writer import upsert_memory

    mem_type = {"preference": "user", "lesson": "negative", "decision": "decision"}.get(kind, "atom")
    if workspace == USER:
        mem_type = "user"
    upsert_memory(workspace, slug, type=mem_type, kind=kind, description=title, body="b", **placement)


def test_another_sessions_write_is_one_itemized_line_once_and_never_the_writers(two_sessions):
    _write_user_preference("A")

    assert prompt_context("B", None) == LINE
    assert prompt_context("B", None) == ""
    assert prompt_context("A", None) == ""


def test_user_changes_are_itemized_with_op_kind_title_and_ref(two_sessions):
    _remember(USER, "user_pref_lang", "preference", "answer in Spanish")
    _remember(USER, "user_fact_city", "fact", "works from Lima")
    assert prompt_context("B", None) == "\n".join([
        "~ user memory changed:",
        '  added    kind=fact "works from Lima"  ref=memory:user/user_fact_city',
        '  added    kind=preference "answer in Spanish"  ref=memory:user/user_pref_lang',
    ])

    _execute("UPDATE memory SET description = 'works from Quito' WHERE name = 'user_fact_city'")
    _execute("UPDATE memory SET deleted_at = '2026-10-31T00:00:00Z' WHERE name = 'user_pref_lang'")

    assert prompt_context("B", None) == "\n".join([
        "~ user memory changed:",
        '  retired  kind=preference "answer in Spanish"  ref=memory:user/user_pref_lang',
        '  updated  kind=fact "works from Quito"  ref=memory:user/user_fact_city',
    ])


def test_a_supersede_and_a_demotion_to_log_read_as_retired(two_sessions, workspace_me):
    from gaia.store.writer import insert_memory_link

    _remember(USER, "user_pref_old", "preference", "reply short")
    _remember(USER, "user_pref_new", "preference", "reply in bullets")
    _remember("me", "decision_anchor", "decision", "ship weekly", initiative="balance", class_="anchor")
    prompt_context("B", None)

    insert_memory_link(USER, "user_pref_new", "user_pref_old", "supersedes")
    _execute("UPDATE memory SET class = 'log' WHERE name = 'decision_anchor'")

    assert prompt_context("B", None) == "\n".join([
        "~ user memory changed:",
        '  retired  kind=preference "reply short"  ref=memory:user/user_pref_old',
        "~ project memory changed:",
        '  me/balance  retired kind=decision "ship weekly"  ref=memory:me/decision_anchor — gaia signals',
    ])


def test_a_read_of_the_row_raises_no_signal(two_sessions):
    _write_user_preference("A")
    assert prompt_context("B", None) == LINE
    reads_before = _scalar("SELECT deliberate_count FROM memory WHERE name = ?", SLUG)

    _gaia("C", "memory", "show", SLUG, "--workspace", USER)
    _gaia(None, "memory", "show", SLUG, "--workspace", USER)

    assert _scalar("SELECT deliberate_count FROM memory WHERE name = ?", SLUG) > reads_before
    assert _scalar("SELECT COUNT(*) FROM signals") == 1
    assert prompt_context("A", None) == ""
    assert prompt_context("B", None) == ""


def _due(n):
    return [{"id": i, "kind": "reminder", "headline": f"due-{i}"} for i in range(1, n + 1)]


def test_with_nothing_changed_the_prompt_context_is_the_due_line_alone(two_sessions, monkeypatch):
    monkeypatch.setattr("gaia.store.reader.due_now", lambda ws: _due(5))

    first = prompt_context("B", None)

    assert first == session_lifecycle.notifications_counter(None)
    assert first == "\U0001F514 5 notifications due (`gaia notifications list --unread` to see them)"
    assert prompt_context("B", None) == ""


def test_the_due_line_and_the_change_lines_share_one_context(two_sessions, monkeypatch):
    _write_user_preference("A")
    monkeypatch.setattr("gaia.store.reader.due_now", lambda ws: _due(1))

    assert prompt_context("B", None) == "\U0001F514 1 notification due:\n  'due-1'  id=1\n" + LINE


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
    _execute("INSERT INTO workspaces (name) VALUES ('me')")
    _execute("INSERT INTO projects (workspace, name, path, project_identity) "
             "VALUES ('me', 'balance', '/w/me/balance', 'github.com/me/balance')")


@pytest.fixture
def century():
    """The index names century-inc, its group branchkinect and the project branchkinect-platform."""
    _execute("INSERT INTO workspaces (name) VALUES ('century-inc')")
    _execute("INSERT INTO projects (workspace, name, group_name, path, project_identity) VALUES "
             "('century-inc', 'branchkinect-platform', 'branchkinect', "
             "'/w/century/branchkinect/branchkinect-platform', 'github.com/century/branchkinect-platform')")


def test_project_rows_group_under_the_names_the_workspace_index_uses(two_sessions, century):
    _remember("century-inc", "negative_retry", "lesson", "retry the deploy",
              project_ref="github.com/century/branchkinect-platform")
    _remember("century-inc", "decision_pr", "decision", "one PR per release", initiative="branchkinect")
    _remember("century-inc", "atom_billing", "fact", "billing in USD")

    assert prompt_context("B", None) == "\n".join([
        "~ project memory changed:",
        '  century-inc  added kind=fact "billing in USD"  ref=memory:century-inc/atom_billing',
        '  century-inc/branchkinect  added kind=decision "one PR per release"  '
        "ref=memory:century-inc/decision_pr",
        '  century-inc/branchkinect-platform  added kind=lesson "retry the deploy"  '
        "ref=memory:century-inc/negative_retry — gaia signals",
    ])


def test_a_project_shows_three_rows_then_the_rest_as_a_count(two_sessions, workspace_me):
    for i in range(1, 6):
        _remember("me", f"atom_fact_{i}", "fact", f"note {i}", initiative="balance")

    assert prompt_context("B", None) == "\n".join([
        "~ project memory changed:",
        '  me/balance  added kind=fact "note 5"  ref=memory:me/atom_fact_5',
        '  me/balance  added kind=fact "note 4"  ref=memory:me/atom_fact_4',
        '  me/balance  added kind=fact "note 3"  ref=memory:me/atom_fact_3',
        "  me/balance ×2 — gaia signals",
    ])


def test_the_cursor_survives_a_compaction_and_keeps_the_sessions_birth(two_sessions):
    from gaia import signals

    _remember(USER, "user_pref_before", "preference", "before the compaction")
    signals.baseline("B")

    assert prompt_context("B", None) == (
        '~ user memory changed:\n  added    kind=preference "before the compaction"  '
        "ref=memory:user/user_pref_before"
    )
    assert [item["ref"] for item in signals.changed_since_birth("B")] == ["memory:user/user_pref_before"]


def test_a_workspace_only_move_raises_no_signal(two_sessions, workspace_me):
    _remember("me", "atom_moved", "fact", "moved", initiative="balance")
    assert prompt_context("B", None).startswith("~ project memory changed:\n  me/balance")
    _execute("INSERT INTO workspaces (name) VALUES ('ws')")
    _execute("UPDATE memory SET workspace = 'ws' WHERE name = 'atom_moved'")

    assert _scalar("SELECT workspace FROM memory WHERE name = 'atom_moved'") == "ws"
    assert _scalar("SELECT COUNT(*) FROM signals") == 1
    assert prompt_context("B", None) == ""


def test_an_unattributed_write_reaches_every_session(two_sessions, workspace_me):
    _remember("me", "atom_terminal", "fact", "terminal", initiative="balance")

    assert prompt_context("A", None).startswith("~ project memory changed:")
    assert prompt_context("B", None).startswith("~ project memory changed:")


def test_an_opencode_child_sessions_write_is_its_parents_own(two_sessions, workspace_me, monkeypatch):
    _execute(
        "INSERT INTO agent_contract_handoffs "
        "(agent_id, session_id, workspace, agent_state, raw_handoff_json, harness_agent_id) "
        "VALUES ('a0123456789abcdef', 'A', 'me', 'DISPATCHED', '{}', 'child-of-A')"
    )
    monkeypatch.setenv("GAIA_HOST_SESSION_ID", "child-of-A")

    _remember("me", "atom_from_child", "fact", "from the child", initiative="balance")

    assert prompt_context("A", None) == ""
    assert prompt_context("B", None) == (
        '~ project memory changed:\n  me/balance  added kind=fact "from the child"  '
        "ref=memory:me/atom_from_child — gaia signals"
    )


def test_an_accepted_memory_proposal_signals_the_other_session_only(
    two_sessions, workspace_me, monkeypatch,
):
    from gaia.store.writer import finalize_agent_contract_handoff, insert_dispatched_handoff

    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    agent = "a" + "2" * 16
    contract_id = f"{agent}.000000000001"
    create = {"slug": SLUG, "type": "user", "kind": "preference",
              "description": "Preference: probe.", "body": "probe"}
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
