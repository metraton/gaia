"""
Integration tests for `gaia memory add` (DB-only writer).

Mirrors the pattern used by ``test_brief_cli.py``:
  - Routes the substrate DB into ``tmp_path`` via ``GAIA_DATA_DIR`` so tests
    never touch the user's real ``~/.gaia/gaia.db``.
  - Pins cwd to ``tmp_path`` so any accidental filesystem write under a
    relative ``.claude/projects/.../memory/`` path would land where the test
    can detect it.
  - Asserts zero filesystem side effects for every successful insert/update.

Covers the AC of the brief that introduced ``gaia memory add`` (B8 follow-up):
DB-canonical curated memory, no MD writes, UPSERT on duplicate name,
clear error on invalid type.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

# Ensure the gaia package and bin/ are importable
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Route ``gaia.paths.db_path()`` to a tmp dir so tests are isolated."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


def _read_memory_row(db_path: Path, workspace: str, name: str) -> dict | None:
    import sqlite3
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT project, name, type, description, body, origin_session_id, "
            "updated_at FROM memory WHERE project = ? AND name = ?",
            (workspace, name),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Insert path
# ---------------------------------------------------------------------------

def test_add_inserts_row_in_db(tmp_db, tmp_path, monkeypatch, capsys):
    """`gaia memory add` writes a row to the memory table."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        name="test-mem",
        type="project",
        body="This is the body of a test memory entry.",
        description="One-line description",
        workspace="me",
        json=False,
    )
    rc = _cmd_add(args)
    assert rc == 0, capsys.readouterr()

    row = _read_memory_row(tmp_db, "me", "test-mem")
    assert row is not None
    assert row["type"] == "project"
    assert row["description"] == "One-line description"
    assert row["body"] == "This is the body of a test memory entry."
    assert row["updated_at"], "updated_at must be set"


def test_add_zero_filesystem_side_effects(tmp_db, tmp_path, monkeypatch, capsys):
    """A successful ``gaia memory add`` must not create any .md file."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        name="no-fs-touch",
        type="project",
        body="body",
        description=None,
        workspace="me",
        json=True,
    )
    rc = _cmd_add(args)
    assert rc == 0, capsys.readouterr()

    # No legacy memory directory should be created.
    legacy = tmp_path / ".claude" / "projects"
    assert not legacy.exists(), f"unexpected FS write at {legacy}"

    # And the slug must not appear as a file/dir anywhere under tmp_path.
    found = list(tmp_path.rglob("no-fs-touch*"))
    assert found == [], f"unexpected slug-named path(s): {found}"


# ---------------------------------------------------------------------------
# Upsert path
# ---------------------------------------------------------------------------

def test_add_duplicate_name_upserts(tmp_db, tmp_path, monkeypatch, capsys):
    """A second add with the same (workspace, name) updates the row, not errors."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    base = dict(name="dup-mem", type="project", workspace="me",
                description=None, json=False)

    rc1 = _cmd_add(argparse.Namespace(body="initial body", **base))
    assert rc1 == 0, capsys.readouterr()

    rc2 = _cmd_add(argparse.Namespace(body="updated body", **base))
    assert rc2 == 0, capsys.readouterr()

    row = _read_memory_row(tmp_db, "me", "dup-mem")
    assert row is not None
    assert row["body"] == "updated body", "second call must overwrite body"

    # Only one row exists for this PK.
    import sqlite3
    con = sqlite3.connect(str(tmp_db))
    try:
        cnt = con.execute(
            "SELECT COUNT(*) FROM memory WHERE project = ? AND name = ?",
            ("me", "dup-mem"),
        ).fetchone()[0]
    finally:
        con.close()
    assert cnt == 1


def test_add_json_action_is_inserted_then_updated(tmp_db, tmp_path,
                                                  monkeypatch, capsys):
    """JSON output exposes ``action`` so callers can distinguish insert vs update."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    base = dict(name="action-mem", type="user", workspace="me",
                description=None, json=True)

    rc1 = _cmd_add(argparse.Namespace(body="b1", **base))
    assert rc1 == 0
    out1 = capsys.readouterr().out
    payload1 = json.loads(out1)
    assert payload1["action"] == "inserted"

    rc2 = _cmd_add(argparse.Namespace(body="b2", **base))
    assert rc2 == 0
    out2 = capsys.readouterr().out
    payload2 = json.loads(out2)
    assert payload2["action"] == "updated"


# ---------------------------------------------------------------------------
# Validation / error paths
# ---------------------------------------------------------------------------

def test_add_invalid_type_returns_error(tmp_db, tmp_path, monkeypatch, capsys):
    """An unknown ``--type`` is rejected with a clear message and exit 1."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        name="bad-type",
        type="bogus",
        body="b",
        description=None,
        workspace="me",
        json=False,
    )
    rc = _cmd_add(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "type" in captured.err.lower()

    # And no row was written. (When validation rejects before _connect(),
    # the DB file does not yet exist; treat that as "no row" too.)
    if tmp_db.exists():
        row = _read_memory_row(tmp_db, "me", "bad-type")
        assert row is None


def test_add_missing_required_flags(tmp_db, tmp_path, monkeypatch, capsys):
    """Missing ``--body`` returns a clear error without writing a row."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        name="missing-body",
        type="project",
        body=None,
        description=None,
        workspace="me",
        json=False,
    )
    rc = _cmd_add(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "body" in captured.err.lower()


# ---------------------------------------------------------------------------
# list / show (curated) / delete / edit
# ---------------------------------------------------------------------------

def _seed_curated(tmp_db, name, type_, body, description=None):
    from gaia.store.writer import upsert_memory
    upsert_memory("me", name, type=type_, body=body,
                  description=description, db_path=tmp_db)


def test_list_returns_rows(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.memory import _cmd_list

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "p1", "project", "body1", description="D1")
    _seed_curated(tmp_db, "u1", "user", "body2", description="D2")

    args = argparse.Namespace(
        type=None, workspace="me", format="json", json=False,
    )
    rc = _cmd_list(args)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    names = sorted(r["name"] for r in payload)
    assert names == ["p1", "u1"]


def test_list_filters_by_type(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.memory import _cmd_list

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "p1", "project", "b")
    _seed_curated(tmp_db, "f1", "feedback", "b")

    args = argparse.Namespace(
        type="project", workspace="me", format="count", json=False,
    )
    rc = _cmd_list(args)
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == "1"


def test_curated_show_prints_body(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.memory import _cmd_curated_show

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "demo", "project", "the body content",
                  description="some description")

    args = argparse.Namespace(name="demo", workspace="me", json=True)
    rc = _cmd_curated_show(args)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "demo"
    assert payload["body"] == "the body content"
    assert payload["description"] == "some description"


def test_curated_show_not_found(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.memory import _cmd_curated_show

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(name="ghost", workspace="me", json=False)
    rc = _cmd_curated_show(args)
    assert rc == 1
    assert "not found" in capsys.readouterr().err.lower()


def test_delete_curated_with_yes(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.memory import _cmd_delete

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "doomed-mem", "project", "body")
    assert _read_memory_row(tmp_db, "me", "doomed-mem") is not None

    args = argparse.Namespace(name="doomed-mem", workspace="me",
                              yes=True, json=False)
    rc = _cmd_delete(args)
    assert rc == 0, capsys.readouterr()
    assert _read_memory_row(tmp_db, "me", "doomed-mem") is None
    # Zero filesystem side effects
    assert not (tmp_path / ".claude").exists()


def test_delete_curated_aborts_on_no(tmp_db, tmp_path, monkeypatch, capsys):
    import builtins
    from cli.memory import _cmd_delete

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "keepme", "project", "body")
    monkeypatch.setattr(builtins, "input", lambda *a, **kw: "n")

    args = argparse.Namespace(name="keepme", workspace="me",
                              yes=False, json=False)
    rc = _cmd_delete(args)
    assert rc == 0
    out = capsys.readouterr().out.lower()
    assert "abort" in out or "not deleted" in out
    assert _read_memory_row(tmp_db, "me", "keepme") is not None


def test_delete_curated_not_found(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.memory import _cmd_delete

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(name="ghost", workspace="me",
                              yes=True, json=False)
    rc = _cmd_delete(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "not found" in captured.err.lower()


def test_edit_curated_overwrite_body(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.memory import _cmd_edit

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "patchme", "project", "old body")

    args = argparse.Namespace(
        name="patchme", workspace="me",
        field="body", content="new body", append=False, json=False,
    )
    rc = _cmd_edit(args)
    assert rc == 0, capsys.readouterr()
    row = _read_memory_row(tmp_db, "me", "patchme")
    assert row["body"] == "new body"


def test_edit_curated_append_description(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.memory import _cmd_edit

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "appendme", "project", "body",
                  description="first")

    args = argparse.Namespace(
        name="appendme", workspace="me",
        field="description", content="second", append=True, json=False,
    )
    rc = _cmd_edit(args)
    assert rc == 0, capsys.readouterr()
    row = _read_memory_row(tmp_db, "me", "appendme")
    assert row["description"] == "first\n\nsecond"


def test_edit_curated_invalid_field(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.memory import _cmd_edit

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "guarded", "project", "body")

    args = argparse.Namespace(
        name="guarded", workspace="me",
        field="type", content="user", append=False, json=False,
    )
    rc = _cmd_edit(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "invalid memory field" in captured.err.lower()


def test_edit_curated_not_found(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.memory import _cmd_edit

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        name="ghost", workspace="me",
        field="body", content="x", append=False, json=False,
    )
    rc = _cmd_edit(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "not found" in captured.err.lower()


def test_edit_curated_empty_content(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.memory import _cmd_edit

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "intact", "project", "body")

    args = argparse.Namespace(
        name="intact", workspace="me",
        field="body", content="", append=False, json=False,
    )
    rc = _cmd_edit(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "content" in captured.err.lower()


def test_edit_curated_zero_fs_side_effects(tmp_db, tmp_path,
                                           monkeypatch, capsys):
    from cli.memory import _cmd_edit

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "fs-check", "project", "body")
    args = argparse.Namespace(
        name="fs-check", workspace="me",
        field="body", content="updated", append=False, json=True,
    )
    rc = _cmd_edit(args)
    assert rc == 0, capsys.readouterr()
    assert not (tmp_path / ".claude").exists()
    assert list(tmp_path.rglob("fs-check")) == []


# ---------------------------------------------------------------------------
# Scoped search
# ---------------------------------------------------------------------------

def test_search_scope_curated(tmp_db, tmp_path, monkeypatch, capsys):
    """`--scope=curated` searches the memory_fts mirror only."""
    from cli.memory import _cmd_search_scoped

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "alpha", "project",
                  "the zenithal-mooncrest token appears here")
    _seed_curated(tmp_db, "beta", "project", "unrelated content")

    args = argparse.Namespace(
        query="zenithal-mooncrest", limit=10, scope="curated",
        workspace="me", json=True,
    )
    rc = _cmd_search_scoped(args)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["scope"] == "curated"
    names = [r["name"] for r in payload["results"]]
    assert "alpha" in names
    assert "beta" not in names


def test_search_scope_both_emits_two_buckets(tmp_db, tmp_path,
                                             monkeypatch, capsys):
    """`--scope=both` returns episodes + curated keys in JSON output."""
    from cli.memory import _cmd_search_scoped

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "alpha", "project",
                  "another zenithal-mooncrest mention")

    args = argparse.Namespace(
        query="zenithal-mooncrest", limit=10, scope="both",
        workspace="me", json=True,
    )
    rc = _cmd_search_scoped(args)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["scope"] == "both"
    assert "episodes" in payload
    assert "curated" in payload
    curated_names = [r["name"] for r in payload["curated"]]
    assert "alpha" in curated_names


def test_add_registers_subcommand_choice():
    """``gaia memory add`` is wired into the argparse tree."""
    import cli.memory as memory_mod

    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="subcommand")
    memory_mod.register(subs)

    mem_parser = subs.choices["memory"]
    nested_subs = None
    for action in mem_parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            nested_subs = action
            break
    assert nested_subs is not None
    for verb in ("add", "list", "show", "delete", "edit",
                 "episode-show", "search"):
        assert verb in nested_subs.choices, f"missing verb: {verb}"
