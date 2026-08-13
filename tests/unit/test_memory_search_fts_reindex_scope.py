"""memory_au (the FTS5 re-index trigger for curated memory) must re-index
memory_fts only when a column full-text search actually indexes changes --
not on every UPDATE.

telemetria-de-uso-en-memoria-curada (brief), task 3: before this trigger's
WHEN clause existed, EVERY UPDATE on `memory` re-indexed the row's full FTS
document, including a future telemetry-only write that lands on every
deliberate read and every automatic injection. That does not grow anything,
but it amplifies write-per-read on the search index -- exactly what a
measurement-only delivery should not cause.

The two cases below run as siblings in the same file so they gate each
other, mirroring test_memory_telemetry_audit_isolation.py's two-face
pattern: a counters-only UPDATE that still re-indexes is a false positive
for "the WHEN clause scoped it"; a real body edit that stops re-indexing
means the WHEN clause is too broad and search now serves stale content
with nobody noticing. Asserting only one half cannot tell those two failure
modes apart.

Re-indexing is observed via SQLite's total_changes() delta across the
UPDATE statement: memory_fts is an external-content FTS5 table, so a firing
memory_au performs two additional virtual-table writes (a 'delete' then an
insert) on top of the `memory` row itself. A delta of exactly 1 means only
the `memory` row changed (the trigger did not fire); a delta greater than 1
means the FTS shadow tables were also written (the trigger fired). A plain
before/after search-result comparison cannot distinguish "did not
re-index" from "re-indexed identical content back to itself", since both
produce the same search outcome -- the change counter is what makes the
non-firing case falsifiable.

Builds its own disposable DB from the REAL gaia/store/schema.sql, in
pytest's tmp_path (never a .gaia-tree path) -- gaia_db_write_guard.py
categorically blocks raw SQL writes to a .gaia-tree database from an
agent's Bash tool by design, and this test stays clear of that boundary
rather than weakening, patching, or routing around it. Using the real
schema.sql, not a hand-reconstructed trigger, is what lets this test catch
a future schema.sql edit that widens or narrows memory_au's WHEN clause --
a hand-copied trigger definition would drift silently and stop meaning
anything.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_SQL = ROOT / "gaia" / "store" / "schema.sql"

_WORKSPACE = "me"
_SLUG = "fts_reindex_probe"
_NEW_TERM = "zzqvortex_probe_term"


def _seed_db(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    con.execute("INSERT INTO workspaces (name) VALUES (?)", (_WORKSPACE,))
    con.execute(
        "INSERT INTO memory (workspace, name, type, body, class, updated_at) "
        "VALUES (?, ?, 'project', 'original searchable body', 'log', '2026-01-01T00:00:00Z')",
        (_WORKSPACE, _SLUG),
    )
    con.commit()
    return con


def _search_hits(con: sqlite3.Connection, term: str) -> list[str]:
    rows = con.execute(
        "SELECT name FROM memory_fts WHERE memory_fts MATCH ? AND workspace = ?",
        (term, _WORKSPACE),
    ).fetchall()
    return [r["name"] for r in rows]


def test_telemetry_only_update_does_not_reindex_fts(tmp_path: Path) -> None:
    """Property: an UPDATE that touches ONLY the telemetry columns produces
    no additional writes on the FTS shadow tables -- memory_au must not
    fire for it."""
    con = _seed_db(tmp_path / "fts_telemetry_only.db")
    try:
        changes_before = con.execute("SELECT total_changes()").fetchone()[0]

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

        changes_after = con.execute("SELECT total_changes()").fetchone()[0]

        assert changes_after - changes_before == 1, (
            "a counters-only UPDATE must change exactly the `memory` row "
            "itself and nothing else -- any larger delta means memory_au "
            "fired and re-indexed memory_fts for a write that carries no "
            "search content, amplifying write-per-read on every telemetry "
            "write"
        )
    finally:
        con.close()


def test_real_body_edit_still_reindexes_fts(tmp_path: Path) -> None:
    """Control for the property above: a genuine body edit DOES re-index
    memory_fts and the new term becomes searchable. Without this, the
    sibling test above cannot distinguish "re-index is correctly scoped"
    from "memory_au never fires at all", which would mean search silently
    serves stale content forever."""
    con = _seed_db(tmp_path / "fts_real_edit_control.db")
    try:
        assert _search_hits(con, _NEW_TERM) == [], (
            "the probe term must not already be findable before the edit"
        )

        changes_before = con.execute("SELECT total_changes()").fetchone()[0]

        con.execute(
            "UPDATE memory SET body = body || ' ' || ?, "
            "updated_at = '2026-08-12T01:00:00Z' "
            "WHERE workspace=? AND name=?",
            (_NEW_TERM, _WORKSPACE, _SLUG),
        )
        con.commit()

        changes_after = con.execute("SELECT total_changes()").fetchone()[0]

        assert changes_after - changes_before > 1, (
            "a real body edit must produce more than one changed row -- if "
            "it does not, memory_au itself is broken, not merely scoped"
        )
        assert _search_hits(con, _NEW_TERM) == [_SLUG], (
            "the new term must be findable after a real body edit -- this "
            "is the control that proves the sibling test's silence means "
            "'re-index correctly excluded telemetry', not 'memory_au never "
            "fires'"
        )
    finally:
        con.close()


def test_fts_integrity_check_passes(tmp_path: Path) -> None:
    """The FTS5 'integrity-check' command must succeed against the schema
    memory_au writes into -- a scoped WHEN clause changes WHEN the trigger
    fires, never the shape of what it writes, so the index must stay
    internally consistent."""
    con = _seed_db(tmp_path / "fts_integrity.db")
    try:
        con.execute("INSERT INTO memory_fts(memory_fts) VALUES('integrity-check')")
        con.commit()
    finally:
        con.close()
