"""
Tests for ``gaia memory checkpoint`` -- the transactional session-close verb.

Brief: session-close-checkpoint-verb.

The verb persists a whole session-close reflection atomically: one record
anchor + N carry-forward threads + N derived_from links, all under ONE DB
transaction. The suite pins the guarantees the dedicated writer exists to
provide:

  1. valid payload  -> anchor + N threads + N derived_from links
  2. empty pendings -> anchor only, no threads, no links
  3. ATOMICITY: an invalid 2nd pending rolls the WHOLE checkpoint back to zero
     rows (the test that justifies a single-transaction writer)
  4. re-run is idempotent (UPSERT rows, re-use edges)
  5. prose pending + empty list -> non-blocking warning, exit 0, still writes
  6. missing scope -> structured missing_scope reject at the CLI, zero rows
  7. dispatch gate: GAIA_DISPATCH_AGENT=developer is refused
  8. malformed payload -> bad_shape, zero rows
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Route the substrate DB into tmp_path so the test never touches the real
    ``~/.gaia/gaia.db``, and start with no leaked dispatch identity."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    monkeypatch.delenv("GAIA_SESSION_ID", raising=False)
    from gaia.paths import db_path
    return db_path()


def _memory_rows(db_path: Path) -> list[tuple]:
    # A reject path may never open a connection, so the schema is never
    # materialized -- an absent table is trivially "zero rows".
    if not db_path.exists():
        return []
    con = sqlite3.connect(str(db_path))
    try:
        return list(con.execute(
            "SELECT name, type, class, status FROM memory "
            "WHERE deleted_at IS NULL ORDER BY name"
        ).fetchall())
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def _link_rows(db_path: Path) -> list[tuple]:
    if not db_path.exists():
        return []
    con = sqlite3.connect(str(db_path))
    try:
        return list(con.execute(
            "SELECT src_name, dst_name, kind FROM memory_links "
            "ORDER BY src_name, dst_name, kind"
        ).fetchall())
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


_RECORD = "project_session_2026_07_14_test"


def _payload(pendientes=None, *, record_body="Closed the arc cleanly."):
    return {
        "resumen": {
            "name": _RECORD,
            "type": "project",
            "description": "session close test",
            "body": record_body,
        },
        "pendientes": pendientes or [],
    }


def _pending(name, body="pending body", description="a pending"):
    return {"name": name, "description": description, "body": body}


# ---------------------------------------------------------------------------
# 1. valid payload -> anchor + N threads + links
# ---------------------------------------------------------------------------

def test_valid_payload_writes_anchor_threads_and_links(tmp_db):
    from gaia.store.writer import close_session_memory
    res = close_session_memory(
        "me",
        _payload([_pending("project_pending_a"), _pending("project_pending_b")]),
    )
    assert res["status"] == "applied"
    assert res["anchor"]["name"] == _RECORD
    assert res["anchor"]["class"] == "anchor"
    assert len(res["threads"]) == 2
    assert all(t["class"] == "thread" and t["memory_status"] == "carry_forward"
               for t in res["threads"])
    assert len(res["links"]) == 2

    rows = _memory_rows(tmp_db)
    assert (_RECORD, "project", "anchor", None) in rows
    assert ("project_pending_a", "project", "thread", "carry_forward") in rows
    assert ("project_pending_b", "project", "thread", "carry_forward") in rows

    links = _link_rows(tmp_db)
    assert ("project_pending_a", _RECORD, "derived_from") in links
    assert ("project_pending_b", _RECORD, "derived_from") in links


# ---------------------------------------------------------------------------
# 2. empty pendings -> anchor only
# ---------------------------------------------------------------------------

def test_empty_pendings_writes_only_anchor(tmp_db):
    from gaia.store.writer import close_session_memory
    res = close_session_memory("me", _payload([]))
    assert res["threads"] == []
    assert res["links"] == []
    assert _memory_rows(tmp_db) == [(_RECORD, "project", "anchor", None)]
    assert _link_rows(tmp_db) == []


# ---------------------------------------------------------------------------
# 3. ATOMICITY: invalid 2nd pending -> zero rows (rollback)
# ---------------------------------------------------------------------------

def test_invalid_second_pending_rolls_back_everything(tmp_db):
    from gaia.store.writer import close_session_memory
    # 'atom_bad' is a curated prefix while the inherited type is 'project' ->
    # _validate_curated_slug raises INSIDE the transaction, after the anchor and
    # the first thread were already inserted. The whole checkpoint must roll back.
    payload = _payload([
        _pending("project_pending_ok"),
        _pending("atom_bad"),
    ])
    with pytest.raises(ValueError):
        close_session_memory("me", payload)

    # Nothing durable: not the anchor, not the first (valid) thread, no link.
    assert _memory_rows(tmp_db) == []
    assert _link_rows(tmp_db) == []


# ---------------------------------------------------------------------------
# 4. re-run idempotent
# ---------------------------------------------------------------------------

def test_rerun_is_idempotent(tmp_db):
    from gaia.store.writer import close_session_memory
    payload = _payload([_pending("project_pending_a")])
    first = close_session_memory("me", payload)
    assert first["anchor"]["action"] == "inserted"
    assert first["links"][0]["action"] == "inserted"

    second = close_session_memory("me", payload)
    assert second["anchor"]["action"] == "updated"
    assert second["links"][0]["action"] == "noop"

    # Row counts are unchanged by the re-run.
    assert len(_memory_rows(tmp_db)) == 2  # anchor + one thread
    assert len(_link_rows(tmp_db)) == 1


# ---------------------------------------------------------------------------
# 5. prose pending + empty list -> warning, not abort
# ---------------------------------------------------------------------------

def test_prose_pending_with_empty_list_warns_without_aborting(tmp_db):
    from gaia.store.writer import close_session_memory
    res = close_session_memory(
        "me",
        _payload([], record_body="Done, but TODO: wire the retry path next."),
    )
    assert res["status"] == "applied"
    assert res["warnings"], "expected a heuristic warning"
    assert "pending" in res["warnings"][0].lower()
    # The record anchor is still written -- the warning does not abort.
    assert _memory_rows(tmp_db) == [(_RECORD, "project", "anchor", None)]


def test_no_warning_when_pendings_present(tmp_db):
    from gaia.store.writer import close_session_memory
    res = close_session_memory(
        "me",
        _payload([_pending("project_pending_a")],
                 record_body="Done, TODO handled by the thread below."),
    )
    assert res["warnings"] == []


# ---------------------------------------------------------------------------
# 6. missing scope -> structured reject at the CLI, zero rows
# ---------------------------------------------------------------------------

def test_missing_scope_rejects_at_cli_and_writes_nothing(tmp_db, tmp_path, capsys):
    from cli import memory as memory_mod
    payload_file = tmp_path / "payload.json"
    import json as _json
    payload_file.write_text(_json.dumps(_payload([_pending("project_pending_a")])))

    ns = argparse.Namespace(
        file=str(payload_file),
        workspace=None, project=None, project_ref=None, json=True,
    )
    rc = memory_mod._cmd_checkpoint(ns)
    assert rc == 1
    out = capsys.readouterr().out
    assert '"code": "missing_scope"' in out
    assert _memory_rows(tmp_db) == []
    assert _link_rows(tmp_db) == []


# ---------------------------------------------------------------------------
# 7. dispatch gate: a non-curator dispatch is refused
# ---------------------------------------------------------------------------

def test_dispatch_gate_refuses_non_curator(tmp_db, monkeypatch):
    from gaia.store.writer import close_session_memory, MemoryWriteForbidden
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")
    with pytest.raises(MemoryWriteForbidden):
        close_session_memory("me", _payload([_pending("project_pending_a")]))
    # The gate fires before any connection is opened -- nothing written.
    assert _memory_rows(tmp_db) == []


def test_dispatch_gate_allows_curator(tmp_db, monkeypatch):
    from gaia.store.writer import close_session_memory
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "gaia-operator")
    res = close_session_memory("me", _payload([]))
    assert res["status"] == "applied"


# ---------------------------------------------------------------------------
# 8. malformed payload -> bad_shape, zero rows
# ---------------------------------------------------------------------------

def test_pendientes_not_a_list_is_bad_shape(tmp_db):
    from gaia.store.writer import close_session_memory, MemorySessionPayloadError
    bad = _payload()
    bad["pendientes"] = "oops"
    with pytest.raises(MemorySessionPayloadError) as ei:
        close_session_memory("me", bad)
    assert ei.value.code == "bad_shape"
    assert _memory_rows(tmp_db) == []


def test_pending_missing_body_is_bad_shape(tmp_db):
    from gaia.store.writer import close_session_memory, MemorySessionPayloadError
    bad = _payload([{"name": "project_pending_a", "description": "no body"}])
    with pytest.raises(MemorySessionPayloadError) as ei:
        close_session_memory("me", bad)
    assert ei.value.code == "bad_shape"
    assert _memory_rows(tmp_db) == []


def test_resumen_missing_type_is_bad_shape(tmp_db):
    from gaia.store.writer import close_session_memory, MemorySessionPayloadError
    bad = {"resumen": {"name": _RECORD, "description": "x", "body": "y"},
           "pendientes": []}
    with pytest.raises(MemorySessionPayloadError) as ei:
        close_session_memory("me", bad)
    assert ei.value.code == "bad_shape"
    assert _memory_rows(tmp_db) == []
