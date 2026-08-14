"""The caveats on the memory telemetry columns are reachable from the numbers.

Three decisions about these columns -- created_at is forward-only so NULL means
unknown age, injection_count carries a mixed pre-v50 prefix, deliberate_count
was zeroed and its capture dropped -- were correct, evidence-backed, and lived
only in migration-file prose. A consumer ranking rows on them saw four numeric
columns that looked equally trustworthy.

What this file pins is not that the caveats EXIST but that they are FOUND:

* ``test_every_access_axis_carries_a_caveat`` derives the axis columns from
  ``_MEMORY_TELEMETRY_COLUMNS`` rather than listing them, so a fourth axis
  fails here until someone decides what its caveat says. That is the guard for
  the consumer that is not written yet -- it fires at the birth of the column,
  not at the birth of the reader.
* ``test_caveated_column_exists_in_schema`` parses the ``memory`` CREATE TABLE,
  so a renamed or dropped column cannot leave a caveat pointing at nothing.
* The behavioural cases run the real CLI against a scratch database and read
  the caveat out of the process output, in every format the counters reach and
  parametrized over ``_MEMORY_LIST_ORDERS`` -- so a new sort key over these
  columns arrives with its own failing case rather than silently unqualified.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BIN_DIR = _REPO_ROOT / "bin"
for _path in (str(_REPO_ROOT), str(_BIN_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from gaia.store.writer import (  # noqa: E402
    MEMORY_TELEMETRY_CAVEATS,
    _MEMORY_LIST_ORDERS,
    _MEMORY_TELEMETRY_COLUMNS,
)

_GAIA = _BIN_DIR / "gaia"
_SCHEMA_SQL = _REPO_ROOT / "gaia" / "store" / "schema.sql"
WORKSPACE = "me"
SLUG = "t_caveat_probe"

#: Emitted once per surface as the line that introduces the family.
_BANNER = "memory telemetry caveats"

#: The age column is not an access axis, so ``_MEMORY_TELEMETRY_COLUMNS``
#: cannot derive it; it is named here because the decision that produced its
#: caveat (forward-only, no backfill) is what makes NULL mean unknown.
_AGE_COLUMN = "created_at"


# ---------------------------------------------------------------------------
# Coverage: the declaration cannot go stale on its own
# ---------------------------------------------------------------------------

def _memory_columns() -> set[str]:
    """Column names of the `memory` CREATE TABLE, read from schema.sql."""
    text = _SCHEMA_SQL.read_text(encoding="utf-8")
    match = re.search(
        r"CREATE TABLE IF NOT EXISTS memory \((.*?)\n\);", text, re.DOTALL
    )
    assert match is not None, "could not locate the memory CREATE TABLE"
    return {
        m.group(1)
        for m in re.finditer(
            r"^\s{4}(\w+)\s+(?:TEXT|INTEGER)\b", match.group(1), re.MULTILINE
        )
    }


@pytest.mark.parametrize("column", sorted(MEMORY_TELEMETRY_CAVEATS))
def test_caveated_column_exists_in_schema(column):
    assert column in _memory_columns(), (
        f"MEMORY_TELEMETRY_CAVEATS carries {column!r}, which is not a column "
        f"of the memory table -- a caveat pointing at nothing warns nobody."
    )


@pytest.mark.parametrize("kind", sorted(_MEMORY_TELEMETRY_COLUMNS))
def test_every_access_axis_carries_a_caveat(kind):
    count_column = _MEMORY_TELEMETRY_COLUMNS[kind][0]
    assert count_column in MEMORY_TELEMETRY_CAVEATS, (
        f"the {kind!r} axis writes {count_column} but declares no caveat. An "
        f"axis whose history, reset, or contamination is undeclared reads as "
        f"clean signal; add its entry to MEMORY_TELEMETRY_CAVEATS or state "
        f"there that it is clean."
    )


def test_the_age_column_carries_a_caveat():
    assert _AGE_COLUMN in MEMORY_TELEMETRY_CAVEATS


@pytest.mark.parametrize("column", sorted(MEMORY_TELEMETRY_CAVEATS))
def test_caveat_names_the_failure_mode_not_just_the_history(column):
    """A caveat that only dates the column tells a reader nothing to branch on."""
    caveat = MEMORY_TELEMETRY_CAVEATS[column]
    assert len(caveat) > 120, f"{column}: caveat too short to carry a reason"
    assert re.search(
        r"\b(NULL|suspect|zeroed|no pre-v50 history|not comparable)\b", caveat
    ), f"{column}: caveat does not name what goes wrong when read naively"


# ---------------------------------------------------------------------------
# Behaviour: the caveat comes back with the numbers
# ---------------------------------------------------------------------------

@pytest.fixture
def seeded(tmp_path_factory) -> dict:
    """A scratch substrate holding one row, built per test."""
    data_dir = tmp_path_factory.mktemp("gaia_data")
    saved = {k: os.environ.get(k) for k in ("GAIA_DATA_DIR", "GAIA_DB")}
    os.environ["GAIA_DATA_DIR"] = str(data_dir)
    os.environ.pop("GAIA_DB", None)
    try:
        from gaia.paths import db_path
        from gaia.store.writer import upsert_memory

        db = db_path()
        upsert_memory(
            WORKSPACE, SLUG, type="project",
            body="body seeded for the caveat probe",
            description="seeded for the caveat probe",
            db_path=db,
        )
        yield {"db": db, "data_dir": data_dir}
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _run(argv, seeded) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["GAIA_DATA_DIR"] = str(seeded["data_dir"])
    env.pop("GAIA_DB", None)
    return subprocess.run(
        [sys.executable, str(_GAIA), *argv],
        capture_output=True, text=True, env=env, timeout=120, check=False,
    )


def _assert_full_family(result, argv):
    assert result.returncode == 0, f"{argv}: rc={result.returncode}\n{result.stderr}"
    assert _BANNER in result.stderr, (
        f"{argv} displayed the telemetry columns without the caveat banner"
    )
    for column, caveat in MEMORY_TELEMETRY_CAVEATS.items():
        assert column in result.stderr, f"{argv}: no caveat for {column}"
        assert caveat in result.stderr, f"{argv}: caveat for {column} truncated"


@pytest.mark.parametrize(
    "argv",
    [
        ["memory", "show", SLUG, "--workspace", WORKSPACE],
        ["memory", "show", SLUG, "--workspace", WORKSPACE, "--json"],
        ["memory", "show", SLUG, "--workspace", WORKSPACE, "--links"],
        ["memory", "list", "--workspace", WORKSPACE],
        ["memory", "list", "--workspace", WORKSPACE, "--json"],
    ],
    ids=lambda a: "-".join(a[1:3] + [x for x in a if x.startswith("--json")
                                     or x.startswith("--links")]),
)
def test_surface_showing_the_counters_also_shows_the_caveats(argv, seeded):
    _assert_full_family(_run(argv, seeded), argv)


@pytest.mark.parametrize("sort_key", sorted(_MEMORY_LIST_ORDERS))
def test_every_ranking_key_returns_the_caveats(sort_key, seeded):
    """Ranking is the read these caveats exist for, so every key owes them."""
    argv = ["memory", "list", "--workspace", WORKSPACE, "--sort", sort_key]
    _assert_full_family(_run(argv, seeded), argv)


def test_json_payload_stays_parseable_with_the_caveats_on_stderr(seeded):
    """The warning must not be buyable at the cost of the stdout contract."""
    result = _run(
        ["memory", "list", "--workspace", WORKSPACE, "--json"], seeded
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, list) and payload[0]["name"] == SLUG
    assert _BANNER in result.stderr


def test_count_format_shows_no_number_to_qualify_and_no_caveats(seeded):
    """The caveats travel with the counters, not with every invocation."""
    result = _run(
        ["memory", "list", "--workspace", WORKSPACE, "--format", "count"],
        seeded,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"
    assert _BANNER not in result.stderr
