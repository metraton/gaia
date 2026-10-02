"""Moving a project between declared workspaces moves the project, and everything of it follows.

What the user feels: after `gaia project move alpha --into b`, workspace b reads
alpha's memory, the brief written for alpha and alpha's context entry with its
declared workflow, and a dispatch to alpha carries that workflow; the brief that
belonged to workspace a as a whole stays in a, and no memory row was re-keyed.
A dry-run says per table what would move and what would stay and writes
nothing; an undeclared target is refused.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tests.conftest import copy_bootstrapped_db

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from modules.context.context_writer import apply_update  # noqa: E402
from modules.context.kernel_builder import build_dispatch_kernel  # noqa: E402
from tools.context.context_provider import resolve_project_by_name  # noqa: E402
from tools.scan.promote import promote_workspace  # noqa: E402

IDENTITY = "github.com/acme/alpha"
WORKFLOW = {"integration": "pull_request", "target": "feat/train"}


def _query(db: Path, sql: str, params: tuple = ()) -> list[tuple]:
    from gaia.store.writer import _connect

    con = _connect(db)
    try:
        return [tuple(r) for r in con.execute(sql, params)]
    finally:
        con.close()


@pytest.fixture()
def alpha_in_a(tmp_path, monkeypatch, bootstrapped_db_template):
    """Project alpha in workspace a with memory, a project brief, a workspace brief and a ficha with a workflow."""
    from gaia.briefs import upsert_brief
    from gaia.store.writer import _connect, declare_workspace, upsert_project

    data = tmp_path / "data"
    db = copy_bootstrapped_db(bootstrapped_db_template, data / "gaia.db")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(db))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        declare_workspace(name, tmp_path / name, db_path=db)

    con = _connect(db)
    con.execute(
        "INSERT OR REPLACE INTO agent_permissions (table_name, agent_name, allow_write) "
        "VALUES ('projects', 'gaia-system', 1)"
    )
    con.execute(
        "INSERT INTO memory (workspace, name, type, description, body, project_ref, "
        "initiative, class, status) VALUES ('a', 'thread_alpha_next', 'project', "
        "'next step', 'ship it', ?, 'alpha', 'thread', 'open')",
        (IDENTITY,),
    )
    con.commit()
    con.close()

    upsert_project(
        "a", "alpha",
        {"project_identity": IDENTITY, "path": str(tmp_path / "a" / "alpha"),
         "remote_url": "git@github.com:acme/alpha.git", "status": "active"},
        "gaia-system", db_path=db, strip_agent_owned=True,
    )
    assert promote_workspace("a", db_path=db)["outcome"] == "applied"
    slug = next(iter(json.loads(_query(
        db, "SELECT payload FROM project_context_contracts "
            "WHERE workspace = 'a' AND contract_name = 'project_identity'",
    )[0][0])))
    declared = apply_update(
        {"contract": "project_identity", "payload": {slug: {"workflow": WORKFLOW}}},
        "gaia-operator", workspace="a", db_path=db,
    )
    assert declared["success"], declared

    upsert_brief("a", "alpha-feature", {"title": "Alpha feature", "project": IDENTITY}, db_path=db)
    upsert_brief("a", "a-roadmap", {"title": "Roadmap of a"}, db_path=db)
    return db


def _kernel_from(db: Path, workspace: str) -> str:
    return build_dispatch_kernel(
        {
            "contract_id": "a0123456789abcdef.beefcafe0123",
            "agent_id": "a0123456789abcdef",
            "workspace": workspace,
            "dispatch_prompt": "ship the fix",
            "kernel_sections": '{"role": "primary", "surface": "app_ci"}',
            "dispatch_project": resolve_project_by_name(workspace, "alpha", db_path=db),
        },
        db_path=db,
    )


def test_after_the_move_b_reads_everything_of_alpha_and_a_keeps_its_own(alpha_in_a):
    from gaia.briefs import get_brief
    from gaia.store.project_move import move_project
    from gaia.store.reader import pending_threads_by_project
    from tools.context.context_provider import _project_identity_entries

    db = alpha_in_a
    memory_keys_before = _query(db, "SELECT workspace, name FROM memory ORDER BY name")

    move_project("alpha", "b", db_path=db)

    assert _query(db, "SELECT workspace, name FROM projects WHERE project_identity = ?",
                  (IDENTITY,)) == [("b", "alpha")]
    assert get_brief("b", "alpha-feature", db_path=db) is not None
    assert get_brief("a", "alpha-feature", db_path=db) is None
    assert get_brief("a", "a-roadmap", db_path=db) is not None
    assert get_brief("b", "a-roadmap", db_path=db) is None

    assert _query(db, "SELECT workspace, name FROM memory ORDER BY name") == memory_keys_before
    assert [r["name"] for r in pending_threads_by_project(["alpha"], db_path=db)] == [
        "thread_alpha_next"
    ]

    b_entries = [e for e in _project_identity_entries("b", db_path=db).values()
                 if isinstance(e, dict) and e.get("name") == "alpha"]
    assert [e.get("workflow") for e in b_entries] == [WORKFLOW]
    assert not any(isinstance(e, dict) and e.get("name") == "alpha"
                   for e in _project_identity_entries("a", db_path=db).values())

    for dispatch_workspace in ("a", "b"):
        lines = _kernel_from(db, dispatch_workspace).splitlines()
        at = next(i for i, line in enumerate(lines) if line.startswith("project: alpha ("))
        assert all(value in lines[at + 1] for value in WORKFLOW.values()), lines


def test_the_dry_run_lists_what_moves_and_what_stays_and_writes_nothing(alpha_in_a):
    from gaia.store.project_move import move_project

    db = alpha_in_a
    tables = ("projects", "briefs", "project_context_contracts", "memory")
    before = {t: _query(db, f"SELECT * FROM {t} ORDER BY 1, 2") for t in tables}

    report = move_project("alpha", "b", dry_run=True, db_path=db)

    assert report["mode"] == "dry-run"
    assert report["tables"]["briefs"] == {"moves": ["alpha-feature"], "stays": ["a-roadmap"]}
    assert report["tables"]["projects"]["moves"] == ["alpha"]
    assert report["tables"]["project_context_contracts"]["moves"] == ["project_identity.alpha"]
    assert report["tables"]["memory"] == {"moves": 0, "stays": 1, "read_through_project": True}
    assert {t: _query(db, f"SELECT * FROM {t} ORDER BY 1, 2") for t in tables} == before


def test_an_undeclared_target_an_unknown_and_an_ambiguous_project_are_refused(alpha_in_a):
    from gaia.store.project_move import ProjectMoveError, move_project
    from gaia.store.writer import upsert_project

    db = alpha_in_a
    with pytest.raises(ProjectMoveError, match="gaia workspace declare"):
        move_project("alpha", "nowhere", db_path=db)
    with pytest.raises(ProjectMoveError, match="no project 'ghost'"):
        move_project("ghost", "b", db_path=db)

    upsert_project("b", "Alpha", {"project_identity": "github.com/other/alpha",
                                  "status": "active"}, "gaia-system", db_path=db)
    with pytest.raises(ProjectMoveError) as refused:
        move_project("alpha", "b", db_path=db)
    assert refused.value.candidates == ["a/alpha", "b/Alpha"]
    assert _query(db, "SELECT workspace FROM projects WHERE project_identity = ?",
                  (IDENTITY,)) == [("a",)]
