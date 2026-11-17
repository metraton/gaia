"""`gaia migrate backup` keeps a verified copy rotation never prunes; `gaia migrate diagnose` names a failing step without touching the store."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GAIA = _REPO_ROOT / "bin" / "gaia"

sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "bin" / "cli"))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
import bootstrap_database as engine  # noqa: E402
import migrate  # noqa: E402
import migration_guard  # noqa: E402

EXPECTED = engine._read_expected_schema_version()


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A store at this code's schema version holding a few rows, outside any real data dir."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    db = tmp_path / "store" / "gaia.db"
    assert migrate.run("apply", db_path=str(db), capture=True).returncode == 0
    con = sqlite3.connect(db)
    con.executemany("INSERT INTO workspaces (name) VALUES (?)", [("alpha",), ("beta",)])
    con.commit()
    con.close()
    return db


def _gaia(db: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "GAIA_DB": str(db)}
    return subprocess.run([sys.executable, str(_GAIA), "migrate", *args, "--db", str(db)],
                          env=env, capture_output=True, text=True, check=False)


def _census(db: Path) -> dict[str, int]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return migration_guard.take_census(con)
    finally:
        con.close()


def _ledger(db: Path) -> int:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    finally:
        con.close()


def test_a_manual_backup_restores_whole_survives_rotation_and_is_listed(store, tmp_path, monkeypatch):
    result = _gaia(store, "backup")
    assert result.returncode == 0, result.stderr
    manual = [p for p in (store.parent / "backups").glob("*.db.gz") if "-manual-" in p.name]
    assert len(manual) == 1, result.stdout

    restored = tmp_path / "restored" / "gaia.db"
    restored.parent.mkdir()
    restore = migrate.run("restore", db_path=str(restored), capture=True, backup=str(manual[0]))
    assert restore.returncode == 0, restore.stderr
    assert _census(restored) == _census(store)

    monkeypatch.setattr(engine, "GAIA_DB", store)
    con = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    try:
        census, now = migration_guard.take_census(con), datetime.now(timezone.utc)
        for minutes in range(1, engine.BACKUPS_KEPT + 2):
            engine._backup(con, EXPECTED, now + timedelta(minutes=minutes), census)
    finally:
        con.close()
    assert manual[0].is_file()
    assert len(engine._stamped_backups(store.parent / "backups", "migrate")) == engine.BACKUPS_KEPT
    assert str(manual[0]) in migrate.plan_report(str(store))["backups"]


def _engine_whose_second_migration_fails(tmp_path: Path) -> Path:
    tree = tmp_path / "tree"
    (tree / "scripts" / "migrations").mkdir(parents=True)
    (tree / "bin" / "cli").mkdir(parents=True)
    (tree / "gaia" / "store").mkdir(parents=True)
    for name in ("bootstrap_database.py", "migration_guard.py"):
        shutil.copy(_REPO_ROOT / "scripts" / name, tree / "scripts" / name)
    shutil.copy(_REPO_ROOT / "gaia" / "store" / "schema.sql", tree / "gaia" / "store" / "schema.sql")
    (tree / "bin" / "cli" / "doctor.py").write_text(f"EXPECTED_SCHEMA_VERSION = {EXPECTED + 2}\n")
    migrations = tree / "scripts" / "migrations"
    (migrations / f"v{EXPECTED}_to_v{EXPECTED + 1}.sql").write_text(
        "-- gaia-compat: backward\nCREATE TABLE diagnose_probe (id INTEGER);\n")
    (migrations / f"v{EXPECTED + 1}_to_v{EXPECTED + 2}.sql").write_text(
        "-- gaia-compat: backward\nINSERT INTO diagnose_missing_table (id) VALUES (1);\n")
    return tree / "scripts" / "bootstrap_database.py"


def _cli(action: str, db: Path, capfd, as_json: bool = False) -> tuple[int, str, str]:
    rc = migrate.cmd_migrate(Namespace(migrate_action=action, db_path=str(db), as_json=as_json))
    out, err = capfd.readouterr()
    return rc, out, err


def test_diagnose_names_the_failing_step_and_leaves_the_store_as_it_was(store, tmp_path, monkeypatch, capfd):
    monkeypatch.setattr(migrate, "ENGINE", _engine_whose_second_migration_fails(tmp_path))
    failing = f"v{EXPECTED + 1}->v{EXPECTED + 2}"
    failing_file = f"v{EXPECTED + 1}_to_v{EXPECTED + 2}.sql"
    digest = hashlib.sha256(store.read_bytes()).hexdigest()

    rc, out, _err = _cli("diagnose", store, capfd)
    assert rc != 0
    assert failing in out and failing_file in out
    assert "no such table: diagnose_missing_table" in out
    assert "gaia migrate backup" in out

    rc, out, _err = _cli("diagnose", store, capfd, as_json=True)
    report = json.loads(out)
    assert rc != 0 and report["exit"] != 0
    assert (report["failed_step"]["from"], report["failed_step"]["to"]) == (EXPECTED + 1, EXPECTED + 2)
    assert report["failed_step"]["file"].endswith(failing_file)
    assert "no such table: diagnose_missing_table" in report["error"]
    assert any(command.startswith("gaia migrate ") for command in report["route"])

    assert hashlib.sha256(store.read_bytes()).hexdigest() == digest
    assert _ledger(store) == EXPECTED
    assert not list((tmp_path / "data" / "scratch").glob("migrate-diagnose-*"))

    rc, _out, err = _cli("apply", store, capfd)
    assert rc != 0
    assert f"failing step: {failing}" in err and failing_file in err
    assert "gaia migrate diagnose" in err
    assert _ledger(store) == EXPECTED


def test_diagnose_on_a_current_store_through_the_cli_passes_and_names_nothing_to_do(store):
    diagnosis = _gaia(store, "diagnose", "--json")
    assert diagnosis.returncode == 0, diagnosis.stderr
    report = json.loads(diagnosis.stdout)
    assert (report["direction"], report["failed_step"]) == ("current", None)
