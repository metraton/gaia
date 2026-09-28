"""The store's schema-direction gate: a Gaia older than its database never writes.

A database migrated by a newer Gaia carries columns and constraints this code
does not know. Writing to it would fail on an unknown column at best and store
rows the newer code misreads at worst, so every write through the store is
refused with a message naming the fix, while reads keep working. The database
version is read once per process, not once per write.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO / "bin") not in sys.path:
    sys.path.insert(0, str(_REPO / "bin"))

from cli.doctor import EXPECTED_SCHEMA_VERSION  # noqa: E402

from gaia.store import reader, writer  # noqa: E402


def _live_version(db: Path) -> int:
    con = sqlite3.connect(str(db))
    try:
        return con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    finally:
        con.close()


def _seal(db: Path, version: int) -> None:
    con = sqlite3.connect(str(db))
    try:
        con.execute(
            "INSERT INTO schema_version (version, applied_at, description) "
            "VALUES (?, '2026-01-01T00:00:00Z', 'sealed by a newer gaia')",
            (version,),
        )
        con.commit()
    finally:
        con.close()


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "gaia.db"
    monkeypatch.setenv("GAIA_DB", str(path))
    return path


def test_fresh_database_is_sealed_at_the_version_schema_sql_builds(db):
    writer._connect(db).close()

    assert _live_version(db) == EXPECTED_SCHEMA_VERSION


def test_fresh_database_stays_unsealed_when_the_build_declares_no_version(
    db, tmp_path, monkeypatch
):
    monkeypatch.setattr(writer, "_DOCTOR_PY", tmp_path / "absent" / "doctor.py")
    writer._expected_schema_version.cache_clear()
    try:
        writer._connect(db).close()
    finally:
        writer._expected_schema_version.cache_clear()

    assert _live_version(db) is None


def test_database_ahead_refuses_writes_naming_the_fix_and_keeps_reads(db):
    notification_id = writer.add_task_notification(task_name="t", headline="before")
    _seal(db, EXPECTED_SCHEMA_VERSION + 1)
    writer._SCHEMA_VERSION_BY_DB.clear()

    with pytest.raises(sqlite3.DatabaseError) as refused:
        writer.add_task_notification(task_name="t", headline="after")

    message = str(refused.value)
    assert "no column named" not in message
    assert f"v{EXPECTED_SCHEMA_VERSION + 1}" in message
    assert f"v{EXPECTED_SCHEMA_VERSION}" in message
    assert "Install a Gaia whose schema version is at least" in message
    assert isinstance(refused.value, writer.SchemaAheadError)

    rows = reader.list_unread_notifications()
    assert [r["id"] for r in rows] == [notification_id]
    assert [r["headline"] for r in rows] == ["before"]


def test_refusal_also_covers_writes_through_an_explicit_cursor(db):
    writer._connect(db).close()
    _seal(db, EXPECTED_SCHEMA_VERSION + 1)
    writer._SCHEMA_VERSION_BY_DB.clear()

    con = writer._connect(db)
    try:
        with pytest.raises(writer.SchemaAheadError):
            con.cursor().execute(
                "INSERT INTO task_notifications (task_name, headline, created_at, unread) "
                "VALUES ('t', 'h', 'now', 1)"
            )
        assert con.execute("SELECT COUNT(*) FROM task_notifications").fetchone()[0] == 0
    finally:
        con.close()


def test_database_behind_or_level_still_accepts_writes(db):
    writer._connect(db).close()
    con = sqlite3.connect(str(db))
    con.execute("DELETE FROM schema_version WHERE version = ?", (EXPECTED_SCHEMA_VERSION,))
    con.execute(
        "INSERT INTO schema_version (version, applied_at, description) "
        "VALUES (?, 'x', 'older')",
        (EXPECTED_SCHEMA_VERSION - 1,),
    )
    con.commit()
    con.close()
    writer._SCHEMA_VERSION_BY_DB.clear()

    assert writer.add_task_notification(task_name="t", headline="h") > 0


def test_database_version_is_read_once_per_process(db, monkeypatch):
    writer._connect(db).close()
    _seal(db, EXPECTED_SCHEMA_VERSION + 1)
    writer._SCHEMA_VERSION_BY_DB.clear()

    reads = []
    real = writer._read_schema_version

    def counting(con):
        reads.append(1)
        return real(con)

    monkeypatch.setattr(writer, "_read_schema_version", counting)

    for _ in range(2):
        with pytest.raises(writer.SchemaAheadError):
            writer.add_task_notification(task_name="t", headline="h")

    assert len(reads) == 1


def test_contract_writer_refuses_a_database_ahead(db):
    from hooks.modules.context.context_writer import apply_update

    writer._connect(db).close()
    _seal(db, EXPECTED_SCHEMA_VERSION + 1)
    writer._SCHEMA_VERSION_BY_DB.clear()

    audit = apply_update(
        {"contract": "stack", "payload": {"language": "python"}},
        "developer",
        workspace="me",
        db_path=db,
    )

    assert audit["success"] is False
    assert "Install a Gaia whose schema version is at least" in audit["error"]
