"""Fixture precondition audit: test-DB schemas must not drift from production.

WHY THIS GUARD EXISTS
=====================
Test fixtures that hand-roll a CREATE TABLE for a production table (instead of
replaying ``gaia/store/schema.sql``) are a latent failure mode: when production
adds a column, the fixture silently keeps the old shape. A test built on that
fixture stays green while exercising a schema that can no longer exist in
production -- a false precondition. The drift only surfaces later as an
``OperationalError`` (e.g. ``no such column``) the day a code path touches the
missing column, far from the fixture that caused it.

This module is the reusable guard against that class of drift. It compares the
column shape (name, type, NOT NULL, DEFAULT) of a table as declared by a test
fixture against the same table in the production schema, and FAILS when the
fixture is missing a column production has. It is **general**: adding a new
``FixtureUnderAudit`` entry audits any (fixture file, table) pair -- it is not
hard-coded to a single case.

HOW IT WORKS
============
1. The production column spec is read by materializing ``gaia/store/schema.sql``
   into an in-memory SQLite DB and running ``PRAGMA table_info``.
2. Each fixture's column spec is read by materializing ONLY that fixture's
   ``CREATE TABLE <table>`` statement (extracted from its source file -- this
   handles both standalone .sql and SQL embedded inside a Python string) into a
   separate in-memory DB and running the same introspection.
3. The two specs are diffed. A column present in production but absent from the
   fixture is a hard failure (the OperationalError-in-waiting). Type / NOT NULL
   / DEFAULT divergences and extra fixture columns are reported too.

INTENTIONAL DIVERGENCES (the allowlist)
=======================================
Some fixtures are deliberately partial -- e.g. a historical snapshot that must
NOT track production forward. Those are declared per-column in
``INTENTIONAL_DIVERGENCES`` with a documented reason, and excluded from the
failure set. An empty allowlist for a fixture means "must match production
exactly (modulo extra columns)". Keep the allowlist minimal and justified:
every entry is a place a future schema change will not be caught.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_SCHEMA = PACKAGE_ROOT / "gaia" / "store" / "schema.sql"


# ---------------------------------------------------------------------------
# Column spec + extraction
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ColumnSpec:
    """A single column's shape as reported by ``PRAGMA table_info``."""

    name: str
    type: str
    notnull: int      # 1 = NOT NULL, 0 = nullable
    default: str | None

    def shape_differs_from(self, other: "ColumnSpec") -> list[str]:
        """Return human-readable reasons this column's shape differs from ``other``."""
        reasons: list[str] = []
        if self.type.upper() != other.type.upper():
            reasons.append(f"type {other.type!r} (prod) vs {self.type!r} (fixture)")
        if self.notnull != other.notnull:
            reasons.append(
                f"NOT NULL={other.notnull} (prod) vs {self.notnull} (fixture)"
            )
        if (self.default or None) != (other.default or None):
            reasons.append(
                f"DEFAULT {other.default!r} (prod) vs {self.default!r} (fixture)"
            )
        return reasons


def _register_gaia_sha256(con: sqlite3.Connection) -> None:
    """Register the ``gaia_sha256`` scalar the production schema's triggers need."""
    con.create_function(
        "gaia_sha256", 1,
        lambda v: hashlib.sha256((v or "").encode()).hexdigest(),
        deterministic=True,
    )


def _table_info(con: sqlite3.Connection, table: str) -> dict[str, ColumnSpec]:
    """Introspect ``table`` on ``con`` into a {column_name: ColumnSpec} map."""
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return {
        row[1]: ColumnSpec(name=row[1], type=row[2], notnull=row[3], default=row[4])
        for row in rows
    }


def _production_columns(table: str) -> dict[str, ColumnSpec]:
    """Materialize the production schema in-memory and introspect ``table``."""
    con = sqlite3.connect(":memory:")
    try:
        _register_gaia_sha256(con)
        con.executescript(PRODUCTION_SCHEMA.read_text())
        cols = _table_info(con, table)
        assert cols, (
            f"Production schema {PRODUCTION_SCHEMA} declares no table {table!r}; "
            "the audit target is misconfigured."
        )
        return cols
    finally:
        con.close()


def _extract_create_table(source: str, table: str) -> str:
    """Extract the ``CREATE TABLE ... <table> ( ... );`` statement from ``source``.

    Works whether the DDL is a standalone .sql file or embedded inside a Python
    string literal (the relay fixture's case): it scans the raw text for the
    CREATE TABLE header and balances parentheses to find the matching close.
    """
    pattern = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?" + re.escape(table) + r"\s*\(",
        re.IGNORECASE,
    )
    match = pattern.search(source)
    if match is None:
        raise AssertionError(
            f"Could not find a CREATE TABLE for {table!r} in the fixture source."
        )
    open_paren = match.end() - 1
    depth = 0
    for i in range(open_paren, len(source)):
        ch = source[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return source[match.start():i + 1] + ";"
    raise AssertionError(
        f"Unbalanced parentheses while extracting CREATE TABLE for {table!r}."
    )


def _fixture_columns(source_file: Path, table: str) -> dict[str, ColumnSpec]:
    """Extract the fixture's ``CREATE TABLE`` for ``table`` and introspect it."""
    ddl = _extract_create_table(source_file.read_text(), table)
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(ddl)
        return _table_info(con, table)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Audit registry: (fixture file, table) pairs to audit + intentional divergences
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FixtureUnderAudit:
    """One auditable (fixture source, table) pair.

    Attributes:
        label: human name shown in test ids / failures.
        source_file: file containing the fixture's CREATE TABLE (sql or .py).
        table: the production table the fixture re-declares.
        allowed_missing: columns the fixture is allowed to omit (each value is
            the documented reason it is intentional). An empty set means the
            fixture must carry every production column.
    """

    label: str
    source_file: Path
    table: str
    allowed_missing: dict[str, str] = field(default_factory=dict)


# The relay test's inline approval_grants schema and bootstrap_m4_schema are the
# two known re-declarers of approval_grants. Add new entries here to audit more
# fixtures; the audit logic below is table-agnostic.
FIXTURES_UNDER_AUDIT: list[FixtureUnderAudit] = [
    FixtureUnderAudit(
        label="relay_e2e_inline_approval_grants",
        source_file=(
            PACKAGE_ROOT / "tests" / "integration"
            / "test_nonce_approval_relay_e2e.py"
        ),
        table="approval_grants",
        allowed_missing={},  # must match production exactly
    ),
    FixtureUnderAudit(
        label="bootstrap_m4_schema_approval_grants",
        source_file=PACKAGE_ROOT / "tests" / "fixtures" / "db_helpers.py",
        table="approval_grants",
        allowed_missing={},  # must match production exactly
    ),
]


# ---------------------------------------------------------------------------
# The audit
# ---------------------------------------------------------------------------

class TestFixturePreconditionAudit:
    """Each audited fixture must declare a schema consistent with production."""

    @pytest.mark.parametrize(
        "fixture",
        FIXTURES_UNDER_AUDIT,
        ids=[f.label for f in FIXTURES_UNDER_AUDIT],
    )
    def test_fixture_schema_matches_production(self, fixture: FixtureUnderAudit):
        """Fixture must carry every production column (minus the allowlist).

        Missing-column drift is a hard failure: it is the OperationalError this
        guard exists to catch before it reaches a code path. Type / NOT NULL /
        DEFAULT divergences on shared columns are also failures, because they
        let a fixture accept rows production would reject (or vice versa).
        """
        assert fixture.source_file.exists(), (
            f"Audited fixture source not found: {fixture.source_file}"
        )

        prod = _production_columns(fixture.table)
        fixt = _fixture_columns(fixture.source_file, fixture.table)

        problems: list[str] = []

        # 1) Columns production has that the fixture omits -> the core drift.
        for col_name, prod_col in prod.items():
            if col_name in fixt:
                continue
            if col_name in fixture.allowed_missing:
                continue  # intentional, documented divergence
            problems.append(
                f"MISSING column {col_name!r} ({prod_col.type}"
                + (" NOT NULL" if prod_col.notnull else "")
                + (f" DEFAULT {prod_col.default}" if prod_col.default else "")
                + ") -- present in production, absent from fixture"
            )

        # 2) Shape drift on columns both declare.
        for col_name, prod_col in prod.items():
            if col_name not in fixt:
                continue
            for reason in fixt[col_name].shape_differs_from(prod_col):
                problems.append(f"COLUMN {col_name!r} shape drift: {reason}")

        # 3) Allowlist hygiene: an allowed_missing entry that is no longer
        #    missing (production dropped it, or the fixture caught up) is stale.
        for col_name in fixture.allowed_missing:
            if col_name not in prod:
                problems.append(
                    f"STALE allowlist entry {col_name!r}: no longer in production"
                )
            elif col_name in fixt:
                problems.append(
                    f"STALE allowlist entry {col_name!r}: fixture now declares it"
                )

        assert not problems, (
            f"Fixture {fixture.label!r} (table {fixture.table!r}) has drifted from "
            f"{PRODUCTION_SCHEMA.name}:\n  - " + "\n  - ".join(problems)
        )
