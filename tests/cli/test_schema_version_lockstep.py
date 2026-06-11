"""Lockstep test: `EXPECTED_SCHEMA_VERSION` in doctor.py must stay in lockstep
with the schema floor and the forward migrations available in
scripts/migrations/.

Why this exists
---------------
`gaia doctor` warns when the live DB schema_version drifts from the CLI's
baked-in expectation. The migration framework was collapsed to a **schema
floor** model (Section 3b of bootstrap_database.sh): the full historical
v1->v17 chain was removed and the baseline is now the floor (v18). Under this
model the bootstrap script:

  1. On a fresh DB, stamps `(version=FLOOR, ...)` directly -- schema.sql
     already produced the floor shape, so there is no v1 seed and no chain walk.
  2. Rejects any DB below the floor (in-place upgrade unsupported).
  3. Reads `EXPECTED_SCHEMA_VERSION` from doctor.py and applies any forward
     migrations scripts/migrations/v{N-1}_to_v{N}.sql (N > FLOOR) for DBs
     behind EXPECTED, stamping the ledger only on success.

The drift modes this test still defends against are:

  * `EXPECTED_SCHEMA_VERSION` bumped in doctor.py without shipping the
    corresponding forward migration file -- bootstrap would abort on a DB
    behind EXPECTED with "missing migration file".
  * A forward migration file added but `EXPECTED_SCHEMA_VERSION` never bumped
    -- doctor would never request the migration, and live DDL silently lags.

Both modes are caught by checking that `EXPECTED_SCHEMA_VERSION` equals the
floor when no forward migrations exist yet, and equals
`max(N for v{N-1}_to_v{N}.sql in scripts/migrations/)` once they do.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))


_DOCTOR_PATH = _BIN_DIR / "cli" / "doctor.py"
_BOOTSTRAP_PATH = _REPO_ROOT / "scripts" / "bootstrap_database.sh"
_MIGRATIONS_DIR = _REPO_ROOT / "scripts" / "migrations"

# Matches the canonical filename pattern v{PREV}_to_v{N}.sql; captures N.
# The historical _fresh / _merge variants no longer exist under the floor
# model, so the strict pattern (no suffix) is the only one we expect.
_MIGRATION_FILENAME = re.compile(r"^v(\d+)_to_v(\d+)\.sql$")


def _read_doctor_expected_version() -> int:
    """Parse `EXPECTED_SCHEMA_VERSION = N` from doctor.py.

    Static parse rather than `import` so the test does not pull in the
    full doctor module (avoids side-effect imports of sqlite/db helpers).
    """
    assert _DOCTOR_PATH.exists(), f"doctor.py not found at {_DOCTOR_PATH}"
    text = _DOCTOR_PATH.read_text()
    m = re.search(r"^EXPECTED_SCHEMA_VERSION\s*=\s*(\d+)\s*$", text, re.MULTILINE)
    assert m is not None, (
        f"Could not locate `EXPECTED_SCHEMA_VERSION = <int>` in {_DOCTOR_PATH}"
    )
    return int(m.group(1))


def _read_bootstrap_floor() -> int:
    """Parse `SCHEMA_FLOOR=N` from bootstrap_database.sh Section 3b.

    The floor is the lowest schema version supported in-place. It is the
    baseline a fresh install is stamped at, and the threshold below which
    bootstrap refuses an in-place upgrade.
    """
    assert _BOOTSTRAP_PATH.exists(), (
        f"bootstrap_database.sh not found at {_BOOTSTRAP_PATH}"
    )
    text = _BOOTSTRAP_PATH.read_text()
    m = re.search(r"^\s*SCHEMA_FLOOR\s*=\s*(\d+)\s*$", text, re.MULTILINE)
    assert m is not None, (
        f"Could not locate `SCHEMA_FLOOR=<int>` in {_BOOTSTRAP_PATH}. "
        f"Section 3b must declare the schema floor literally."
    )
    return int(m.group(1))


def _read_migrations_max_target() -> int:
    """Return the highest target version across forward migration files,
    or the floor when no forward migrations exist yet.

    Under the floor model the baseline is the floor (no v1 seed, no chain).
    When scripts/migrations/ holds no v{PREV}_to_v{N}.sql files, the expected
    version is exactly the floor. Once forward migrations are added, the
    expected version is the highest target N among them.
    """
    floor = _read_bootstrap_floor()

    if not _MIGRATIONS_DIR.is_dir():
        return floor

    targets: list[int] = []
    for entry in _MIGRATIONS_DIR.iterdir():
        m = _MIGRATION_FILENAME.match(entry.name)
        if m is None:
            continue
        prev, target = int(m.group(1)), int(m.group(2))
        # Sanity check: each file must be a single-step migration v(N-1)->v(N).
        assert target == prev + 1, (
            f"Migration file {entry.name} is not a single-step migration "
            f"(v{prev} -> v{target}). Use one v(N-1)_to_v(N).sql per step."
        )
        # Forward-only: every migration must advance the schema PAST the floor.
        # A file targeting the floor or below is a leftover from the collapsed
        # historical chain and should not exist.
        assert target > floor, (
            f"Migration file {entry.name} targets v{target}, at or below the "
            f"floor v{floor}. The historical chain was collapsed into the "
            f"floor; only forward migrations (v{floor + 1}+) belong here."
        )
        targets.append(target)

    if not targets:
        return floor
    return max(targets)


def test_expected_version_matches_floor_and_migrations() -> None:
    """`EXPECTED_SCHEMA_VERSION` must equal the floor (no forward migrations)
    or the highest forward-migration target (once they exist).

    If this fails you either:
      - Bumped `EXPECTED_SCHEMA_VERSION` in doctor.py without dropping a
        new scripts/migrations/v{N-1}_to_v{N}.sql file -- a DB behind EXPECTED
        will abort with "missing migration file".
      - Added a forward migration file without bumping
        `EXPECTED_SCHEMA_VERSION` -- the loop in bootstrap.sh Section 3c will
        never request it.

    Fix: update both in the same commit (the doctor constant and the file).
    """
    expected = _read_doctor_expected_version()
    target = _read_migrations_max_target()

    assert expected == target, (
        f"Schema version / migration drift detected:\n"
        f"  bin/cli/doctor.py EXPECTED_SCHEMA_VERSION = {expected}\n"
        f"  floor-or-max-migration-target            = {target}\n"
        f"These must be equal. When no forward migrations exist, "
        f"EXPECTED_SCHEMA_VERSION must equal the floor; when they do, it must "
        f"equal the highest migration target. Update both in the same commit."
    )


def test_doctor_expected_not_below_floor() -> None:
    """`EXPECTED_SCHEMA_VERSION` must never be below the floor.

    The floor is the lowest supported schema. The CLI cannot expect a version
    older than the baseline a fresh install produces.
    """
    expected = _read_doctor_expected_version()
    floor = _read_bootstrap_floor()
    assert expected >= floor, (
        f"EXPECTED_SCHEMA_VERSION ({expected}) is below the schema floor "
        f"({floor}). The CLI cannot expect a version older than the floor."
    )


def test_bootstrap_baseline_is_floor() -> None:
    """Bootstrap's baseline stamp MUST be the floor (v18 today).

    Under the floor model Section 3b stamps `(version=SCHEMA_FLOOR, ...)` on a
    fresh DB rather than seeding v1 and walking the chain. The floor declared
    in bootstrap must match the floor the CLI expects (doctor's
    EXPECTED_SCHEMA_VERSION, when no forward migrations exist).
    """
    floor = _read_bootstrap_floor()
    assert floor == 18, (
        f"bootstrap_database.sh Section 3b must declare SCHEMA_FLOOR=18 as the "
        f"baseline floor; got {floor}."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
