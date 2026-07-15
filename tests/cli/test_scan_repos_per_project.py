"""
Repos-per-container grouping under the basename-naming rule.

  * The project NAME is ALWAYS the repo basename (no numeric-suffix
    disambiguation for distinct-basename siblings).
  * A repo sitting directly under the workspace (e.g. ``engram``) has NO
    container -> ``container`` is ``None``.
  * A folder holding MULTIPLE repos (e.g. ``desing-repos`` with three repos)
    is the shared ``container`` (group_name) of each of its repos; every repo
    is still its own project keyed by its own basename.

Verification surface:
    gaia scan --workspace github-repos <root> --dry-run --json
    -> a repo directly under the workspace has container None; repos under a
       grouping folder share that folder as their container.

Grouping is expressed in the report via the ``container`` field (group_name):
every repo of one grouping folder shares the same ``container`` value. The
``project`` field is the DB storage slot, now the repo basename.

Temp tree + temp DB, so nothing touches the real workspace or gaia.db.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pytest

from tools.scan import classify as classify_mod


def _mk_repo(base: Path, *segments: str) -> Path:
    repo = base.joinpath(*segments)
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    return repo


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_dir = tmp_path / "gaia_data"
    db_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(db_dir))
    from gaia.paths import db_path
    return db_path()


def _group_by_container(report) -> dict[str, list[str]]:
    """Return {proyecto -> [repo, ...]} from a scan report."""
    groups: dict[str, list[str]] = defaultdict(list)
    for p in report.projects:
        groups[p["container"]].append(p["repo"])
    return dict(groups)


def test_singleton_collapses_project_equals_repo(tmp_path, tmp_db):
    """A repo directly under the workspace (N=1) has no container; its project
    name is the repo basename."""
    root = tmp_path / "github-repos"
    _mk_repo(root, "engram")

    report = classify_mod.scan(root, "github-repos", db_path=tmp_db, apply=False)

    assert len(report.projects) == 1, report.projects
    p = report.projects[0]
    assert p["repo"] == "engram"
    assert p["project"] == "engram"   # name = repo basename
    assert p["container"] is None      # no grouping folder (R4 collapse)


def test_multi_repo_container_is_one_project_with_many_repos(tmp_path, tmp_db):
    """A container with >1 repo (N>1) is ONE proyecto with each repo a child."""
    root = tmp_path / "github-repos"
    _mk_repo(root, "desing-repos", "beautiful-html-templates")
    _mk_repo(root, "desing-repos", "drawio-skill")
    _mk_repo(root, "desing-repos", "effective-html")

    report = classify_mod.scan(root, "github-repos", db_path=tmp_db, apply=False)

    groups = _group_by_container(report)
    # One proyecto grouping all three repos.
    assert "desing-repos" in groups, groups
    assert sorted(groups["desing-repos"]) == [
        "beautiful-html-templates",
        "drawio-skill",
        "effective-html",
    ], groups
    assert len(groups["desing-repos"]) > 1, "multi-repo container did not group"


def test_uniform_rule_no_special_casing(tmp_path, tmp_db):
    """Both shapes coexist under the same rule: engram has no container (repo
    directly under the workspace) and the three desing-repos repos share the
    container 'desing-repos', each keyed by its own basename."""
    root = tmp_path / "github-repos"
    _mk_repo(root, "engram")
    _mk_repo(root, "desing-repos", "beautiful-html-templates")
    _mk_repo(root, "desing-repos", "drawio-skill")
    _mk_repo(root, "desing-repos", "effective-html")

    report = classify_mod.scan(root, "github-repos", db_path=tmp_db, apply=False)
    groups = _group_by_container(report)

    # The repo directly under the workspace has no container (grouped under None).
    assert groups.get(None) == ["engram"], groups
    # The grouping folder holds its three repos (each its own basename project).
    assert sorted(groups.get("desing-repos", [])) == [
        "beautiful-html-templates",
        "drawio-skill",
        "effective-html",
    ], groups
    # Every classified repo landed in exactly one group (no orphan/dup).
    total_repos = sum(len(v) for v in groups.values())
    assert total_repos == len(report.projects) == 4, (groups, report.projects)
    # Names are the basenames (no numeric-suffix disambiguation).
    names = {p["project"] for p in report.projects}
    assert names == {
        "engram",
        "beautiful-html-templates",
        "drawio-skill",
        "effective-html",
    }, names
