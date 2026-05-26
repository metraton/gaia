"""
Tests for ``gaia memory link`` (T4) -- memory_links graph primitives.

Brief: memory-model-refactor-class-status-links-structural-enforcement (T4).

Coverage:
  * writer.insert_memory_link happy path
  * writer.insert_memory_link rejects dangling src
  * writer.insert_memory_link rejects dangling dst
  * writer.insert_memory_link rejects invalid kind (writer-side guard)
  * writer.insert_memory_link is idempotent on duplicate (default)
  * writer.insert_memory_link raises on duplicate when if_exists='error'
  * writer.delete_memory_link removes an existing edge
  * writer.delete_memory_link is idempotent on missing (default)
  * writer.delete_memory_link raises on missing when if_missing='error'
  * SQL integrity: row count in memory_links matches expectation
  * SQL integrity: CHECK constraint blocks invalid kind at the DB layer
  * CLI: ``gaia memory link a b --kind=relates_to`` creates the row
  * CLI: ``gaia memory link a b --kind=relates_to --delete`` removes it
  * CLI: re-running link is a no-op (exit 0, descriptive output)
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Route the substrate DB into tmp_path so the test never touches the real
    ``~/.gaia/gaia.db``."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    # Make sure no leftover dispatch env from a previous test leaks in.
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    from gaia.paths import db_path
    return db_path()


@pytest.fixture()
def seeded(tmp_db):
    """Seed two curated atoms that subsequent tests can link together."""
    from gaia.store.writer import upsert_memory
    upsert_memory("me", "atom_src", type="atom", body="src body")
    upsert_memory("me", "atom_dst", type="atom", body="dst body")
    return tmp_db


def _link_rows(db_path: Path) -> list[tuple]:
    con = sqlite3.connect(str(db_path))
    try:
        return list(con.execute(
            "SELECT workspace, src_name, dst_name, kind, created_at "
            "FROM memory_links ORDER BY src_name, dst_name, kind"
        ).fetchall())
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Writer: insert_memory_link
# ---------------------------------------------------------------------------

def test_insert_link_happy_path(seeded):
    from gaia.store.writer import insert_memory_link
    res = insert_memory_link("me", "atom_src", "atom_dst", "relates_to")
    assert res["status"] == "applied"
    assert res["action"] == "inserted"
    assert res["created_at"] is not None
    rows = _link_rows(seeded)
    assert len(rows) == 1
    assert rows[0][:4] == ("me", "atom_src", "atom_dst", "relates_to")


def test_insert_link_rejects_missing_src(seeded):
    from gaia.store.writer import insert_memory_link
    with pytest.raises(ValueError) as exc_info:
        insert_memory_link("me", "does_not_exist", "atom_dst", "relates_to")
    assert "does_not_exist" in str(exc_info.value)
    assert "src" in str(exc_info.value).lower()
    assert _link_rows(seeded) == []


def test_insert_link_rejects_missing_dst(seeded):
    from gaia.store.writer import insert_memory_link
    with pytest.raises(ValueError) as exc_info:
        insert_memory_link("me", "atom_src", "missing_target", "relates_to")
    assert "missing_target" in str(exc_info.value)
    assert "dst" in str(exc_info.value).lower()
    assert _link_rows(seeded) == []


def test_insert_link_rejects_invalid_kind(seeded):
    """Writer-side guard fires before SQLite's CHECK."""
    from gaia.store.writer import insert_memory_link
    with pytest.raises(ValueError) as exc_info:
        insert_memory_link("me", "atom_src", "atom_dst", "nonsense_kind")
    msg = str(exc_info.value)
    assert "nonsense_kind" in msg
    assert "relates_to" in msg  # error names valid choices
    assert _link_rows(seeded) == []


def test_insert_link_idempotent_default(seeded):
    """Re-creating the same edge returns action=noop with default if_exists."""
    from gaia.store.writer import insert_memory_link
    res1 = insert_memory_link("me", "atom_src", "atom_dst", "relates_to")
    res2 = insert_memory_link("me", "atom_src", "atom_dst", "relates_to")
    assert res1["action"] == "inserted"
    assert res2["action"] == "noop"
    assert res2["status"] == "applied"
    # created_at on the noop reflects the original row, not a fresh stamp.
    assert res2["created_at"] == res1["created_at"]
    # And there's still only one row in the table.
    assert len(_link_rows(seeded)) == 1


def test_insert_link_strict_raises_on_duplicate(seeded):
    """if_exists='error' makes the duplicate path raise ValueError."""
    from gaia.store.writer import insert_memory_link
    insert_memory_link("me", "atom_src", "atom_dst", "relates_to")
    with pytest.raises(ValueError) as exc_info:
        insert_memory_link(
            "me", "atom_src", "atom_dst", "relates_to",
            if_exists="error",
        )
    assert "already exists" in str(exc_info.value)


def test_insert_link_schema_check_blocks_invalid_kind(seeded):
    """If the writer guard were bypassed, the SQLite CHECK would still block."""
    con = sqlite3.connect(str(seeded))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO memory_links (workspace, src_name, dst_name, "
                "kind, created_at) VALUES (?, ?, ?, ?, ?)",
                ("me", "atom_src", "atom_dst", "totally_bogus", "now"),
            )
            con.commit()
    finally:
        con.close()


def test_insert_link_multiple_kinds_between_same_pair(seeded):
    """The PK includes kind, so the same (src,dst) can carry multiple edges."""
    from gaia.store.writer import insert_memory_link
    r1 = insert_memory_link("me", "atom_src", "atom_dst", "relates_to")
    r2 = insert_memory_link("me", "atom_src", "atom_dst", "derived_from")
    assert r1["action"] == "inserted"
    assert r2["action"] == "inserted"
    assert len(_link_rows(seeded)) == 2


# ---------------------------------------------------------------------------
# Writer: delete_memory_link
# ---------------------------------------------------------------------------

def test_delete_link_happy_path(seeded):
    from gaia.store.writer import insert_memory_link, delete_memory_link
    insert_memory_link("me", "atom_src", "atom_dst", "relates_to")
    res = delete_memory_link("me", "atom_src", "atom_dst", "relates_to")
    assert res["status"] == "applied"
    assert res["action"] == "deleted"
    assert _link_rows(seeded) == []


def test_delete_link_idempotent_default(seeded):
    """Deleting a non-existent edge returns action=noop with default if_missing."""
    from gaia.store.writer import delete_memory_link
    res = delete_memory_link("me", "atom_src", "atom_dst", "relates_to")
    assert res["status"] == "applied"
    assert res["action"] == "noop"


def test_delete_link_strict_raises_on_missing(seeded):
    """if_missing='error' makes the missing path raise ValueError."""
    from gaia.store.writer import delete_memory_link
    with pytest.raises(ValueError) as exc_info:
        delete_memory_link(
            "me", "atom_src", "atom_dst", "relates_to",
            if_missing="error",
        )
    assert "not found" in str(exc_info.value)


def test_delete_link_rejects_invalid_kind(seeded):
    from gaia.store.writer import delete_memory_link
    with pytest.raises(ValueError):
        delete_memory_link("me", "atom_src", "atom_dst", "nonsense_kind")


# ---------------------------------------------------------------------------
# Writer: enforcement still gates link mutation (T3 behaviour preserved)
# ---------------------------------------------------------------------------

def test_insert_link_rejected_for_non_curator_dispatch(seeded, monkeypatch):
    from gaia.store.writer import insert_memory_link, MemoryWriteForbidden
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")
    with pytest.raises(MemoryWriteForbidden):
        insert_memory_link("me", "atom_src", "atom_dst", "relates_to")
    assert _link_rows(seeded) == []


def test_delete_link_rejected_for_non_curator_dispatch(seeded, monkeypatch):
    from gaia.store.writer import (
        insert_memory_link, delete_memory_link, MemoryWriteForbidden,
    )
    insert_memory_link("me", "atom_src", "atom_dst", "relates_to")
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")
    with pytest.raises(MemoryWriteForbidden):
        delete_memory_link("me", "atom_src", "atom_dst", "relates_to")
    # Row survives the rejection.
    assert len(_link_rows(seeded)) == 1


# ---------------------------------------------------------------------------
# CLI: gaia memory link
# ---------------------------------------------------------------------------

def _build_link_parser():
    """Build a minimal argparse harness that mirrors the registered subparser
    layout so we can drive ``_cmd_link`` without spawning a subprocess."""
    import cli.memory as memory_mod
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="cmd")
    memory_mod.register(subparsers)
    return parser, memory_mod


def test_cli_link_create_then_delete(seeded, capsys):
    parser, _mem = _build_link_parser()

    args = parser.parse_args([
        "memory", "link", "atom_src", "atom_dst",
        "--kind=relates_to", "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}, stdout={captured.out}"
    assert "Created link" in captured.out
    assert "atom_src" in captured.out and "atom_dst" in captured.out
    assert "relates_to" in captured.out
    assert len(_link_rows(seeded)) == 1

    # Re-run -> idempotent no-op, still exit 0.
    args = parser.parse_args([
        "memory", "link", "atom_src", "atom_dst",
        "--kind=relates_to", "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0
    assert "Skipped" in captured.out or "no-op" in captured.out
    assert len(_link_rows(seeded)) == 1

    # Delete it.
    args = parser.parse_args([
        "memory", "link", "atom_src", "atom_dst",
        "--kind=relates_to", "--workspace=me", "--delete",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0
    assert "Deleted link" in captured.out
    assert _link_rows(seeded) == []


def test_cli_link_rejects_invalid_kind_via_argparse(seeded, capsys):
    """argparse's choices= kills the bad kind before it reaches the handler."""
    parser, _mem = _build_link_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([
            "memory", "link", "atom_src", "atom_dst", "--kind=bogus",
        ])
    assert exc_info.value.code != 0


def test_cli_link_errors_on_missing_dst(seeded, capsys):
    parser, _mem = _build_link_parser()
    args = parser.parse_args([
        "memory", "link", "atom_src", "ghost_target",
        "--kind=relates_to", "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "ghost_target" in (captured.err + captured.out)


def test_cli_link_json_output(seeded, capsys):
    parser, _mem = _build_link_parser()
    args = parser.parse_args([
        "memory", "link", "atom_src", "atom_dst",
        "--kind=supersedes", "--workspace=me", "--json",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}, stdout={captured.out}"
    payload = json.loads(captured.out)
    assert payload["action"] == "inserted"
    assert payload["kind"] == "supersedes"
    assert payload["src_name"] == "atom_src"
    assert payload["dst_name"] == "atom_dst"


def test_cli_link_delete_missing_is_idempotent(seeded, capsys):
    """Deleting a link that doesn't exist returns 0 with action=noop (CLI)."""
    parser, _mem = _build_link_parser()
    args = parser.parse_args([
        "memory", "link", "atom_src", "atom_dst",
        "--kind=relates_to", "--workspace=me", "--delete",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0
    assert "Skipped" in captured.out or "no-op" in captured.out
