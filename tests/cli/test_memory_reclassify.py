"""
Tests for ``gaia memory reclassify`` and the T5 --class/--status flags on
``gaia memory add`` / ``gaia memory edit``.

Brief: memory-model-refactor-class-status-links-structural-enforcement (T5).

Coverage:
  * writer.reclassify_memory happy path (class + status on a thread row)
  * writer.reclassify_memory rejects status when class is not thread
  * writer.reclassify_memory rejects invalid class enum
  * writer.reclassify_memory rejects invalid status enum
  * writer.reclassify_memory rejects missing row
  * writer.reclassify_memory rejects call with neither class_ nor status
  * writer.reclassify_memory auto-clears status when class moves thread->anchor
  * writer.reclassify_memory explicit clear via status=""
  * writer.reclassify_memory enforcement: non-curator dispatch -> rejected
  * CLI: reclassify happy path
  * CLI: reclassify error when status set on non-thread class
  * CLI: reclassify error when --status=open on existing anchor row
  * CLI: reclassify --status=null clears the column on a thread row
  * CLI: add --class --status creates the row with those values
  * CLI: edit --class --status updates the row
  * CLI: edit can operate as pure reclassify (no --field)
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
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
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    from gaia.paths import db_path
    return db_path()


@pytest.fixture()
def seeded(tmp_db):
    """Seed three rows: one atom (no class set), one anchor, one thread.

    Slugs must follow the curated taxonomy: type=atom requires an ``atom_``
    prefix (enforced by ``_validate_curated_slug``). The class/status fields
    are independent of the slug prefix -- the prefix encodes the *type*, not
    the *class*.
    """
    from gaia.store.writer import upsert_memory, reclassify_memory
    upsert_memory("me", "atom_plain", type="atom", body="plain body")
    upsert_memory("me", "atom_anchor_seed", type="atom", body="anchor body")
    reclassify_memory("me", "atom_anchor_seed", class_="anchor")
    upsert_memory("me", "atom_thread_seed", type="atom", body="thread body")
    reclassify_memory(
        "me", "atom_thread_seed", class_="thread", status="open",
    )
    return tmp_db


def _row(db_path: Path, name: str) -> tuple | None:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        r = con.execute(
            "SELECT name, class, status FROM memory "
            "WHERE workspace='me' AND name=?",
            (name,),
        ).fetchone()
        return None if r is None else (r["name"], r["class"], r["status"])
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Writer: reclassify_memory
# ---------------------------------------------------------------------------

def test_reclassify_happy_path_class_only(seeded):
    from gaia.store.writer import reclassify_memory
    res = reclassify_memory("me", "atom_plain", class_="anchor")
    assert res["status"] == "applied"
    assert res["action"] == "reclassified"
    assert res["class"] == "anchor"
    assert res["memory_status"] is None
    assert _row(seeded, "atom_plain") == ("atom_plain", "anchor", None)


def test_reclassify_happy_path_class_and_status_thread(seeded):
    from gaia.store.writer import reclassify_memory
    res = reclassify_memory(
        "me", "atom_plain", class_="thread", status="carry_forward",
    )
    assert res["class"] == "thread"
    assert res["memory_status"] == "carry_forward"
    assert _row(seeded, "atom_plain") == (
        "atom_plain", "thread", "carry_forward",
    )


def test_reclassify_status_only_on_existing_thread(seeded):
    """Status alone is valid when the existing row is already a thread."""
    from gaia.store.writer import reclassify_memory
    res = reclassify_memory("me", "atom_thread_seed", status="graduated")
    assert res["class"] == "thread"
    assert res["memory_status"] == "graduated"


def test_reclassify_rejects_status_without_thread_class(seeded):
    """status='open' on a row whose class is anchor must fail."""
    from gaia.store.writer import reclassify_memory
    with pytest.raises(ValueError) as exc_info:
        reclassify_memory("me", "atom_anchor_seed", status="open")
    msg = str(exc_info.value).lower()
    assert "status only applies to class=thread" in msg


def test_reclassify_rejects_status_when_class_set_to_log(seeded):
    """Even when class is being set in the same call, log+status fails."""
    from gaia.store.writer import reclassify_memory
    with pytest.raises(ValueError) as exc_info:
        reclassify_memory(
            "me", "atom_plain", class_="log", status="open",
        )
    assert "thread" in str(exc_info.value).lower()
    # The original row stays untouched on failure.
    # v11: rows upserted without explicit class get the DEFAULT 'log'.
    assert _row(seeded, "atom_plain") == ("atom_plain", "log", None)


def test_reclassify_rejects_invalid_class(seeded):
    from gaia.store.writer import reclassify_memory
    with pytest.raises(ValueError) as exc_info:
        reclassify_memory("me", "atom_plain", class_="bogus")
    assert "bogus" in str(exc_info.value)
    assert "anchor" in str(exc_info.value)


def test_reclassify_rejects_invalid_status(seeded):
    from gaia.store.writer import reclassify_memory
    with pytest.raises(ValueError) as exc_info:
        reclassify_memory("me", "atom_thread_seed", status="weirdo")
    assert "weirdo" in str(exc_info.value)


def test_reclassify_rejects_missing_row(seeded):
    from gaia.store.writer import reclassify_memory
    with pytest.raises(ValueError) as exc_info:
        reclassify_memory("me", "nonexistent_slug", class_="anchor")
    assert "nonexistent_slug" in str(exc_info.value)
    assert "not found" in str(exc_info.value)


def test_reclassify_rejects_no_flags(seeded):
    """Calling with neither class_ nor status is a programmer error."""
    from gaia.store.writer import reclassify_memory
    with pytest.raises(ValueError) as exc_info:
        reclassify_memory("me", "atom_plain")
    assert "at least one" in str(exc_info.value).lower()


def test_reclassify_auto_clears_status_on_thread_demotion(seeded):
    """thread+status=open -> anchor: status auto-NULLs, no explicit flag."""
    from gaia.store.writer import reclassify_memory
    # Sanity-check baseline.
    assert _row(seeded, "atom_thread_seed") == ("atom_thread_seed", "thread", "open")
    res = reclassify_memory("me", "atom_thread_seed", class_="anchor")
    assert res["class"] == "anchor"
    assert res["memory_status"] is None
    assert _row(seeded, "atom_thread_seed") == ("atom_thread_seed", "anchor", None)


def test_reclassify_explicit_clear_via_empty_string(seeded):
    """status='' explicitly nulls the column even when class stays thread."""
    from gaia.store.writer import reclassify_memory
    # thread_seed starts with status='open'.
    res = reclassify_memory("me", "atom_thread_seed", status="")
    assert res["class"] == "thread"
    assert res["memory_status"] is None
    assert _row(seeded, "atom_thread_seed") == ("atom_thread_seed", "thread", None)


def test_reclassify_enforcement_blocks_non_curator(seeded, monkeypatch):
    """T3 enforcement still applies to reclassify."""
    from gaia.store.writer import reclassify_memory, MemoryWriteForbidden
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")
    with pytest.raises(MemoryWriteForbidden):
        reclassify_memory("me", "atom_plain", class_="anchor")
    # Row stays untouched.
    # v11: rows upserted without explicit class get the DEFAULT 'log'.
    assert _row(seeded, "atom_plain") == ("atom_plain", "log", None)


def test_reclassify_enforcement_allows_curator(seeded, monkeypatch):
    from gaia.store.writer import reclassify_memory
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "gaia-orchestrator")
    res = reclassify_memory("me", "atom_plain", class_="anchor")
    assert res["class"] == "anchor"


# ---------------------------------------------------------------------------
# CLI: gaia memory reclassify
# ---------------------------------------------------------------------------

def _build_parser():
    """Mirror the registered subparser layout for in-process CLI testing."""
    import cli.memory as memory_mod
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="cmd")
    memory_mod.register(subparsers)
    return parser, memory_mod


def test_cli_reclassify_happy_path(seeded, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "reclassify", "atom_plain",
        "--class=anchor", "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}, stdout={captured.out}"
    assert "Reclassified atom_plain" in captured.out
    assert "class=anchor" in captured.out
    assert _row(seeded, "atom_plain") == ("atom_plain", "anchor", None)


def test_cli_reclassify_requires_at_least_one_flag(seeded, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "reclassify", "atom_plain", "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "at least one" in (captured.err + captured.out).lower()


def test_cli_reclassify_rejects_status_on_anchor(seeded, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "reclassify", "atom_anchor_seed",
        "--status=open", "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 1
    combined = (captured.err + captured.out).lower()
    assert "status only applies to class=thread" in combined


def test_cli_reclassify_explicit_null_clears_status(seeded, capsys):
    parser, _ = _build_parser()
    # thread_seed starts as (thread, open).
    args = parser.parse_args([
        "memory", "reclassify", "atom_thread_seed",
        "--status=null", "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}, stdout={captured.out}"
    assert _row(seeded, "atom_thread_seed") == ("atom_thread_seed", "thread", None)


def test_cli_reclassify_invalid_class_blocked_by_argparse(seeded, capsys):
    parser, _ = _build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([
            "memory", "reclassify", "atom_plain",
            "--class=garbage", "--workspace=me",
        ])
    assert exc_info.value.code != 0


def test_cli_reclassify_json_output(seeded, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "reclassify", "atom_plain",
        "--class=thread", "--status=open", "--workspace=me", "--json",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["action"] == "reclassified"
    assert payload["class"] == "thread"
    assert payload["memory_status"] == "open"


def test_cli_reclassify_enforcement(seeded, monkeypatch, capsys):
    parser, _ = _build_parser()
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")
    args = parser.parse_args([
        "memory", "reclassify", "atom_plain",
        "--class=anchor", "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 1
    combined = (captured.err + captured.out).lower()
    assert "developer" in combined
    # Row stays unchanged.
    # v11: rows upserted without explicit class get the DEFAULT 'log'.
    assert _row(seeded, "atom_plain") == ("atom_plain", "log", None)


# ---------------------------------------------------------------------------
# CLI: gaia memory add --class --status
# ---------------------------------------------------------------------------

def test_cli_add_with_class_and_status(tmp_db, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "add",
        "--name=atom_thread_new",
        "--type=atom",
        "--body=new thread body",
        "--class=thread",
        "--status=open",
        "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}, stdout={captured.out}"
    assert _row(tmp_db, "atom_thread_new") == ("atom_thread_new", "thread", "open")
    # Non-JSON path mentions the resulting class+status.
    assert "class=thread" in captured.out
    assert "status=open" in captured.out


def test_cli_add_with_class_only(tmp_db, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "add",
        "--name=atom_anchor_new",
        "--type=atom",
        "--body=fresh anchor",
        "--class=anchor",
        "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}, stdout={captured.out}"
    assert _row(tmp_db, "atom_anchor_new") == (
        "atom_anchor_new", "anchor", None,
    )


def test_cli_add_status_on_anchor_class_errors(tmp_db, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "add",
        "--name=atom_bad_status",
        "--type=atom",
        "--body=should fail reclassify",
        "--class=anchor",
        "--status=open",
        "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "thread" in (captured.err + captured.out).lower()
    # The primary upsert landed but reclassify failed: row exists with
    # class='log' (v11 DEFAULT) and status=NULL since reclassify never ran.
    # (We do NOT roll back the upsert -- documented behaviour in the
    # _cmd_add docstring.)
    row = _row(tmp_db, "atom_bad_status")
    assert row is not None
    assert row[1] == "log"  # v11 DEFAULT 'log' applied on upsert
    assert row[2] is None   # status never set


# ---------------------------------------------------------------------------
# CLI: gaia memory edit --class --status
# ---------------------------------------------------------------------------

def test_cli_edit_with_class_and_status(seeded, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "edit",
        "--name=atom_plain",
        "--class=thread",
        "--status=open",
        "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}, stdout={captured.out}"
    assert _row(seeded, "atom_plain") == ("atom_plain", "thread", "open")


def test_cli_edit_field_and_reclassify_in_one_call(seeded, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "edit",
        "--name=atom_plain",
        "--field=body",
        "--content=patched body",
        "--class=anchor",
        "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}, stdout={captured.out}"
    assert _row(seeded, "atom_plain") == ("atom_plain", "anchor", None)


def test_cli_edit_status_null_clears_on_thread(seeded, capsys):
    """edit can clear a thread's status via --status=null."""
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "edit",
        "--name=atom_thread_seed",
        "--status=null",
        "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}, stdout={captured.out}"
    assert _row(seeded, "atom_thread_seed") == ("atom_thread_seed", "thread", None)


def test_cli_edit_requires_some_change(seeded, capsys):
    """Without --field or --class/--status, edit fails with a clear error."""
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "edit",
        "--name=atom_plain",
        "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 1
    combined = (captured.err + captured.out).lower()
    assert "field" in combined or "class" in combined


def test_cli_edit_enforcement_blocks_non_curator(seeded, monkeypatch, capsys):
    parser, _ = _build_parser()
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")
    args = parser.parse_args([
        "memory", "edit",
        "--name=atom_plain",
        "--class=anchor",
        "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "developer" in (captured.err + captured.out).lower()
