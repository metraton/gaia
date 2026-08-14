"""v50 row age (memory.created_at), forward-only BY DECISION -- task 3 of
usar-la-telemetria-de-memoria-edad-sesgo-y-pesaje.

Property under test, not a command list: the normal insertion route
(``gaia memory add`` -> ``upsert_memory``) stamps a brand-new row with its
real creation time; a pre-existing row -- whether it predates v50 (NULL) or
was born after it (a real timestamp) -- is NEVER given a new ``created_at``
by an edit, because editing is not being born. The count of rows with
``created_at IS NULL`` therefore never drops just because the system keeps
running: it can only be held constant (an edit) or, by construction, never
increases (nothing un-nulls a NULL).

Uses a real temporary SQLite DB (writer._connect materializes the current
schema.sql on first connect, so ``created_at`` already exists as the v50
column), mirroring the fixture pattern in test_memory_audience.py.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

_WORKSPACE = "me"


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    """Route the substrate DB into tmp_path; seed a workspace and two
    pre-existing rows shaped like rows that lived through the v49->v50
    migration -- inserted with no ``created_at`` at all, so the column
    defaults to NULL exactly as it does for a real pre-migration row."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    from gaia.paths import db_path
    from gaia.store.writer import _connect

    path = db_path()
    con = _connect(path)
    try:
        con.execute("INSERT INTO workspaces (name) VALUES (?)", (_WORKSPACE,))
        con.execute(
            "INSERT INTO memory (workspace, name, type, body, updated_at) "
            "VALUES (?, 'atom_preexisting_one', 'atom', 'pre-v50 row one', "
            "'2026-01-01T00:00:00Z')",
            (_WORKSPACE,),
        )
        con.execute(
            "INSERT INTO memory (workspace, name, type, body, updated_at) "
            "VALUES (?, 'atom_preexisting_two', 'atom', 'pre-v50 row two', "
            "'2026-01-01T00:00:00Z')",
            (_WORKSPACE,),
        )
        con.commit()
    finally:
        con.close()
    return path


def _build_parser():
    import cli.memory as memory_mod
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="cmd")
    memory_mod.register(subparsers)
    return parser


def _created_at(db_path: Path, name: str):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT created_at FROM memory WHERE workspace = ? AND name = ?",
            (_WORKSPACE, name),
        ).fetchone()
        return row["created_at"] if row else None
    finally:
        con.close()


def _null_created_at_count(db_path: Path) -> int:
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(
            "SELECT COUNT(*) FROM memory WHERE created_at IS NULL"
        ).fetchone()[0]
    finally:
        con.close()


def _run(parser, argv):
    """Run ``gaia memory <argv>`` through the real CLI parser + handler."""
    args = parser.parse_args(["memory", *argv])
    return args.func(args)


class TestRowAgeForwardOnly:
    def test_preexisting_rows_start_with_null_created_at(self, seeded):
        # Sanity precondition: the fixture reproduces the pre-v50 shape
        # before anything under test runs.
        assert _created_at(seeded, "atom_preexisting_one") is None
        assert _created_at(seeded, "atom_preexisting_two") is None
        assert _null_created_at_count(seeded) == 2

    def test_new_row_via_gaia_memory_add_gets_a_real_created_at(self, seeded):
        parser = _build_parser()
        before_null = _null_created_at_count(seeded)

        rc = _run(parser, [
            "add", "--name=atom_newborn_row", "--type=atom",
            "--body=born after v50, must carry a real timestamp",
            f"--workspace={_WORKSPACE}",
        ])
        assert rc in (0, None)

        created = _created_at(seeded, "atom_newborn_row")
        assert created is not None
        # Real ISO8601 stamp, not a sentinel -- same clock shape the rest of
        # the store already writes (_now_iso).
        assert created.endswith("Z")
        assert len(created) == len("2026-08-13T00:00:00Z")

        # The new row is NOT NULL, so the NULL count must be unchanged --
        # a birth adds to the denominator, never to the NULL numerator.
        assert _null_created_at_count(seeded) == before_null

    def test_editing_a_preexisting_row_leaves_created_at_null(self, seeded):
        parser = _build_parser()
        assert _created_at(seeded, "atom_preexisting_one") is None
        before_null = _null_created_at_count(seeded)

        rc = _run(parser, [
            "add", "--name=atom_preexisting_one", "--type=atom",
            "--body=edited body, this is a correction not a birth",
            f"--workspace={_WORKSPACE}",
        ])
        assert rc in (0, None)

        # Editing is not being born: still NULL, and the body did change
        # (proves the edit really landed rather than being a no-op).
        assert _created_at(seeded, "atom_preexisting_one") is None
        con = sqlite3.connect(str(seeded))
        try:
            body = con.execute(
                "SELECT body FROM memory WHERE workspace=? AND name=?",
                (_WORKSPACE, "atom_preexisting_one"),
            ).fetchone()[0]
        finally:
            con.close()
        assert body == "edited body, this is a correction not a birth"

        # The NULL count did not drop just because the system kept running
        # and processed a write.
        assert _null_created_at_count(seeded) == before_null

    def test_editing_a_newborn_row_preserves_its_real_created_at(self, seeded):
        """Forward-only cuts both ways: created_at is immutable once set,
        not just 'never invented for old rows'. A second upsert on a row
        that already has a real created_at must not re-stamp it either."""
        parser = _build_parser()
        _run(parser, [
            "add", "--name=atom_immutable_birth", "--type=atom",
            "--body=first write",
            f"--workspace={_WORKSPACE}",
        ])
        first_created = _created_at(seeded, "atom_immutable_birth")
        assert first_created is not None

        _run(parser, [
            "add", "--name=atom_immutable_birth", "--type=atom",
            "--body=second write, same slug",
            f"--workspace={_WORKSPACE}",
        ])
        assert _created_at(seeded, "atom_immutable_birth") == first_created

    def test_null_count_never_drops_across_a_mixed_sequence_of_writes(
        self, seeded,
    ):
        """The property the task asks to demonstrate directly: across a
        sequence of ordinary system activity (a new row born, an old row
        edited), the count of unknown-age rows never goes down."""
        parser = _build_parser()
        baseline = _null_created_at_count(seeded)
        assert baseline == 2

        _run(parser, [
            "add", "--name=atom_activity_one", "--type=atom",
            "--body=new row during activity",
            f"--workspace={_WORKSPACE}",
        ])
        assert _null_created_at_count(seeded) == baseline

        _run(parser, [
            "add", "--name=atom_preexisting_two", "--type=atom",
            "--body=old row edited during activity",
            f"--workspace={_WORKSPACE}",
        ])
        assert _null_created_at_count(seeded) == baseline

        _run(parser, [
            "add", "--name=atom_activity_two", "--type=atom",
            "--body=second new row during activity",
            f"--workspace={_WORKSPACE}",
        ])
        assert _null_created_at_count(seeded) == baseline
