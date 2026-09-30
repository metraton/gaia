"""The store writer, not only the CLI, keeps curated memory append-only.

An existing name is written again only with explicit replace intent, a
type=user name is never overwritten nor restored, and a session checkpoint
never rewrites nor restores a row: changed knowledge travels as a new row that
supersedes the old one, and the whole checkpoint stays all-or-nothing.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (_REPO_ROOT, _REPO_ROOT / "bin"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from gaia.store import writer  # noqa: E402

USER = "_gaia_user"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Route the substrate DB into tmp_path -- never the real ~/.gaia/gaia.db."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for key in ("GAIA_DISPATCH_AGENT", "GAIA_SESSION_ID", "GAIA_WORKSPACE",
                "GAIA_DISPATCH_WORKSPACE", "GAIA_DB"):
        monkeypatch.delenv(key, raising=False)
    from gaia.paths import db_path
    return db_path()


def _rows(db_path: Path) -> dict[tuple[str, str], tuple]:
    con = sqlite3.connect(str(db_path))
    try:
        return {
            (ws, name): (body, deleted_at) for ws, name, body, deleted_at in con.execute(
                "SELECT workspace, name, body, deleted_at FROM memory"
            )
        }
    finally:
        con.close()


def _links(db_path: Path) -> set[tuple[str, str, str]]:
    con = sqlite3.connect(str(db_path))
    try:
        return set(con.execute("SELECT src_name, dst_name, kind FROM memory_links"))
    finally:
        con.close()


def _checkpoint(resumen: dict, pendientes: list | None = None):
    return writer.close_session_memory(
        "me", {"resumen": resumen, "pendientes": pendientes or []},
        initiative="bildwiz",
    )


def test_upsert_refuses_an_existing_name_without_replace_intent(db):
    writer.upsert_memory("me", "atom_cadence", type="atom", body="weekly",
                         initiative="bildwiz")

    with pytest.raises(writer.MemoryNameExistsError):
        writer.upsert_memory("me", "atom_cadence", type="atom", body="daily",
                             initiative="bildwiz")

    assert _rows(db)[("me", "atom_cadence")][0] == "weekly"
    writer.upsert_memory("me", "atom_cadence", type="atom", body="daily",
                         initiative="bildwiz", replace=True)
    assert _rows(db)[("me", "atom_cadence")][0] == "daily"


def test_upsert_does_not_restore_a_deleted_row_without_replace_intent(db):
    writer.upsert_memory("me", "atom_cadence", type="atom", body="weekly",
                         initiative="bildwiz")
    writer.delete_memory("me", "atom_cadence")

    with pytest.raises(writer.MemoryNameExistsError):
        writer.upsert_memory("me", "atom_cadence", type="atom", body="daily",
                             initiative="bildwiz")

    assert _rows(db)[("me", "atom_cadence")][1] is not None


def test_replace_does_not_restore_a_deleted_row(db):
    writer.upsert_memory("me", "atom_cadence", type="atom", body="weekly",
                         initiative="bildwiz")
    writer.delete_memory("me", "atom_cadence")

    with pytest.raises(writer.MemoryNameExistsError):
        writer.upsert_memory("me", "atom_cadence", type="atom", body="daily",
                             initiative="bildwiz", replace=True)

    assert _rows(db)[("me", "atom_cadence")][1] is not None


def test_append_to_a_deleted_row_is_refused(db):
    writer.upsert_memory("me", "atom_cadence", type="atom", body="weekly",
                         initiative="bildwiz")
    writer.delete_memory("me", "atom_cadence")

    with pytest.raises(ValueError, match="deleted"):
        writer.update_memory_field("me", "atom_cadence", "body", "daily")

    assert _rows(db)[("me", "atom_cadence")][0] == "weekly"


def test_append_refuses_a_row_deleted_between_check_and_write(db, monkeypatch):
    writer.upsert_memory("me", "atom_cadence", type="atom", body="weekly",
                         initiative="bildwiz")
    checked = writer._live_memory_row

    def check_then_delete(con, workspace, name, columns):
        row = checked(con, workspace, name, columns)
        con.execute("UPDATE memory SET deleted_at = '2026-09-30T00:00:00Z' "
                    "WHERE workspace = ? AND name = ?", (workspace, name))
        con.commit()
        return row

    monkeypatch.setattr(writer, "_live_memory_row", check_then_delete)

    with pytest.raises(ValueError, match="deleted before the append"):
        writer.update_memory_field("me", "atom_cadence", "body", "daily")

    assert _rows(db)[("me", "atom_cadence")][0] == "weekly"


@pytest.mark.parametrize("deleted_end", ["src", "dst"])
def test_link_refuses_a_deleted_row_at_either_end(db, deleted_end):
    for name in ("atom_new", "atom_old"):
        writer.upsert_memory("me", name, type="atom", body=name, initiative="bildwiz")
    writer.delete_memory("me", "atom_new" if deleted_end == "src" else "atom_old")

    with pytest.raises(ValueError, match=f"{deleted_end} memory .* is deleted"):
        writer.insert_memory_link("me", "atom_new", "atom_old", "supersedes")

    assert _links(db) == set()


def test_update_memory_field_appends_and_never_overwrites(db):
    writer.upsert_memory("me", "atom_cadence", type="atom", body="weekly",
                         initiative="bildwiz")

    writer.update_memory_field("me", "atom_cadence", "body", "daily")

    assert _rows(db)[("me", "atom_cadence")][0] == "weekly\n\ndaily"


def test_replace_with_a_failing_class_step_writes_nothing(db):
    writer.upsert_memory("me", "atom_cadence", type="atom", body="weekly",
                         initiative="bildwiz")

    with pytest.raises(ValueError, match="status only applies"):
        writer.upsert_memory("me", "atom_cadence", type="atom", body="daily",
                             initiative="bildwiz", status="open", replace=True)

    assert _rows(db)[("me", "atom_cadence")][0] == "weekly"


def test_a_deleted_user_row_is_never_restored_even_with_replace(db):
    writer.upsert_memory("me", "user_prefers_plain", type="user", body="plain")
    writer.delete_memory(USER, "user_prefers_plain")

    with pytest.raises(writer.MemoryUserScopeError) as exc:
        writer.upsert_memory("me", "user_prefers_plain", type="user",
                             body="rich", replace=True)

    assert exc.value.code == "user_name_collision"
    assert _rows(db)[(USER, "user_prefers_plain")][1] is not None


def test_checkpoint_with_an_existing_name_is_refused_whole(db):
    writer.upsert_memory("me", "thread_bildwiz_release", type="project",
                         body="old", initiative="bildwiz", class_="thread")

    with pytest.raises(writer.MemoryNameExistsError):
        _checkpoint(
            {"name": "project_session_close", "type": "project", "body": "record"},
            [{"name": "thread_bildwiz_release", "body": "rewritten"}],
        )

    rows = _rows(db)
    assert rows[("me", "thread_bildwiz_release")][0] == "old"
    assert ("me", "project_session_close") not in rows


def test_checkpoint_cannot_restore_a_deleted_user_row(db):
    writer.upsert_memory("me", "user_session_close", type="user", body="first")
    writer.delete_memory(USER, "user_session_close")

    with pytest.raises(writer.MemoryUserScopeError) as exc:
        _checkpoint({"name": "user_session_close", "type": "user", "body": "again"})

    assert exc.value.code == "user_name_collision"
    body, deleted_at = _rows(db)[(USER, "user_session_close")]
    assert body == "first"
    assert deleted_at is not None


def test_checkpoint_carries_changed_knowledge_as_a_superseding_row(db):
    writer.upsert_memory("me", "project_release_weekly", type="project",
                         body="weekly", initiative="bildwiz")

    _checkpoint({"name": "project_release_daily", "type": "project",
                 "body": "daily", "supersedes": "project_release_weekly"})

    assert _rows(db)[("me", "project_release_weekly")][0] == "weekly"
    assert ("project_release_daily", "project_release_weekly", "supersedes") in _links(db)


def test_checkpoint_superseding_a_missing_row_writes_nothing(db):
    with pytest.raises(ValueError):
        _checkpoint({"name": "project_release_daily", "type": "project",
                     "body": "daily", "supersedes": "project_never_written"})

    assert _rows(db) == {}


def test_cli_add_refuses_when_the_existence_lookup_fails(db, monkeypatch, capsys):
    from cli import memory as memory_mod

    writer.upsert_memory("me", "atom_cadence", type="atom", body="weekly",
                         initiative="bildwiz")

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(writer, "get_memory", broken)
    rc = memory_mod._cmd_add(argparse.Namespace(
        name="atom_cadence", type="atom", body="daily", body_file=None,
        description=None, workspace="me", class_=None, status=None,
        project=None, project_ref=None, audience=None, initiative="bildwiz",
        replace=True, json=True,
    ))

    assert rc == 1
    assert json.loads(capsys.readouterr().out)["code"] == "name_check_failed"
    assert _rows(db)[("me", "atom_cadence")][0] == "weekly"


@pytest.mark.parametrize("replace", [False, True])
def test_cli_add_over_a_user_name_is_a_collision(db, capsys, replace):
    from cli import memory as memory_mod

    writer.upsert_memory("me", "user_prefers_plain", type="user", body="plain")
    rc = memory_mod._cmd_add(argparse.Namespace(
        name="user_prefers_plain", type="user", body="rich", body_file=None,
        description=None, workspace=None, class_=None, status=None,
        project=None, project_ref=None, audience=None, initiative=None,
        replace=replace, json=True,
    ))

    assert rc == 1
    assert json.loads(capsys.readouterr().out)["code"] == "user_name_collision"
    assert _rows(db)[(USER, "user_prefers_plain")][0] == "plain"
