"""
M1-T1 (AC-2): distinct repos sharing a basename never overwrite each other.

The collision-key defect: the projects UPSERT keyed on ``(workspace, name)``
meant two DIFFERENT physical repos that resolve to the same
``(workspace, project)`` -- e.g. several repos nested directly under one
container directory, all of whose ``project`` is the container basename --
silently overwrote each other, so a multi-repo container produced ONE row
instead of N.

The fix (``gaia/store/writer.py::_find_collision_free_name`` +
``preview_project_name``, wired through ``tools/scan/classify.py``)
disambiguates the second-and-later occupant of a ``(workspace, name)`` slot
when its ``project_identity`` differs from the row already there, so N
distinct repos yield N distinct, unclobbered rows on both the dry-run preview
and a real persisting scan.

AC command (plan_id=19, T1):
    gaia scan --workspace github-repos <container> --dry-run --json
    -> all repos under a multi-repo container appear as distinct rows,
       none overwritten.

These tests build the same shape as the real /home/jorge/ws/github-repos
``desing-repos`` case (three repos under one container) against a temp tree
and temp DB, so nothing touches the real workspace or ~/.gaia/gaia.db.
"""

from __future__ import annotations

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


# ---------------------------------------------------------------------------
# Dry-run: the AC command surface
# ---------------------------------------------------------------------------

def test_dry_run_multi_repo_container_yields_distinct_rows(tmp_path, tmp_db):
    """Three distinct repos under one container all appear as distinct
    project rows in the dry-run report -- none silently overwritten.

    Mirrors the AC command:
        gaia scan --workspace github-repos <container> --dry-run --json
    """
    root = tmp_path / "github-repos"
    _mk_repo(root, "desing-repos", "beautiful-html-templates")
    _mk_repo(root, "desing-repos", "drawio-skill")
    _mk_repo(root, "desing-repos", "effective-html")

    report = classify_mod.scan(root, "github-repos", db_path=tmp_db, apply=False)

    # Every repo classified to the same container basename `desing-repos`
    # (R2: the project is the segment before the repo). Without the collision
    # fix these would all be project="desing-repos" and collapse to one row.
    projects = [p["project"] for p in report.projects]
    assert len(report.projects) == 3, report.projects

    # AC: distinct rows -- three DIFFERENT project names, no silent merge.
    assert len(set(projects)) == 3, (
        f"basename collision was NOT disambiguated -- distinct repos "
        f"collapsed to the same project name: {projects}"
    )
    # The disambiguation is deterministic: base name + numbered suffixes.
    assert "desing-repos" in projects
    assert "desing-repos-2" in projects
    assert "desing-repos-3" in projects

    # Each row still carries its own distinct physical identity.
    identities = {p["project_identity"] for p in report.projects}
    assert len(identities) == 3


def test_dry_run_does_not_touch_db(tmp_path, tmp_db):
    """The collision-preview path must not materialize the DB in dry-run."""
    root = tmp_path / "github-repos"
    _mk_repo(root, "container", "repo-a")
    _mk_repo(root, "container", "repo-b")

    classify_mod.scan(root, "github-repos", db_path=tmp_db, apply=False)

    assert not tmp_db.exists(), (
        "dry-run collision preview created the DB file -- it must resolve "
        "names purely in-memory when the DB does not yet exist"
    )


# ---------------------------------------------------------------------------
# Real persisting scan: N distinct repos -> N unclobbered rows
# ---------------------------------------------------------------------------

def test_apply_multi_repo_container_persists_distinct_rows(tmp_path, tmp_db):
    """A real (apply=True) scan of a multi-repo container persists N distinct
    rows -- the same guarantee as the dry-run, proven against the DB."""
    from gaia.store.writer import _connect

    root = tmp_path / "github-repos"
    _mk_repo(root, "desing-repos", "beautiful-html-templates")
    _mk_repo(root, "desing-repos", "drawio-skill")
    _mk_repo(root, "desing-repos", "effective-html")

    report = classify_mod.scan(root, "github-repos", db_path=tmp_db, apply=True)
    assert all(p["applied"] for p in report.projects), report.projects

    con = _connect(tmp_db)
    try:
        rows = con.execute(
            "SELECT name, project_identity FROM projects WHERE workspace = ?",
            ("github-repos",),
        ).fetchall()
    finally:
        con.close()

    # Three distinct persisted rows, three distinct identities.
    assert len(rows) == 3, [dict(r) for r in rows]
    names = {r["name"] for r in rows}
    identities = {r["project_identity"] for r in rows}
    assert len(names) == 3, names
    assert len(identities) == 3, identities


def test_same_repo_rescanned_does_not_duplicate(tmp_path, tmp_db):
    """The collision fix must NOT break same-repo identity-collapse: scanning
    the SAME container twice still yields exactly N rows, not 2N."""
    from gaia.store.writer import _connect

    root = tmp_path / "github-repos"
    _mk_repo(root, "desing-repos", "repo-a")
    _mk_repo(root, "desing-repos", "repo-b")

    classify_mod.scan(root, "github-repos", db_path=tmp_db, apply=True)
    classify_mod.scan(root, "github-repos", db_path=tmp_db, apply=True)

    con = _connect(tmp_db)
    try:
        count = con.execute(
            "SELECT COUNT(*) FROM projects WHERE workspace = ?",
            ("github-repos",),
        ).fetchone()[0]
    finally:
        con.close()

    assert count == 2, (
        f"rescan of the same container duplicated rows (expected 2, got "
        f"{count}) -- identity-collapse regressed"
    )


# ---------------------------------------------------------------------------
# M2-T6 (AC-5): the collision must be SURFACED as an explicit warning, not just
# silently disambiguated. The M1 tests above prove no data loss; these prove
# the event is VISIBLE.
# ---------------------------------------------------------------------------

def test_dry_run_emits_collision_warning(tmp_path, tmp_db):
    """A multi-repo container whose repos would collide on the same project
    slot surfaces an explicit warning per would-be-collided repo (AC-5)."""
    root = tmp_path / "github-repos"
    _mk_repo(root, "desing-repos", "beautiful-html-templates")
    _mk_repo(root, "desing-repos", "drawio-skill")
    _mk_repo(root, "desing-repos", "effective-html")

    report = classify_mod.scan(root, "github-repos", db_path=tmp_db, apply=False)

    # Three repos want the same slot ("desing-repos"); the first keeps it,
    # the other two are disambiguated -> two explicit collision warnings.
    assert report.warnings, "no collision warning emitted -- AC-5 unmet"
    assert len(report.warnings) == 2, report.warnings
    for w in report.warnings:
        assert w["kind"] == "repo_collision"
        assert w["workspace"] == "github-repos"
        assert w["requested_project"] == "desing-repos"
        assert w["assigned_project"] != "desing-repos"
        assert w["repo"] in {"drawio-skill", "effective-html", "beautiful-html-templates"}
        assert "collide" in w["message"]


def test_warning_present_in_json_surface(tmp_path, tmp_db):
    """The CLI JSON surface (report.to_dict) carries the warnings, so
    ``gaia scan --dry-run --json`` shows the collision, not silence (AC-5)."""
    root = tmp_path / "github-repos"
    _mk_repo(root, "desing-repos", "drawio-skill")
    _mk_repo(root, "desing-repos", "effective-html")

    report = classify_mod.scan(root, "github-repos", db_path=tmp_db, apply=False)
    payload = report.to_dict()

    assert "warnings" in payload, "warnings key missing from JSON surface"
    assert len(payload["warnings"]) == 1, payload["warnings"]
    assert payload["warnings"][0]["kind"] == "repo_collision"


def test_apply_run_also_emits_collision_warning(tmp_path, tmp_db):
    """A real persisting scan (apply=True) surfaces the same warning -- the
    signal is not limited to dry-run."""
    root = tmp_path / "github-repos"
    _mk_repo(root, "desing-repos", "drawio-skill")
    _mk_repo(root, "desing-repos", "effective-html")

    report = classify_mod.scan(root, "github-repos", db_path=tmp_db, apply=True)

    assert report.warnings, "apply-run emitted no collision warning -- AC-5 unmet"
    assert all(w["kind"] == "repo_collision" for w in report.warnings)


def test_no_collision_no_warning(tmp_path, tmp_db):
    """A clean scan with no colliding repos emits NO warning -- the signal is
    specific to real collisions, not noise on every scan."""
    root = tmp_path / "github-repos"
    _mk_repo(root, "engram")          # singleton collapse, no collision
    _mk_repo(root, "gogcli")          # distinct singleton, no collision

    report = classify_mod.scan(root, "github-repos", db_path=tmp_db, apply=False)

    assert report.warnings == [], (
        f"clean scan emitted spurious collision warnings: {report.warnings}"
    )
