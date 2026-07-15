"""
AC-2 / AC-5 under the basename-naming rule: distinct repos never overwrite each
other, and a genuine name collision is surfaced as an explicit warning.

Naming rule (see tools/scan/classify.py): the project NAME is ALWAYS the repo
basename, and the container folder is recorded separately in ``group_name``.
Two DIFFERENT physical repos collide only when they share the SAME basename
under one workspace (e.g. ``team-a/iac`` and ``team-b/iac``). The writer
(``gaia/store/writer.py::_find_collision_free_name`` + ``preview_project_name``,
wired through ``tools/scan/classify.py``) disambiguates the second-and-later
occupant with a numbered suffix so N distinct repos yield N distinct,
unclobbered rows -- and the collision is surfaced as a ``repo_collision``
warning (AC-5), not silently merged.

Distinct-basename siblings under one grouping folder do NOT collide anymore:
they simply become N projects keyed by their own basenames, sharing one
``container`` (group_name).

These tests build the shapes against a temp tree and temp DB, so nothing
touches the real workspace or ~/.gaia/gaia.db.
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
# Distinct basenames under one grouping folder: N rows, no suffix, no warning
# ---------------------------------------------------------------------------

def test_distinct_basenames_yield_distinct_rows_no_suffix(tmp_path, tmp_db):
    """Three DISTINCT-basename repos under one grouping folder become three
    projects keyed by their own basenames -- no numeric-suffix disambiguation,
    and they share the same container (group_name)."""
    root = tmp_path / "github-repos"
    _mk_repo(root, "desing-repos", "beautiful-html-templates")
    _mk_repo(root, "desing-repos", "drawio-skill")
    _mk_repo(root, "desing-repos", "effective-html")

    report = classify_mod.scan(root, "github-repos", db_path=tmp_db, apply=False)

    assert len(report.projects) == 3, report.projects
    names = {p["project"] for p in report.projects}
    # Names are the repo basenames, with no -2/-3 suffix (no collision).
    assert names == {
        "beautiful-html-templates",
        "drawio-skill",
        "effective-html",
    }, names
    # Every repo shares the same container (the grouping folder).
    assert {p["container"] for p in report.projects} == {"desing-repos"}
    # Each row still carries its own distinct physical identity.
    identities = {p["project_identity"] for p in report.projects}
    assert len(identities) == 3
    # Distinct basenames do NOT collide -> no collision warning.
    assert report.warnings == [], report.warnings


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


def test_apply_multi_repo_container_persists_distinct_rows(tmp_path, tmp_db):
    """A real (apply=True) scan of a grouping folder persists N distinct rows
    keyed by basename -- the same guarantee as the dry-run, proven against the
    DB."""
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
            "SELECT name, group_name, project_identity FROM projects WHERE workspace = ?",
            ("github-repos",),
        ).fetchall()
    finally:
        con.close()

    assert len(rows) == 3, [dict(r) for r in rows]
    names = {r["name"] for r in rows}
    identities = {r["project_identity"] for r in rows}
    assert names == {
        "beautiful-html-templates",
        "drawio-skill",
        "effective-html",
    }, names
    assert len(identities) == 3, identities
    # The container folder is persisted in group_name for every row.
    assert {r["group_name"] for r in rows} == {"desing-repos"}


def test_same_repo_rescanned_does_not_duplicate(tmp_path, tmp_db):
    """Same-repo identity-collapse: scanning the SAME grouping folder twice
    still yields exactly N rows, not 2N."""
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
        f"rescan of the same grouping folder duplicated rows (expected 2, got "
        f"{count}) -- identity-collapse regressed"
    )


# ---------------------------------------------------------------------------
# AC-5: a GENUINE basename collision (two different repos, same basename under
# one workspace) must be disambiguated AND surfaced as an explicit warning.
# ---------------------------------------------------------------------------

def test_dry_run_emits_collision_warning(tmp_path, tmp_db):
    """Two DIFFERENT repos sharing the basename 'iac' under one workspace
    collide on the 'iac' slot; the first keeps it, the second is disambiguated
    with an explicit warning (AC-5)."""
    root = tmp_path / "github-repos"
    _mk_repo(root, "team-a", "iac")
    _mk_repo(root, "team-b", "iac")

    report = classify_mod.scan(root, "github-repos", db_path=tmp_db, apply=False)

    # Two repos want the same slot ("iac"); one keeps it, one is disambiguated
    # -> exactly one explicit collision warning.
    assert report.warnings, "no collision warning emitted -- AC-5 unmet"
    assert len(report.warnings) == 1, report.warnings
    w = report.warnings[0]
    assert w["kind"] == "repo_collision"
    assert w["workspace"] == "github-repos"
    assert w["requested_project"] == "iac"
    assert w["assigned_project"] != "iac"
    assert w["repo"] == "iac"
    assert "collide" in w["message"]

    # Two distinct persisted-name slots, both basename-derived.
    names = {p["project"] for p in report.projects}
    assert names == {"iac", "iac-2"}, names


def test_warning_present_in_json_surface(tmp_path, tmp_db):
    """The CLI JSON surface (report.to_dict) carries the warnings, so
    ``gaia scan --dry-run --json`` shows the collision, not silence (AC-5)."""
    root = tmp_path / "github-repos"
    _mk_repo(root, "team-a", "iac")
    _mk_repo(root, "team-b", "iac")

    report = classify_mod.scan(root, "github-repos", db_path=tmp_db, apply=False)
    payload = report.to_dict()

    assert "warnings" in payload, "warnings key missing from JSON surface"
    assert len(payload["warnings"]) == 1, payload["warnings"]
    assert payload["warnings"][0]["kind"] == "repo_collision"


def test_apply_run_also_emits_collision_warning(tmp_path, tmp_db):
    """A real persisting scan (apply=True) surfaces the same warning -- the
    signal is not limited to dry-run."""
    root = tmp_path / "github-repos"
    _mk_repo(root, "team-a", "iac")
    _mk_repo(root, "team-b", "iac")

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
