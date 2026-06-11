#!/usr/bin/env python3
"""Build/pre-publish schema-drift guard.

Fails (non-zero) when gaia/store/schema.sql has changed but the schema version
was not bumped and no new migration was added for it. This catches at build
time the drift that `gaia doctor` only flags at runtime as a warning.

How it works
------------
A committed fingerprint file (scripts/migrations/schema.checksum) pins the
sha256 of schema.sql to the EXPECTED_SCHEMA_VERSION it corresponds to:

    version=18
    sha256=<hex digest of gaia/store/schema.sql>

The guard reads EXPECTED_SCHEMA_VERSION from bin/cli/doctor.py and the live
sha256 of schema.sql, then:

  1. No fingerprint file yet (first run): record the current
     (version, sha256) and pass. This is how the baseline for the current
     floor (v18) is established.

  2. Recorded version == EXPECTED but sha256 differs: schema.sql changed
     without a version bump. FAIL with a message telling the dev to bump
     EXPECTED_SCHEMA_VERSION in doctor.py and add a migration file.

  3. Recorded version < EXPECTED (a bump happened): verify the corresponding
     forward migration file scripts/migrations/v{EXPECTED-1}_to_v{EXPECTED}.sql
     exists, then re-record the new (version, sha256). FAIL if the migration
     file is missing.

  4. Recorded version == EXPECTED and sha256 matches: no drift, pass.

  5. Recorded version > EXPECTED: the fingerprint is ahead of the CLI -- this
     is a misconfiguration (someone lowered EXPECTED). FAIL.

Usage:
    python3 scripts/check_schema_drift.py            # check (+ record on first run / after bump)
    python3 scripts/check_schema_drift.py --record   # force re-record for current EXPECTED (rare)

Exit codes:
    0  no drift (or baseline/bump recorded)
    1  drift detected, or missing migration after a bump, or misconfiguration
    2  internal error (file not found, parse failure)
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_SQL = _REPO_ROOT / "gaia" / "store" / "schema.sql"
_DOCTOR_PY = _REPO_ROOT / "bin" / "cli" / "doctor.py"
_MIGRATIONS_DIR = _REPO_ROOT / "scripts" / "migrations"
_CHECKSUM_FILE = _MIGRATIONS_DIR / "schema.checksum"


def _fail(msg: str) -> None:
    print(f"[schema-drift] FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def _err(msg: str) -> None:
    print(f"[schema-drift] ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def _read_expected_version() -> int:
    if not _DOCTOR_PY.is_file():
        _err(f"doctor.py not found at {_DOCTOR_PY}")
    text = _DOCTOR_PY.read_text()
    m = re.search(r"^EXPECTED_SCHEMA_VERSION\s*=\s*(\d+)\s*$", text, re.MULTILINE)
    if m is None:
        _err(f"could not parse EXPECTED_SCHEMA_VERSION from {_DOCTOR_PY}")
    return int(m.group(1))


def _schema_sha256() -> str:
    if not _SCHEMA_SQL.is_file():
        _err(f"schema.sql not found at {_SCHEMA_SQL}")
    return hashlib.sha256(_SCHEMA_SQL.read_bytes()).hexdigest()


def _read_checksum_file() -> tuple[int, str] | None:
    if not _CHECKSUM_FILE.is_file():
        return None
    version: int | None = None
    sha: str | None = None
    for raw in _CHECKSUM_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if key == "version":
            try:
                version = int(val)
            except ValueError:
                _err(f"malformed version in {_CHECKSUM_FILE}: {val!r}")
        elif key == "sha256":
            sha = val
    if version is None or sha is None:
        _err(f"{_CHECKSUM_FILE} is missing version= or sha256=")
    return version, sha


def _write_checksum_file(version: int, sha: str) -> None:
    content = (
        "# Schema fingerprint for the build/pre-publish drift guard.\n"
        "# Generated and verified by scripts/check_schema_drift.py.\n"
        "# Pins the sha256 of gaia/store/schema.sql to the schema version it\n"
        "# corresponds to (EXPECTED_SCHEMA_VERSION in bin/cli/doctor.py).\n"
        "# Do NOT edit by hand: bump EXPECTED_SCHEMA_VERSION + add a migration,\n"
        "# then re-run the guard to refresh this file.\n"
        f"version={version}\n"
        f"sha256={sha}\n"
    )
    _CHECKSUM_FILE.write_text(content)


def _migration_for(version: int) -> Path:
    return _MIGRATIONS_DIR / f"v{version - 1}_to_v{version}.sql"


def main() -> None:
    force_record = "--record" in sys.argv[1:]

    expected = _read_expected_version()
    live_sha = _schema_sha256()
    recorded = _read_checksum_file()

    if force_record:
        _write_checksum_file(expected, live_sha)
        print(
            f"[schema-drift] recorded fingerprint for v{expected} "
            f"(sha256={live_sha[:12]}...) [--record]"
        )
        sys.exit(0)

    # Case 1: first run -- establish the baseline for the current version.
    if recorded is None:
        _write_checksum_file(expected, live_sha)
        print(
            f"[schema-drift] no fingerprint on record; baseline recorded for "
            f"v{expected} (sha256={live_sha[:12]}...)"
        )
        sys.exit(0)

    rec_version, rec_sha = recorded

    # Case 5: recorded ahead of the CLI -- misconfiguration.
    if rec_version > expected:
        _fail(
            f"recorded schema fingerprint is for v{rec_version} but "
            f"EXPECTED_SCHEMA_VERSION is v{expected} (lower). The CLI cannot "
            f"expect a version older than the recorded fingerprint. Restore "
            f"EXPECTED_SCHEMA_VERSION to >= v{rec_version}."
        )

    # Case 3: a version bump happened -- require the migration, then re-record.
    if rec_version < expected:
        mig = _migration_for(expected)
        if not mig.is_file():
            _fail(
                f"EXPECTED_SCHEMA_VERSION was bumped to v{expected} "
                f"(fingerprint was for v{rec_version}), but the forward "
                f"migration {mig.relative_to(_REPO_ROOT)} is missing. Add the "
                f"migration file in the same commit as the version bump."
            )
        _write_checksum_file(expected, live_sha)
        print(
            f"[schema-drift] version bump v{rec_version} -> v{expected} "
            f"detected; migration {mig.name} present; fingerprint re-recorded "
            f"(sha256={live_sha[:12]}...)"
        )
        sys.exit(0)

    # rec_version == expected from here on.

    # Case 4: fingerprint matches -- no drift.
    if rec_sha == live_sha:
        print(
            f"[schema-drift] OK: schema.sql matches the recorded fingerprint "
            f"for v{expected}"
        )
        sys.exit(0)

    # Case 2: schema.sql changed without a version bump -- DRIFT.
    _fail(
        f"gaia/store/schema.sql changed but EXPECTED_SCHEMA_VERSION is still "
        f"v{expected} and no new migration was added.\n"
        f"           recorded sha256: {rec_sha}\n"
        f"           current  sha256: {live_sha}\n"
        f"           To fix: bump EXPECTED_SCHEMA_VERSION in bin/cli/doctor.py "
        f"to v{expected + 1}, add scripts/migrations/v{expected}_to_v{expected + 1}.sql, "
        f"then re-run this guard to refresh scripts/migrations/schema.checksum.\n"
        f"           (If the schema.sql edit is genuinely a no-op change that "
        f"needs no migration, re-run with --record to re-pin the fingerprint.)"
    )


if __name__ == "__main__":
    main()
