"""Curated-memory telemetry columns must never widen trg_memory_history's audit
scope or touch updated_at -- the ordering axis for injected context.

telemetria-de-uso-en-memoria-curada (brief) names this the number-one risk of
the whole entry: a telemetry write that trips trg_memory_history would grow
memory_history without bound on every deliberate read or automatic injection,
and a telemetry write that moves updated_at would reorder the injected memory
block on every read -- exactly what the entry's central acceptance criterion
forbids. Tasks 5 and 6 wire the real telemetry writer against this same
invariant; this test is the net that has to stay in place once they do, not a
one-off check for task 2's schema change alone.

The two cases below run as siblings in the same file so they gate each other:
a telemetry-only UPDATE that trips memory_history (or moves updated_at) is a
false positive for "telemetry is isolated"; a real body edit that does NOT
trip memory_history (or does NOT move updated_at) means the audit trigger
itself is silently broken, not that telemetry behaves. Asserting only the
first half cannot tell those two failure modes apart.

Builds its own disposable DB from the REAL gaia/store/schema.sql, in pytest's
tmp_path (never a .gaia-tree path) -- gaia_db_write_guard.py categorically
blocks raw SQL writes to a .gaia-tree database from an agent's Bash tool by
design, and this test stays clear of that boundary rather than weakening,
patching, or routing around it. Using the real schema.sql, not a
hand-reconstructed trigger, is what lets this test catch a future schema.sql
edit that widens trg_memory_history's WHEN clause to include a telemetry
column -- a hand-copied trigger definition would drift silently and stop
meaning anything.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_SQL = ROOT / "gaia" / "store" / "schema.sql"

_WORKSPACE = "me"
_SLUG = "telemetry_probe"


def _seed_db(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    con.execute("INSERT INTO workspaces (name) VALUES (?)", (_WORKSPACE,))
    con.execute(
        "INSERT INTO memory (workspace, name, type, body, class, updated_at) "
        "VALUES (?, ?, 'project', 'original body', 'log', '2026-01-01T00:00:00Z')",
        (_WORKSPACE, _SLUG),
    )
    con.commit()
    return con


def test_telemetry_only_update_leaves_audit_and_ordering_untouched(
    tmp_path: Path,
) -> None:
    """Property: an UPDATE that touches ONLY the telemetry columns produces no
    new memory_history rows and does not move updated_at."""
    con = _seed_db(tmp_path / "telemetry_only.db")
    try:
        before_history = con.execute(
            "SELECT COUNT(*) FROM memory_history"
        ).fetchone()[0]
        before_row = con.execute(
            "SELECT updated_at FROM memory WHERE workspace=? AND name=?",
            (_WORKSPACE, _SLUG),
        ).fetchone()

        con.execute(
            "UPDATE memory SET "
            "injection_count = injection_count + 1, "
            "deliberate_count = deliberate_count + 1, "
            "last_injected_at = '2026-08-12T00:00:00Z', "
            "last_deliberate_at = '2026-08-12T00:00:00Z' "
            "WHERE workspace=? AND name=?",
            (_WORKSPACE, _SLUG),
        )
        con.commit()

        after_history = con.execute(
            "SELECT COUNT(*) FROM memory_history"
        ).fetchone()[0]
        after_row = con.execute(
            "SELECT updated_at, injection_count, deliberate_count "
            "FROM memory WHERE workspace=? AND name=?",
            (_WORKSPACE, _SLUG),
        ).fetchone()

        assert after_history == before_history, (
            "a counters-only UPDATE must not add memory_history rows -- every "
            "deliberate read or automatic injection would otherwise grow the "
            "audit trail without bound"
        )
        assert after_row["updated_at"] == before_row["updated_at"], (
            "a counters-only UPDATE must not move updated_at -- it is the sort "
            "key context injection uses, and touching it would reorder the "
            "injected memory block on every read"
        )
        assert after_row["injection_count"] == 1
        assert after_row["deliberate_count"] == 1
    finally:
        con.close()


def test_real_body_edit_updates_timestamp_and_audits(tmp_path: Path) -> None:
    """Control for the property above: a genuine body edit DOES bump
    updated_at and DOES land a memory_history row. Without this, the sibling
    test above cannot distinguish "telemetry is excluded" from "the audit
    trigger never fires at all"."""
    con = _seed_db(tmp_path / "real_edit_control.db")
    try:
        before_history = con.execute(
            "SELECT COUNT(*) FROM memory_history"
        ).fetchone()[0]
        before_row = con.execute(
            "SELECT updated_at FROM memory WHERE workspace=? AND name=?",
            (_WORKSPACE, _SLUG),
        ).fetchone()

        con.execute(
            "UPDATE memory SET body = 'edited body', "
            "updated_at = '2026-08-12T00:00:00Z' "
            "WHERE workspace=? AND name=?",
            (_WORKSPACE, _SLUG),
        )
        con.commit()

        after_history = con.execute(
            "SELECT COUNT(*) FROM memory_history"
        ).fetchone()[0]
        after_row = con.execute(
            "SELECT updated_at FROM memory WHERE workspace=? AND name=?",
            (_WORKSPACE, _SLUG),
        ).fetchone()

        assert after_history == before_history + 1, (
            "a real body edit must land exactly one memory_history row -- if "
            "it does not, the audit trigger itself is broken, not merely quiet"
        )
        assert after_row["updated_at"] != before_row["updated_at"], (
            "a real body edit is expected to move updated_at -- this is the "
            "control that proves the sibling test's silence means 'excluded', "
            "not 'the trigger never fires'"
        )
    finally:
        con.close()
