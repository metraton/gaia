"""A dispatch into a project carries the workflow that project declared, and only that.

What the user feels: once a project's context entry declares how work lands
there -- directly or through a pull request, the target branch, the channel --
every specialist dispatched to that project reads it in its contract; a project
that declared nothing gets no guessed default; and re-scanning the workspace
does not erase the declaration. Runs against a temporary HOME, GAIA_DATA_DIR
and GAIA_DB.
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

WORKSPACE = "me"
DECLARED = {
    "integration": "pull_request",
    "target": "feat/release-train",
    "channel": "github.com/acme/alpha",
}


@pytest.fixture()
def db(tmp_path, monkeypatch, bootstrapped_db_template):
    data = tmp_path / "data"
    db = copy_bootstrapped_db(bootstrapped_db_template, data / "gaia.db")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(db))
    monkeypatch.chdir(tmp_path)
    return db


def _scan(db: Path, tmp_path: Path, name: str, *, remote: str) -> None:
    """Record ``name`` as a scanned repository and promote the workspace, as `gaia scan` does."""
    from gaia.store.writer import _connect, upsert_project

    con = _connect(db)
    con.execute(
        "INSERT OR REPLACE INTO agent_permissions (table_name, agent_name, allow_write) "
        "VALUES ('projects', 'gaia-system', 1)"
    )
    con.commit()
    con.close()
    upsert_project(
        WORKSPACE, name,
        {
            "project_identity": f"id/{name}",
            "path": str(tmp_path / name),
            "status": "active",
            "missing_since": None,
            "remote_url": remote,
        },
        "gaia-system", db_path=db, strip_agent_owned=True,
    )
    assert promote_workspace(WORKSPACE, db_path=db)["outcome"] in ("applied", "no-op")


def _slug_of(db: Path, name: str) -> str:
    from gaia.store.writer import _connect

    con = _connect(db)
    try:
        row = con.execute(
            "SELECT payload FROM project_context_contracts "
            "WHERE workspace = ? AND contract_name = 'project_identity'",
            (WORKSPACE,),
        ).fetchone()
    finally:
        con.close()
    entries = json.loads(row[0])
    return next(slug for slug, entry in entries.items() if entry.get("name") == name)


def _contract_lines(db: Path, project: str) -> list[str]:
    kernel = build_dispatch_kernel(
        {
            "contract_id": "a0123456789abcdef.beefcafe0123",
            "agent_id": "a0123456789abcdef",
            "workspace": WORKSPACE,
            "dispatch_prompt": "ship the fix",
            "kernel_sections": '{"role": "primary", "surface": "app_ci"}',
            "dispatch_project": resolve_project_by_name(WORKSPACE, project, db_path=db),
        },
        db_path=db,
    )
    return kernel.splitlines()


def test_only_the_project_that_declared_its_workflow_carries_it_and_a_rescan_keeps_it(
    db, tmp_path,
):
    _scan(db, tmp_path, "alpha", remote="git@github.com:acme/alpha.git")
    _scan(db, tmp_path, "beta", remote="git@github.com:acme/beta.git")
    declared = apply_update(
        {"contract": "project_identity",
         "payload": {_slug_of(db, "alpha"): {"workflow": DECLARED}}},
        "gaia-operator", workspace=WORKSPACE, db_path=db,
    )
    assert declared["success"], declared

    _scan(db, tmp_path, "alpha", remote="https://github.com/acme/alpha-moved.git")

    alpha = _contract_lines(db, "alpha")
    beta = _contract_lines(db, "beta")
    project_at = next(i for i, line in enumerate(alpha) if line.startswith("project: alpha"))
    workflow_line = alpha[project_at + 1]
    assert all(value in workflow_line for value in DECLARED.values())

    assert len(beta) == len(alpha) - 1
    assert not any(value in line for line in beta for value in DECLARED.values())


def test_a_workflow_declared_before_the_first_scan_reaches_the_kernel(db, tmp_path):
    declared = apply_update(
        {"contract": "project_identity", "payload": {"alpha": {"workflow": DECLARED}}},
        "gaia-operator", workspace=WORKSPACE, db_path=db,
    )
    assert declared["success"], declared

    _scan(db, tmp_path, "alpha", remote="git@github.com:acme/alpha.git")
    _scan(db, tmp_path, "beta", remote="git@github.com:acme/beta.git")

    alpha = _contract_lines(db, "alpha")
    project_at = next(i for i, line in enumerate(alpha) if line.startswith("project: alpha"))
    assert all(value in alpha[project_at + 1] for value in DECLARED.values())
