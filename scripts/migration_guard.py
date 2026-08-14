#!/usr/bin/env python3
"""migration_guard.py -- consent gate for migrations that reach existing data.

Section 3c of ``bootstrap_database.py`` applies every pending migration file it
finds in the source tree, unattended, on any invocation that bootstraps. That is
harmless while migrations only add structure and unsafe the moment one rewrites
or removes rows: a commit in the source tree becomes a data mutation on the live
database with no human in between.

THE DISTINCTION DOES NOT DEPEND ON ANYONE DECLARING IT
    A convention an author can forget is a suggestion, not a gate. So nothing
    here reads a header, a marker, or a filename. Two facts decide, and neither
    can be omitted by a distracted author:

      * what the migration's own SQL does -- a statement cannot rewrite rows
        without BEING a statement that rewrites rows, and it must say so in the
        only language the runner will execute;
      * how many rows the tables it names held BEFORE this bootstrap run
        started, read from the target database itself.

    A migration is blocked only where those two meet: a row-reaching statement
    whose target table already held rows. Both inputs are evidence, not
    testimony.

SILENCE FALLS ON THE SAFE SIDE, IN BOTH ITS FORMS
    The author's silence is not a case at all: saying nothing yields the same
    classification as saying anything, because only the SQL is read. The
    PARSER's silence is the real edge, and it is gated: a statement whose
    leading form this module does not recognise is UNRECOGNISED, never
    "assumed structural", and it is treated as reaching every row in the
    database.

A FRESH DATABASE IS NEVER GATED, BY CONSTRUCTION
    The census is taken once, before any schema or migration runs, so a
    database this run is creating has an empty census and every count is zero.
    The whole chain -- including a data-reaching migration -- applies
    unattended on a fresh install or a throwaway test database. That is not an
    exemption branch, an installer flag, or an environment variable someone has
    to remember: there is nothing to disable, because a migration that reaches
    no existing row was never at risk of destroying one.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

ENV_CONSENT = "GAIA_MIGRATION_CONSENT"

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
# it can only add rows, never overwrite one that already exists.
_STRUCTURAL = (
    re.compile(r"^create\b", re.I),
    re.compile(r"^drop\s+(?:index|trigger|view)\b", re.I),
    re.compile(r"^alter\s+table\s+\S+\s+add\b", re.I),
    re.compile(r"^insert\b", re.I),
    re.compile(r"^select\b", re.I),
    re.compile(r"^pragma\b", re.I),
    re.compile(r"^(?:commit|end|rollback|savepoint|release)\b", re.I),
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
    """What one migration would reach, and whether it may proceed unattended."""

    migration: str
    reaches: tuple[Reach, ...]
    consented: bool

    @property
    def blocked(self) -> bool:
        return bool(self.reaches) and not self.consented


def take_census(con) -> dict[str, int]:
    """Row count per table, as it stands right now.

    Called once before schema.sql and before the first migration, so what it
    reports is exactly the data that pre-dates this run -- the only data a
    migration can destroy. A table created later in the same run is absent
    here and therefore counts as zero, which is correct: rows this run
    produced were not at risk from it.
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


_SPLIT = re.compile(r";|\bbegin\b", re.I)


def _statements(sql: str) -> list[str]:
    """Split into classifiable fragments on the semicolon and on BEGIN.

    A trigger body is deliberately NOT held together: splitting it turns each
    statement the trigger would run into its own fragment, which is what lets a
    body that deletes rows be seen at all. BEGIN has to separate too, because a
    trigger's FIRST body statement carries no semicolon before it and would
    otherwise hide inside the structural `CREATE TRIGGER` fragment. The header
    still classifies structural on its own, so an FTS mirror trigger -- whose
    body only inserts -- is not gated.
    """
    fragments = []
    for raw in _SPLIT.split(strip_comments(sql)):
        collapsed = " ".join(raw.split())
        if collapsed:
            fragments.append(collapsed)
    return fragments


def scan(sql: str, census: dict[str, int]) -> tuple[Reach, ...]:
    """Statements that reach rows that already exist, with the count each finds."""
    total = sum(census.values())
    found: list[Reach] = []
    for fragment in _statements(sql):
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


def consented(migration: str, environ=None) -> bool:
    """Whether this exact migration was named in the consent variable.

    Only exact stems are honoured, and there is no wildcard: consent to
    `v49_to_v50` approves that file and nothing that ships after it.
    """
    raw = (os.environ if environ is None else environ).get(ENV_CONSENT, "")
    return migration in {part.strip() for part in raw.split(",") if part.strip()}


def assess(
    migration: str,
    sql: str,
    census: dict[str, int],
    environ=None,
) -> Verdict:
    return Verdict(migration, scan(sql, census), consented(migration, environ))


def format_block(verdict: Verdict, mig_file: Path, db_path: Path, ledger_at: int) -> str:
    lines = [
        f"BLOCKED: migration {verdict.migration} reaches data that already exists.",
        "",
        f"  file:     {mig_file}",
        f"  database: {db_path}",
        "",
        "  A migration that only adds structure applies unattended. This one does",
        "  not, because these statements would reach rows present before this run:",
        "",
    ]
    for reach in verdict.reaches:
        where = "anywhere in the database" if reach.table == UNKNOWN_TABLE else f"`{reach.table}`"
        lines.append(f"    {reach.verb} on {where} -- {reach.rows} row(s) at risk")
        lines.append(f"      {reach.excerpt}")
    lines += [
        "",
        "  NOTHING WAS APPLIED. No transaction was opened for this migration and",
        f"  the schema_version ledger stays at v{ledger_at}.",
        "",
        "  To continue deliberately, re-run the SAME command with this migration",
        "  named in the consent variable:",
        "",
        f"      {ENV_CONSENT}={verdict.migration} <the command you just ran>",
        "",
        "  Naming the version IS the consent: it approves this migration only and",
        "  approves nothing that ships later. Read the file first -- the statements",
        "  above are the ones that will run.",
    ]
    return "\n".join(lines)
