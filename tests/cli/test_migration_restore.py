"""`gaia migrate` restores a backup, plans read-only and loses nothing on failure, on copies under tmp_path."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GAIA = _REPO_ROOT / "bin" / "gaia"
_SCHEMA_SQL = _REPO_ROOT / "gaia" / "store" / "schema.sql"
_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "published_bases"

sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "bin" / "cli"))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
sys.path.insert(0, str(_FIXTURES))
import bootstrap_database as engine  # noqa: E402
import migrate  # noqa: E402
import migration_guard  # noqa: E402
from loader import load_base  # noqa: E402

from gaia.paths.backup import restore_gzip  # noqa: E402


def _expected_version() -> int:
    text = (_REPO_ROOT / "bin" / "cli" / "doctor.py").read_text(encoding="utf-8")
    return int(re.search(r"^EXPECTED_SCHEMA_VERSION\s*=\s*(\d+)", text, re.M).group(1))


EXPECTED = _expected_version()


def _gaia(tmp: Path, *args: str) -> subprocess.CompletedProcess:
    db = tmp / "gaia.db"
    env = {**os.environ, "GAIA_DB": str(db), "GAIA_DATA_DIR": str(tmp), "WORKSPACE": str(tmp)}
    return subprocess.run(
        [sys.executable, str(_GAIA), "migrate", *args, "--db", str(db)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _ledger(db: Path) -> int:
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    finally:
        con.close()


def _user_rows(db: Path) -> dict[str, list]:
    con = sqlite3.connect(db)
    try:
        return {
            "memory": sorted(con.execute("SELECT name, description FROM memory")),
            "briefs": sorted(r[0] for r in con.execute("SELECT name FROM briefs")),
        }
    finally:
        con.close()


def _digest(db: Path) -> str:
    return hashlib.sha256(db.read_bytes()).hexdigest()


def test_a_v21_database_upgrades_restores_and_upgrades_again_losing_nothing(tmp_path):
    db = tmp_path / "gaia.db"
    load_base(21, db)
    before = _user_rows(db)
    label = migration_guard.chain_label(21, EXPECTED)

    upgraded = _gaia(tmp_path, "apply", "--consent-chain", label)
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    assert _ledger(db) == EXPECTED
    sqlite3.connect(db).execute("PRAGMA journal_mode = WAL").close()
    (backup,) = (tmp_path / "backups").glob("*-pre-migrate-*.db.gz")

    restored = _gaia(tmp_path, "restore", "--from", str(backup))

    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert _ledger(db) == 21
    assert _user_rows(db) == before
    assert backup.is_file(), "the backup survives its own restore"
    (kept,) = (tmp_path / "backups").glob("*-pre-restore-*.db.gz")
    replaced = tmp_path / "replaced.db"
    restore_gzip(kept, replaced)
    assert _ledger(replaced) == EXPECTED, "the database the restore replaced is kept"
    assert not list(tmp_path.glob("gaia.db.restore*"))
    listed = _gaia(tmp_path, "plan").stdout
    assert f"  {kept}" in listed and f"  {backup}" in listed and "gaia migrate restore --from" in listed

    again =_gaia(tmp_path, "apply", "--consent-chain", label)
    assert again.returncode == 0, again.stdout + again.stderr
    assert _ledger(db) == EXPECTED
    assert _user_rows(db) == before


def test_a_backup_that_does_not_verify_is_refused_and_the_database_is_untouched(tmp_path):
    db = tmp_path / "gaia.db"
    load_base(51, db)
    digest = _digest(db)
    bogus = tmp_path / "bogus.db.gz"
    with gzip.open(bogus, "wb") as packed:
        packed.write(b"not a database " * 512)

    result = _gaia(tmp_path, "restore", "--from", str(bogus))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "did not verify" in result.stderr
    assert _digest(db) == digest
    assert not (tmp_path / "backups").exists()
    assert not list(tmp_path.glob("gaia.db.restore*"))


def test_plan_json_describes_the_pending_change_and_writes_nothing(tmp_path):
    db = tmp_path / "gaia.db"
    load_base(51, db)
    digest = _digest(db)

    result = _gaia(tmp_path, "plan", "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["direction"] == "behind"
    assert (report["ledger"], report["expected"]) == (51, EXPECTED)
    assert report["chain"] == migration_guard.chain_label(51, EXPECTED)
    assert len(report["migrations"]) == EXPECTED - 51
    assert report["consent_command"] == migration_guard.consent_command(report["chain"])
    assert report["backup_dir"] == str(tmp_path / "backups")
    assert migrate.plan_report(str(db)) == report
    assert _digest(db) == digest

    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO schema_version (version, applied_at, description) VALUES (?, 'now', 'newer')",
        (EXPECTED + 1,),
    )
    con.commit()
    con.close()
    ahead = migrate.plan_report(str(db))
    assert ahead["direction"] == "ahead-refused"
    assert ahead["ledger"] == EXPECTED + 1


_LATENT_TRIGGER = (
    "CREATE TRIGGER __latent_trigger AFTER DELETE ON memory "
    "BEGIN SELECT OLD.__column_that_never_existed; END;"
)


def _snapshot(db: Path):
    con = sqlite3.connect(db)
    try:
        objects = con.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall()
        rows = sorted(con.execute("SELECT * FROM memory"))
        return objects, rows, con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    finally:
        con.close()


def test_a_migration_failing_on_a_latent_trigger_leaves_rows_objects_and_ledger(tmp_path):
    scripts = tmp_path / "scripts"
    shutil.copytree(_REPO_ROOT / "scripts" / "migrations", scripts / "migrations")
    for name in ("bootstrap_database.py", "migration_guard.py"):
        shutil.copy(_REPO_ROOT / "scripts" / name, scripts / name)
    (tmp_path / "bin" / "cli").mkdir(parents=True)
    (tmp_path / "bin" / "cli" / "doctor.py").write_text(f"EXPECTED_SCHEMA_VERSION = {EXPECTED}\n", encoding="utf-8")
    last = scripts / "migrations" / f"v{EXPECTED - 1}_to_v{EXPECTED}.sql"
    last.write_text(
        last.read_text(encoding="utf-8")
        + "\nUPDATE memory SET description = 'clobbered';\nALTER TABLE memory RENAME TO memory_old;\n",
        encoding="utf-8",
    )
    db = tmp_path / "gaia.db"
    load_base(51, db)
    sqlite3.connect(db).executescript(_LATENT_TRIGGER)
    before = _snapshot(db)
    assert before[1], "the base must carry memory rows"

    result = subprocess.run(
        [sys.executable, str(scripts / "bootstrap_database.py"), "--consent-chain",
         migration_guard.chain_label(51, EXPECTED)],
        env={**os.environ, "GAIA_DB": str(db), "SCHEMA_FILE": str(_SCHEMA_SQL), "WORKSPACE": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "__latent_trigger" in result.stderr
    assert _snapshot(db) == before


def test_each_kind_of_backup_keeps_its_newest_copies_without_evicting_the_other(tmp_path):
    migrate_stamps = [f"2026110{d}T120000000000Z" for d in range(1, 6)]
    restore_stamps = [f"2026110{d}T130000000000Z" for d in range(1, 3)]
    for stamp in migrate_stamps:
        (tmp_path / f"gaia-v51-pre-migrate-{stamp}.db.gz").write_bytes(b"x")
    for stamp in restore_stamps:
        (tmp_path / f"gaia-v75-pre-restore-{stamp}.db.gz").write_bytes(b"x")

    engine._prune_backups(tmp_path, "migrate")

    left = sorted(p.name for p in tmp_path.iterdir())
    kept_migrate = [n for n in left if "-pre-migrate-" in n]
    assert kept_migrate == [f"gaia-v51-pre-migrate-{s}.db.gz" for s in migrate_stamps[-engine.BACKUPS_KEPT:]]
    assert len([n for n in left if "-pre-restore-" in n]) == len(restore_stamps)
