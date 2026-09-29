"""
User memory has no workspace -- brief ``una-gaia-cualquier-instalacion``, AC-11.

Who the user is does not depend on which project a session opened in, so every
``type=user`` row lives in the workspace-less sentinel ``USER_WORKSPACE =
"_gaia_user"`` (same pattern as the host sentinel, ``test_memory_host_scope``)
and every canonical reader unions it in:

  (a) add --type user from workspace A (no scope flag, or an explicit
      --workspace) writes the row under the sentinel, never under A.
  (b) get-relevant --sections=anchor reaches that row from workspace B and from
      a directory that is no registered workspace at all.
  (c) a name already in the sentinel is reported (user_name_collision, exit 1)
      and the stored row is left untouched, whichever workspace asks.
  (d) rows that are not type=user keep landing in the caller's workspace.
  (e) checkpoint of a user record lands in the sentinel and its pending threads
      reach the digest and the pending-by-initiative count from any workspace.
  (f) relocate_memory moves user rows only INTO the sentinel and nothing but
      user rows into it.

Uses the real writer/CLI path against a temporary GAIA_DATA_DIR, so the real
INSERT sites and the real read union are exercised end-to-end.
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

SENTINEL = "_gaia_user"


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Route the substrate DB into tmp_path -- never the real ~/.gaia/gaia.db."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for key in ("GAIA_DISPATCH_AGENT", "GAIA_SESSION_ID", "GAIA_WORKSPACE",
                "GAIA_DISPATCH_WORKSPACE", "GAIA_DB"):
        monkeypatch.delenv(key, raising=False)
    from gaia.paths import db_path
    return db_path()


def _rows(db_path: Path) -> list[tuple]:
    if not db_path.exists():
        return []
    con = sqlite3.connect(str(db_path))
    try:
        return list(con.execute(
            "SELECT workspace, name, type, body FROM memory "
            "WHERE deleted_at IS NULL ORDER BY workspace, name"
        ).fetchall())
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def _add_args(**overrides):
    base = dict(
        name=None, type="user", body="body text", body_file=None,
        description=None, workspace=None, class_=None, status=None,
        project=None, project_ref=None, audience=None, initiative=None,
        json=True,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _get_relevant_args(**overrides):
    base = dict(
        workspace=None, limit=8, max_chars=1500, types=None,
        sections=None, initiative=None, json=True,
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


def _in_workspace(monkeypatch, name):
    monkeypatch.setenv("GAIA_WORKSPACE", name)


def _anchor_names(capsys, **overrides) -> set[str]:
    rc = memory_mod._cmd_get_relevant(
        _get_relevant_args(sections="anchor", **overrides)
    )
    assert rc == 0
    out = _json.loads(capsys.readouterr().out)
    return {i["name"] for i in out["items"]}


# ---------------------------------------------------------------------------
# (a) a user row written from any workspace lives in the sentinel
# ---------------------------------------------------------------------------

def test_add_user_from_workspace_without_scope_flag_lands_in_sentinel(
    tmp_db, monkeypatch, capsys,
):
    _in_workspace(monkeypatch, "alpha")
    rc = memory_mod._cmd_add(_add_args(
        name="user_prefers_plain_reports", class_="anchor",
    ))
    assert rc == 0
    out = _json.loads(capsys.readouterr().out)
    assert out["workspace"] == SENTINEL
    assert out["user_scoped"] is True

    rows = _rows(tmp_db)
    assert (SENTINEL, "user_prefers_plain_reports", "user", "body text") in rows
    assert not any(r[0] == "alpha" for r in rows)


def test_add_user_with_explicit_workspace_still_lands_in_sentinel(
    tmp_db, monkeypatch, capsys,
):
    rc = memory_mod._cmd_add(_add_args(
        name="user_explicit_workspace_ignored", workspace="alpha",
    ))
    assert rc == 0
    capsys.readouterr()
    workspaces = {r[0] for r in _rows(tmp_db)}
    assert workspaces == {SENTINEL}


# ---------------------------------------------------------------------------
# (b) readers reach the sentinel from any workspace, registered or not
# ---------------------------------------------------------------------------

def test_anchor_section_reads_user_row_from_other_workspace_and_unregistered_dir(
    tmp_db, tmp_path, monkeypatch, capsys,
):
    _in_workspace(monkeypatch, "alpha")
    assert memory_mod._cmd_add(_add_args(
        name="user_prefers_plain_reports", class_="anchor",
    )) == 0
    capsys.readouterr()

    _in_workspace(monkeypatch, "beta")
    assert "user_prefers_plain_reports" in _anchor_names(capsys)

    monkeypatch.delenv("GAIA_WORKSPACE")
    stray = tmp_path / "not_a_workspace"
    stray.mkdir()
    monkeypatch.chdir(stray)
    assert "user_prefers_plain_reports" in _anchor_names(capsys)


def test_list_and_show_reach_user_row_from_other_workspace(
    tmp_db, monkeypatch, capsys,
):
    _in_workspace(monkeypatch, "alpha")
    assert memory_mod._cmd_add(_add_args(name="user_prefers_plain_reports")) == 0
    capsys.readouterr()

    _in_workspace(monkeypatch, "beta")
    list_args = argparse.Namespace(
        workspace=None, type="user", audience=None, cls=None, status=None,
        sort=None, order=None, format="json", limit=None, json=True,
    )
    assert memory_mod._cmd_list(list_args) == 0
    listed = capsys.readouterr().out
    assert "user_prefers_plain_reports" in listed

    show_args = argparse.Namespace(
        workspace=None, name="user_prefers_plain_reports", links=False,
        history=False, json=True,
    )
    assert memory_mod._cmd_curated_show(show_args) == 0
    assert "user_prefers_plain_reports" in capsys.readouterr().out


def test_anchor_section_does_not_leak_other_workspace_rows(
    tmp_db, monkeypatch, capsys,
):
    _in_workspace(monkeypatch, "alpha")
    assert memory_mod._cmd_add(_add_args(
        name="atom_alpha_only", type="atom", class_="anchor",
        workspace="alpha",
    )) == 0
    capsys.readouterr()

    _in_workspace(monkeypatch, "beta")
    assert "atom_alpha_only" not in _anchor_names(capsys)


# ---------------------------------------------------------------------------
# (c) a name already in the sentinel is reported, never overwritten
# ---------------------------------------------------------------------------

def test_add_user_name_collision_is_reported_and_does_not_overwrite(
    tmp_db, monkeypatch, capsys,
):
    _in_workspace(monkeypatch, "alpha")
    assert memory_mod._cmd_add(_add_args(
        name="user_prefers_plain_reports", body="original body",
    )) == 0
    capsys.readouterr()

    _in_workspace(monkeypatch, "beta")
    rc = memory_mod._cmd_add(_add_args(
        name="user_prefers_plain_reports", body="clobbering body",
    ))
    assert rc == 1
    err = _json.loads(capsys.readouterr().out)
    assert err["code"] == "user_name_collision"
    assert "user_prefers_plain_reports" in err["error"]

    assert _rows(tmp_db) == [
        (SENTINEL, "user_prefers_plain_reports", "user", "original body"),
    ]


# ---------------------------------------------------------------------------
# (d) only type=user leaves the workspace axis
# ---------------------------------------------------------------------------

def test_add_non_user_type_keeps_the_callers_workspace(
    tmp_db, monkeypatch, capsys,
):
    rc = memory_mod._cmd_add(_add_args(
        name="atom_stays_in_alpha", type="atom", workspace="alpha",
    ))
    assert rc == 0
    out = _json.loads(capsys.readouterr().out)
    assert out["workspace"] == "alpha"
    assert ("alpha", "atom_stays_in_alpha", "atom", "body text") in _rows(tmp_db)


# ---------------------------------------------------------------------------
# (e) checkpoint of a user record: sentinel write + digest/count reads
# ---------------------------------------------------------------------------

def _user_checkpoint(tmp_path):
    payload = tmp_path / "payload.json"
    payload.write_text(_json.dumps({
        "resumen": {
            "name": "user_session_close", "type": "user",
            "description": "record", "body": "record body",
        },
        "pendientes": [{
            "name": "user_pending_thread", "description": "still open",
            "body": "pending body",
        }],
    }))
    return payload


def test_checkpoint_user_lands_in_sentinel_and_pending_reaches_other_workspace(
    tmp_db, tmp_path, monkeypatch, capsys,
):
    _in_workspace(monkeypatch, "alpha")
    rc = memory_mod._cmd_checkpoint(_checkpoint_args(
        _user_checkpoint(tmp_path), workspace="alpha", initiative="aos",
    ))
    assert rc == 0
    capsys.readouterr()
    assert {r[0] for r in _rows(tmp_db)} == {SENTINEL}

    _in_workspace(monkeypatch, "beta")
    rc = memory_mod._cmd_get_relevant(_get_relevant_args())
    assert rc == 0
    digest = _json.loads(capsys.readouterr().out)
    assert "user_pending_thread" in {i["name"] for i in digest["items"]}

    from gaia.store.reader import count_pending_by_initiative
    assert count_pending_by_initiative("beta", ["aos"]) == {"aos": 1}


# ---------------------------------------------------------------------------
# (f) relocate_memory: user rows move only into the sentinel
# ---------------------------------------------------------------------------

def _seed_row(workspace, name, type_):
    from gaia.store.writer import _connect, _ensure_workspace_row
    con = _connect()
    try:
        _ensure_workspace_row(con, workspace)
        con.execute(
            "INSERT INTO memory (workspace, name, type, body) "
            "VALUES (?, ?, ?, 'b')", (workspace, name, type_),
        )
        con.commit()
    finally:
        con.close()


def test_relocate_user_row_into_sentinel_is_allowed(tmp_db):
    from gaia.store.writer import relocate_memory
    _seed_row("me", "user_legacy_row", "user")
    result = relocate_memory("me", SENTINEL, ["user_legacy_row"])
    assert result["moved"] == ["user_legacy_row"]
    assert [r[:2] for r in _rows(tmp_db)] == [(SENTINEL, "user_legacy_row")]


def test_relocate_user_row_into_sentinel_reports_name_collision(tmp_db):
    from gaia.store.writer import relocate_memory
    _seed_row("me", "user_dup", "user")
    _seed_row(SENTINEL, "user_dup", "user")
    with pytest.raises(ValueError, match="already has memory"):
        relocate_memory("me", SENTINEL, ["user_dup"])
    skipped = relocate_memory("me", SENTINEL, ["user_dup"], on_conflict="skip")
    assert skipped["skipped"] == ["user_dup"]


def test_relocate_rejects_non_user_row_into_sentinel(tmp_db):
    from gaia.store.writer import relocate_memory, MemoryUserScopeError
    _seed_row("me", "atom_not_user", "atom")
    with pytest.raises(MemoryUserScopeError):
        relocate_memory("me", SENTINEL, ["atom_not_user"])
    assert [r[0] for r in _rows(tmp_db)] == ["me"]


def test_relocate_rejects_user_row_out_of_sentinel(tmp_db):
    from gaia.store.writer import relocate_memory, MemoryUserScopeError
    _seed_row(SENTINEL, "user_stays_put", "user")
    with pytest.raises(MemoryUserScopeError):
        relocate_memory(SENTINEL, "ws", ["user_stays_put"])
    assert [r[0] for r in _rows(tmp_db)] == [SENTINEL]


# ---------------------------------------------------------------------------
# (g) every reader and by-name verb finds a sentinel row without --workspace
# ---------------------------------------------------------------------------

def _seed_user_and_host_rows(monkeypatch, capsys):
    _in_workspace(monkeypatch, "alpha")
    assert memory_mod._cmd_add(_add_args(
        name="user_prefers_plain_reports", body="plain reports please",
        description="plain reports",
    )) == 0
    assert memory_mod._cmd_add(_add_args(
        name="user_second_note", body="second note", description="second",
    )) == 0
    assert memory_mod._cmd_add(_add_args(
        name="atom_host_note", type="atom", body="host note",
        description="host", initiative="gaia_system", workspace="alpha",
    )) == 0
    capsys.readouterr()
    _in_workspace(monkeypatch, "beta")


def test_search_reaches_user_row_from_other_workspace(
    tmp_db, monkeypatch, capsys,
):
    _seed_user_and_host_rows(monkeypatch, capsys)
    rc = memory_mod._cmd_search_scoped(argparse.Namespace(
        query="plain", scope="memory", limit=10, workspace=None, json=True,
    ))
    assert rc == 0
    names = {r["name"] for r in _json.loads(capsys.readouterr().out)["results"]}
    assert "user_prefers_plain_reports" in names


def test_legacy_types_get_relevant_reaches_user_row_from_other_workspace(
    tmp_db, monkeypatch, capsys,
):
    _seed_user_and_host_rows(monkeypatch, capsys)
    rc = memory_mod._cmd_get_relevant(_get_relevant_args(types="user"))
    assert rc == 0
    items = _json.loads(capsys.readouterr().out)["items"]
    assert {"user_prefers_plain_reports", "user_second_note"} <= {
        i["name"] for i in items
    }


def test_append_reclassify_link_story_resolve_sentinel_rows_without_workspace(
    tmp_db, monkeypatch, capsys,
):
    _seed_user_and_host_rows(monkeypatch, capsys)

    assert memory_mod._cmd_append(argparse.Namespace(
        name="user_prefers_plain_reports", body="and short", body_file=None,
        workspace=None, json=True,
    )) == 0
    assert memory_mod._cmd_append(argparse.Namespace(
        name="atom_host_note", body="host extra", body_file=None,
        workspace=None, json=True,
    )) == 0
    assert memory_mod._cmd_reclassify(argparse.Namespace(
        name="user_prefers_plain_reports", class_="anchor", status=None,
        workspace=None, json=True,
    )) == 0
    assert memory_mod._cmd_reclassify(argparse.Namespace(
        name="atom_host_note", class_="anchor", status=None,
        workspace=None, json=True,
    )) == 0
    assert memory_mod._cmd_link(argparse.Namespace(
        src_name="user_second_note", dst_name="user_prefers_plain_reports",
        kind="derived_from", delete=False, workspace=None, json=True,
    )) == 0
    capsys.readouterr()

    from cli.memory_story import _cmd_story
    assert _cmd_story(argparse.Namespace(
        name="user_prefers_plain_reports", max_depth=5, workspace=None,
        json=True,
    )) == 0
    assert "user_second_note" in capsys.readouterr().out

    rows = {(r[0], r[1]): r[3] for r in _rows(tmp_db)}
    assert rows[(SENTINEL, "user_prefers_plain_reports")].endswith("and short")
    assert rows[("_gaia_host", "atom_host_note")].endswith("host extra")
    assert not any(r[0] == "beta" for r in _rows(tmp_db))
