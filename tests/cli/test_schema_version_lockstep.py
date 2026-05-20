"""Lockstep test: `EXPECTED_SCHEMA_VERSION` in doctor.py must equal the
maximum `INSERT INTO schema_version` version seeded by bootstrap_database.sh.

Why this exists
---------------
`gaia doctor` warns when the live DB schema_version drifts from the CLI's
baked-in expectation. The bootstrap script seeds the initial row. If a
migration bumps one but not the other, every fresh install warns about
phantom drift -- or worse, real drift goes unnoticed because the doctor
already thinks the DB is "old".

This test is the only mechanical guarantee that the two stay in sync.
Adding a new migration row to bootstrap_database.sh without bumping
`EXPECTED_SCHEMA_VERSION` (or vice versa) fails here.
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


def _read_bootstrap_max_version() -> int:
    """Parse the maximum version seeded by `INSERT ... INTO schema_version`.

    Matches both single-line and multi-line forms:
        INSERT OR IGNORE INTO schema_version (version, applied_at, description)
        VALUES (1, '...', 'initial schema');

    Scans the file for every `INSERT ... INTO schema_version` statement,
    extracts the integer immediately following the first `VALUES (`, and
    returns the maximum. Empty result is an error -- the bootstrap MUST
    seed at least one version row.
    """
    assert _BOOTSTRAP_PATH.exists(), (
        f"bootstrap_database.sh not found at {_BOOTSTRAP_PATH}"
    )
    text = _BOOTSTRAP_PATH.read_text()

    # Find every INSERT into schema_version, capture the version number
    # from the first VALUES(...) tuple that follows.
    versions: list[int] = []
    pattern = re.compile(
        r"INSERT\s+(?:OR\s+(?:IGNORE|REPLACE)\s+)?INTO\s+schema_version[^;]*?VALUES\s*\(\s*(\d+)\s*,",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(text):
        versions.append(int(match.group(1)))

    assert versions, (
        f"No `INSERT ... INTO schema_version VALUES (N, ...)` statements found "
        f"in {_BOOTSTRAP_PATH}. The bootstrap must seed at least version 1."
    )
    return max(versions)


def test_schema_version_lockstep_between_doctor_and_bootstrap() -> None:
    """The DB version seeded at bootstrap must match the CLI's expectation.

    If this fails, you either:
      - Bumped `EXPECTED_SCHEMA_VERSION` in doctor.py without adding a
        corresponding `INSERT OR IGNORE INTO schema_version` row in
        bootstrap_database.sh (fresh installs will warn forever), or
      - Added a new schema_version row in bootstrap_database.sh without
        bumping `EXPECTED_SCHEMA_VERSION` (doctor will silently treat
        the new schema as "ahead of expected").

    Fix: update both numbers in the same commit.
    """
    expected = _read_doctor_expected_version()
    bootstrap_max = _read_bootstrap_max_version()

    assert expected == bootstrap_max, (
        f"Schema version drift detected:\n"
        f"  bin/cli/doctor.py EXPECTED_SCHEMA_VERSION = {expected}\n"
        f"  scripts/bootstrap_database.sh max schema_version row = {bootstrap_max}\n"
        f"These must be equal. Update both in the same commit when adding a "
        f"new migration."
    )


def test_bootstrap_seeds_at_least_one_version() -> None:
    """Sanity: bootstrap must always seed at least version 1.

    A bootstrap with zero `INSERT INTO schema_version` rows would create
    a DB whose `MAX(version)` is NULL/0, making doctor's drift detection
    fire on every fresh install.
    """
    bootstrap_max = _read_bootstrap_max_version()
    assert bootstrap_max >= 1, (
        f"bootstrap_database.sh must seed at least version 1; got "
        f"max={bootstrap_max}."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
