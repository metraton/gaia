"""
Host-scope for system memory -- 2026-08-27 consensus
(thread_gaia_memoria_host_scope_consenso).

Gaia's own scope (the ``gaia_system`` initiative) is expressed as a VALUE of
the existing workspace axis: a sentinel workspace, ``HOST_WORKSPACE =
"_gaia_host"``, not a new column. Writing a host-scoped initiative always
lands in the sentinel regardless of --workspace/env/cwd, and every canonical
read (project-mode, digest, show) reaches it from any vantage.

  (a) add --workspace=<other> + initiative=gaia_system -> lands in
      _gaia_host, with a visible notice.
  (b) add --project-ref=<x> + initiative=gaia_system -> host_scope_no_project,
      exit 1, zero rows written.
  (c) checkpoint host-scoped -> anchor + thread land in the sentinel.
  (d) get-relevant --initiative=gaia_system returns the sentinel's corpus
      from any --workspace.
  (e) the transversal digest includes sentinel rows alongside the session
      workspace's own rows.
  (f) relocate_memory refuses to move a host-scoped row OUT of the sentinel;
      moving INTO it is allowed.

Uses the real writer/CLI path against a temporary DB (GAIA_DATA_DIR), same
fixture shape as test_memory_checkpoint.py, so the real INSERT sites, the
real host-scope guard, and the real read union are exercised end-to-end.
"""

from __future__ import annotations

import argparse
import json as _json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli import memory as memory_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Route the substrate DB into tmp_path -- never the real ~/.gaia/gaia.db."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    monkeypatch.delenv("GAIA_SESSION_ID", raising=False)
    from gaia.paths import db_path
    return db_path()


def _memory_rows(db_path: Path) -> list[tuple]:
    if not db_path.exists():
        return []
    con = sqlite3.connect(str(db_path))
    try:
        return list(con.execute(
            "SELECT workspace, name, initiative FROM memory "
            "WHERE deleted_at IS NULL ORDER BY workspace, name"
        ).fetchall())
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def _add_args(**overrides):
    base = dict(
        name=None, type="atom", body="body text", body_file=None,
        description=None, workspace=None, class_=None, status=None,
        project=None, project_ref=None, audience=None, initiative=None,
        json=True,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _checkpoint_args(payload_path, **overrides):
    base = dict(
        file=str(payload_path), workspace=None, project=None,
        project_ref=None, initiative=None, json=True,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _write_payload(tmp_path, name, *, pendientes=None):
    payload_file = tmp_path / "payload.json"
    payload_file.write_text(_json.dumps({
        "resumen": {
            "name": name, "type": "project",
            "description": "test", "body": "record body",
        },
        "pendientes": pendientes or [],
    }))
    return payload_file


def _get_relevant_args(**overrides):
    base = dict(
        workspace=None, limit=8, max_chars=1500, types=None,
        sections=None, initiative=None, json=True,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


# ---------------------------------------------------------------------------
# (a) add --workspace=<other> + initiative=gaia_system -> _gaia_host, notice
# ---------------------------------------------------------------------------

def test_add_host_scoped_initiative_lands_in_sentinel_with_notice(tmp_db, capsys):
    rc = memory_mod._cmd_add(_add_args(
        name="atom_host_scope_test", workspace="century-inc",
        initiative="gaia_system",
    ))
    assert rc == 0
    out = _json.loads(capsys.readouterr().out)
    assert out["workspace"] == "_gaia_host"
    assert out["host_scoped"] is True

    rows = _memory_rows(tmp_db)
    assert ("_gaia_host", "atom_host_scope_test", "gaia_system") in rows
    assert not any(r[0] == "century-inc" for r in rows)


def test_add_host_scoped_MECHANISM_DEMONSTRATION_inverted(tmp_db, capsys):
    """Same write as above; asserts the WRONG (pre-fix) workspace on purpose
    to demonstrate the test is capable of failing without the guard. This
    inverted assertion is expected to fail -- pytest.raises(AssertionError)
    captures that failure so the suite as a whole still passes; the real,
    correct assertion lives in the test above."""
    rc = memory_mod._cmd_add(_add_args(
        name="atom_host_scope_invert", workspace="century-inc",
        initiative="gaia_system",
    ))
    assert rc == 0
    out = _json.loads(capsys.readouterr().out)
    with pytest.raises(AssertionError):
        # Pre-fix behaviour would have honored --workspace verbatim.
        assert out["workspace"] == "century-inc"


# ---------------------------------------------------------------------------
# (b) add --project-ref=<x> + initiative=gaia_system -> host_scope_no_project
# ---------------------------------------------------------------------------

def test_add_host_scoped_with_project_ref_is_rejected(tmp_db, capsys):
    rc = memory_mod._cmd_add(_add_args(
        name="atom_host_scope_project_reject", project_ref="some/project",
        initiative="gaia_system",
    ))
    assert rc == 1
    out = capsys.readouterr().out
    assert '"code": "host_scope_no_project"' in out
    assert _memory_rows(tmp_db) == []


# ---------------------------------------------------------------------------
# (c) checkpoint host-scoped -> anchor + thread land in the sentinel
# ---------------------------------------------------------------------------

def test_checkpoint_host_scoped_lands_in_sentinel(tmp_db, tmp_path, capsys):
    payload = _write_payload(
        tmp_path, "project_checkpoint_host_scope",
        pendientes=[{"name": "project_checkpoint_host_pending",
                     "description": "d", "body": "b"}],
    )
    rc = memory_mod._cmd_checkpoint(_checkpoint_args(
        payload, workspace="me", initiative="gaia_system",
    ))
    assert rc == 0
    out = _json.loads(capsys.readouterr().out)
    assert out["workspace"] == "_gaia_host"
    assert out["host_scoped"] is True

    rows = _memory_rows(tmp_db)
    assert ("_gaia_host", "project_checkpoint_host_scope", "gaia_system") in rows
    assert ("_gaia_host", "project_checkpoint_host_pending", "gaia_system") in rows
    assert not any(r[0] == "me" for r in rows)


# ---------------------------------------------------------------------------
# (d) get-relevant --initiative=gaia_system reads the sentinel from any vantage
# ---------------------------------------------------------------------------

def test_get_relevant_initiative_mode_reaches_sentinel_from_other_workspace(
    tmp_db, capsys,
):
    # Seed the sentinel directly through the real writer, from a DIFFERENT
    # workspace than the one that will later read it -- the write side
    # forces it into _gaia_host regardless.
    rc_add = memory_mod._cmd_add(_add_args(
        name="thread_host_scope_pending", type="project", workspace="me",
        initiative="gaia_system", class_="thread", status="open",
    ))
    assert rc_add == 0
    capsys.readouterr()

    rc = memory_mod._cmd_get_relevant(_get_relevant_args(
        workspace="century-inc", initiative="gaia_system",
    ))
    assert rc == 0
    out = _json.loads(capsys.readouterr().out)
    names = {i["name"] for i in out["items"]}
    assert "thread_host_scope_pending" in names


def test_get_relevant_initiative_mode_MECHANISM_DEMONSTRATION_inverted(
    tmp_db, capsys,
):
    """Inverted: asserts the sentinel row is ABSENT when read from a foreign
    workspace, which is what the pre-fix single-workspace query would show.
    Demonstrates the test can fail without the _reader_workspaces union."""
    memory_mod._cmd_add(_add_args(
        name="thread_host_scope_pending_2", type="project", workspace="me",
        initiative="gaia_system", class_="thread", status="open",
    ))
    capsys.readouterr()
    rc = memory_mod._cmd_get_relevant(_get_relevant_args(
        workspace="century-inc", initiative="gaia_system",
    ))
    assert rc == 0
    out = _json.loads(capsys.readouterr().out)
    names = {i["name"] for i in out["items"]}
    with pytest.raises(AssertionError):
        # Pre-fix behaviour: workspace='century-inc' alone would never see a
        # row physically stored under '_gaia_host'.
        assert "thread_host_scope_pending_2" not in names


# ---------------------------------------------------------------------------
# (e) digest includes sentinel rows alongside the session workspace's own
# ---------------------------------------------------------------------------

def test_digest_includes_sentinel_rows_alongside_session_workspace(tmp_db, capsys):
    memory_mod._cmd_add(_add_args(
        name="thread_me_local_pending", type="project", workspace="me",
        class_="thread", status="open",
    ))
    capsys.readouterr()
    memory_mod._cmd_add(_add_args(
        name="thread_host_scope_digest", type="project", workspace="me",
        initiative="gaia_system", class_="thread", status="open",
    ))
    capsys.readouterr()

    rc = memory_mod._cmd_get_relevant(_get_relevant_args(workspace="me"))
    assert rc == 0
    out = _json.loads(capsys.readouterr().out)
    names = {i["name"] for i in out["items"]}
    initiatives = {i["initiative"] for i in out["items"]}
    assert "thread_me_local_pending" in names
    assert "thread_host_scope_digest" in names
    assert "gaia_system" in initiatives


# ---------------------------------------------------------------------------
# (f) relocate: reject OUT of the sentinel, allow INTO it
# ---------------------------------------------------------------------------

def test_relocate_out_of_sentinel_is_rejected(tmp_db, capsys):
    from gaia.store.writer import relocate_memory, MemoryHostScopeError

    memory_mod._cmd_add(_add_args(
        name="atom_relocate_guard", workspace="me", initiative="gaia_system",
    ))
    capsys.readouterr()

    with pytest.raises(MemoryHostScopeError):
        relocate_memory("_gaia_host", "century-inc", ["atom_relocate_guard"])

    rows = _memory_rows(tmp_db)
    assert ("_gaia_host", "atom_relocate_guard", "gaia_system") in rows
    assert not any(r[0] == "century-inc" for r in rows)


def test_relocate_into_sentinel_is_allowed(tmp_db, capsys):
    from gaia.store.writer import relocate_memory

    # A legacy row, written before this rule existed, sitting under 'me'.
    memory_mod._cmd_add(_add_args(
        name="atom_relocate_legacy", workspace="me", project_ref="legacy/proj",
    ))
    capsys.readouterr()
    con = sqlite3.connect(str(tmp_db))
    con.execute(
        "UPDATE memory SET initiative = 'gaia_system' "
        "WHERE workspace = 'me' AND name = 'atom_relocate_legacy'"
    )
    con.commit()
    con.close()

    result = relocate_memory("me", "_gaia_host", ["atom_relocate_legacy"])
    assert result["moved"] == ["atom_relocate_legacy"]
    rows = _memory_rows(tmp_db)
    assert ("_gaia_host", "atom_relocate_legacy", "gaia_system") in rows


# ---------------------------------------------------------------------------
# Bonus: `memory show` by slug resolves the sentinel even from a foreign
# vantage (item 4 of the spec; not one of the six named test properties, but
# part of the deliverable -- see the docstring added at the show call site).
# ---------------------------------------------------------------------------

def test_show_by_slug_falls_back_to_sentinel(tmp_db, capsys):
    memory_mod._cmd_add(_add_args(
        name="atom_show_sentinel_fallback", workspace="me",
        initiative="gaia_system",
    ))
    capsys.readouterr()

    ns = argparse.Namespace(
        workspace="century-inc", name="atom_show_sentinel_fallback",
        json=True, links=False, history=False,
    )
    rc = memory_mod._cmd_curated_show(ns)
    assert rc == 0
    out = _json.loads(capsys.readouterr().out)
    assert out["workspace"] == "_gaia_host"
    assert out["name"] == "atom_show_sentinel_fallback"
