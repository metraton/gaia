"""A migration chain interrupted at any point leaves the database as it was.

The chain from a published base (v51) to the current version is run with a
fault injected at migration k, for every k, in three places:

  * inside the migration -- a statement that fails after its real ones;
  * between the migration and its seal -- the ledger insert for k aborts;
  * a hard kill between the migration and its seal -- the process exits
    without running any rollback, the way a crash or a closed terminal does.

After each, sqlite_master and MAX(schema_version) must equal what they were
before the run. Everything runs on copies under tmp_path; the engine is staged
beside a copy of the real migrations so the faults never touch the source tree.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_SQL = _REPO_ROOT / "gaia" / "store" / "schema.sql"
_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "published_bases"
_BASE = 51

sys.path.insert(0, str(_FIXTURES))
from loader import load_base  # noqa: E402


def _expected_version() -> int:
    text = (_REPO_ROOT / "bin" / "cli" / "doctor.py").read_text(encoding="utf-8")
    return int(re.search(r"^EXPECTED_SCHEMA_VERSION\s*=\s*(\d+)", text, re.M).group(1))


EXPECTED = _expected_version()
CHAIN = tuple(range(_BASE + 1, EXPECTED + 1))
LABEL = f"v{_BASE}..v{EXPECTED}"


def _stage(tmp: Path) -> Path:
    scripts = tmp / "scripts"
    shutil.copytree(_REPO_ROOT / "scripts" / "migrations", scripts / "migrations")
    for name in ("bootstrap_database.py", "migration_guard.py"):
        shutil.copy(_REPO_ROOT / "scripts" / name, scripts / name)
    doctor = tmp / "bin" / "cli"
    doctor.mkdir(parents=True)
    (doctor / "doctor.py").write_text(f"EXPECTED_SCHEMA_VERSION = {EXPECTED}\n", encoding="utf-8")
    return scripts / "bootstrap_database.py"


def _live(tmp: Path) -> Path:
    db = tmp / "gaia.db"
    load_base(_BASE, db)
    return db


def _snapshot(db: Path):
    con = sqlite3.connect(db)
    try:
        objects = con.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        version = con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        return objects, version
    finally:
        con.close()


def _env(db: Path, tmp: Path) -> dict:
    return {**os.environ, "GAIA_DB": str(db), "SCHEMA_FILE": str(_SCHEMA_SQL), "WORKSPACE": str(tmp)}


def _append(runner: Path, k: int, sql: str) -> None:
    mig = runner.parent / "migrations" / f"v{k - 1}_to_v{k}.sql"
    mig.write_text(mig.read_text(encoding="utf-8") + "\n" + sql + "\n", encoding="utf-8")


def _run(runner: Path, db: Path, tmp: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(runner), "--consent-chain", LABEL],
        env=_env(db, tmp),
        capture_output=True,
        text=True,
        check=False,
    )


_KILL_AT_SEAL = """
import os, sys
sys.path.insert(0, {scripts!r})
import bootstrap_database as engine
real_stamp = engine._stamp
def dying_stamp(con, version, description, now_utc):
    if version == {k}:
        os._exit(9)
    real_stamp(con, version, description, now_utc)
engine._stamp = dying_stamp
sys.exit(engine.main(["--consent-chain", {label!r}]))
"""


@pytest.mark.parametrize("k", CHAIN, ids=[f"k=v{k}" for k in CHAIN])
@pytest.mark.parametrize("fault", ("inside-migration", "before-seal", "killed-before-seal"))
def test_interruption_leaves_objects_and_version_untouched(fault, k, tmp_path):
    runner = _stage(tmp_path)
    db = _live(tmp_path)
    before = _snapshot(db)
    assert before[1] == _BASE

    if fault == "inside-migration":
        _append(runner, k, "INSERT INTO __injected_missing_table VALUES (1);")
        result = _run(runner, db, tmp_path)
    elif fault == "before-seal":
        _append(
            runner,
            k,
            "CREATE TRIGGER __injected_seal_abort BEFORE INSERT ON schema_version "
            f"WHEN NEW.version = {k} BEGIN SELECT RAISE(ABORT, 'injected'); END;",
        )
        result = _run(runner, db, tmp_path)
    else:
        script = _KILL_AT_SEAL.format(scripts=str(runner.parent), k=k, label=LABEL)
        result = subprocess.run(
            [sys.executable, "-c", script],
            env=_env(db, tmp_path),
            capture_output=True,
            text=True,
            check=False,
        )

    assert result.returncode != 0, result.stdout + result.stderr
    after = _snapshot(db)
    assert after[1] == before[1], f"ledger moved: v{before[1]} -> v{after[1]}"
    assert after[0] == before[0], "sqlite_master changed after an interrupted chain"


def test_the_uninterrupted_chain_reaches_the_expected_version(tmp_path):
    runner = _stage(tmp_path)
    db = _live(tmp_path)
    result = _run(runner, db, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _snapshot(db)[1] == EXPECTED
