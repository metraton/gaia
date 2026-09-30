"""`gaia workspace retire <source> --into <target>` folds one workspace into another.

The seeded database has the shape of the real split this verb exists for: a
workspace ``me`` that owns projects, briefs, memory and history, and a
workspace ``ws`` that is meant to own them from now on. What the user feels:

- a dry-run changes nothing;
- the apply moves what the report announced and leaves a backup behind;
- the undo gives back the database the apply started from;
- history rows are never rewritten, yet the target reads them through the alias;
- a collision nobody resolved stops the apply before anything is written;
- running it again after it is done does nothing.

Temporary HOME, GAIA_DATA_DIR and GAIA_DB only -- never the real ~/.gaia/gaia.db.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (_REPO_ROOT, _REPO_ROOT / "bin"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

HISTORY_TABLES = ("episodes", "episode_anomalies", "harness_events", "agent_contract_handoffs")

# The SQL triggers append a before/after line to these logs on every re-key, so
# an apply followed by an undo leaves two more lines in each: that is the audit
# trail doing its job, not state the undo failed to restore.
PROVENANCE_TABLES = ("memory_history", "project_history", "project_context_contracts_history")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    home = tmp_path / "home"
    data = tmp_path / "data"
    home.mkdir()
    data.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(data / "gaia.db"))
    for key in ("GAIA_DISPATCH_WORKSPACE", "GAIA_WORKSPACE", "GAIA_DISPATCH_AGENT"):
        monkeypatch.delenv(key, raising=False)

    from gaia.paths import db_path
    from gaia.store.writer import _connect

    path = db_path()
    con = _connect(path)
    try:
        _seed(con, tmp_path)
        con.commit()
    finally:
        con.close()
    return path


def _seed(con: sqlite3.Connection, root: Path) -> None:
    ts = "2026-09-01T00:00:00Z"
    for name, path in (("me", root / "ws" / "me"), ("ws", root / "ws")):
        con.execute("INSERT INTO workspaces (name, root_path) VALUES (?, ?)", (name, str(path)))
    # The user scope exists once any type=user row has been written.
    con.execute("INSERT INTO workspaces (name) VALUES ('_gaia_user')")
    con.execute(
        "INSERT INTO projects (workspace, name, path, status) VALUES ('me', 'gaia', ?, 'active')",
        (str(root / "ws" / "me" / "gaia"),),
    )
    con.execute("INSERT INTO apps (workspace, project, name) VALUES ('me', 'gaia', 'cli')")
    con.execute(
        "INSERT INTO project_facets (workspace, project, scope, key) "
        "VALUES ('me', 'gaia', 'language', 'python')"
    )
    con.execute("INSERT INTO briefs (workspace, name, title) VALUES ('me', 'one-gaia', 'One Gaia')")
    brief_id = con.execute("SELECT id FROM briefs WHERE name = 'one-gaia'").fetchone()[0]
    con.execute("INSERT INTO plans (brief_id) VALUES (?)", (brief_id,))
    con.execute(
        "INSERT INTO project_context_contracts (workspace, contract_name, payload) "
        "VALUES ('me', 'stack', '{}')"
    )
    con.execute("INSERT INTO integrations (workspace, name) VALUES ('me', 'github')")
    con.execute("INSERT INTO integrations (workspace, name) VALUES ('ws', 'slack')")
    con.execute(
        "INSERT INTO scheduled_tasks (workspace, name, schedule_spec) VALUES ('me', 'nightly', '{}')"
    )
    con.execute(
        "INSERT INTO task_notifications (workspace, task_name, headline) "
        "VALUES ('me', 'nightly', 'ran')"
    )
    for name, kind, initiative in (
        ("gaia_thread", "project", "gaia"),
        ("gaia_lesson", "feedback", None),
        ("user_voice", "user", "gaia"),
    ):
        con.execute(
            "INSERT INTO memory (workspace, name, type, body, initiative) VALUES ('me', ?, ?, 'b', ?)",
            (name, kind, initiative),
        )
    con.execute(
        "INSERT INTO memory (workspace, name, type, body) VALUES ('ws', 'ws_note', 'project', 'b')"
    )
    con.execute(
        "INSERT INTO memory_links (workspace, src_name, dst_name, kind) "
        "VALUES ('me', 'gaia_thread', 'gaia_lesson', 'relates_to')"
    )
    con.execute(
        "INSERT INTO memory_links (workspace, src_name, dst_name, kind) "
        "VALUES ('me', 'user_voice', 'gaia_thread', 'relates_to')"
    )
    con.execute(
        "INSERT INTO episodes (episode_id, workspace, timestamp, title) "
        "VALUES ('ep-me-1', 'me', ?, 'merge rehearsal')",
        (ts,),
    )
    con.execute(
        "INSERT INTO episode_anomalies (episode_id, workspace, timestamp, type) "
        "VALUES ('ep-me-1', 'me', ?, 'no_tool_use')",
        (ts,),
    )
    con.execute(
        "INSERT INTO harness_events (workspace, ts, type) VALUES ('me', ?, 'command.executed')",
        (ts,),
    )
    con.execute(
        "INSERT INTO agent_contract_handoffs (agent_id, workspace, agent_state, raw_handoff_json, "
        "contract_id) VALUES ('a0123456789abcdef', 'me', 'COMPLETE', '{}', 'a0123456789abcdef.x')"
    )


def _checksums(path: Path) -> dict[str, str]:
    """One digest per real table, over every row including its rowid."""
    con = sqlite3.connect(str(path))
    try:
        virtual = {
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND sql LIKE 'CREATE VIRTUAL%'"
            )
        }
        tables = [
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
            if not any(r[0] == v or r[0].startswith(v + "_") for v in virtual)
        ]
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


def _rows(path: Path, sql: str, params: tuple = ()) -> list[tuple]:
    con = sqlite3.connect(str(path))
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def _gaia(*argv: str, capsys) -> tuple[int, dict]:
    from cli import workspace as workspace_cli

    parser = argparse.ArgumentParser(prog="gaia")
    workspace_cli.register(parser.add_subparsers(dest="command"))
    args = parser.parse_args(list(argv))
    capsys.readouterr()
    rc = args.func(args)
    out = capsys.readouterr().out
    return rc, json.loads(out) if out.strip() else {}


def test_dry_run_reports_and_changes_nothing(db, capsys):
    before = _checksums(db)
    rc, report = _gaia("workspace", "retire", "me", "--into", "ws", "--dry-run", "--json", capsys=capsys)
    assert rc == 0, report
    assert report["mode"] == "dry-run"
    moves = report["tables"]
    assert moves["projects"]["move"] == 1
    assert moves["briefs"]["move"] == 1
    assert moves["memory"]["move"] == 2
    assert moves["memory_user"]["move"] == 1
    assert moves["memory_links"]["split"] == 1
    assert report["user_rows_to_adjudicate"] == [
        {"name": "user_voice", "initiative": "gaia", "project_ref": None}
    ]
    assert report["history"]["episodes"] == 1
    assert _checksums(db) == before


def test_apply_moves_ownership_keeps_history_and_undo_restores(db, capsys):
    from gaia.project import cli_workspace
    from gaia.store.reader import cross_surface_query
    from gaia.store.writer import list_agent_contract_handoffs

    before = _checksums(db)
    history_before = {t: _rows(db, f"SELECT * FROM {t}") for t in HISTORY_TABLES}

    rc, result = _gaia("workspace", "retire", "me", "--into", "ws", "--yes", "--json", capsys=capsys)
    assert rc == 0, result
    assert result["mode"] == "applied"
    assert Path(result["backup"]).is_file()
    assert Path(result["ledger"]).is_file()

    by_ws = dict(_rows(db, "SELECT workspace, COUNT(*) FROM projects GROUP BY workspace"))
    assert by_ws == {"ws": 1}
    assert _rows(db, "SELECT workspace FROM apps") == [("ws",)]
    assert _rows(db, "SELECT workspace FROM briefs") == [("ws",)]
    assert sorted(_rows(db, "SELECT workspace, name FROM memory")) == [
        ("_gaia_user", "user_voice"), ("ws", "gaia_lesson"), ("ws", "gaia_thread"), ("ws", "ws_note"),
    ]
    assert _rows(db, "SELECT workspace, src_name FROM memory_links") == [("ws", "gaia_thread")]
    assert _rows(db, "SELECT alias, target FROM workspace_aliases") == [("me", "ws")]

    assert {t: _rows(db, f"SELECT * FROM {t}") for t in HISTORY_TABLES} == history_before
    episodes = cross_surface_query(surface="episodes", workspace="ws")
    assert [e["raw"]["episode_id"] for e in episodes] == ["ep-me-1"]
    assert [h["workspace"] for h in list_agent_contract_handoffs(workspace="ws")] == ["me"]
    assert cli_workspace("me") == "ws"

    applied = _checksums(db)
    assert {"projects", "briefs", "memory", "workspace_aliases"} <= {
        t for t in before if before[t] != applied.get(t)
    }

    rc, undone = _gaia("workspace", "retire", "--undo", result["ledger"], "--yes", "--json", capsys=capsys)
    assert rc == 0, undone
    after = _checksums(db)
    changed = {t for t in before if before[t] != after.get(t)}
    assert changed <= set(PROVENANCE_TABLES), changed
    assert cli_workspace("me") == "me"


def test_unresolved_collision_aborts_without_writing(db, capsys):
    con = sqlite3.connect(str(db))
    con.execute("INSERT INTO integrations (workspace, name) VALUES ('ws', 'github')")
    con.commit()
    con.close()
    before = _checksums(db)

    rc, result = _gaia("workspace", "retire", "me", "--into", "ws", "--yes", "--json", capsys=capsys)
    assert rc != 0
    assert result["collisions"]["integrations"] == ["github"]
    assert _checksums(db) == before
    assert not (db.parent / "backups").exists()

    rc, result = _gaia(
        "workspace", "retire", "me", "--into", "ws", "--yes", "--json",
        "--on-conflict", "integrations=keep-target", capsys=capsys,
    )
    assert rc == 0, result
    assert _rows(db, "SELECT workspace, name FROM integrations ORDER BY name") == [
        ("ws", "github"), ("ws", "slack"),
    ]


def test_second_run_does_nothing(db, capsys):
    rc, _ = _gaia("workspace", "retire", "me", "--into", "ws", "--yes", "--json", capsys=capsys)
    assert rc == 0
    before = _checksums(db)
    backups = sorted((db.parent / "backups").iterdir())

    rc, again = _gaia("workspace", "retire", "me", "--into", "ws", "--yes", "--json", capsys=capsys)
    assert rc == 0, again
    assert again["mode"] == "noop"
    assert _checksums(db) == before
    assert sorted((db.parent / "backups").iterdir()) == backups
