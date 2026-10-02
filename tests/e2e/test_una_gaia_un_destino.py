"""One Gaia, one destination: two installs over one database behave as one.

Workspace A runs the plugin channel and workspace B the npm channel, both over
the same HOME, data home and database. Each test is something the user feels
when they work across the two: what one workspace wrote is there from the
other, switching channel loses nothing, folding one workspace into the other
is governed and reversible, removing one install leaves the other working, and
every session is born with the same picture of the user.

HOME, GAIA_DATA_DIR, GAIA_DB and every workspace live under tmp_path; nothing
here reads the real ~/.gaia or a real workspace.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.conftest import copy_bootstrapped_db

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (_REPO_ROOT, _REPO_ROOT / "hooks", _REPO_ROOT / "bin"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from modules.context.kernel_builder import build_kernel_context  # noqa: E402
from modules.core import paths, plugin_setup  # noqa: E402
from modules.session import session_manifest  # noqa: E402
from modules.session.session_context_writer import SessionContextWriter  # noqa: E402

_GAIA_BIN = _REPO_ROOT / "bin" / "gaia"
_USER_PROMPT_SUBMIT = _REPO_ROOT / "hooks" / "user_prompt_submit.py"
USER_SCOPE = "_gaia_user"
WS_A, WS_B = "ws_a", "ws_b"
_NPM_PACKAGE = "@jaguilar87/gaia"


@pytest.fixture()
def one_home(tmp_path, monkeypatch, bootstrapped_db_template):
    """Workspace A (project pa) and workspace B (project pb) over one database."""
    data = tmp_path / "data"
    db = copy_bootstrapped_db(bootstrapped_db_template, data / "gaia.db")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(db))
    for key in ("GAIA_DISPATCH_WORKSPACE", "GAIA_WORKSPACE", "GAIA_DISPATCH_AGENT",
                "GAIA_SESSION_ID", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA", "INIT_CWD",
                "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(key, raising=False)
    paths.clear_path_cache()

    dirs = {}
    con = sqlite3.connect(db)
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES (?, ?)",
                    (USER_SCOPE, USER_SCOPE))
        for ws, project in ((WS_A, "pa"), (WS_B, "pb")):
            root = tmp_path / ws
            folder = root / project
            folder.mkdir(parents=True)
            dirs[ws] = SimpleNamespace(root=root, project=folder, name=project)
            con.execute("INSERT INTO workspaces (name, root_path, identity) VALUES (?, ?, ?)",
                        (ws, str(root), ws))
            con.execute("INSERT INTO projects (workspace, name, path, status) "
                        "VALUES (?, ?, ?, 'active')", (ws, project, str(folder)))
            con.execute(
                "INSERT INTO project_context_contracts (workspace, contract_name, payload) "
                "VALUES (?, 'project_identity', ?)",
                (ws, json.dumps({project: {"name": project, "local_path": str(folder),
                                           "description": f"{project} project"}})),
            )
        con.commit()
    finally:
        con.close()
    yield SimpleNamespace(tmp=tmp_path, db=db, data=data, home=home, a=dirs[WS_A], b=dirs[WS_B],
                          monkeypatch=monkeypatch)
    paths.clear_path_cache()


def _gaia(cwd: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_GAIA_BIN), *argv], cwd=str(cwd), env=dict(os.environ),
        capture_output=True, text=True, timeout=120,
    )


def _ok(result: subprocess.CompletedProcess) -> dict:
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout) if result.stdout.strip() else {}


def _write_user_row(cwd: Path, name: str, sentence: str) -> None:
    _ok(_gaia(cwd, "memory", "add", "--name", name, "--type", "user", "--class", "anchor",
              "--description", sentence, "--body", sentence, "--json"))


def _query(db: Path, sql: str, params: tuple = ()) -> list[tuple]:
    con = sqlite3.connect(db)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def _execute(db: Path, *statements: tuple) -> None:
    con = sqlite3.connect(db)
    try:
        con.execute("PRAGMA foreign_keys = ON")
        for sql, params in statements:
            con.execute(sql, params)
        con.commit()
    finally:
        con.close()


def _checksums(db: Path) -> dict[str, str]:
    """One digest per real table, over every row including its rowid."""
    con = sqlite3.connect(db)
    try:
        virtual = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND sql LIKE 'CREATE VIRTUAL%'")}
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
            if not any(r[0] == v or r[0].startswith(v + "_") for v in virtual)]
        digests = {}
        for table in tables:
            try:
                rows = con.execute(f'SELECT rowid, * FROM "{table}"').fetchall()
            except sqlite3.OperationalError:
                rows = con.execute(f'SELECT * FROM "{table}"').fetchall()
            digests[table] = hashlib.sha256(repr(sorted(rows, key=repr)).encode()).hexdigest()
        return digests
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 1. What the user is belongs to no workspace
# ---------------------------------------------------------------------------

def test_a_user_row_written_from_a_is_visible_from_b(one_home):
    sentence = "Preferencia: versioned artifacts are written in English."
    _write_user_row(one_home.a.project, "user_pref_english", sentence)

    shown = _ok(_gaia(one_home.b.project, "memory", "show", "user_pref_english", "--json"))

    assert sentence in json.dumps(shown)
    assert _query(one_home.db, "SELECT workspace FROM memory WHERE name = 'user_pref_english'") \
        == [(USER_SCOPE,)]


# ---------------------------------------------------------------------------
# 2. A project's memory and briefs follow the project
# ---------------------------------------------------------------------------

def test_a_projects_memory_and_brief_written_from_a_are_read_from_b_without_workspace(one_home):
    _ok(_gaia(one_home.a.project, "memory", "add", "--name", "pa_open_thread", "--type", "project",
              "--initiative", "pa", "--class", "thread", "--status", "open",
              "--description", "Pendiente: land the pa release.", "--body", "land it",
              "--workspace", WS_A, "--json"))
    _ok(_gaia(one_home.a.project, "brief", "new", "pa-brief", "--headless",
              "--title", "PA brief", "--objective", "ship pa", "--json"))
    assert _query(one_home.db, "SELECT workspace FROM briefs WHERE name = 'pa-brief'") == [(WS_A,)]

    pending = _ok(_gaia(one_home.b.project, "memory", "get-relevant", "--initiative", "pa", "--json"))
    brief = _gaia(one_home.b.project, "brief", "show", "pa-brief")

    assert [item["name"] for item in pending["items"]] == ["pa_open_thread"]
    assert brief.returncode == 0, brief.stdout + brief.stderr
    assert "ship pa" in brief.stdout


# ---------------------------------------------------------------------------
# 3. Changing channel loses nothing and welcomes nobody as a first install
# ---------------------------------------------------------------------------

def test_switching_channel_keeps_the_session_and_shows_no_first_install_notice(one_home):
    monkeypatch = one_home.monkeypatch
    (one_home.b.root / ".claude").mkdir()

    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(one_home.tmp / "channel-plugin"))
    paths.clear_path_cache()
    SessionContextWriter().update_context({"event_type": "probe", "detail": "written by the plugin"})

    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(one_home.tmp / "channel-other"))
    paths.clear_path_cache()
    context = json.loads((paths.get_session_dir() / "context.json").read_text())
    assert [e["detail"] for e in context["critical_events"]] == ["written by the plugin"]

    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT)}
    payload = {"session_id": "one-home", "hook_event_name": "UserPromptSubmit", "prompt": "hello"}
    proc = subprocess.run(
        [sys.executable, str(_USER_PROMPT_SUBMIT)], input=json.dumps(payload), env=env,
        cwd=one_home.b.root, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "installed for the first time" not in proc.stdout
    assert not (one_home.tmp / "channel-other" / ".plugin-initialized").exists()
    assert plugin_setup.marker_path().resolve().is_relative_to(one_home.data.resolve())


# ---------------------------------------------------------------------------
# 4. Folding B into A: governed and reversible
# ---------------------------------------------------------------------------

def _seed_b(one_home) -> None:
    """What B owns and wrote: ownership rows, a collision with A, and history."""
    ts = "2026-09-01T00:00:00Z"
    db = one_home.db
    _write_user_row(one_home.b.project, "b_user", "Hecho: the user runs the B workspace.")
    _ok(_gaia(one_home.b.project, "brief", "new", "b-brief", "--headless",
              "--title", "B brief", "--objective", "ship pb", "--json"))
    for name, kind, extra in (("b_thread", "project", ("--initiative", "pb")),
                              ("b_lesson", "feedback", ()),
                              ("shared_name", "feedback", ())):
        _ok(_gaia(one_home.b.project, "memory", "add", "--name", name, "--type", kind, *extra,
                  "--description", "Hecho: from B.", "--body", "b", "--workspace", WS_B, "--json"))
    _ok(_gaia(one_home.a.project, "memory", "add", "--name", "shared_name", "--type", "feedback",
              "--description", "Hecho: from A.", "--body", "a", "--workspace", WS_A, "--json"))
    _ok(_gaia(one_home.b.project, "memory", "link", "b_thread", "b_lesson", "--kind", "relates_to",
              "--workspace", WS_B, "--json"))
    _execute(
        db,
        ("INSERT INTO apps (workspace, project, name) VALUES (?, 'pb', 'cli')", (WS_B,)),
        ("INSERT INTO project_facets (workspace, project, scope, key) "
         "VALUES (?, 'pb', 'language', 'python')", (WS_B,)),
        ("INSERT INTO integrations (workspace, name) VALUES (?, 'github')", (WS_B,)),
        ("INSERT INTO integrations (workspace, name) VALUES (?, 'github')", (WS_A,)),
        ("INSERT INTO episodes (episode_id, workspace, timestamp, title) "
         "VALUES ('ep-b-1', ?, ?, 'written from B')", (WS_B, ts)),
        ("INSERT INTO harness_events (workspace, ts, type) VALUES (?, ?, 'command.executed')",
         (WS_B, ts)),
        ("INSERT INTO agent_contract_handoffs (agent_id, workspace, agent_state, raw_handoff_json, "
         "contract_id) VALUES ('a0123456789abcdef', ?, 'COMPLETE', '{}', 'a0123456789abcdef.b')",
         (WS_B,)),
    )


def _retire(one_home, *flags: str) -> tuple[int, dict]:
    result = _gaia(one_home.tmp, "workspace", "retire", WS_B, "--into", WS_A, "--json", *flags)
    return result.returncode, json.loads(result.stdout) if result.stdout.strip() else {}


def _undo(one_home, ledger: str) -> tuple[int, dict]:
    result = _gaia(one_home.tmp, "workspace", "retire", "--undo", ledger, "--yes", "--json")
    return result.returncode, json.loads(result.stdout) if result.stdout.strip() else {}


def _resolve(strategy: str) -> tuple[str, ...]:
    return tuple(
        arg for table in ("integrations", "memory", "project_context_contracts")
        for arg in ("--on-conflict", f"{table}={strategy}")
    )


_RESOLVE = _resolve("keep-target")


def test_folding_b_into_a_is_previewed_applied_and_undone(one_home):
    _seed_b(one_home)
    before = _checksums(one_home.db)

    rc, dry = _retire(one_home, "--dry-run", *_RESOLVE)
    assert rc == 0 and dry["mode"] == "dry-run", dry
    assert _checksums(one_home.db) == before
    assert not (one_home.data / "backups").exists()

    rc, applied = _retire(one_home, "--yes", *_RESOLVE)
    assert rc == 0 and applied["mode"] == "applied", applied
    assert Path(applied["backup"]).is_file() and Path(applied["ledger"]).is_file()
    assert _query(one_home.db, "SELECT DISTINCT workspace FROM briefs WHERE name = 'b-brief'") \
        == [(WS_A,)]
    assert _query(one_home.db, "SELECT workspace FROM memory WHERE name = 'b_user'") \
        == [(USER_SCOPE,)]
    assert _query(one_home.db, "SELECT workspace FROM episodes WHERE episode_id = 'ep-b-1'") \
        == [(WS_B,)], "history keeps the name it was written under"
    assert _query(one_home.db, "SELECT alias, target FROM workspace_aliases") == [(WS_B, WS_A)]

    from gaia.store.reader import cross_surface_query
    episodes = cross_surface_query(surface="episodes", workspace=WS_A)
    assert "ep-b-1" in {e["raw"]["episode_id"] for e in episodes}
    from_a = _gaia(one_home.a.project, "brief", "show", "b-brief")
    assert from_a.returncode == 0, from_a.stdout + from_a.stderr

    rc, again = _retire(one_home, "--yes", *_RESOLVE)
    assert rc == 0 and again["mode"] == "noop", again

    rc, undone = _undo(one_home, applied["ledger"])
    assert rc == 0 and undone["mode"] == "undone", undone
    assert _query(one_home.db, "SELECT DISTINCT workspace FROM briefs WHERE name = 'b-brief'") \
        == [(WS_B,)]
    assert _query(one_home.db, "SELECT workspace FROM memory WHERE name = 'b_thread'") == [(WS_B,)]
    assert _query(one_home.db, "SELECT COUNT(*) FROM workspace_aliases") == [(0,)]
    assert sorted(_query(one_home.db, "SELECT workspace FROM integrations")) == [(WS_A,), (WS_B,)]
    after = _checksums(one_home.db)
    changed = {t for t in before if before[t] != after.get(t)}
    assert changed <= set(_PROVENANCE_TABLES), changed


# The SQL triggers append a before/after line to these logs on every re-key, so
# an apply followed by an undo leaves lines the undo is right not to erase.
_PROVENANCE_TABLES = ("memory_history", "project_history", "project_context_contracts_history")
_HISTORY_TABLES = ("episodes", "episode_anomalies", "harness_events", "agent_contract_handoffs")
_OWNED = ("projects", "apps", "project_facets", "briefs", "project_context_contracts", "integrations")


def _count(db: Path, table: str, workspace: str) -> int:
    return _query(db, f'SELECT COUNT(*) FROM "{table}" WHERE workspace = ?', (workspace,))[0][0]


def _sizes(db: Path, tables: tuple[str, ...]) -> dict[str, dict[str, int]]:
    return {t: {ws: _count(db, t, ws) for ws in (WS_A, WS_B, USER_SCOPE)} for t in tables}


@pytest.mark.parametrize("strategy", ["keep-target", "keep-source"])
def test_the_dry_run_report_is_what_apply_does(one_home, strategy):
    _seed_b(one_home)
    tables = _OWNED + ("memory", "memory_links") + _HISTORY_TABLES
    before = _sizes(one_home.db, tables)

    _, dry = _retire(one_home, "--dry-run", *_resolve(strategy))
    rc, applied = _retire(one_home, "--yes", *_resolve(strategy))
    assert rc == 0, applied
    after = _sizes(one_home.db, tables)

    left = ("mode", "backup", "ledger")
    assert {k: v for k, v in applied.items() if k not in left} \
        == {k: v for k, v in dry.items() if k not in left}

    # keep-target drops the source's row, keep-source the target's; either way
    # one row per resolved key disappears and the source is left with none.
    resolved = {t: len(keys) for t, keys in dry["resolved"].items()}
    target_loses = lambda table: resolved.get(table, 0) if strategy == "keep-source" else 0  # noqa: E731
    source_drops = lambda table: resolved.get(table, 0) if strategy == "keep-target" else 0  # noqa: E731
    for table in _OWNED:
        moved = dry["tables"][table]["move"]
        assert after[table][WS_B] == 0, table
        assert after[table][WS_A] == before[table][WS_A] + moved - target_loses(table), table
        assert before[table][WS_B] == moved + source_drops(table), table

    memory = dry["tables"]["memory"]["move"]
    memory_user = dry["tables"]["memory_user"]["move"]
    assert after["memory"][WS_B] == 0
    assert after["memory"][WS_A] == before["memory"][WS_A] + memory - target_loses("memory")
    assert after["memory"][USER_SCOPE] == before["memory"][USER_SCOPE] + memory_user
    assert before["memory"][WS_B] == memory + memory_user + source_drops("memory")

    links = dry["tables"]["memory_links"]
    total = lambda size: sum(size["memory_links"].values())  # noqa: E731
    assert after["memory_links"][WS_B] == 0
    assert total(before) - total(after) == links["split"] + links["duplicate"]

    for table in _HISTORY_TABLES:
        assert after[table][WS_B] == before[table][WS_B] == dry["history"][table], table

    ledger = json.loads(Path(applied["ledger"]).read_text())
    assert len(ledger["dropped"]) == sum(resolved.values()) + links["split"] + links["duplicate"]


def test_the_dry_run_names_the_collisions_the_apply_refuses_on(one_home):
    _seed_b(one_home)
    before = _checksums(one_home.db)

    _, dry = _retire(one_home, "--dry-run")
    rc, refused = _retire(one_home, "--yes")

    assert dry["collisions"] and dry["collisions"] == refused["collisions"]
    assert rc != 0
    assert _checksums(one_home.db) == before


# ---------------------------------------------------------------------------
# 4b. Undo after the destination moved on
# ---------------------------------------------------------------------------

def _memory_row(workspace: str, name: str, body: str = "after the merge") -> tuple:
    return ("INSERT INTO memory (workspace, name, type, body) VALUES (?, ?, 'feedback', ?)",
            (workspace, name, body))


_DIVERGENCES = {
    "a_new_row_in_the_destination": (
        [_memory_row(WS_A, "written_after_merge")],
        ("SELECT body FROM memory WHERE name = 'written_after_merge'", ())),
    "a_moved_row_edited_in_the_destination": (
        [("UPDATE memory SET body = 'edited after the merge' "
          "WHERE workspace = ? AND name = 'b_lesson'", (WS_A,))],
        ("SELECT body FROM memory WHERE name = 'b_lesson' AND body = 'edited after the merge'", ())),
    "a_new_app_under_a_moved_project": (
        [("INSERT INTO apps (workspace, project, name) VALUES (?, 'pb', 'late_app')", (WS_A,))],
        ("SELECT name FROM apps WHERE name = 'late_app'", ())),
    "an_older_install_writing_under_the_old_name": (
        [_memory_row(WS_B, "late_note")],
        ("SELECT body FROM memory WHERE name = 'late_note'", ())),
    "a_moved_name_written_again_under_the_old_name": (
        [_memory_row(WS_B, "b_thread", "written again in the old workspace")],
        ("SELECT body FROM memory WHERE body = 'written again in the old workspace'", ())),
}


@pytest.mark.parametrize("divergence", sorted(_DIVERGENCES))
def test_undo_after_the_destination_diverged_refuses_or_keeps_what_was_written_since(
    one_home, divergence,
):
    statements, probe = _DIVERGENCES[divergence]
    _seed_b(one_home)
    rc, applied = _retire(one_home, "--yes", *_RESOLVE)
    assert rc == 0, applied
    _execute(one_home.db, *statements)
    written_since = _query(one_home.db, *probe)
    assert written_since, "the probe must see the row it is about to protect"
    before_undo = _checksums(one_home.db)

    result = _gaia(one_home.tmp, "workspace", "retire", "--undo", applied["ledger"], "--yes", "--json")

    if result.returncode == 0:
        assert _query(one_home.db, *probe) == written_since
    else:
        assert result.returncode == 2 and "Traceback" not in result.stderr, result.stdout + result.stderr
        assert json.loads(result.stdout)["error"]
        assert _checksums(one_home.db) == before_undo


def test_undo_keeps_the_user_scope_when_a_user_row_was_written_into_it_since(one_home):
    _execute(
        one_home.db,
        ("DELETE FROM workspaces WHERE name = ?", (USER_SCOPE,)),
        ("INSERT INTO memory (workspace, name, type, body) VALUES (?, 'legacy_user', 'user', 'old')",
         (WS_B,)),
    )
    rc, applied = _retire(one_home, "--yes", *_RESOLVE)
    assert rc == 0, applied
    assert _query(one_home.db, "SELECT workspace FROM memory WHERE name = 'legacy_user'") \
        == [(USER_SCOPE,)]
    _write_user_row(one_home.a.project, "user_after_merge", "Hecho: written after the merge.")

    rc, undone = _undo(one_home, applied["ledger"])

    assert rc == 0, undone
    assert _query(one_home.db, "SELECT workspace FROM memory WHERE name = 'user_after_merge'") \
        == [(USER_SCOPE,)]
    assert _query(one_home.db, "SELECT workspace FROM memory WHERE name = 'legacy_user'") \
        == [(WS_B,)]


# ---------------------------------------------------------------------------
# 5. Removing B's install leaves A working and the database alone
# ---------------------------------------------------------------------------

def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n")


def _npm_workspace(root: Path) -> None:
    """What the package manager leaves before `gaia install` runs: a user dep beside Gaia's."""
    deps = {_NPM_PACKAGE: "file:gaia.tgz", "left-pad": "^1.0.0"}
    _write_json(root / "package.json", {"name": "consumer", "version": "1.0.0", "dependencies": deps})
    package = root / "node_modules" / _NPM_PACKAGE
    _write_json(package / "package.json", {"name": _NPM_PACKAGE, "version": "5.5.0"})
    (package / "bin").mkdir()
    (package / "bin" / "gaia").write_text("#!/usr/bin/env python3\n")
    (root / "node_modules" / "left-pad").mkdir()
    (root / "node_modules" / "left-pad" / "index.js").write_text("module.exports = 1\n")
    _write_json(root / ".claude" / "settings.local.json", {"env": {"USER_VAR": "keep"}})


def _tree(root: Path) -> dict:
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                found[rel] = ("link", os.readlink(path))
            elif path.is_dir():
                found[rel] = ("dir",)
            else:
                found[rel] = ("file", path.read_bytes())
    return found


def test_removing_bs_install_leaves_a_working_and_the_database_untouched(one_home):
    from cli import install, uninstall

    _ok(_gaia(one_home.a.project, "memory", "add", "--name", "pa_fact", "--type", "project",
              "--initiative", "pa", "--description", "Hecho: pa is written from A.",
              "--body", "pa is written from A", "--workspace", WS_A, "--json"))
    _write_json(one_home.a.root / ".claude" / "settings.json",
                {"enabledPlugins": {"gaia@gaia-marketplace": True}})
    _write_json(one_home.a.root / ".claude" / "settings.local.json",
                {"permissions": {"allow": ["UserTool(x)"]}})
    _npm_workspace(one_home.b.root)
    quiet = {"action": "noop", "details": ""}
    with patch.object(install, "_run_bootstrap", return_value={"rc": 0, "detail": ""}), \
         patch.object(install, "_seed_contract_permissions", return_value=quiet), \
         patch.object(install, "_seed_surface_routing", return_value=quiet), \
         patch.object(install, "_workspace_status", return_value=quiet), \
         patch.object(install, "_warn_launcher_shadowed", return_value=None), \
         patch.object(install, "_warn_launcher_dir_absent", return_value=None):
        assert install.cmd_install(argparse.Namespace(
            postinstall=False, quiet=True, verbose=False, db_path=None,
            workspace=str(one_home.b.root), host="claude_code", skip_workspace=False, path=True,
            no_path=False, strict_wiring=False)) == 0
    assert os.path.lexists(one_home.b.root / ".claude" / "hooks")
    a_before, db_before = _tree(one_home.a.root), _checksums(one_home.db)

    with redirect_stdout(io.StringIO()):
        assert uninstall.cmd_uninstall(argparse.Namespace(
            preuninstall=False, workspace=str(one_home.b.root), dry_run=False, quiet=False,
            json=True, db_path=str(one_home.db), no_backup=False,
            snapshot_dir=str(one_home.data / "snapshots"))) == 0

    assert not os.path.lexists(one_home.b.root / ".claude" / "hooks")
    assert not os.path.lexists(one_home.b.root / "node_modules" / _NPM_PACKAGE)
    assert json.loads((one_home.b.root / "package.json").read_text())["dependencies"] \
        == {"left-pad": "^1.0.0"}
    assert _tree(one_home.a.root) == a_before
    assert _checksums(one_home.db) == db_before
    shown = _ok(_gaia(one_home.a.project, "memory", "show", "pa_fact", "--json"))
    assert "pa is written from A" in json.dumps(shown)


# ---------------------------------------------------------------------------
# 6 and 7. Every session is born with the same picture of the user
# ---------------------------------------------------------------------------

_FACT = "Hecho: Jorge runs DevOps for a consultancy and works from two workspaces."
_PREFERENCES = {
    "user_pref_english": "Preferencia: versioned artifacts are written in English.",
    "user_pref_ghx": "Preferencia: GitHub is operated through ghx per command.",
}
_ANCHOR = "PROJECT ANCHOR TEXT that belongs to project pa only."


def _born(one_home, *, plugin: bool) -> str:
    """The birth block as the plugin session in A (or the npm session in B) receives it."""
    monkeypatch = one_home.monkeypatch
    if plugin:
        plugin_root = one_home.tmp / "plugin-root"
        _write_json(plugin_root / "package.json", {"name": _NPM_PACKAGE, "version": "5.5.0"})
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        monkeypatch.chdir(one_home.a.project)
    else:
        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        monkeypatch.chdir(one_home.b.project)
    return session_manifest.build_session_context()


def _user_part(block: str) -> str:
    return block[block.index(session_manifest.BIRTH_SECTION_HEADERS[2]):]


def _standing_user_rows(one_home) -> None:
    _write_user_row(one_home.a.project, "user_jorge", _FACT)
    for name, sentence in _PREFERENCES.items():
        _write_user_row(one_home.b.project, name, sentence)
    _ok(_gaia(one_home.a.project, "memory", "add", "--name", "pa_anchor", "--type", "project",
              "--initiative", "pa", "--class", "anchor",
              "--description", _ANCHOR, "--body", _ANCHOR,
              "--workspace", WS_A, "--json"))


def test_a_plugin_session_in_a_and_an_npm_session_in_b_are_born_with_the_same_user(one_home):
    _standing_user_rows(one_home)

    blocks = [_born(one_home, plugin=True), _born(one_home, plugin=False)]

    for block in blocks:
        assert re.findall(r"(?m)^## .+$", block) == list(session_manifest.BIRTH_SECTION_HEADERS)
        for sentence in (_FACT, *_PREFERENCES.values()):
            assert sentence in block
        assert _ANCHOR not in block
    assert _user_part(blocks[0]) == _user_part(blocks[1])


def test_a_new_user_row_that_supersedes_one_from_another_workspace_replaces_it_everywhere(one_home):
    old = "Preferencia: comments stay dense wherever they explain a decision."
    new = "Preferencia: comments stay minimal; the code says what it does."
    _write_user_row(one_home.a.project, "user_pref_old", old)
    _write_user_row(one_home.b.project, "user_pref_new", new)
    before = [_born(one_home, plugin=True), _born(one_home, plugin=False)]
    assert all(old in block and new in block for block in before)

    _ok(_gaia(one_home.b.project, "memory", "link", "user_pref_new", "user_pref_old",
              "--kind", "supersedes", "--json"))

    blocks = [_born(one_home, plugin=True), _born(one_home, plugin=False)]
    for workspace in (WS_A, WS_B):
        kernel = build_kernel_context(
            {"contract_id": "a0123456789abcdef.beefcafe0123", "agent_id": "a0123456789abcdef",
             "workspace": workspace, "dispatch_prompt": "fix the build",
             "kernel_sections": '{"role": "primary", "surface": "app_ci"}'},
            agent_name="developer", agents_dir=one_home.tmp / "agents", db_path=one_home.db,
        )
        blocks.append(kernel)
    for text in blocks:
        assert new in text
        assert old not in text
