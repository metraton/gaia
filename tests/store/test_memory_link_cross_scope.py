"""A supersedes link can join rows of different owners and the lineage survives.

A user row now lives in the workspace-less user scope while the row it
replaces may still sit under the workspace it was first written in. The link
between them is accepted, the replaced row leaves both the session's birth
block and every dispatched subagent's kernel, `gaia memory story` walks from
either end, a link to a row that does not exist is still refused, and the
schema change keeps older installations writing to the shared database.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_GAIA = _REPO / "bin" / "gaia"
for _path in (_REPO, _REPO / "bin", _REPO / "hooks", _REPO / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


@pytest.fixture
def gaia(tmp_path):
    """The real CLI over a throwaway HOME, data dir and database, with one legacy and one current user row."""
    home = tmp_path / "home"
    home.mkdir()
    data = tmp_path / "data"
    env = {
        **os.environ,
        "HOME": str(home),
        "GAIA_DATA_DIR": str(data),
        "GAIA_DB": str(data / "gaia.db"),
    }
    env.pop("GAIA_DISPATCH_AGENT", None)

    def run(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_GAIA), *argv],
            env=env, cwd=tmp_path, capture_output=True, text=True, check=False,
        )

    run.db = data / "gaia.db"
    added = run(
        "memory", "add", "--name", "user_pref_new", "--type", "user", "--class", "anchor",
        "--audience", "executor", "--description", "Preferencia: nueva.", "--body", "nueva",
    )
    assert added.returncode == 0, added.stderr
    con = sqlite3.connect(str(run.db))
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name) VALUES ('me')")
        con.execute(
            "INSERT INTO memory (workspace, name, type, description, body, class, audience, "
            "updated_at, created_at) VALUES ('me', 'user_pref_old', 'user', 'Preferencia: vieja.', "
            "'vieja', 'anchor', 'executor', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )
        con.commit()
    finally:
        con.close()
    return run


def _link(gaia, dst: str = "user_pref_old") -> subprocess.CompletedProcess:
    return gaia("memory", "link", "user_pref_new", dst, "--kind=supersedes", "--workspace", "me")


def test_a_user_row_supersedes_its_predecessor_in_another_workspace(gaia):
    """The cross-owner link is written, and the replaced row leaves the birth block and the kernel."""
    from gaia.store.reader import user_anchor_rows

    result = _link(gaia)

    assert result.returncode == 0, result.stdout + result.stderr
    birth = gaia("memory", "get-relevant", "--workspace", "me", "--sections", "anchor", "--json")
    injected = {item["name"] for item in json.loads(birth.stdout)["items"]}
    kernel = {row["name"] for row in user_anchor_rows(gaia.db)}
    assert "user_pref_new" in injected and "user_pref_old" not in injected
    assert "user_pref_new" in kernel and "user_pref_old" not in kernel


def test_story_walks_the_cross_owner_lineage_from_either_end(gaia):
    """From the new row the old one is its superseded predecessor, and from the old one the new is its successor."""
    assert _link(gaia).returncode == 0

    forward = json.loads(gaia("memory", "story", "user_pref_new", "--workspace", "me", "--json").stdout)
    backward = json.loads(gaia("memory", "story", "user_pref_old", "--workspace", "me", "--json").stdout)

    roles_forward = {node["name"]: node["role"] for node in forward["nodes"]}
    roles_backward = {node["name"]: node["role"] for node in backward["nodes"]}
    assert roles_forward.get("user_pref_old") == "superseded"
    assert roles_backward.get("user_pref_new") == "successor"
    assert all(state["present"] for state in forward["final_states"])


def test_retiring_the_old_rows_workspace_keeps_the_lineage(gaia):
    """Folding the old row's workspace into another carries the link's dst with the row, so it stays retired."""
    from gaia.store.reader import build_memory_story, user_anchor_rows
    from gaia.store.workspace_retire import apply_retire

    assert _link(gaia).returncode == 0
    con = sqlite3.connect(str(gaia.db))
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name) VALUES ('ws')")
        con.commit()
    finally:
        con.close()

    report = apply_retire("me", "ws", db_path=gaia.db)

    assert report["tables"]["memory_links"]["repoint"] == 1
    kernel = {row["name"] for row in user_anchor_rows(gaia.db)}
    assert kernel == {"user_pref_new"}
    story = build_memory_story("_gaia_user", "user_pref_new", db_path=gaia.db)
    assert all(state["present"] for state in story["final_states"])
    assert {node["name"] for node in story["nodes"]} == {"user_pref_new", "user_pref_old"}


def test_a_link_to_a_row_that_does_not_exist_is_still_refused(gaia):
    """Crossing owners does not open a door to dangling edges."""
    result = _link(gaia, dst="user_pref_missing")

    assert result.returncode != 0
    con = sqlite3.connect(str(gaia.db))
    try:
        assert con.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0] == 0
    finally:
        con.close()


def test_the_schema_change_keeps_older_installations_writing(tmp_path):
    """The migration is declared backward and the shipped chain does not raise the minimum writer version."""
    import migration_guard
    from cli.doctor import EXPECTED_SCHEMA_VERSION

    current = EXPECTED_SCHEMA_VERSION
    migration = _REPO / "scripts" / "migrations" / f"v{current - 1}_to_v{current}.sql"
    assert "dst_workspace" in migration.read_text(encoding="utf-8")
    assert not migration_guard.is_breaking(migration.read_text(encoding="utf-8"))

    db = tmp_path / "fresh.db"
    env = {**os.environ, "GAIA_DB": str(db), "SCHEMA_FILE": str(_REPO / "gaia" / "store" / "schema.sql")}
    result = subprocess.run(
        [sys.executable, str(_REPO / "scripts" / "bootstrap_database.py")],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    con = sqlite3.connect(str(db))
    try:
        minimums = dict(con.execute("SELECT version, min_code_version FROM schema_version"))
    finally:
        con.close()
    assert minimums[current] == minimums[current - 1]
