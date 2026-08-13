"""gaia.store.writer.record_memory_access -- the P1 telemetry helper.

telemetria-de-uso-en-memoria-curada (brief), task 5: what counts is a
property, not a command list -- "deliberate" is any surface that renders a
row's full body on explicit request, "injection" is a row rendered inside an
automatic context block. This module tests the SHARED low-level helper both
surfaces call: it must bump exactly the requested counter/timestamp pair,
leave the other counter alone, never touch ``updated_at`` (the sort key
context injection depends on), and degrade to a no-op ``False`` -- never an
exception -- when the write cannot land, so a telemetry failure never breaks
the read it instruments.

Builds its own disposable DB from the REAL gaia/store/schema.sql, in pytest's
tmp_path (never a .gaia-tree path) -- mirrors
tests/unit/test_memory_telemetry_audit_isolation.py and
tests/unit/test_memory_search_fts_reindex_scope.py, the two existing tests
built the same way for this same entry.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_SQL = ROOT / "gaia" / "store" / "schema.sql"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gaia.store import writer  # noqa: E402

_WORKSPACE = "me"
_SLUG = "telemetry_writer_probe"


def _seed_db(db_path: Path) -> None:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    con.execute("INSERT INTO workspaces (name) VALUES (?)", (_WORKSPACE,))
    con.execute(
        "INSERT INTO memory (workspace, name, type, body, class, updated_at) "
        "VALUES (?, ?, 'project', 'original body', 'log', '2026-01-01T00:00:00Z')",
        (_WORKSPACE, _SLUG),
    )
    con.commit()
    con.close()


def _read_row(db_path: Path) -> sqlite3.Row:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT injection_count, deliberate_count, last_injected_at, "
            "       last_deliberate_at, updated_at "
            "FROM memory WHERE workspace=? AND name=?",
            (_WORKSPACE, _SLUG),
        ).fetchone()
    finally:
        con.close()


def _history_count(db_path: Path) -> int:
    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT COUNT(*) FROM memory_history").fetchone()[0]
    finally:
        con.close()


class TestRecordMemoryAccessDeliberate:
    def test_bumps_deliberate_counter_and_timestamp_only(self, tmp_path: Path) -> None:
        db_path = tmp_path / "deliberate.db"
        _seed_db(db_path)
        before = _read_row(db_path)
        before_history = _history_count(db_path)

        ok = writer.record_memory_access(_WORKSPACE, _SLUG, "deliberate", db_path=db_path)

        after = _read_row(db_path)
        after_history = _history_count(db_path)

        assert ok is True
        assert after["deliberate_count"] == before["deliberate_count"] + 1
        assert after["injection_count"] == before["injection_count"]
        assert after["last_deliberate_at"] is not None
        assert after["last_injected_at"] is None
        assert after["updated_at"] == before["updated_at"]
        assert after_history == before_history

    def test_bumps_injection_counter_and_timestamp_only(self, tmp_path: Path) -> None:
        db_path = tmp_path / "injection.db"
        _seed_db(db_path)
        before = _read_row(db_path)

        ok = writer.record_memory_access(_WORKSPACE, _SLUG, "injection", db_path=db_path)

        after = _read_row(db_path)

        assert ok is True
        assert after["injection_count"] == before["injection_count"] + 1
        assert after["deliberate_count"] == before["deliberate_count"]
        assert after["last_injected_at"] is not None
        assert after["last_deliberate_at"] is None
        assert after["updated_at"] == before["updated_at"]

    def test_repeated_calls_accumulate(self, tmp_path: Path) -> None:
        db_path = tmp_path / "repeat.db"
        _seed_db(db_path)
        for _ in range(3):
            writer.record_memory_access(_WORKSPACE, _SLUG, "deliberate", db_path=db_path)
        after = _read_row(db_path)
        assert after["deliberate_count"] == 3
        assert after["injection_count"] == 0

    def test_invalid_kind_raises_value_error(self, tmp_path: Path) -> None:
        db_path = tmp_path / "invalid_kind.db"
        _seed_db(db_path)
        with pytest.raises(ValueError):
            writer.record_memory_access(_WORKSPACE, _SLUG, "bogus", db_path=db_path)


class TestRecordMemoryAccessBestEffort:
    """Property: a telemetry write that cannot land degrades to ``False`` and
    never raises -- and, critically, never blocks a concurrent read of the
    same row using a separate connection."""

    def test_locked_db_returns_false_without_raising(self, tmp_path: Path) -> None:
        db_path = tmp_path / "locked.db"
        _seed_db(db_path)

        locker = sqlite3.connect(db_path)
        locker.isolation_level = None
        try:
            locker.execute("BEGIN IMMEDIATE")

            ok = writer.record_memory_access(
                _WORKSPACE, _SLUG, "deliberate", db_path=db_path
            )

            assert ok is False
        finally:
            locker.rollback()
            locker.close()

        # The lock is released -- the row must be untouched by the failed
        # attempt (no partial write, no orphaned counter bump).
        after = _read_row(db_path)
        assert after["deliberate_count"] == 0
        assert after["last_deliberate_at"] is None

    def test_read_survives_while_telemetry_write_is_locked_out(
        self, tmp_path: Path
    ) -> None:
        """The property the whole task exists for: measuring usage can never
        cost the read being measured. A concurrent reader (get_memory, a
        SEPARATE connection) must still return the row while the telemetry
        writer is locked out on the very same DB file."""
        db_path = tmp_path / "locked_read.db"
        _seed_db(db_path)

        locker = sqlite3.connect(db_path)
        locker.isolation_level = None
        try:
            locker.execute("BEGIN IMMEDIATE")

            row = writer.get_memory(_WORKSPACE, _SLUG, db_path=db_path)
            assert row is not None
            assert row["body"] == "original body"

            ok = writer.record_memory_access(
                _WORKSPACE, _SLUG, "deliberate", db_path=db_path
            )
            assert ok is False
        finally:
            locker.rollback()
            locker.close()

    def test_nonexistent_parent_directory_returns_false(self, tmp_path: Path) -> None:
        """A connect-time failure (unwritable/unreachable path) degrades the
        same way as a lock -- no exception escapes the helper."""
        bogus = tmp_path / "does" / "not" / "exist" / "no_permission.db"

        class _RaisingPath:
            """Path stand-in whose .parent.mkdir() always raises, simulating
            an unwritable filesystem without touching a real one."""

            def __init__(self, real: Path) -> None:
                self._real = real

            def __str__(self) -> str:
                return str(self._real)

            @property
            def parent(self):
                class _P:
                    def mkdir(self, *a, **kw):
                        raise OSError("simulated: permission denied")
                return _P()

        ok = writer.record_memory_access(
            _WORKSPACE, _SLUG, "deliberate", db_path=_RaisingPath(bogus)
        )
        assert ok is False
