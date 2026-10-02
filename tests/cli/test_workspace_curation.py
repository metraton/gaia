"""Curating the workspace registry hides what nothing should list and loses no history.

Covers `gaia workspace curate` (phantoms, stale aliases, dangling facets),
`gaia workspace declare` over a retire alias, the integrations writer, the
`gaia doctor` registry check, `gaia brief set-project --force/--clear` and the
copy mark promotion gives a folded clone's project-context entry.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    data = tmp_path / "gaia_data"
    data.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    from gaia.paths import db_path
    from gaia.store.writer import _connect

    path = db_path()
    _connect(path).close()
    return path


def _sql(db: Path, statement: str, params=()) -> list[tuple]:
    con = sqlite3.connect(str(db))
    try:
        rows = con.execute(statement, params).fetchall()
        con.commit()
        return rows
    finally:
        con.close()


def _declared(db: Path, name: str, root: Path) -> Path:
    from gaia.store.writer import declare_workspace

    root.mkdir(parents=True, exist_ok=True)
    declare_workspace(name, root, db_path=db)
    return root


def _handoff(db: Path, workspace: str) -> None:
    _sql(db, "INSERT INTO agent_contract_handoffs (agent_id, workspace, agent_state, "
             "raw_handoff_json, created_at) VALUES ('a000001', ?, 'COMPLETE', '{}', "
             "'2026-01-01T00:00:00Z')", (workspace,))


def _curate(**kw) -> int:
    from bin.cli.workspace import _cmd_curate

    args = {"into": [], "dry_run": False, "yes": True, "json": False, **kw}
    return _cmd_curate(argparse.Namespace(**args))


def test_an_empty_phantom_is_hidden_and_kept(db, capsys):
    from gaia.store.provider import get_context

    _sql(db, "INSERT INTO workspaces (name) VALUES ('phantom')")
    assert get_context("phantom", db_path=db) is not None

    assert _curate(dry_run=True) == 0
    assert "hide phantom (status retired)" in capsys.readouterr().out
    assert _sql(db, "SELECT status FROM workspaces WHERE name = 'phantom'") == [("active",)]

    assert _curate() == 0
    assert get_context("phantom", db_path=db) is None
    assert _sql(db, "SELECT status FROM workspaces WHERE name = 'phantom'") == [("retired",)]


def test_a_phantom_with_collateral_is_retired_into_its_owner_and_its_history_stays_readable(
    db, tmp_path,
):
    from gaia.store.workspace_retire import alias_target, workspace_scope
    from gaia.store.writer import _connect

    root = _declared(db, "aaxis", tmp_path / "aaxis")
    _sql(db, "INSERT INTO workspaces (name) VALUES ('bildwiz')")
    _sql(db, "INSERT INTO projects (workspace, name, path, status) VALUES "
             "('bildwiz', 'platform', ?, 'active')", (str(root / "bildwiz" / "platform"),))
    _handoff(db, "bildwiz")

    assert _curate() == 0

    con = _connect(db)
    try:
        assert alias_target(con, "bildwiz") == "aaxis"
        assert "bildwiz" in workspace_scope(con, "aaxis")
    finally:
        con.close()
    assert _sql(db, "SELECT workspace FROM projects WHERE name = 'platform'") == [("aaxis",)]
    assert _sql(db, "SELECT status FROM workspaces WHERE name = 'bildwiz'") == [("retired",)]
    assert _sql(db, "SELECT COUNT(*) FROM agent_contract_handoffs WHERE workspace = 'bildwiz'") == [(1,)]


def test_a_phantom_owning_rows_with_no_owner_is_left_alone(db, capsys):
    _sql(db, "INSERT INTO workspaces (name) VALUES ('orphan')")
    _sql(db, "INSERT INTO briefs (workspace, name, status) VALUES ('orphan', 'b', 'open')")

    assert _curate() == 0
    assert "left orphan (briefs=1)" in capsys.readouterr().out
    assert _sql(db, "SELECT status FROM workspaces WHERE name = 'orphan'") == [("active",)]


def test_declaring_a_retired_name_makes_it_mean_itself(db, tmp_path, capsys):
    from bin.cli.workspace import _cmd_declare
    from gaia.store.workspace_retire import alias_target, apply_retire
    from gaia.store.writer import _connect

    _declared(db, "ws", tmp_path / "ws")
    _sql(db, "INSERT INTO workspaces (name) VALUES ('me')")
    apply_retire("me", "ws", db_path=db)
    (tmp_path / "ws" / "me").mkdir()
    args = argparse.Namespace(name="me", path=str(tmp_path / "ws" / "me"), dry_run=True)

    assert _cmd_declare(args) == 0
    assert "alias me -> ws would be dropped" in capsys.readouterr().out

    args.dry_run = False
    assert _cmd_declare(args) == 0
    con = _connect(db)
    try:
        assert alias_target(con, "me") == "me"
    finally:
        con.close()
    assert _sql(db, "SELECT status FROM workspaces WHERE name = 'me'") == [("active",)]


def test_a_stale_alias_left_by_an_earlier_declare_is_dropped(db, tmp_path):
    _declared(db, "ws", tmp_path / "ws")
    _declared(db, "me", tmp_path / "me")
    _sql(db, "INSERT INTO workspace_aliases (alias, target) VALUES ('me', 'ws')")

    assert _curate() == 0
    assert _sql(db, "SELECT * FROM workspace_aliases") == []


def test_an_integration_read_from_prose_is_refused_and_hidden(db):
    from gaia.store import save_integration
    from gaia.store.provider import get_context
    from gaia.store.writer import bulk_upsert

    _sql(db, "INSERT INTO workspaces (name) VALUES ('ws')")
    assert save_integration("ws", "the", kind="pkg", db_path=db)["status"] == "refused"
    assert save_integration("ws", "acli", kind="cli", version="1.2", db_path=db)["status"] == "applied"
    assert bulk_upsert("integrations", "ws", [{"name": "and"}], "gaia-system",
                       db_path=db)["rejected"] == 1
    _sql(db, "INSERT INTO integrations (workspace, name, kind) VALUES ('ws', 'or', 'pkg')")

    listed = [i["name"] for i in get_context("ws", db_path=db)["workspace"]["integrations"]]
    assert listed == ["acli"]
    assert _sql(db, "SELECT COUNT(*) FROM integrations WHERE name = 'or'") == [(1,)]


def test_a_facet_for_a_folder_that_is_gone_is_not_listed_and_curate_drops_it(db, tmp_path, capsys):
    from bin.cli.context import _cmd_project

    root = _declared(db, "ws", tmp_path / "ws")
    live = root / "repo-wt"
    live.mkdir()
    _sql(db, "INSERT INTO projects (workspace, name, path, status) VALUES "
             "('ws', 'repo', ?, 'active')", (str(root / "repo"),))
    _sql(db, "INSERT INTO project_facets (workspace, project, scope, key, value) VALUES "
             "('ws', 'repo', 'worktree', ?, 'main'), ('ws', 'repo', 'worktree', ?, 'gone')",
         (str(live), str(root / "repo-gone")))

    _cmd_project(argparse.Namespace(name="repo", workspace="ws", json=True))
    keys = [f["key"] for f in json.loads(capsys.readouterr().out)["facets"]]
    assert keys == [str(live)]

    assert _curate() == 0
    assert _sql(db, "SELECT key FROM project_facets") == [(str(live),)]


def test_doctor_warns_on_every_condition_and_passes_once_curated(db, tmp_path):
    from bin.cli import doctor

    root = _declared(db, "ws", tmp_path / "ws")
    _declared(db, "me", tmp_path / "me")
    _sql(db, "INSERT INTO workspace_aliases (alias, target) VALUES ('me', 'ws')")
    _sql(db, "INSERT INTO workspaces (name) VALUES ('old')")
    _sql(db, "INSERT INTO projects (workspace, name, path, status) VALUES "
             "('old', 'repo', ?, 'active')", (str(root / "repo"),))
    _sql(db, "INSERT INTO project_facets (workspace, project, scope, key) VALUES "
             "('old', 'repo', 'copy', ?)", (str(tmp_path / "gone"),))

    found = doctor.check_workspace_registry()
    assert found["severity"] == "warning"
    for fragment in ("old -> ws", "me -> ws", str(tmp_path / "gone")):
        assert fragment in found["detail"]

    assert _curate() == 0
    assert doctor.check_workspace_registry()["severity"] == "pass"


def test_prune_never_deletes_a_declared_or_retired_workspace(db, tmp_path):
    from gaia.store.writer import prune_empty_workspaces

    _declared(db, "me", tmp_path / "me")
    _sql(db, "INSERT INTO workspaces (name, status) VALUES ('gone', 'retired')")

    result = prune_empty_workspaces(apply=True, db_path=db)
    assert result["pruned"] == []
    assert {h["workspace"] for h in result["held"]} == {"me", "gone"}


def test_a_scan_does_not_bring_a_retired_workspace_back(db):
    from gaia.store.writer import mark_workspace_demoted, set_workspace_last_scan_at

    _sql(db, "INSERT INTO workspaces (name, status) VALUES ('gone', 'retired')")
    set_workspace_last_scan_at("gone", db_path=db)
    mark_workspace_demoted("gone", db_path=db)
    assert _sql(db, "SELECT status FROM workspaces WHERE name = 'gone'") == [("retired",)]


def test_brief_set_project_retags_with_force_and_clears(db):
    from gaia.briefs.store import set_brief_project

    _sql(db, "INSERT INTO workspaces (name) VALUES ('ws')")
    _sql(db, "INSERT INTO projects (workspace, name, project_identity, status) VALUES "
             "('ws', 'a', 'github.com/o/a', 'active'), ('ws', 'b', 'github.com/o/b', 'active')")
    _sql(db, "INSERT INTO briefs (workspace, name, status, project) VALUES "
             "('ws', 'feat', 'open', 'github.com/o/a')")

    with pytest.raises(ValueError, match="--force"):
        set_brief_project("ws", "feat", "b", db_path=db)
    set_brief_project("ws", "feat", "b", force=True, db_path=db)
    assert _sql(db, "SELECT project FROM briefs") == [("github.com/o/b",)]
    set_brief_project("ws", "feat", None, db_path=db)
    assert _sql(db, "SELECT project FROM briefs") == [(None,)]


def test_promotion_marks_a_folded_clone_entry_so_a_dispatch_from_it_lands_on_the_project(
    db, tmp_path,
):
    from tools.context.context_provider import resolve_dispatch_project
    from tools.scan.promote import promote_workspace

    root = _declared(db, "ws", tmp_path / "ws")
    project, clone = root / "app", root / "app-copy"
    clone.mkdir()
    _sql(db, "INSERT INTO projects (workspace, name, path, remote_url, project_identity, "
             "status) VALUES ('ws', 'app', ?, 'git@github.com:o/app.git', 'github.com/o/app', "
             "'active')", (str(project),))
    _sql(db, "INSERT INTO project_facets (workspace, project, scope, key) VALUES "
             "('ws', 'app', 'copy', ?)", (str(clone),))
    payload = {
        "app": {"name": "app", "local_path": str(project),
                "remote_url": "git@github.com:o/app.git"},
        "app_copy": {"name": "app-copy", "local_path": str(clone),
                     "remote_url": "git@github.com:o/app.git"},
    }
    _sql(db, "INSERT INTO project_context_contracts (workspace, contract_name, payload) "
             "VALUES ('ws', 'project_identity', ?)", (json.dumps(payload),))

    report = promote_workspace("ws", db_path=db)

    assert report["marked_copy_entries"] == 1
    assert resolve_dispatch_project("ws", str(clone), db_path=db) == f"app ({project})"
