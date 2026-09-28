"""Every published base upgrades to the current schema through `gaia migrate`.

Each fixture under tests/fixtures/published_bases/ is the database a release
actually produced (see generate.py there), carrying representative rows. For
each one this drives the real CLI -- `gaia migrate plan`, then `apply` -- and
holds the properties of the upgrade path: a backup exists before anything
changed, the ledger lands on the expected version, a chain that reaches data
asks exactly one consent for the whole chain, a structure-only chain asks
none, and the result carries every object a fresh install has.

Every case works in its own tmp directory; the user's ~/.gaia is never read.
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GAIA = _REPO_ROOT / "bin" / "gaia"
_SCHEMA_SQL = _REPO_ROOT / "gaia" / "store" / "schema.sql"
_MIGRATIONS = _REPO_ROOT / "scripts" / "migrations"
_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "published_bases"

sys.path.insert(0, str(_REPO_ROOT / "scripts"))
import migration_guard  # noqa: E402

BASES = (21, 26, 33, 37, 51, 57)


def _expected_version() -> int:
    text = (_REPO_ROOT / "bin" / "cli" / "doctor.py").read_text(encoding="utf-8")
    return int(re.search(r"^EXPECTED_SCHEMA_VERSION\s*=\s*(\d+)", text, re.M).group(1))


def load_base(version: int, db: Path) -> None:
    con = sqlite3.connect(db)
    con.executescript((_FIXTURES / f"v{version}.sql").read_text(encoding="utf-8"))
    con.close()


def _gaia(tmp: Path, db: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "GAIA_DB": str(db), "GAIA_DATA_DIR": str(tmp), "WORKSPACE": str(tmp)}
    return subprocess.run(
        [sys.executable, str(_GAIA), "migrate", *args, "--db", str(db)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _scalar(db: Path, sql: str):
    con = sqlite3.connect(db)
    try:
        return con.execute(sql).fetchone()[0]
    finally:
        con.close()


def _chain_reaches_data(db: Path, start: int, expected: int) -> bool:
    con = sqlite3.connect(db)
    census = migration_guard.take_census(con)
    con.close()
    return any(
        migration_guard.scan(
            (_MIGRATIONS / f"v{n - 1}_to_v{n}.sql").read_text(encoding="utf-8"), census
        )
        for n in range(start + 1, expected + 1)
    )


def _objects(db: Path) -> dict[tuple[str, str], set[str]]:
    """(type, name) of every table, index and trigger, with a table's columns."""
    con = sqlite3.connect(db)
    try:
        out = {}
        for kind, name in con.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE type IN ('table', 'index', 'trigger') AND name NOT LIKE 'sqlite_%'"
        ):
            cols = (
                {row[1] for row in con.execute(f'PRAGMA table_info("{name}")')}
                if kind == "table"
                else set()
            )
            out[(kind, name)] = cols
        return out
    finally:
        con.close()


@pytest.fixture(scope="module")
def fresh_objects(tmp_path_factory):
    db = tmp_path_factory.mktemp("fresh") / "fresh.db"
    con = sqlite3.connect(db)
    con.executescript(_SCHEMA_SQL.read_text(encoding="utf-8"))
    con.close()
    return _objects(db)


@pytest.mark.parametrize("base", BASES, ids=[f"v{b}" for b in BASES])
def test_published_base_upgrades_through_gaia_migrate(base, tmp_path, fresh_objects):
    expected = _expected_version()
    db = tmp_path / "gaia.db"
    load_base(base, db)
    memory_rows = _scalar(db, "SELECT COUNT(*) FROM memory")
    assert memory_rows >= 2, "the fixture must carry representative rows"
    label = migration_guard.chain_label(base, expected)

    plan = _gaia(tmp_path, db, "plan")
    assert plan.returncode == 0, plan.stdout + plan.stderr
    assert f"chain {label}" in plan.stdout
    assert _scalar(db, "SELECT MAX(version) FROM schema_version") == base

    consents_asked = 0
    applied = _gaia(tmp_path, db, "apply")
    if applied.returncode != 0:
        assert applied.stderr.count("BLOCKED") == 1, applied.stderr
        assert f"gaia migrate apply --consent-chain {label}" in applied.stderr
        assert _scalar(db, "SELECT MAX(version) FROM schema_version") == base
        consents_asked += 1
        applied = _gaia(tmp_path, db, "apply", "--consent-chain", label)
    assert applied.returncode == 0, applied.stdout + applied.stderr

    assert consents_asked == (1 if _chain_reaches_data_from_backup(tmp_path, base, expected) else 0)

    backups = list((tmp_path / "backups").glob("*.db"))
    assert len(backups) == 1, "exactly one pre-migrate backup"
    assert _scalar(backups[0], "SELECT MAX(version) FROM schema_version") == base

    assert _scalar(db, "SELECT MAX(version) FROM schema_version") == expected
    assert _scalar(db, "SELECT COUNT(*) FROM memory") == memory_rows

    upgraded = _objects(db)
    missing = sorted(key for key in fresh_objects if key not in upgraded)
    assert not missing, f"objects a fresh install has but v{base} did not get: {missing}"
    short_columns = {
        key[1]: sorted(fresh_objects[key] - upgraded[key])
        for key in fresh_objects
        if key[0] == "table" and fresh_objects[key] - upgraded[key]
    }
    assert not short_columns, f"columns missing after upgrading v{base}: {short_columns}"


def _chain_reaches_data_from_backup(tmp: Path, base: int, expected: int) -> bool:
    """Classify the chain against the pre-migrate state, which the backup holds."""
    backup = next((tmp / "backups").glob("*.db"))
    return _chain_reaches_data(backup, base, expected)
