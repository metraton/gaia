#!/usr/bin/env python3
"""migration_guard.py -- consent gate for migration chains that reach existing data.

``bootstrap_database.py`` (the engine behind ``gaia migrate``) applies every
pending migration file between the database's ledger and the version the code
expects. That is harmless while migrations only add structure and unsafe the
moment one rewrites or removes rows: a commit in the source tree becomes a data
mutation on the live database with no human in between.

THE DISTINCTION DOES NOT DEPEND ON ANYONE DECLARING IT
    A convention an author can forget is a suggestion, not a gate. So the
    consent gate reads no header, marker, or filename. Two facts decide, and neither
    can be omitted by a distracted author:

      * what the migration's own SQL does -- a statement cannot rewrite rows
        without BEING a statement that rewrites rows, and it must say so in the
        only language the runner will execute;
      * how many rows the tables it names held BEFORE this run started, read
        from the target database itself.

    A migration reaches data only where those two meet: a row-reaching
    statement whose target table already held rows. Both inputs are evidence,
    not testimony.

ONE CONSENT COVERS ONE CHAIN (D68)
    A chain that only adds structure applies on its own, after a backup. A
    chain in which any migration reaches data asks once for the whole chain,
    and the consent names the chain (``v51..v58``) rather than a file, so it
    approves exactly the span the user was shown and nothing that ships later.

SILENCE FALLS ON THE SAFE SIDE
    A statement whose leading form this module does not recognise is
    UNRECOGNISED, never "assumed structural", and it is treated as reaching
    every row in the database.

A FRESH DATABASE IS NEVER GATED, BY CONSTRUCTION
    The census is taken once, before any schema or migration runs, so a
    database this run is creating has an empty census and every count is zero.
    There is nothing to disable, because a migration that reaches no existing
    row was never at risk of destroying one.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

CONSENT_FLAG = "--consent-chain"

UNKNOWN_TABLE = "?"

# A leading schema qualifier is consumed rather than captured, so `main.memory`
# resolves to the same census key as `memory`. Capturing the qualifier instead
# would look up a table name that no census can hold and read as zero rows.
_IDENT = r"(?:[\"'`\[]?\w+[\"'`\]]?\s*\.\s*)?[\"'`\[]?(\w+)[\"'`\]]?"

# Matched in order, before the structural forms, so `INSERT OR REPLACE` is
# classified by its overwrite and never by its leading INSERT.
_ROW_REACHING = (
    ("INSERT OR REPLACE", re.compile(rf"^insert\s+or\s+replace\s+into\s+{_IDENT}", re.I)),
    ("REPLACE INTO", re.compile(rf"^replace\s+into\s+{_IDENT}", re.I)),
    ("UPDATE", re.compile(rf"^update\s+(?:or\s+\w+\s+)?{_IDENT}", re.I)),
    ("DELETE", re.compile(rf"^delete\s+from\s+{_IDENT}", re.I)),
    ("DROP TABLE", re.compile(rf"^drop\s+table\s+(?:if\s+exists\s+)?{_IDENT}", re.I)),
    ("ALTER TABLE DROP", re.compile(rf"^alter\s+table\s+{_IDENT}\s+drop\b", re.I)),
    ("ALTER TABLE RENAME", re.compile(rf"^alter\s+table\s+{_IDENT}\s+rename\b", re.I)),
)

# A form here changes the shape of the database and no row of it. DROP is
# enumerated by object type rather than allowed wholesale: an index, a trigger
# and a view carry no rows of their own, while DROP TABLE discards every row of
# one and is matched above. A plain INSERT belongs here for the same reason --
# it can only add rows, never overwrite one that already exists. CREATE covers
# CREATE TRIGGER whole: its body runs on future writes, never on rows that
# exist when the migration applies.
_STRUCTURAL = (
    re.compile(r"^create\b", re.I),
    re.compile(r"^drop\s+(?:index|trigger|view)\b", re.I),
    re.compile(r"^alter\s+table\s+\S+\s+add\b", re.I),
    re.compile(r"^insert\b", re.I),
    re.compile(r"^select\b", re.I),
    re.compile(r"^pragma\b", re.I),
    re.compile(r"^(?:vacuum|analyze|reindex)\b", re.I),
)


@dataclass(frozen=True)
class Reach:
    """One statement that can reach rows, and how many it would find."""

    verb: str
    table: str
    rows: int
    excerpt: str


@dataclass(frozen=True)
class Verdict:
    """What one migration of a chain would reach in the existing data."""

    migration: str
    reaches: tuple[Reach, ...]


def take_census(con) -> dict[str, int]:
    """Row count per table, as it stands right now.

    Called once before anything in the run writes, so what it reports is
    exactly the data that pre-dates this run -- the only data a migration can
    destroy. A table created later in the same run is absent here and
    therefore counts as zero.
    """
    census: dict[str, int] = {}
    try:
        names = [
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
    except Exception:  # noqa: BLE001 -- a database with no readable catalog is a fresh one
        return census
    for name in names:
        try:
            census[name] = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        except Exception:  # noqa: BLE001 -- an unreadable shadow table holds nothing this gate owns
            continue
    return census


def strip_comments(sql: str) -> str:
    """Remove SQL comments while leaving string literals intact.

    Not cosmetic: migration headers in this repo discuss the statements below
    them in prose, so an unstripped file offers `UPDATE`, `DROP TABLE` and
    stray semicolons that never execute. Classifying those would gate on
    documentation.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out.append(sql[i : j + 1])
            i = j + 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j == -1 else j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j == -1 else j + 2
            out.append(" ")
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def split_statements(sql: str) -> list[str]:
    """Split a script into the statements SQLite itself would see.

    Boundaries come from ``sqlite3.complete_statement`` -- SQLite's own
    tokenizer -- so a semicolon inside a string, a comment, or a trigger body
    never ends a statement. The runner executes these one at a time inside a
    single transaction, and the gate classifies the same list, so what is
    judged is exactly what runs. Fragments holding only comments are dropped.
    """
    statements: list[str] = []
    start = 0
    for i, ch in enumerate(sql):
        if ch == ";" and sqlite3.complete_statement(sql[start : i + 1]):
            statements.append(sql[start : i + 1])
            start = i + 1
    statements.append(sql[start:])
    return [s.strip() for s in statements if strip_comments(s).strip()]


def normalize(statement: str) -> str:
    """One statement without comments, on one line."""
    return " ".join(strip_comments(statement).split())


def scan(sql: str, census: dict[str, int]) -> tuple[Reach, ...]:
    """Statements that reach rows that already exist, with the count each finds."""
    total = sum(census.values())
    found: list[Reach] = []
    for statement in split_statements(sql):
        fragment = normalize(statement)
        verb, table = _classify(fragment)
        if verb is None:
            continue
        rows = total if table == UNKNOWN_TABLE else census.get(table, 0)
        if rows > 0:
            found.append(Reach(verb, table, rows, _excerpt(fragment)))
    return tuple(found)


def _classify(fragment: str) -> tuple[str | None, str]:
    for verb, pattern in _ROW_REACHING:
        match = pattern.match(fragment)
        if match:
            return verb, match.group(1)
    for pattern in _STRUCTURAL:
        if pattern.match(fragment):
            return None, ""
    return "UNRECOGNISED", UNKNOWN_TABLE


def _excerpt(fragment: str, limit: int = 90) -> str:
    return fragment if len(fragment) <= limit else fragment[: limit - 3] + "..."


def assess(migration: str, sql: str, census: dict[str, int]) -> Verdict:
    return Verdict(migration, scan(sql, census))


# Compatibility is the one thing the SQL cannot say about itself: whether code
# written before this migration can still write afterwards. So, unlike the
# consent gate above, it is declared -- one header line per file -- and a test
# refuses a file without it, or one declared backward that rewrites rows.
COMPAT_BACKWARD, COMPAT_BREAKING = "backward", "breaking"
_COMPAT_RE = re.compile(r"^--\s*gaia-compat:\s*(\S+)\s*$", re.MULTILINE)


def compat_mark(sql: str) -> str | None:
    """The file's single valid ``-- gaia-compat:`` value, or None when absent,
    repeated, or not one of backward/breaking."""
    marks = _COMPAT_RE.findall(sql)
    if len(marks) != 1 or marks[0] not in (COMPAT_BACKWARD, COMPAT_BREAKING):
        return None
    return marks[0]


def destructive_statements(sql: str) -> list[str]:
    """Statements that rewrite, remove or rename rows, whatever the table holds."""
    return [
        normalize(statement)
        for statement in split_statements(sql)
        if _classify(normalize(statement))[0] is not None
    ]


def is_breaking(sql: str) -> bool:
    """Whether code older than this migration must stop writing after it.

    An unmarked file counts as breaking, so a forgotten mark can only refuse an
    older installation, never let it write to a structure it does not know.
    """
    return compat_mark(sql) != COMPAT_BACKWARD


def last_breaking_version(migrations_dir: Path, version: int) -> int | None:
    """The highest target version <= ``version`` whose migration is breaking."""
    targets = []
    for path in migrations_dir.glob("v*_to_v*.sql"):
        target = int(path.stem.rsplit("_to_v", 1)[1])
        if target <= version and is_breaking(path.read_text(encoding="utf-8")):
            targets.append(target)
    return max(targets, default=None)


def chain_label(current: int, expected: int) -> str:
    """The name a consent must carry: the exact span of versions it approves."""
    return f"v{current}..v{expected}"


def consent_command(label: str) -> str:
    return f"gaia migrate apply {CONSENT_FLAG} {label}"


def format_chain_block(
    verdicts: list[Verdict], label: str, db_path: Path, ledger_at: int
) -> str:
    """The refusal a user reads when a chain reaches data and was not consented."""
    lines = [
        f"BLOCKED: migration chain {label} reaches data that already exists.",
        "",
        f"  database: {db_path}",
        "",
        "  A chain that only adds structure applies on its own after a backup.",
        "  This one does not, because these statements would reach rows present",
        "  before this run:",
        "",
    ]
    for verdict in verdicts:
        for reach in verdict.reaches:
            where = (
                "anywhere in the database"
                if reach.table == UNKNOWN_TABLE
                else f"`{reach.table}`"
            )
            lines.append(
                f"    {verdict.migration}: {reach.verb} on {where} -- "
                f"{reach.rows} row(s) at risk"
            )
            lines.append(f"      {reach.excerpt}")
    lines += [
        "",
        "  NOTHING WAS APPLIED. No transaction was opened and the schema_version",
        f"  ledger stays at v{ledger_at}.",
        "",
        "  Review the chain with `gaia migrate plan`, then consent to it once:",
        "",
        f"      {consent_command(label)}",
        "",
        "  The consent names this chain only: a backup is taken first, and it",
        "  approves nothing that ships later.",
    ]
    return "\n".join(lines)
