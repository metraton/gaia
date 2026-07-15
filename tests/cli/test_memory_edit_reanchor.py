"""Tests for the ``gaia memory edit --project`` / ``--project-ref`` RE-ANCHOR path.

Closes the documented gap (``project_scan_v2_followups``): ``gaia memory add``
could only anchor ``memory.project_ref`` at WRITE time, so a row written with a
NULL or wrong ``project_ref`` had no correction path. ``edit`` now re-anchors an
existing row in place, resolving the project scope with the SAME contract
``add`` uses (name -> stable identity, or a direct identity string, never a
silent NULL on an unknown project).

Coverage:
  * CLI: edit --project resolves a name to its project_identity and re-anchors
  * CLI: edit --project-ref sets the identity directly
  * CLI: edit --project on an unknown project -> structured error (exit 1)
  * CLI: edit --project + --project-ref together -> argparse rejects (mutually exclusive)
  * CLI: edit --project JSON output carries the reanchor block
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
    """Route the substrate DB into tmp_path; seed a project + a memory row.

    The project 'gaia' carries a project_identity so --project can resolve it.
    The memory row 'orphan_note' starts with project_ref NULL (the bug case).
    """
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    from gaia.paths import db_path
    from gaia.store.writer import _connect, upsert_memory

    path = db_path()
    con = _connect(path)
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        con.execute(
            "INSERT INTO projects (workspace, name, project_identity, status) "
            "VALUES ('me', 'gaia', 'github.com/metraton/gaia', 'active')"
        )
        con.commit()
    finally:
        con.close()

    upsert_memory("me", "orphan_note", type="project", body="a note with no anchor")
    return path


def _project_ref(db_path: Path, name: str):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        r = con.execute(
            "SELECT project_ref FROM memory WHERE workspace='me' AND name=?",
            (name,),
        ).fetchone()
        return r["project_ref"] if r else None
    finally:
        con.close()


def _build_parser():
    import cli.memory as memory_mod
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="cmd")
    memory_mod.register(subparsers)
    return parser, memory_mod


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_edit_project_resolves_name_and_reanchors(seeded, capsys):
    parser, _ = _build_parser()
    assert _project_ref(seeded, "orphan_note") is None
    args = parser.parse_args([
        "memory", "edit", "--name=orphan_note",
        "--project=gaia", "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}, stdout={captured.out}"
    assert _project_ref(seeded, "orphan_note") == "github.com/metraton/gaia"
    assert "Re-anchored" in captured.out


def test_edit_project_ref_direct(seeded, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "edit", "--name=orphan_note",
        "--project-ref=github.com/x/direct", "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}, stdout={captured.out}"
    assert _project_ref(seeded, "orphan_note") == "github.com/x/direct"


def test_edit_project_unknown_is_structured_error(seeded, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "edit", "--name=orphan_note",
        "--project=does-not-exist", "--workspace=me",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "not found" in (captured.err + captured.out).lower()
    # The row is NOT silently anchored to anything on failure.
    assert _project_ref(seeded, "orphan_note") is None


def test_edit_project_and_project_ref_mutually_exclusive(seeded):
    parser, _ = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([
            "memory", "edit", "--name=orphan_note",
            "--project=gaia", "--project-ref=github.com/x/direct",
            "--workspace=me",
        ])


def test_edit_project_json_output(seeded, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args([
        "memory", "edit", "--name=orphan_note",
        "--project=gaia", "--workspace=me", "--json",
    ])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}, stdout={captured.out}"
    payload = json.loads(captured.out)
    assert payload["reanchor"]["after_project_ref"] == "github.com/metraton/gaia"
    assert payload["reanchor"]["before_project_ref"] is None
