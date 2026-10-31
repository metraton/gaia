"""`gaia map`: each NOW source present and absent on a temp database, the read-only proof, and the orchestrator lane."""

from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import IsolatedRuntimeEnv, copy_bootstrapped_db

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GAIA = _REPO_ROOT / "bin" / "gaia"
_HOOKS_DIR = str(_REPO_ROOT / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

SESSION = "sess-map-1"
SECRETS = ("SECRET-HEADLINE-7f3", "MEMORY-BODY-9c1")


def _load_map():
    spec = importlib.util.spec_from_file_location("cli_map_under_test", _REPO_ROOT / "bin" / "cli" / "map.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def runtime(tmp_path, bootstrapped_db_template, monkeypatch):
    env = IsolatedRuntimeEnv(tmp_path / "runtime")
    db = copy_bootstrapped_db(bootstrapped_db_template, Path(env["GAIA_DB"]))
    for key in ("HOME", "GAIA_DB", "GAIA_DATA_DIR"):
        monkeypatch.setenv(key, env[key])
    con = sqlite3.connect(db)
    con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES ('me', 'me')")
    con.commit()
    con.close()
    return env, db


def _exec(db: Path, sql: str, params: tuple = ()) -> int:
    con = sqlite3.connect(db)
    try:
        cur = con.execute(sql, params)
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def _seed_due(db):
    _exec(db, "INSERT INTO task_notifications (workspace, task_name, headline, kind, unread) "
              "VALUES ('me', 'reminder', ?, 'reminder', 0)", (SECRETS[0],))


def _handoff(db, state, session):
    return _exec(db, "INSERT INTO agent_contract_handoffs "
                     "(contract_id, agent_id, workspace, agent_state, session_id, raw_handoff_json) "
                     "VALUES (?, 'a0123456789abcdef0', 'me', ?, ?, '{}')",
                 (f"a0123456789abcdef0.{state.lower()}", state, session))


def _seed_proposal(db):
    handoff = _handoff(db, "COMPLETE", "sess-other")
    _exec(db, "INSERT INTO proposals (handoff_id, item_index, kind, payload) VALUES (?, 0, 'memory', ?)",
          (handoff, '{"create": {"slug": "x", "body": "%s"}}' % SECRETS[1]))


def _seed_dispatched(db):
    _handoff(db, "DISPATCHED", SESSION)


def _seed_signal(db):
    from gaia.signals import _write_cursor

    _write_cursor(SESSION, 0, 0)
    _exec(db, "INSERT INTO signals (subject, source, workspace, ref, op, session_id) "
              "VALUES ('user', 'memory', 'me', 'user_x', 'update', 'sess-other')")


def _seed_plan(db):
    brief = _exec(db, "INSERT INTO briefs (workspace, name, status) VALUES ('me', 'export-csv', 'open')")
    plan = _exec(db, "INSERT INTO plans (brief_id, status) VALUES (?, 'active')", (brief,))
    _exec(db, "INSERT INTO tasks (plan_id, order_num, status) VALUES (?, 1, 'done')", (plan,))
    _exec(db, "INSERT INTO tasks (plan_id, order_num, status) VALUES (?, 2, 'pending')", (plan,))


SOURCES = {
    "due": (_seed_due, lambda m: m._due_lines("me"), "gaia notifications show <id>"),
    "proposals": (_seed_proposal, lambda m: m._proposal_lines(), "gaia contract proposals accept|dismiss"),
    "dispatched": (_seed_dispatched, lambda m: m._dispatched_lines(SESSION, caller_agent=""), "--state DISPATCHED"),
    "signals": (_seed_signal, lambda m: m._signal_lines(SESSION), "gaia signals --since 0"),
    "plans": (_seed_plan, lambda m: m._plan_lines("me"), "gaia plan show export-csv ; gaia task show --id"),
}


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_each_now_source_absent_then_present(runtime, name):
    _env, db = runtime
    seed, read, verb = SOURCES[name]
    mod = _load_map()
    assert read(mod) == []
    seed(db)
    lines = read(mod)
    assert len(lines) == 1 and verb in lines[0][1], lines


def test_paused_plan_is_not_in_now(runtime):
    _env, db = runtime
    _seed_plan(db)
    _exec(db, "UPDATE plans SET paused_at = '2026-10-01T00:00:00Z'")
    assert _load_map()._plan_lines("me") == []


def test_due_verbs_are_runnable_commands(runtime):
    import argparse
    import shlex

    _env, db = runtime
    _seed_due(db)
    mod = _load_map()
    (_what, verbs), = mod._due_lines("me")
    assert "gaia notifications snooze <id> --for 1d" in verbs
    spec = importlib.util.spec_from_file_location("cli_notifications_under_test",
                                                  _REPO_ROOT / "bin" / "cli" / "notifications.py")
    notifications = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(notifications)
    parser = argparse.ArgumentParser(exit_on_error=False)
    notifications.register(parser.add_subparsers())
    for command in verbs.split("(")[0].split(";"):
        words = shlex.split(command.replace("<id>", "7"))
        assert words[0] == "gaia", command
        parser.parse_args(words[1:])


def test_callers_own_dispatched_turn_is_not_listed(runtime):
    _env, db = runtime
    _seed_dispatched(db)
    mod = _load_map()
    assert mod._dispatched_lines(SESSION, caller_agent="gaia-system") == []
    assert len(mod._dispatched_lines(SESSION, caller_agent="")) == 1


def test_empty_now_is_omitted_and_static_sections_print(runtime):
    mod = _load_map()
    text = mod.render_map(mod.collect_now(SESSION, "me"), "me")
    assert "NOW --" not in text
    assert "HABITS --" in text and "YOU WANT -> USE" in text


def test_intent_expands_one_row_and_unknown_intent_fails(runtime, capsys):
    mod = _load_map()
    assert mod.cmd_map(type("A", (), {"intent": "remember"})()) == 0
    out = capsys.readouterr().out
    assert "command:  gaia memory add|append|search|show" in out and "example:" in out
    assert mod.cmd_map(type("A", (), {"intent": "nope"})()) == 2


def _seed_names(db):
    _exec(db, "INSERT OR IGNORE INTO workspaces (name, identity, root_path) VALUES ('aaxis', 'aaxis', '/w/aaxis')")
    for name, path, group in (("bildwiz-iac", "/w/aaxis/bildwiz/bildwiz-iac", "bildwiz"),
                              ("newco-pitot", "/w/aaxis/bildwiz/newco-pitot", "bildwiz"),
                              ("aos", "/w/aaxis/aos/aos", "aos")):
        _exec(db, "INSERT INTO projects (workspace, name, path, group_name, status) "
                  "VALUES ('aaxis', ?, ?, ?, 'active')", (name, path, group))


def test_a_word_that_is_not_an_intent_resolves_as_a_name(runtime, capsys):
    _env, db = runtime
    _seed_names(db)
    mod = _load_map()

    assert mod.cmd_map(type("A", (), {"intent": "BildWiz"})()) == 0
    out = capsys.readouterr().out
    assert "bildwiz -- group of 2 projects in workspace aaxis: bildwiz-iac, newco-pitot" in out
    assert "-> gaia context project bildwiz --workspace aaxis" in out

    assert mod.cmd_map(type("A", (), {"intent": "aaxis"})()) == 0
    assert "aaxis -- workspace of 3 projects in 2 groups: aos, bildwiz" in capsys.readouterr().out


def test_a_name_at_two_levels_is_named_as_both(runtime, capsys):
    _env, db = runtime
    _seed_names(db)

    assert _load_map().cmd_map(type("A", (), {"intent": "aos"})()) == 0
    out = capsys.readouterr().out
    assert "aos -- group of 1 projects" in out and "aos -- project in workspace aaxis, group aos" in out
    assert "names more than one level" in out


def test_retired_and_undeclared_workspaces_are_not_names(runtime, capsys):
    _env, db = runtime
    _seed_names(db)
    _exec(db, "INSERT INTO workspaces (name, identity, status) VALUES ('rnd', 'rnd', 'retired')")
    _exec(db, "INSERT INTO workspaces (name, identity) VALUES ('bildwiz', 'bildwiz')")
    mod = _load_map()

    assert mod.cmd_map(type("A", (), {"intent": "bildwiz"})()) == 0
    out = capsys.readouterr().out
    assert "workspace of" not in out and "more than one level" not in out, out
    assert mod.cmd_map(type("A", (), {"intent": "rnd"})()) == 2


def test_a_misspelled_name_answers_with_the_closest_names(runtime, capsys):
    _env, db = runtime
    _seed_names(db)

    assert _load_map().cmd_map(type("A", (), {"intent": "buildwiz"})()) == 1
    out = capsys.readouterr().out
    assert out.startswith("No name 'buildwiz'; closest names:") and "bildwiz -- group" in out


def test_closest_names_at_different_levels_are_not_one_name_at_two_levels(runtime, capsys):
    _env, db = runtime
    _seed_names(db)
    _exec(db, "INSERT INTO projects (workspace, name, path, group_name, status) "
              "VALUES ('aaxis', 'bildwizz', '/w/aaxis/bildwizz', NULL, 'active')")

    assert _load_map().cmd_map(type("A", (), {"intent": "buildwiz"})()) == 1
    out = capsys.readouterr().out
    assert "bildwiz -- group" in out and "bildwizz -- project" in out, out
    assert "more than one level" not in out


def _sha(db: Path) -> str:
    return hashlib.sha256(db.read_bytes()).hexdigest()


def test_cli_writes_nothing_and_prints_no_secret_or_body(runtime, tmp_path):
    env, db = runtime
    for seed, _read, _verb in SOURCES.values():
        seed(db)
    _exec(db, "UPDATE task_notifications SET workspace = NULL")
    _exec(db, "UPDATE briefs SET workspace = 'global'")
    before_sha, before_dump = _sha(db), list(sqlite3.connect(db).iterdump())
    run_env = dict(env)
    run_env["GAIA_HOST_SESSION_ID"] = SESSION
    run_env.pop("GAIA_DISPATCH_AGENT", None)
    result = subprocess.run([sys.executable, str(_GAIA), "map"], cwd=tmp_path, env=run_env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert _sha(db) == before_sha
    assert list(sqlite3.connect(db).iterdump()) == before_dump
    for source_line in ("due:", "proposal(s)", "never closed", "unread signal", "next task"):
        assert source_line in result.stdout, result.stdout
    for secret in SECRETS:
        assert secret not in result.stdout


@pytest.mark.parametrize("words", ["map", "map remember"])
def test_orchestrator_guard_admits_map(words):
    from modules.security.gaia_cli_only_guard import check

    payload = {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Bash"}
    assert check(f"{_GAIA} {words}", payload) == (True, None)
    assert check(f"{_GAIA} map --fix", payload)[0] is False
