"""Behavioral tests for scan-v2 SV3 memory resilience (never lose data).

Exercises the writer/reader API (not raw SQL) against a temp DB materialized
from the real schema.sql via gaia.store.writer._connect, proving the four
loss-vector guarantees hold end-to-end:

  1. archive-on-upsert  -- upsert_memory archives the previous body.
  2. tombstone-on-delete -- delete_memory soft-deletes; the row + body survive
     and do NOT surface in reads (get_memory / list_memory / query / injection).
  3. relocate origin trace -- relocate_memory records the origin workspace and
     removes dangling partial_links.
  4. wipe/migrate preservation -- wipe_workspace(preserve_memory=True) keeps
     curated memory across a workspace wipe.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch) -> Path:
    """Fresh DB from the real schema.sql; curator guard neutralised (human
    caller) by clearing GAIA_DISPATCH_AGENT."""
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    from gaia.store.writer import _connect

    path = db_path()
    con = _connect(path)
    con.execute("INSERT INTO workspaces (name) VALUES ('me')")
    con.commit()
    con.close()
    return path


# ---------------------------------------------------------------------------
# Vector 1: archive-on-upsert
# ---------------------------------------------------------------------------

def test_upsert_archives_previous_body(db: Path) -> None:
    from gaia.store.writer import upsert_memory, _connect

    upsert_memory("me", "project_x", type="project", body="version one", db_path=db)
    upsert_memory("me", "project_x", type="project", body="version two", db_path=db)

    con = _connect(db)
    try:
        rows = con.execute(
            "SELECT before_body, after_body FROM memory_history "
            "WHERE workspace='me' AND name='project_x' ORDER BY id"
        ).fetchall()
    finally:
        con.close()

    assert len(rows) == 1, "the overwrite should have archived exactly one prior version"
    assert rows[0]["before_body"] == "version one"
    assert rows[0]["after_body"] == "version two"


# ---------------------------------------------------------------------------
# Vector 2: tombstone-on-delete
# ---------------------------------------------------------------------------

def test_delete_is_tombstone_row_survives(db: Path) -> None:
    from gaia.store.writer import upsert_memory, delete_memory, _connect

    upsert_memory("me", "project_x", type="project", body="keep me", db_path=db)
    assert delete_memory("me", "project_x", db_path=db) is True

    con = _connect(db)
    try:
        row = con.execute(
            "SELECT body, deleted_at FROM memory WHERE workspace='me' AND name='project_x'"
        ).fetchone()
    finally:
        con.close()

    assert row is not None, "tombstone must NOT physically remove the row"
    assert row["body"] == "keep me", "the body must survive the tombstone"
    assert row["deleted_at"] is not None


def test_tombstone_not_surfaced_in_get_or_list(db: Path) -> None:
    from gaia.store.writer import (
        upsert_memory, delete_memory, get_memory, list_memory,
    )

    upsert_memory("me", "project_x", type="project", body="b", db_path=db)
    delete_memory("me", "project_x", db_path=db)

    assert get_memory("me", "project_x", db_path=db) is None
    assert get_memory("me", "project_x", include_deleted=True, db_path=db) is not None
    names = [r["name"] for r in list_memory("me", db_path=db)]
    assert "project_x" not in names
    names_incl = [r["name"] for r in list_memory("me", include_deleted=True, db_path=db)]
    assert "project_x" in names_incl


def test_tombstone_not_surfaced_in_query_or_search(db: Path) -> None:
    from gaia.store.writer import upsert_memory, delete_memory, search_memory_curated
    from gaia.store.reader import cross_surface_query

    upsert_memory("me", "project_findme", type="project",
                  body="unique_token_zzz", db_path=db)
    delete_memory("me", "project_findme", db_path=db)

    # reader._query_memory path (gaia query)
    hits = cross_surface_query(surface="memory", workspace="me", db_path=db)
    assert all(h["raw"]["name"] != "project_findme" for h in hits)

    # FTS path (gaia memory search --scope=memory)
    fts = search_memory_curated("me", "unique_token_zzz", db_path=db)
    assert all(r["name"] != "project_findme" for r in fts)


def test_upsert_resurrects_tombstone(db: Path) -> None:
    from gaia.store.writer import upsert_memory, delete_memory, get_memory

    upsert_memory("me", "project_x", type="project", body="b1", db_path=db)
    delete_memory("me", "project_x", db_path=db)
    assert get_memory("me", "project_x", db_path=db) is None

    upsert_memory("me", "project_x", type="project", body="b2", db_path=db)
    row = get_memory("me", "project_x", db_path=db)
    assert row is not None, "re-adding a tombstoned slug must clear the tombstone"
    assert row["body"] == "b2"
    assert row["deleted_at"] is None


def test_hard_delete_removes_row(db: Path) -> None:
    from gaia.store.writer import upsert_memory, delete_memory, _connect

    upsert_memory("me", "project_x", type="project", body="b", db_path=db)
    assert delete_memory("me", "project_x", hard=True, db_path=db) is True

    con = _connect(db)
    try:
        row = con.execute(
            "SELECT 1 FROM memory WHERE workspace='me' AND name='project_x'"
        ).fetchone()
    finally:
        con.close()
    assert row is None, "hard delete must physically remove the row"


# ---------------------------------------------------------------------------
# Vector 3: relocate origin trace + dangling partial_links
# ---------------------------------------------------------------------------

def test_relocate_records_origin_workspace(db: Path) -> None:
    from gaia.store.writer import upsert_memory, relocate_memory, _connect

    upsert_memory("me", "project_x", type="project", body="b", db_path=db)
    res = relocate_memory("me", "other", ["project_x"], db_path=db)
    assert res["moved"] == ["project_x"]

    con = _connect(db)
    try:
        row = con.execute(
            "SELECT before_workspace, after_workspace FROM memory_history "
            "WHERE name='project_x' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    assert row["before_workspace"] == "me"
    assert row["after_workspace"] == "other"


def test_relocate_removes_dangling_partial_link(db: Path) -> None:
    from gaia.store.writer import (
        upsert_memory, insert_memory_link, relocate_memory, _connect,
    )

    upsert_memory("me", "decision_a", type="decision", body="a", db_path=db)
    upsert_memory("me", "decision_b", type="decision", body="b", db_path=db)
    insert_memory_link("me", "decision_a", "decision_b", kind="relates_to", db_path=db)

    # Move only ONE endpoint -> the link becomes dangling.
    res = relocate_memory("me", "other", ["decision_a"], db_path=db)
    assert len(res["partial_links"]) == 1

    con = _connect(db)
    try:
        remaining = con.execute(
            "SELECT COUNT(*) AS c FROM memory_links WHERE workspace='me'"
        ).fetchone()["c"]
        # both memory rows still exist (data preserved)
        rows = con.execute("SELECT COUNT(*) AS c FROM memory").fetchone()["c"]
    finally:
        con.close()
    assert remaining == 0, "the dangling link must be removed, not left behind"
    assert rows == 2, "both memory rows (the data) must survive the relocate"


def test_relocate_keeps_intra_set_link(db: Path) -> None:
    from gaia.store.writer import (
        upsert_memory, insert_memory_link, relocate_memory, _connect,
    )

    upsert_memory("me", "decision_a", type="decision", body="a", db_path=db)
    upsert_memory("me", "decision_b", type="decision", body="b", db_path=db)
    insert_memory_link("me", "decision_a", "decision_b", kind="relates_to", db_path=db)

    res = relocate_memory("me", "other", ["decision_a", "decision_b"], db_path=db)
    assert len(res["links_moved"]) == 1
    assert res["partial_links"] == []

    con = _connect(db)
    try:
        moved = con.execute(
            "SELECT COUNT(*) AS c FROM memory_links WHERE workspace='other'"
        ).fetchone()["c"]
    finally:
        con.close()
    assert moved == 1, "a link with both endpoints in the moved set travels with them"


# ---------------------------------------------------------------------------
# Vector 4: wipe/migrate preservation
# ---------------------------------------------------------------------------

def test_wipe_preserves_memory_by_default(db: Path) -> None:
    from gaia.store.writer import upsert_memory, wipe_workspace, get_memory, _connect

    upsert_memory("me", "project_keep", type="project", body="survive", db_path=db)
    # a scannable child that SHOULD be cleared
    con = _connect(db)
    try:
        con.execute("INSERT INTO projects (workspace, name) VALUES ('me', 'gaia')")
        con.commit()
    finally:
        con.close()

    wipe_workspace("me", db_path=db)

    row = get_memory("me", "project_keep", db_path=db)
    assert row is not None, "memory must survive a default wipe"
    assert row["body"] == "survive"

    con = _connect(db)
    try:
        ws = con.execute("SELECT 1 FROM workspaces WHERE name='me'").fetchone()
        proj = con.execute(
            "SELECT COUNT(*) AS c FROM projects WHERE workspace='me'"
        ).fetchone()["c"]
    finally:
        con.close()
    assert ws is not None, "the workspaces row must be restored"
    assert proj == 0, "scannable children (projects) must be cleared by the wipe"


def test_wipe_preserves_memory_links(db: Path) -> None:
    from gaia.store.writer import (
        upsert_memory, insert_memory_link, wipe_workspace, _connect,
    )

    upsert_memory("me", "decision_a", type="decision", body="a", db_path=db)
    upsert_memory("me", "decision_b", type="decision", body="b", db_path=db)
    insert_memory_link("me", "decision_a", "decision_b", kind="relates_to", db_path=db)

    wipe_workspace("me", db_path=db)

    con = _connect(db)
    try:
        links = con.execute(
            "SELECT COUNT(*) AS c FROM memory_links WHERE workspace='me'"
        ).fetchone()["c"]
        mem = con.execute(
            "SELECT COUNT(*) AS c FROM memory WHERE workspace='me'"
        ).fetchone()["c"]
    finally:
        con.close()
    assert mem == 2
    assert links == 1


def test_wipe_purge_memory_destroys(db: Path) -> None:
    from gaia.store.writer import upsert_memory, wipe_workspace, _connect

    upsert_memory("me", "project_x", type="project", body="b", db_path=db)
    wipe_workspace("me", preserve_memory=False, db_path=db)

    con = _connect(db)
    try:
        mem = con.execute(
            "SELECT COUNT(*) AS c FROM memory WHERE workspace='me'"
        ).fetchone()["c"]
        ws = con.execute("SELECT 1 FROM workspaces WHERE name='me'").fetchone()
    finally:
        con.close()
    assert mem == 0, "explicit purge must destroy memory (full CASCADE)"
    assert ws is None
