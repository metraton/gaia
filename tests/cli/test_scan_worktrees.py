"""
A linked git worktree is a VIEW of a repository, not a project of its own.

``resolve_project_identity`` fingerprints a repo by its git-common-dir, which is
IDENTICAL for a repo and every worktree linked to it. Discovery used to accept
any ``.git`` entry -- directory or file -- as a repo, so a worktree reached
``upsert_project`` carrying the base repo's identity, took the writer's
identity-collapse branch, and UPDATED the base repo's row in place: its
``path``, ``remote_url``, ``primary_language``, ``role`` and ``group_name`` were
replaced with the worktree's, and because discovery is sorted, the worktree
(``<repo>-wt-<x>``, sorting after ``<repo>``) always won.

The fix has two halves, and this module covers both:

  * EXCLUDE -- ``_list_repos`` drops linked worktrees, so the base repo keeps
    its own row and its own path even when the worktree is processed after it.
  * PLACE   -- the worktree is not dropped silently: it is recorded as a
    ``worktree``-scope row in ``project_facets``, derived from the base repo's
    row, with the branch it holds as the value.

A SUBMODULE also replaces its ``.git`` directory with a gitfile but is NOT a
worktree (it owns its own repository), so it must keep behaving exactly as
before -- covered here so the exclusion cannot widen onto it.

Test isolation: real ``git init`` / ``git worktree add`` / ``git submodule add``
trees under tmp_path, GAIA_DATA_DIR redirected to a temp dir, and the explicit
db_path threaded through every scan call -- the real ~/.gaia/gaia.db is never
touched.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.scan import classify as classify_mod
from tools.scan.store_populator import (
    _list_repos,
    is_linked_worktree,
    worktree_facets,
)


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_dir = tmp_path / "gaia_data"
    db_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(db_dir))
    from gaia.paths import db_path
    return db_path()


_GIT_IDENTITY = [
    "-c", "user.email=scan-test@example.invalid",
    "-c", "user.name=scan-test",
]


def _git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(path), *_GIT_IDENTITY, *args],
        capture_output=True, text=True, check=True,
    )


def _init_repo(path: Path, remote: str | None = None) -> Path:
    """Create a real git repo at ``path`` with one commit and an optional remote."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet", str(path)], check=True,
                   capture_output=True, text=True)
    _git(path, "commit", "--quiet", "--allow-empty", "-m", "init")
    if remote:
        _git(path, "remote", "add", "origin", remote)
    return path


def _add_worktree(base: Path, target: Path, branch: str) -> Path:
    _git(base, "worktree", "add", "--quiet", "-b", branch, str(target))
    return target


def _project_rows(db_path: Path, workspace: str):
    """Return ``[(name, path), ...]`` for a workspace, ordered by name."""
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT name, path FROM projects WHERE workspace = ? ORDER BY name",
            (workspace,),
        ).fetchall()
    finally:
        con.close()
    return [(r["name"], r["path"]) for r in rows]


def _facet_rows(db_path: Path, workspace: str, project: str, scope: str):
    """Return ``[(key, value), ...]`` of one scope for a project."""
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT key, value FROM project_facets "
            "WHERE workspace = ? AND project = ? AND scope = ? ORDER BY key",
            (workspace, project, scope),
        ).fetchall()
    finally:
        con.close()
    return [(r["key"], r["value"]) for r in rows]


@pytest.fixture()
def workspace_with_worktree(tmp_path):
    """A workspace holding one plain repo, one repo, and that repo's worktree.

    Layout (``root`` is the workspace segment matched by ``W``)::

        <tmp>/century/                      <- W = "century"
            plain-repo/                     <- ordinary repo
            widget/                         <- base repo
            widget-wt-gateway/              <- LINKED WORKTREE of widget

    ``widget-wt-gateway`` sorts AFTER ``widget``, so discovery reaches it second
    -- the exact ordering that made the worktree overwrite the base repo's row.
    """
    root = tmp_path / "century"
    root.mkdir()
    plain = _init_repo(root / "plain-repo", remote="git@github.com:acme/plain-repo.git")
    base = _init_repo(root / "widget", remote="git@github.com:acme/widget.git")
    worktree = _add_worktree(base, root / "widget-wt-gateway", "gateway")
    return {"root": root, "plain": plain, "base": base, "worktree": worktree}


# ---------------------------------------------------------------------------
# Detection: worktree vs repo vs submodule
# ---------------------------------------------------------------------------

def test_is_linked_worktree_true_only_for_the_worktree(workspace_with_worktree):
    """git-dir != git-common-dir isolates the worktree from ordinary repos."""
    assert is_linked_worktree(workspace_with_worktree["worktree"]) is True
    assert is_linked_worktree(workspace_with_worktree["base"]) is False
    assert is_linked_worktree(workspace_with_worktree["plain"]) is False


def test_submodule_is_not_treated_as_a_worktree(tmp_path):
    """A submodule uses a gitfile too, but owns its repository -- not excluded.

    This is the false-positive guard: keying the exclusion on "``.git`` is a
    file" would have swallowed every submodule. git-dir == git-common-dir for a
    submodule (both ``<super>/.git/modules/<name>``), so it stays a repo.
    """
    upstream = _init_repo(tmp_path / "upstream")
    super_repo = _init_repo(tmp_path / "super")
    _git(
        super_repo, "-c", "protocol.file.allow=always",
        "submodule", "add", "--quiet", str(upstream), "vendor/dep",
    )
    submodule = super_repo / "vendor" / "dep"

    assert (submodule / ".git").is_file(), "fixture must produce a gitfile submodule"
    assert is_linked_worktree(submodule) is False


def test_list_repos_excludes_the_worktree(workspace_with_worktree):
    """Discovery returns the two real repos and not the worktree."""
    found = _list_repos(workspace_with_worktree["root"])
    assert found == sorted([
        workspace_with_worktree["plain"],
        workspace_with_worktree["base"],
    ])


def test_list_repos_on_a_worktree_root_returns_nothing(workspace_with_worktree):
    """Pointing discovery straight at a worktree still yields no project."""
    assert _list_repos(workspace_with_worktree["worktree"]) == []


def test_list_repos_keeps_the_superproject(tmp_path):
    """A repo carrying a submodule is discovered exactly as before."""
    container = tmp_path / "ws"
    container.mkdir()
    upstream = _init_repo(tmp_path / "upstream")
    super_repo = _init_repo(container / "super")
    _git(
        super_repo, "-c", "protocol.file.allow=always",
        "submodule", "add", "--quiet", str(upstream), "vendor/dep",
    )
    assert _list_repos(container) == [super_repo]


# ---------------------------------------------------------------------------
# The derived record
# ---------------------------------------------------------------------------

def test_worktree_facets_report_path_and_branch(workspace_with_worktree):
    """The base repo reports its worktree; the worktree is not self-reported."""
    facets = worktree_facets(workspace_with_worktree["base"])
    assert facets == [{
        "scope": "worktree",
        "key": str(workspace_with_worktree["worktree"].resolve()),
        "value": "gateway",
    }]


def test_worktree_facets_empty_for_a_repo_without_worktrees(workspace_with_worktree):
    assert worktree_facets(workspace_with_worktree["plain"]) == []


# ---------------------------------------------------------------------------
# End-to-end scan: the regression this fix exists for
# ---------------------------------------------------------------------------

def test_scan_keeps_the_base_repo_path_and_records_the_worktree(
    workspace_with_worktree, tmp_db
):
    """The base repo keeps ITS path, and the worktree lands as a derived facet.

    Before the fix the worktree -- discovered after the base repo because it
    sorts later -- collapsed onto the base repo's row by shared identity and
    replaced ``projects.path`` with its own.
    """
    root = workspace_with_worktree["root"]
    report = classify_mod.scan(root, "century", db_path=tmp_db, apply=True)

    assert report.error is None
    assert {p["project"] for p in report.projects} == {"plain-repo", "widget"}

    rows = dict(_project_rows(tmp_db, "century"))
    assert set(rows) == {"plain-repo", "widget"}
    assert rows["widget"] == str(workspace_with_worktree["base"])
    assert rows["plain-repo"] == str(workspace_with_worktree["plain"])

    assert _facet_rows(tmp_db, "century", "widget", "worktree") == [
        (str(workspace_with_worktree["worktree"].resolve()), "gateway")
    ]
    assert _facet_rows(tmp_db, "century", "plain-repo", "worktree") == []


def test_scan_emits_no_collision_warning_for_a_worktree(
    workspace_with_worktree, tmp_db
):
    """No repo_collision warning: the worktree never competes for a slot."""
    report = classify_mod.scan(
        workspace_with_worktree["root"], "century", db_path=tmp_db, apply=False
    )
    assert [w for w in report.warnings if w.get("kind") == "repo_collision"] == []
    assert {r["repo"] for r in report.repos_found} == {"plain-repo", "widget"}


def test_rescan_prunes_a_removed_worktree(workspace_with_worktree, tmp_db):
    """The derived record is refreshed, not accumulated: remove -> row gone."""
    root = workspace_with_worktree["root"]
    classify_mod.scan(root, "century", db_path=tmp_db, apply=True)
    assert _facet_rows(tmp_db, "century", "widget", "worktree") != []

    _git(
        workspace_with_worktree["base"], "worktree", "remove",
        str(workspace_with_worktree["worktree"]),
    )
    classify_mod.scan(root, "century", db_path=tmp_db, apply=True)

    assert _facet_rows(tmp_db, "century", "widget", "worktree") == []
    rows = dict(_project_rows(tmp_db, "century"))
    assert rows["widget"] == str(workspace_with_worktree["base"])
