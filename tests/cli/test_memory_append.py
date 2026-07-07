"""
Tests for ``gaia memory append`` -- the primary additive memory verb.

Vocabulary decision (Option C): memory is AGGREGATED and RECLASSIFIED, not
mutated. ``append`` grows an existing note's body (separator ``\n\n``),
preserving the prior body in ``memory_history``, and is classified
NON-mutative (T0) -- it needs no T3 approval.

Coverage:
  * writer path: append concatenates onto existing body (via edit --append)
  * CLI: `gaia memory append <slug> --body=...` grows the body
  * CLI: append preserves the prior body in memory_history (trg_memory_history)
  * CLI: append on a missing slug errors cleanly
  * CLI: append requires --body or --body-file (argparse mutually-exclusive)
  * CLI: --body-file variant
  * register: `append` is a registered nested action
  * SECURITY PIN: `gaia memory append` is classified NON-mutative (not T3),
    while `gaia memory edit` / `delete` stay MUTATIVE (T3). This pins the
    security-classification decision so a future edit to MUTATIVE_VERBS that
    added `append` would fail loudly here.
"""

from __future__ import annotations

import argparse
import json
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
_HOOKS_DIR = _REPO_ROOT / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Route the substrate DB into tmp_path so tests never touch ~/.gaia."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    from gaia.paths import db_path
    return db_path()


@pytest.fixture()
def seeded(tmp_db):
    """Seed one atom row with a known body."""
    from gaia.store.writer import upsert_memory
    upsert_memory("me", "atom_running", type="atom", body="first line")
    return tmp_db


def _body(db_path: Path, name: str) -> str | None:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        r = con.execute(
            "SELECT body FROM memory WHERE workspace='me' AND name=?",
            (name,),
        ).fetchone()
        return None if r is None else r["body"]
    finally:
        con.close()


def _history_rows(db_path: Path, name: str) -> list[sqlite3.Row]:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT before_body, after_body FROM memory_history "
            "WHERE workspace='me' AND name=? ORDER BY changed_at",
            (name,),
        ).fetchall()
    finally:
        con.close()


def _build_parser():
    """Mirror the registered subparser layout for in-process CLI testing."""
    import cli.memory as memory_mod
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="cmd")
    memory_mod.register(subparsers)
    return parser, memory_mod


# ---------------------------------------------------------------------------
# register: append is present
# ---------------------------------------------------------------------------

def test_register_append_action_present():
    parser, _ = _build_parser()
    subs = None
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            subs = action
            break
    mem_parser = subs.choices["memory"]
    nested = None
    for action in mem_parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            nested = action
            break
    assert "append" in nested.choices


# ---------------------------------------------------------------------------
# CLI: append grows the body
# ---------------------------------------------------------------------------

def test_cli_append_grows_body(seeded, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "append", "atom_running",
        "--body=second line", "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}, stdout={captured.out}"
    # Additive: separator is \n\n, original body preserved at the front.
    assert _body(seeded, "atom_running") == "first line\n\nsecond line"
    assert "Appended to memory 'atom_running'" in captured.out


def test_cli_append_preserves_history(seeded):
    """The prior body must survive in memory_history (trg_memory_history)."""
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "append", "atom_running",
        "--body=addendum", "--workspace=me",
    ])
    assert args.func(args) == 0
    rows = _history_rows(seeded, "atom_running")
    assert len(rows) == 1, "append must record exactly one history row"
    assert rows[0]["before_body"] == "first line"
    assert rows[0]["after_body"] == "first line\n\naddendum"


def test_cli_append_is_additive_across_multiple_calls(seeded):
    parser, _ = _build_parser()
    for text in ("b", "c"):
        args = parser.parse_args([
            "memory", "append", "atom_running",
            f"--body={text}", "--workspace=me",
        ])
        assert args.func(args) == 0
    assert _body(seeded, "atom_running") == "first line\n\nb\n\nc"
    # Each append leaves a history row -> full lineage recoverable.
    assert len(_history_rows(seeded, "atom_running")) == 2


def test_cli_append_missing_slug_errors(seeded, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "append", "atom_does_not_exist",
        "--body=x", "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc != 0
    assert "not found" in (captured.out + captured.err).lower()


def test_cli_append_requires_body(seeded):
    """argparse mutually-exclusive group is required -> SystemExit without body."""
    parser, _ = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["memory", "append", "atom_running", "--workspace=me"])


def test_cli_append_body_file(seeded, tmp_path, capsys):
    parser, _ = _build_parser()
    body_file = tmp_path / "more.md"
    body_file.write_text("from a file", encoding="utf-8")
    args = parser.parse_args([
        "memory", "append", "atom_running",
        f"--body-file={body_file}", "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}"
    assert _body(seeded, "atom_running") == "first line\n\nfrom a file"


def test_cli_append_json_output(seeded, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "append", "atom_running",
        "--body=z", "--workspace=me", "--json",
    ])
    assert args.func(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "atom_running"
    assert payload["field"] == "body"
    assert payload["action"] == "appended"


# ---------------------------------------------------------------------------
# SECURITY PIN: append is non-mutative (T0), edit/delete stay T3
# ---------------------------------------------------------------------------

def test_append_is_classified_non_mutative():
    """`gaia memory append` must classify NON-mutative (no T3 approval).

    This is the load-bearing security decision of Option C: appending only
    grows the record, so it must not require consent. `append` is absent from
    MUTATIVE_VERBS, so it is READ_ONLY "by elimination". If someone later adds
    `append` to MUTATIVE_VERBS, this test fails and flags the regression.
    """
    from modules.security.mutative_verbs import detect_mutative_command
    r = detect_mutative_command("gaia memory append atom_running --body=more")
    assert r.is_mutative is False, (
        f"gaia memory append must be non-mutative (T0); got {r.category}"
    )


def test_edit_and_delete_stay_mutative():
    """The correction/removal verbs stay T3 -- they change what reads see or
    reduce recoverability, the directions that need consent."""
    from modules.security.mutative_verbs import detect_mutative_command
    edit_r = detect_mutative_command("gaia memory edit --name=x --field=body --content=z")
    delete_r = detect_mutative_command("gaia memory delete x")
    assert edit_r.is_mutative is True, "gaia memory edit must stay T3"
    assert delete_r.is_mutative is True, "gaia memory delete must stay T3"


def test_add_and_reclassify_remain_non_mutative():
    """Sanity: the sibling non-mutative verbs are unchanged by this work."""
    from modules.security.mutative_verbs import detect_mutative_command
    add_r = detect_mutative_command("gaia memory add --name=x --type=project --body=y")
    reclass_r = detect_mutative_command("gaia memory reclassify x --class=anchor")
    assert add_r.is_mutative is False
    assert reclass_r.is_mutative is False
