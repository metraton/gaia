"""Lockstep test: `EXPECTED_SCHEMA_VERSION` in doctor.py must equal the
highest migration version available in scripts/migrations/.

Why this exists
---------------
`gaia doctor` warns when the live DB schema_version drifts from the CLI's
baked-in expectation. After the migration-framework rewrite (Section 3c of
bootstrap_database.sh), the bootstrap script no longer hard-codes a row
per advertised version. Instead it:

  1. Seeds the baseline `(version=1, ...)` row literally.
  2. Reads `EXPECTED_SCHEMA_VERSION` from doctor.py.
  3. Loops from current+1 through EXPECTED, applying scripts/migrations/
     v{N-1}_to_v{N}.sql and stamping the ledger only on success.

The drift modes this test still defends against are:

  * `EXPECTED_SCHEMA_VERSION` bumped in doctor.py without shipping the
    corresponding migration file -- bootstrap would abort on every fresh
    install with "missing migration file".
  * A migration file added but `EXPECTED_SCHEMA_VERSION` never bumped --
    doctor would never request the migration, and live DDL silently lags.

Both modes are caught by checking that `EXPECTED_SCHEMA_VERSION` equals
`max(N for v{N-1}_to_v{N}.sql in scripts/migrations/) + 1` (or just 1 if
no migrations exist yet).
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


def _read_migrations_max_target() -> int:
    """Return the highest target version N across all v{PREV}_to_v{N}.sql files.

    Returns 1 when no migration files exist -- that is the baseline state
    where the only schema_version row is the literal v1 seeded by bootstrap.
    """
    if not _MIGRATIONS_DIR.is_dir():
        return 1

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
        targets.append(target)

    if not targets:
        return 1
    return max(targets)


def _read_bootstrap_baseline_version() -> int:
    """Return the literal version stamped in bootstrap.sh Section 3b.

    Section 3b is the only place a literal `VALUES (N, ...)` exists in
    bootstrap.sh -- everything else is parameterised by Section 3c. We
    keep this check because the baseline MUST be 1; otherwise the loop
    in Section 3c would never have a starting point on a brand-new DB.
    """
    assert _BOOTSTRAP_PATH.exists(), (
        f"bootstrap_database.sh not found at {_BOOTSTRAP_PATH}"
    )
    text = _BOOTSTRAP_PATH.read_text()

    pattern = re.compile(
        r"INSERT\s+(?:OR\s+(?:IGNORE|REPLACE)\s+)?INTO\s+schema_version[^;]*?VALUES\s*\(\s*(\d+)\s*,",
        re.IGNORECASE | re.DOTALL,
    )
    literal_versions = [
        int(m.group(1)) for m in pattern.finditer(text)
        # Skip parameterised inserts (e.g. VALUES (${N}, ...)) which the
        # regex above does not match anyway, but be defensive about it.
    ]
    assert literal_versions, (
        f"No literal `INSERT ... INTO schema_version VALUES (N, ...)` found "
        f"in {_BOOTSTRAP_PATH}. The baseline v1 seed must remain literal."
    )
    return min(literal_versions)


def test_expected_version_matches_migrations_dir() -> None:
    """`EXPECTED_SCHEMA_VERSION` must equal the highest migration target.

    If this fails you either:
      - Bumped `EXPECTED_SCHEMA_VERSION` in doctor.py without dropping a
        new scripts/migrations/v{N-1}_to_v{N}.sql file -- every fresh
        install will abort with "missing migration file".
      - Added a migration file without bumping `EXPECTED_SCHEMA_VERSION`
        -- the loop in bootstrap.sh Section 3c will never request it.

    Fix: update both in the same commit (the doctor constant and the file).
    """
    expected = _read_doctor_expected_version()
    max_migration = _read_migrations_max_target()

    assert expected == max_migration, (
        f"Schema version / migration drift detected:\n"
        f"  bin/cli/doctor.py EXPECTED_SCHEMA_VERSION = {expected}\n"
        f"  scripts/migrations/ max target            = {max_migration}\n"
        f"These must be equal. Update both in the same commit when "
        f"introducing a new migration."
    )


def test_bootstrap_baseline_is_v1() -> None:
    """Bootstrap's literal seed MUST be (version=1, ...).

    The migration loop in Section 3c iterates from MAX(version)+1; if the
    baseline ever drifted away from 1 the loop would skip the first
    migration on every fresh install.
    """
    baseline = _read_bootstrap_baseline_version()
    assert baseline == 1, (
        f"bootstrap_database.sh Section 3b must seed version=1 as the "
        f"baseline; got {baseline}."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
