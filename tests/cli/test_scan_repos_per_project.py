"""
M2-T5 (AC-4): the uniform 1/N repos-per-project rule.

  * A container with exactly ONE repo collapses: proyecto == repo (e.g. a repo
    sitting directly under the workspace, like ``engram``).
  * A container with MULTIPLE repos makes the container the proyecto, with each
    repo as a distinct child under that one proyecto (e.g. ``desing-repos``
    with three repos).

Verification surface (the plan's AC command):
    gaia scan --workspace github-repos <container> --dry-run --json
    -> a singleton collapses project==repo; a multi-repo container produces one
       project with >1 repo.

Grouping is expressed in the report via the ``container`` field (the proyecto
level): every repo of one container shares the same ``container`` value, so
grouping the projects by ``container`` yields exactly the 1/N shape. The
``project`` field remains the M1 collision-disambiguated DB storage slot and is
intentionally NOT what AC-4 groups on -- that separation is what lets M1 stay
green while AC-4 sees "one project with >1 repo".

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
    """A repo directly under the workspace (N=1) collapses: proyecto == repo."""
    root = tmp_path / "github-repos"
    _mk_repo(root, "engram")

    report = classify_mod.scan(root, "github-repos", db_path=tmp_db, apply=False)

    groups = _group_by_container(report)
    assert "engram" in groups, groups
    # Exactly one repo, and the proyecto name equals the repo name (collapse).
    assert groups["engram"] == ["engram"], groups


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
    """Both shapes coexist under the same rule: engram collapses (1 repo,
    proyecto==repo) and desing-repos groups (one proyecto, 3 repos)."""
    root = tmp_path / "github-repos"
    _mk_repo(root, "engram")
    _mk_repo(root, "desing-repos", "beautiful-html-templates")
    _mk_repo(root, "desing-repos", "drawio-skill")
    _mk_repo(root, "desing-repos", "effective-html")

    report = classify_mod.scan(root, "github-repos", db_path=tmp_db, apply=False)
    groups = _group_by_container(report)

    # N=1 -> project == repo.
    assert groups.get("engram") == ["engram"], groups
    # N>1 -> one project, many repos.
    assert len(groups.get("desing-repos", [])) == 3, groups
    # Every classified repo landed in exactly one proyecto (no orphan/dup).
    total_repos = sum(len(v) for v in groups.values())
    assert total_repos == len(report.projects) == 4, (groups, report.projects)
