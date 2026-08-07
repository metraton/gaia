"""Tests for the ``gaia memory`` --audience write/read surface (v45).

`memory.audience` is orthogonal to type/class/status/project_ref/initiative:
it names WHICH AGENT ROLE a curated memory row's content is FOR
('orchestrator' / 'executor' / 'any'). This wave adds the column plus its
CLI read/write surface only -- the kernel builder and the event injector are
untouched, and no row is auto-tagged.

Coverage:
  * CLI: ``add --audience=executor`` sets it at insertion time
  * CLI: ``add`` without --audience defaults a NEW row to 'any'
  * CLI: a correction ``add`` upsert without --audience PRESERVES the
    existing row's audience (never silently resets it to 'any')
  * CLI: ``add --audience=bogus`` is rejected by argparse (choices)
  * CLI: ``edit --audience=orchestrator`` PATCHes an existing row
  * CLI: ``edit --audience`` on an unknown row -> structured error (exit 1)
  * CLI: ``edit --audience=bogus`` is rejected by argparse (choices)
  * CLI: ``list --audience=executor`` filters to matching rows only
  * CLI: ``show`` includes ``audience`` in both text and --json output
  * Writer: ``upsert_memory``/``set_memory_audience``/``list_memory`` reject
    an out-of-enum value directly (ValueError), independent of the CLI's
    argparse ``choices=`` gate
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    """Route the substrate DB into tmp_path; seed a workspace + one row.

    ``GAIA_DISPATCH_AGENT`` is cleared so the curator gate
    (``_assert_dispatch_can_write_memory``) treats this as a direct human/CLI
    caller, not a subagent dispatch -- the same discipline
    test_memory_edit_reanchor.py already applies.
    """
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    from gaia.paths import db_path
    from gaia.store.writer import _connect, upsert_memory

    path = db_path()
    con = _connect(path)
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        con.commit()
    finally:
        con.close()

    upsert_memory("me", "atom_seed", type="atom", body="seed row, audience untouched")
    return path


def _audience(db_path: Path, name: str):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        r = con.execute(
            "SELECT audience FROM memory WHERE workspace='me' AND name=?",
            (name,),
        ).fetchone()
        return r["audience"] if r else None
    finally:
        con.close()


def _build_parser():
    import cli.memory as memory_mod
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="cmd")
    memory_mod.register(subparsers)
    return parser, memory_mod


# ---------------------------------------------------------------------------
# add --audience
# ---------------------------------------------------------------------------

def test_add_with_audience_sets_it_on_a_new_row(seeded, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "add", "--name=atom_new_executor", "--type=atom",
        "--body=text", "--workspace=me", "--audience=executor", "--json",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}, stdout={captured.out}"
    assert _audience(seeded, "atom_new_executor") == "executor"
    payload = json.loads(captured.out)
    assert payload["audience"] == "executor"


def test_add_without_audience_defaults_new_row_to_any(seeded):
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "add", "--name=atom_default_any", "--type=atom",
        "--body=text", "--workspace=me",
    ])
    rc = args.func(args)
    assert rc == 0
    assert _audience(seeded, "atom_default_any") == "any"


def test_add_correction_upsert_preserves_existing_audience(seeded, capsys):
    """A plain correction upsert that does not mention --audience must not
    reset a previously-tagged row back to 'any'."""
    parser, memory_mod = _build_parser()

    # Tag it executor first.
    args1 = parser.parse_args([
        "memory", "add", "--name=atom_seed", "--type=atom",
        "--body=seed row, audience untouched", "--workspace=me",
        "--audience=executor",
    ])
    rc1 = args1.func(args1)
    assert rc1 == 0
    assert _audience(seeded, "atom_seed") == "executor"

    # Correct the body only -- no --audience flag this time.
    args2 = parser.parse_args([
        "memory", "add", "--name=atom_seed", "--type=atom",
        "--body=corrected body text", "--workspace=me",
    ])
    rc2 = args2.func(args2)
    captured = capsys.readouterr()
    assert rc2 == 0, f"stderr={captured.err}"
    assert _audience(seeded, "atom_seed") == "executor", (
        "a correction upsert that omits --audience must preserve the "
        "existing value, not silently reset it to 'any'"
    )


def test_add_rejects_invalid_audience_choice(seeded):
    parser, _ = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([
            "memory", "add", "--name=atom_bad", "--type=atom",
            "--body=text", "--workspace=me", "--audience=nonsense",
        ])


# ---------------------------------------------------------------------------
# edit --audience
# ---------------------------------------------------------------------------

def test_edit_audience_patches_existing_row(seeded, capsys):
    parser, _ = _build_parser()
    assert _audience(seeded, "atom_seed") == "any"
    args = parser.parse_args([
        "memory", "edit", "--name=atom_seed",
        "--audience=orchestrator", "--workspace=me", "--json",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}, stdout={captured.out}"
    assert _audience(seeded, "atom_seed") == "orchestrator"
    payload = json.loads(captured.out)
    assert payload["audience"]["before_audience"] == "any"
    assert payload["audience"]["after_audience"] == "orchestrator"


def test_edit_audience_text_output(seeded, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "edit", "--name=atom_seed",
        "--audience=executor", "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}"
    assert "'any' -> 'executor'" in captured.out


def test_edit_audience_unknown_row_is_structured_error(seeded, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "edit", "--name=does_not_exist",
        "--audience=executor", "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "not found" in (captured.err + captured.out).lower()


def test_edit_rejects_invalid_audience_choice(seeded):
    parser, _ = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([
            "memory", "edit", "--name=atom_seed",
            "--audience=nonsense", "--workspace=me",
        ])


def test_edit_requires_at_least_one_action_still_holds(seeded, capsys):
    """Pre-existing contract unaffected: edit with none of
    field/class/status/project/audience is still a usage error."""
    parser, _ = _build_parser()
    args = parser.parse_args(["memory", "edit", "--name=atom_seed", "--workspace=me"])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "required" in (captured.err + captured.out).lower()


# ---------------------------------------------------------------------------
# list --audience
# ---------------------------------------------------------------------------

def test_list_filters_by_audience(seeded, capsys):
    parser, _ = _build_parser()

    for name, audience in (
        ("atom_exec_1", "executor"),
        ("atom_exec_2", "executor"),
        ("atom_orch_1", "orchestrator"),
    ):
        args = parser.parse_args([
            "memory", "add", f"--name={name}", "--type=atom",
            "--body=text", "--workspace=me", f"--audience={audience}",
        ])
        rc = args.func(args)
        assert rc == 0
    capsys.readouterr()  # drain the add-command output

    args = parser.parse_args([
        "memory", "list", "--workspace=me", "--audience=executor", "--json",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}"
    rows = json.loads(captured.out)
    names = {r["name"] for r in rows}
    assert names == {"atom_exec_1", "atom_exec_2"}
    assert all(r["audience"] == "executor" for r in rows)


def test_list_without_audience_filter_returns_all(seeded, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args(["memory", "list", "--workspace=me", "--json"])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0
    rows = json.loads(captured.out)
    names = {r["name"] for r in rows}
    assert "atom_seed" in names


def test_list_rejects_invalid_audience_choice(seeded):
    parser, _ = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["memory", "list", "--workspace=me", "--audience=nonsense"])


# ---------------------------------------------------------------------------
# show --json / text includes audience
# ---------------------------------------------------------------------------

def test_show_json_includes_audience(seeded, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "add", "--name=atom_show_me", "--type=atom",
        "--body=text", "--workspace=me", "--audience=executor",
    ])
    rc = args.func(args)
    assert rc == 0
    capsys.readouterr()

    args = parser.parse_args([
        "memory", "show", "atom_show_me", "--workspace=me", "--json",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}"
    payload = json.loads(captured.out)
    assert payload["audience"] == "executor"


def test_show_text_includes_audience(seeded, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "add", "--name=atom_show_text", "--type=atom",
        "--body=text", "--workspace=me", "--audience=orchestrator",
    ])
    rc = args.func(args)
    assert rc == 0
    capsys.readouterr()

    args = parser.parse_args(["memory", "show", "atom_show_text", "--workspace=me"])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}"
    assert "audience: orchestrator" in captured.out


# ---------------------------------------------------------------------------
# Writer-level validation, independent of the CLI's argparse choices= gate
# ---------------------------------------------------------------------------

def test_writer_upsert_memory_rejects_invalid_audience(seeded):
    from gaia.store.writer import upsert_memory
    with pytest.raises(ValueError):
        upsert_memory(
            "me", "atom_writer_bad", type="atom", body="x", audience="nonsense",
        )


def test_writer_set_memory_audience_rejects_invalid_value(seeded):
    from gaia.store.writer import set_memory_audience
    with pytest.raises(ValueError):
        set_memory_audience("me", "atom_seed", "nonsense")


def test_writer_set_memory_audience_unknown_row_raises(seeded):
    from gaia.store.writer import set_memory_audience
    with pytest.raises(ValueError):
        set_memory_audience("me", "does_not_exist", "executor")


def test_writer_list_memory_rejects_invalid_audience_filter(seeded):
    from gaia.store.writer import list_memory
    with pytest.raises(ValueError):
        list_memory("me", audience="nonsense")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
