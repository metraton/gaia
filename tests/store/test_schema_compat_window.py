"""The schema compatibility window: older Gaia keeps writing to a compatible newer database.

Several installations of different versions share one database. Each sealed
migration records in ``schema_version.min_code_version`` the oldest code
version that can still write to the database; only a migration declared
``-- gaia-compat: breaking`` raises it. So an installation older than the
database keeps reading and writing while its version is at least that minimum,
warning once per process, and is refused -- with a message naming the fix --
only once a breaking migration has passed it by.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO / "bin") not in sys.path:
    sys.path.insert(0, str(_REPO / "bin"))

from cli.doctor import EXPECTED_SCHEMA_VERSION  # noqa: E402

from gaia.store import reader, writer  # noqa: E402

_SCHEMA_SQL = _REPO / "gaia" / "store" / "schema.sql"
_FIX_TEXT = "Install a Gaia whose schema version is at least"


def _ensure_min_column(con: sqlite3.Connection) -> None:
    columns = [row[1] for row in con.execute("PRAGMA table_info(schema_version)")]
    if "min_code_version" not in columns:
        con.execute("ALTER TABLE schema_version ADD COLUMN min_code_version INTEGER")


def _seal_ahead(db: Path, version: int, minimum: int | None) -> None:
    """Stamp ``db`` as a newer Gaia's migration would: version plus its minimum."""
    con = sqlite3.connect(str(db))
    try:
        _ensure_min_column(con)
        con.execute(
            "INSERT INTO schema_version (version, applied_at, description, min_code_version) "
            "VALUES (?, '2026-01-01T00:00:00Z', 'sealed by a newer gaia', ?)",
            (version, minimum),
        )
        con.commit()
    finally:
        con.close()


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "gaia.db"
    monkeypatch.setenv("GAIA_DB", str(path))
    writer._connect(path).close()
    writer._SCHEMA_VERSION_BY_DB.clear()
    yield path
    writer._SCHEMA_VERSION_BY_DB.clear()


def test_a_newer_database_within_the_window_accepts_writes_and_warns_once(db, capsys):
    _seal_ahead(db, EXPECTED_SCHEMA_VERSION + 1, EXPECTED_SCHEMA_VERSION)

    first = writer.add_task_notification(task_name="t", headline="first")
    second = writer.add_task_notification(task_name="t", headline="second")

    rows = reader.list_unread_notifications()
    assert sorted(r["id"] for r in rows) == sorted([first, second])
    warnings = capsys.readouterr().err
    assert warnings.count(f"schema v{EXPECTED_SCHEMA_VERSION + 1}") == 1
    assert "gaia install" in warnings


def test_a_newer_database_past_a_breaking_migration_refuses_writes_and_keeps_reads(db):
    kept = writer.add_task_notification(task_name="t", headline="before")
    _seal_ahead(db, EXPECTED_SCHEMA_VERSION + 1, EXPECTED_SCHEMA_VERSION + 1)
    writer._SCHEMA_VERSION_BY_DB.clear()

    with pytest.raises(writer.SchemaAheadError) as refused:
        writer.add_task_notification(task_name="t", headline="after")

    message = str(refused.value)
    assert f"v{EXPECTED_SCHEMA_VERSION + 1}" in message
    assert f"v{EXPECTED_SCHEMA_VERSION}" in message
    assert _FIX_TEXT in message
    assert [r["id"] for r in reader.list_unread_notifications()] == [kept]


def test_a_newer_database_with_no_recorded_minimum_is_refused(db):
    _seal_ahead(db, EXPECTED_SCHEMA_VERSION + 1, None)

    with pytest.raises(writer.SchemaAheadError) as refused:
        writer.add_task_notification(task_name="t", headline="h")

    assert _FIX_TEXT in str(refused.value)


def _stage_engine(tmp: Path, expected: int) -> Path:
    """The real engine behind `gaia migrate`, over a migrations dir this test owns."""
    scripts = tmp / "scripts"
    (scripts / "migrations").mkdir(parents=True)
    for name in ("bootstrap_database.py", "migration_guard.py"):
        (scripts / name).write_text(
            (_REPO / "scripts" / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    doctor = tmp / "bin" / "cli"
    doctor.mkdir(parents=True)
    (doctor / "doctor.py").write_text(
        f"EXPECTED_SCHEMA_VERSION = {expected}\n", encoding="utf-8"
    )
    return scripts / "bootstrap_database.py"


def _run_engine(engine: Path, db: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["GAIA_DB"] = str(db)
    env["SCHEMA_FILE"] = str(_SCHEMA_SQL)
    return subprocess.run(
        [sys.executable, str(engine)], env=env, capture_output=True, text=True, check=False
    )


def _minimums(db: Path) -> dict[int, int | None]:
    con = sqlite3.connect(str(db))
    try:
        return dict(con.execute("SELECT version, min_code_version FROM schema_version"))
    finally:
        con.close()


def test_each_seal_carries_the_minimum_and_only_a_breaking_migration_raises_it(tmp_path):
    base = EXPECTED_SCHEMA_VERSION
    engine = _stage_engine(tmp_path, base + 2)
    migrations = engine.parent / "migrations"
    (migrations / f"v{base}_to_v{base + 1}.sql").write_text(
        "-- gaia-compat: backward\nCREATE TABLE IF NOT EXISTS compat_probe_a (id INTEGER);\n",
        encoding="utf-8",
    )
    (migrations / f"v{base + 1}_to_v{base + 2}.sql").write_text(
        "-- gaia-compat: breaking\nCREATE TABLE IF NOT EXISTS compat_probe_b (id INTEGER);\n",
        encoding="utf-8",
    )
    db = tmp_path / "live.db"
    con = sqlite3.connect(str(db))
    con.executescript(_SCHEMA_SQL.read_text(encoding="utf-8"))
    _ensure_min_column(con)
    con.execute(
        "INSERT INTO schema_version (version, applied_at, description, min_code_version) "
        "VALUES (?, '2026-01-01T00:00:00Z', 'fixture', 51)",
        (base,),
    )
    con.commit()
    con.close()

    result = _run_engine(engine, db)

    assert result.returncode == 0, result.stdout + result.stderr
    minimums = _minimums(db)
    assert minimums[base + 1] == 51
    assert minimums[base + 2] == base + 2


def test_the_shipped_chain_seals_every_version_with_the_last_breaking_one(tmp_path):
    db = tmp_path / "fresh.db"

    result = _run_engine(_REPO / "scripts" / "bootstrap_database.py", db)

    assert result.returncode == 0, result.stdout + result.stderr
    minimums = _minimums(db)
    assert minimums[51] == 51
    assert minimums[52] == 51
    assert minimums[EXPECTED_SCHEMA_VERSION] == 51


def test_an_older_engine_leaves_a_compatible_newer_database_untouched(tmp_path):
    base = EXPECTED_SCHEMA_VERSION
    db = tmp_path / "live.db"
    writer._connect(db).close()
    _seal_ahead(db, base + 1, base)

    result = _run_engine(_REPO / "scripts" / "bootstrap_database.py", db)

    assert result.returncode == 0, result.stdout + result.stderr
    assert max(_minimums(db)) == base + 1
