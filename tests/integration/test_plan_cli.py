"""
Integration tests for `gaia plan` CLI (DB-canonical).

Mirrors test_brief_cli.py / test_memory_cli.py:
  - Routes the substrate DB into ``tmp_path`` via ``GAIA_DATA_DIR``.
  - Pins cwd to ``tmp_path`` so any accidental filesystem write is detected.
  - Asserts zero filesystem side effects for every mutating verb.

Covers save / show / list / delete / set-status with their legal +
edge cases (missing brief, illegal transition, no plan attached, etc.).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pytest

# Ensure repo root and bin/ are importable
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Route the substrate DB into ``tmp_path``."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


def _seed_brief(tmp_db, name, status="draft"):
    from gaia.briefs import upsert_brief
    upsert_brief("me", name, {"status": status, "title": name},
                 db_path=tmp_db)


def _read_plan_row(db_path: Path, brief_name: str) -> dict | None:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT p.id, p.brief_id, p.status, p.content, p.updated_at "
            "FROM plans p JOIN briefs b ON b.id = p.brief_id "
            "WHERE b.name = ? AND b.workspace = 'me'",
            (brief_name,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


# ---------------------------------------------------------------------------
# save (insert + update)
# ---------------------------------------------------------------------------

def test_save_inserts_plan(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.plan import _cmd_save

    monkeypatch.chdir(tmp_path)
    _seed_brief(tmp_db, "feature-x")
    args = argparse.Namespace(
        brief="feature-x", content="initial plan body",
        status="draft", workspace="me", json=False,
    )
    rc = _cmd_save(args)
    assert rc == 0, capsys.readouterr()

    row = _read_plan_row(tmp_db, "feature-x")
    assert row is not None
    assert row["content"] == "initial plan body"
    assert row["status"] == "draft"


def test_save_updates_existing_plan(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.plan import _cmd_save

    monkeypatch.chdir(tmp_path)
    _seed_brief(tmp_db, "feature-x")
    base = dict(brief="feature-x", workspace="me", json=True)

    rc1 = _cmd_save(argparse.Namespace(content="v1", status="draft", **base))
    assert rc1 == 0
    payload1 = json.loads(capsys.readouterr().out)
    assert payload1["action"] == "inserted"

    rc2 = _cmd_save(argparse.Namespace(content="v2", status="active", **base))
    assert rc2 == 0
    payload2 = json.loads(capsys.readouterr().out)
    assert payload2["action"] == "updated"

    row = _read_plan_row(tmp_db, "feature-x")
    assert row["content"] == "v2"
    assert row["status"] == "active"

    # Only one row total
    con = sqlite3.connect(str(tmp_db))
    try:
        cnt = con.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
    finally:
        con.close()
    assert cnt == 1


def test_save_without_status_preserves_the_live_status(tmp_db, tmp_path,
                                                       monkeypatch, capsys):
    """Saving content on an ACTIVE plan must not demote it to 'draft'.

    The insert default was applied to the update path too, so every content
    save reset the lifecycle -- and `gaia plan set-status` is curator-only, so
    the planner could not repair what it had just broken.
    """
    from cli.plan import _cmd_save

    monkeypatch.chdir(tmp_path)
    _seed_brief(tmp_db, "feature-x")
    base = dict(brief="feature-x", workspace="me", json=True)

    _cmd_save(argparse.Namespace(content="v1", status="active", **base))
    capsys.readouterr()
    assert _read_plan_row(tmp_db, "feature-x")["status"] == "active"

    rc = _cmd_save(argparse.Namespace(content="v2", status=None, **base))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "updated"
    assert payload["plan_status"] == "active"

    row = _read_plan_row(tmp_db, "feature-x")
    assert row["content"] == "v2"
    assert row["status"] == "active", (
        "an update without --status must preserve the live status"
    )


def test_save_without_status_on_insert_is_draft(tmp_db, tmp_path,
                                                monkeypatch, capsys):
    """'draft' is still the default -- but only where there is nothing to
    preserve."""
    from cli.plan import _cmd_save

    monkeypatch.chdir(tmp_path)
    _seed_brief(tmp_db, "fresh-plan")
    rc = _cmd_save(argparse.Namespace(
        brief="fresh-plan", content="v1", status=None, workspace="me", json=True,
    ))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "inserted"
    assert payload["plan_status"] == "draft"
    assert _read_plan_row(tmp_db, "fresh-plan")["status"] == "draft"


def test_save_with_explicit_status_still_transitions(tmp_db, tmp_path,
                                                     monkeypatch, capsys):
    """Preservation is the DEFAULT, not a lock: an explicit --status wins."""
    from cli.plan import _cmd_save

    monkeypatch.chdir(tmp_path)
    _seed_brief(tmp_db, "feature-y")
    base = dict(brief="feature-y", workspace="me", json=True)

    _cmd_save(argparse.Namespace(content="v1", status="active", **base))
    capsys.readouterr()
    rc = _cmd_save(argparse.Namespace(content="v2", status="draft", **base))
    assert rc == 0
    capsys.readouterr()
    assert _read_plan_row(tmp_db, "feature-y")["status"] == "draft"


def test_save_brief_not_found(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.plan import _cmd_save

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        brief="ghost", content="anything", status="draft",
        workspace="me", json=False,
    )
    rc = _cmd_save(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "not found" in captured.err.lower()


def test_save_zero_fs_side_effects(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.plan import _cmd_save

    monkeypatch.chdir(tmp_path)
    _seed_brief(tmp_db, "fs-check")
    args = argparse.Namespace(
        brief="fs-check", content="body", status="draft",
        workspace="me", json=True,
    )
    rc = _cmd_save(args)
    assert rc == 0, capsys.readouterr()
    assert not (tmp_path / ".claude").exists()
    assert list(tmp_path.rglob("fs-check")) == []


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------

def test_show_returns_plan(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.plan import _cmd_show, _cmd_save

    monkeypatch.chdir(tmp_path)
    _seed_brief(tmp_db, "shown")
    _cmd_save(argparse.Namespace(brief="shown", content="show me",
                                 status="active", workspace="me", json=False))
    capsys.readouterr()  # discard

    args = argparse.Namespace(brief_name="shown", workspace="me", json=True)
    rc = _cmd_show(args)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["brief_name"] == "shown"
    assert payload["status"] == "active"
    assert payload["content"] == "show me"


def test_show_no_plan(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.plan import _cmd_show

    monkeypatch.chdir(tmp_path)
    _seed_brief(tmp_db, "lonely")
    args = argparse.Namespace(brief_name="lonely", workspace="me", json=False)
    rc = _cmd_show(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "no plan" in captured.err.lower()


def test_show_brief_not_found(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.plan import _cmd_show

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(brief_name="ghost", workspace="me", json=False)
    rc = _cmd_show(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "no plan" in captured.err.lower() or "not found" in captured.err.lower()


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def test_list_returns_all_plans(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.plan import _cmd_list, _cmd_save

    monkeypatch.chdir(tmp_path)
    for slug in ("plan-a", "plan-b", "plan-c"):
        _seed_brief(tmp_db, slug)
        _cmd_save(argparse.Namespace(brief=slug, content="body",
                                     status="draft", workspace="me",
                                     json=False))
    capsys.readouterr()

    args = argparse.Namespace(brief=None, status=None, format="json",
                              workspace="me")
    rc = _cmd_list(args)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    names = sorted(p["brief_name"] for p in payload)
    assert names == ["plan-a", "plan-b", "plan-c"]


def test_list_filter_by_brief(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.plan import _cmd_list, _cmd_save

    monkeypatch.chdir(tmp_path)
    for slug in ("filter-a", "filter-b"):
        _seed_brief(tmp_db, slug)
        _cmd_save(argparse.Namespace(brief=slug, content="b",
                                     status="draft", workspace="me",
                                     json=False))
    capsys.readouterr()

    args = argparse.Namespace(brief="filter-a", status=None,
                              format="count", workspace="me")
    rc = _cmd_list(args)
    assert rc == 0
    assert capsys.readouterr().out.strip() == "1"


def test_list_filter_by_status(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.plan import _cmd_list, _cmd_save

    monkeypatch.chdir(tmp_path)
    for slug, status in [("d-1", "draft"), ("a-1", "active"), ("a-2", "active")]:
        _seed_brief(tmp_db, slug)
        _cmd_save(argparse.Namespace(brief=slug, content="b", status=status,
                                     workspace="me", json=False))
    capsys.readouterr()

    args = argparse.Namespace(brief=None, status="active",
                              format="count", workspace="me")
    rc = _cmd_list(args)
    assert rc == 0
    assert capsys.readouterr().out.strip() == "2"


# ---------------------------------------------------------------------------
# delete (does not affect the brief)
# ---------------------------------------------------------------------------

def test_delete_with_yes_removes_plan_keeps_brief(tmp_db, tmp_path,
                                                  monkeypatch, capsys):
    from cli.plan import _cmd_delete, _cmd_save
    from gaia.briefs import get_brief

    monkeypatch.chdir(tmp_path)
    _seed_brief(tmp_db, "deletable")
    _cmd_save(argparse.Namespace(brief="deletable", content="body",
                                 status="draft", workspace="me", json=False))
    capsys.readouterr()

    args = argparse.Namespace(brief_name="deletable", workspace="me",
                              yes=True, json=False)
    rc = _cmd_delete(args)
    assert rc == 0, capsys.readouterr()

    assert _read_plan_row(tmp_db, "deletable") is None
    # Brief untouched
    assert get_brief("me", "deletable", db_path=tmp_db) is not None
    # Zero filesystem side effects
    assert not (tmp_path / ".claude").exists()


def test_delete_aborts_on_no(tmp_db, tmp_path, monkeypatch, capsys):
    import builtins
    from cli.plan import _cmd_delete, _cmd_save

    monkeypatch.chdir(tmp_path)
    _seed_brief(tmp_db, "keepme")
    _cmd_save(argparse.Namespace(brief="keepme", content="b",
                                 status="draft", workspace="me", json=False))
    capsys.readouterr()
    monkeypatch.setattr(builtins, "input", lambda *a, **kw: "n")

    args = argparse.Namespace(brief_name="keepme", workspace="me",
                              yes=False, json=False)
    rc = _cmd_delete(args)
    assert rc == 0
    out = capsys.readouterr().out.lower()
    assert "abort" in out or "not deleted" in out
    assert _read_plan_row(tmp_db, "keepme") is not None


def test_delete_no_plan_attached(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.plan import _cmd_delete

    monkeypatch.chdir(tmp_path)
    _seed_brief(tmp_db, "lonely")
    args = argparse.Namespace(brief_name="lonely", workspace="me",
                              yes=True, json=False)
    rc = _cmd_delete(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "no plan" in captured.err.lower()


# ---------------------------------------------------------------------------
# set-status
# ---------------------------------------------------------------------------

def test_set_status_legal_transition(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.plan import _cmd_set_status, _cmd_save

    monkeypatch.chdir(tmp_path)
    _seed_brief(tmp_db, "transit")
    _cmd_save(argparse.Namespace(brief="transit", content="b",
                                 status="draft", workspace="me", json=False))
    capsys.readouterr()

    args = argparse.Namespace(brief_name="transit", new_status="active",
                              workspace="me", json=False)
    rc = _cmd_set_status(args)
    assert rc == 0, capsys.readouterr()

    assert _read_plan_row(tmp_db, "transit")["status"] == "active"


def test_set_status_illegal_transition(tmp_db, tmp_path, monkeypatch, capsys):
    """draft -> closed is illegal; draft must go through 'active' first."""
    from cli.plan import _cmd_set_status, _cmd_save

    monkeypatch.chdir(tmp_path)
    _seed_brief(tmp_db, "stuck")
    _cmd_save(argparse.Namespace(brief="stuck", content="b",
                                 status="draft", workspace="me", json=False))
    capsys.readouterr()

    args = argparse.Namespace(brief_name="stuck", new_status="closed",
                              workspace="me", json=False)
    rc = _cmd_set_status(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "illegal" in captured.err.lower()
    # State unchanged
    assert _read_plan_row(tmp_db, "stuck")["status"] == "draft"


def test_set_status_brief_not_found(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.plan import _cmd_set_status

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(brief_name="ghost", new_status="active",
                              workspace="me", json=False)
    rc = _cmd_set_status(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "not found" in captured.err.lower()


def test_set_status_no_plan(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.plan import _cmd_set_status

    monkeypatch.chdir(tmp_path)
    _seed_brief(tmp_db, "no-plan-here")
    args = argparse.Namespace(brief_name="no-plan-here", new_status="active",
                              workspace="me", json=False)
    rc = _cmd_set_status(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "no plan" in captured.err.lower()


def test_set_status_idempotent_noop(tmp_db, tmp_path, monkeypatch, capsys):
    """Setting the current status returns action='noop'."""
    from cli.plan import _cmd_set_status, _cmd_save

    monkeypatch.chdir(tmp_path)
    _seed_brief(tmp_db, "idem")
    _cmd_save(argparse.Namespace(brief="idem", content="b",
                                 status="draft", workspace="me", json=False))
    capsys.readouterr()

    args = argparse.Namespace(brief_name="idem", new_status="draft",
                              workspace="me", json=True)
    rc = _cmd_set_status(args)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "noop"


# ---------------------------------------------------------------------------
# save --content-file (the large-body lane)
# ---------------------------------------------------------------------------
# A plan narrative is routinely tens of kilobytes of markdown carrying quotes,
# backticks and '$'. Passing that through one shell-quoted --content risks
# corrupting the plan of record on a single unescaped character, so the body
# must be reachable from a file. These cases pin the file lane's fidelity
# (byte-for-byte, no shell in the path) and its error surface.

def _save_parser():
    """Return the nested `save` subparser as `register` wires it."""
    import cli.plan as plan_mod

    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="subcommand")
    plan_mod.register(subs)
    plan_parser = subs.choices["plan"]
    nested = next(
        a for a in plan_parser._actions
        if isinstance(a, argparse._SubParsersAction)
    )
    return nested.choices["save"]


def test_save_reads_multikilobyte_body_from_content_file(
    tmp_db, tmp_path, monkeypatch, capsys,
):
    from cli.plan import _cmd_save

    monkeypatch.chdir(tmp_path)
    _seed_brief(tmp_db, "feature-big")

    body = "\n".join(
        f"## Row {i}\n\nBody line {i} with detail text to give the file bulk.\n"
        for i in range(400)
    )
    assert len(body) > 20_000, "fixture must exceed the inline-argument regime"
    body_file = tmp_path / "plan_body.md"
    body_file.write_text(body, encoding="utf-8")

    args = argparse.Namespace(
        brief="feature-big", content=None, content_file=str(body_file),
        status="draft", workspace="me", json=False,
    )
    rc = _cmd_save(args)
    assert rc == 0, capsys.readouterr()

    row = _read_plan_row(tmp_db, "feature-big")
    assert row["content"] == body


def test_save_content_file_preserves_shell_hostile_characters(
    tmp_db, tmp_path, monkeypatch, capsys,
):
    from cli.plan import _cmd_save

    monkeypatch.chdir(tmp_path)
    _seed_brief(tmp_db, "feature-quotes")

    body = (
        "# Plan\n\n"
        "Single 'quoted' and \"double quoted\" text.\n"
        "Inline `backticks` and a fenced block:\n\n"
        "```bash\n"
        "echo \"$HOME\" && echo '$(whoami)'\n"
        "```\n\n"
        "A literal $VAR and a trailing backslash \\\n"
    )
    body_file = tmp_path / "hostile.md"
    body_file.write_text(body, encoding="utf-8")

    rc = _cmd_save(argparse.Namespace(
        brief="feature-quotes", content=None, content_file=str(body_file),
        status="draft", workspace="me", json=False,
    ))
    assert rc == 0, capsys.readouterr()
    assert _read_plan_row(tmp_db, "feature-quotes")["content"] == body


def test_save_content_file_missing_path_errors(
    tmp_db, tmp_path, monkeypatch, capsys,
):
    from cli.plan import _cmd_save

    monkeypatch.chdir(tmp_path)
    _seed_brief(tmp_db, "feature-x")

    missing = tmp_path / "nope.md"
    rc = _cmd_save(argparse.Namespace(
        brief="feature-x", content=None, content_file=str(missing),
        status="draft", workspace="me", json=True,
    ))
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert "--content-file" in payload["error"]
    assert _read_plan_row(tmp_db, "feature-x") is None


def test_save_without_either_content_flag_errors(
    tmp_db, tmp_path, monkeypatch, capsys,
):
    from cli.plan import _cmd_save

    monkeypatch.chdir(tmp_path)
    _seed_brief(tmp_db, "feature-x")

    rc = _cmd_save(argparse.Namespace(
        brief="feature-x", content=None, content_file=None,
        status="draft", workspace="me", json=True,
    ))
    assert rc == 1
    assert "--content-file" in json.loads(capsys.readouterr().out)["error"]


def test_save_content_and_content_file_are_mutually_exclusive():
    save_p = _save_parser()

    with pytest.raises(SystemExit):
        save_p.parse_args([
            "--brief", "b", "--content", "inline", "--content-file", "/tmp/x.md",
        ])

    with pytest.raises(SystemExit):
        save_p.parse_args(["--brief", "b"])

    assert save_p.parse_args(
        ["--brief", "b", "--content-file", "/tmp/x.md"]
    ).content_file == "/tmp/x.md"


# ---------------------------------------------------------------------------
# Subcommand registration
# ---------------------------------------------------------------------------

def test_register_wires_subcommands():
    import cli.plan as plan_mod

    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="subcommand")
    plan_mod.register(subs)

    plan_parser = subs.choices["plan"]
    nested = None
    for action in plan_parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            nested = action
            break
    assert nested is not None
    for verb in ("save", "show", "list", "delete", "set-status"):
        assert verb in nested.choices, f"missing verb: {verb}"
