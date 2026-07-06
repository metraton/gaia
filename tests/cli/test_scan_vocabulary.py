"""
M2-T4 (AC-3): the model and CLI expose the workspace -> proyecto -> repo
hierarchy, with each repo carrying its own absolute path.

Verification surface (chosen as the plan's "comando equivalente"):
    gaia scan --workspace <W> <root> --dry-run --json

The plan's literal AC command ``gaia projects list --workspace ...`` has no
corresponding subcommand today, and the AC text explicitly allows "(o el
comando equivalente que exponga los tres niveles)". ``gaia scan --dry-run
--json`` is that equivalent: it is the existing read-only surface that emits,
per repo, the three vocabulary levels plus the absolute path -- no new
subcommand is built.

Vocabulary produced by ``tools/scan/classify.py::scan`` per matched repo:
  * ``workspace``  -- the workspace name W (top level).
  * ``container``  -- the PROYECTO level: the classified project grouping one
                      or more repos (equals ``repo`` on a singleton collapse).
  * ``repo``       -- the repo (basename of the folder holding ``.git``).
  * ``path``       -- the repo's own absolute path, distinct from the
                      container/project grouping.

These tests build a temp tree + temp DB, so nothing touches the real
workspace or ~/.gaia/gaia.db.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tools.scan import classify as classify_mod


def _mk_repo(base: Path, *segments: str) -> Path:
    """Create ``base/segments.../.git`` and return the repo dir."""
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


def test_report_exposes_three_levels_plus_absolute_path(tmp_path, tmp_db):
    """Every matched repo in the dry-run report carries the three vocabulary
    levels (workspace, proyecto/container, repo) and its own absolute path."""
    root = tmp_path / "github-repos"
    _mk_repo(root, "desing-repos", "drawio-skill")
    _mk_repo(root, "engram")  # singleton, collapses

    report = classify_mod.scan(root, "github-repos", db_path=tmp_db, apply=False)

    assert report.projects, "no projects classified"
    for p in report.projects:
        # The three levels are present and non-empty.
        assert p["workspace"] == "github-repos"
        assert p["container"], f"missing proyecto level: {p}"
        assert p["repo"], f"missing repo level: {p}"
        # Each repo carries its own absolute path.
        assert p["path"], f"missing path: {p}"
        assert os.path.isabs(p["path"]), f"path not absolute: {p['path']}"


def test_repo_path_is_distinct_from_the_project_grouping(tmp_path, tmp_db):
    """Each repo's ``path`` points at the repo directory itself -- distinct
    from (deeper than) the container/proyecto grouping, so two repos under the
    same proyecto have distinct absolute paths."""
    root = tmp_path / "github-repos"
    _mk_repo(root, "desing-repos", "drawio-skill")
    _mk_repo(root, "desing-repos", "effective-html")

    report = classify_mod.scan(root, "github-repos", db_path=tmp_db, apply=False)

    by_repo = {p["repo"]: p for p in report.projects}
    assert "drawio-skill" in by_repo
    assert "effective-html" in by_repo

    # Both share the proyecto (container), but each path is the repo's own dir.
    assert by_repo["drawio-skill"]["container"] == "desing-repos"
    assert by_repo["effective-html"]["container"] == "desing-repos"

    p1 = by_repo["drawio-skill"]["path"]
    p2 = by_repo["effective-html"]["path"]
    assert p1 != p2, "distinct repos collapsed to one path"
    assert Path(p1).name == "drawio-skill"
    assert Path(p2).name == "effective-html"
    # The path is deeper than the proyecto grouping directory.
    assert Path(p1).parent.name == "desing-repos"


def test_cli_json_surface_carries_the_vocabulary(tmp_path, tmp_db):
    """The CLI JSON surface (report.to_dict) -- what ``gaia scan --dry-run
    --json`` prints -- exposes the vocabulary fields for each repo, so the
    chosen equivalent command genuinely satisfies AC-3."""
    root = tmp_path / "github-repos"
    _mk_repo(root, "engram")

    report = classify_mod.scan(root, "github-repos", db_path=tmp_db, apply=False)
    payload = report.to_dict()

    assert payload["projects"], "empty projects payload"
    entry = payload["projects"][0]
    for key in ("workspace", "container", "repo", "path"):
        assert key in entry, f"vocabulary key {key!r} missing from JSON: {entry}"
