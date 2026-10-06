"""Every shipped migration declares whether older code can still write after it.

The minimum code version sealed in `schema_version.min_code_version` is derived
from these marks, so a missing mark, or a file that rewrites rows while claiming
to be backward, would let an older installation write to a structure it does
not know.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_MIGRATIONS = _REPO / "scripts" / "migrations"
sys.path.insert(0, str(_REPO / "scripts"))

import migration_guard  # noqa: E402

_FILES = sorted(_MIGRATIONS.glob("v*_to_v*.sql"))


def header_violations(sql: str) -> list[str]:
    """What is wrong with one migration's compatibility declaration."""
    mark = migration_guard.compat_mark(sql)
    if mark is None:
        return ["missing or invalid `-- gaia-compat: backward|breaking` line"]
    if mark == migration_guard.COMPAT_BACKWARD:
        return [
            f"declared backward but rewrites rows: {statement}"
            for statement in migration_guard.destructive_statements(sql)
        ]
    return []


def test_the_migrations_directory_is_not_empty():
    assert _FILES


@pytest.mark.parametrize("path", _FILES, ids=[p.name for p in _FILES])
def test_each_shipped_migration_declares_an_honest_mark(path):
    assert header_violations(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE IF NOT EXISTS t (id INTEGER);\n",
        "-- gaia-compat: sideways\nCREATE TABLE IF NOT EXISTS t (id INTEGER);\n",
        "-- gaia-compat: backward\n-- gaia-compat: breaking\nCREATE INDEX i ON t(id);\n",
    ],
    ids=["absent", "unknown-value", "repeated"],
)
def test_a_file_without_one_valid_mark_fails(sql):
    assert header_violations(sql)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE memory SET body = '';",
        "DELETE FROM memory;",
        "DROP TABLE memory;",
        "ALTER TABLE memory RENAME TO memory_old;",
    ],
)
def test_a_destructive_file_declared_backward_fails(statement):
    sql = f"-- gaia-compat: backward\n{statement}\n"

    assert header_violations(sql)
    assert header_violations(sql.replace("backward", "breaking")) == []


def test_an_unmarked_file_counts_as_breaking():
    assert migration_guard.is_breaking("CREATE TABLE IF NOT EXISTS t (id INTEGER);")
    assert not migration_guard.is_breaking("-- gaia-compat: backward\nCREATE INDEX i ON t(id);")
