"""
M3-T8 (AC-6): every ``gaia scan`` persists the repo's stack fingerprint as
rows in ``project_facets`` (scope/key/value), and a rescan REFRESHES the
fingerprint without duplicating.

The stack fingerprint (languages, frameworks with version, build tools, and
detected infrastructure / deployment / orchestration aspects) is computed by
the scanners (``tools/scan/scanners/``) and, as of T8, persisted through the
CLI scan path (``tools/scan/classify.py::scan`` -> ``store_populator.
populate_facets`` -> ``project_facets``) rather than being discarded. The
table is 100% scanner-owned: a rescan upserts the current facets (keyed on
(workspace, project, scope, key)) and prunes the stale ones for the project.

AC command surface (plan_id=19, T8):
    gaia scan --workspace me /home/user/ws/me/gaia --dry-run --json
    -> the detected stack (e.g. python/helm/terraform) appears as facet rows
       (scope/key/value) in the persisted project_facets payload, equivalent
       to SELECT scope, key, value FROM project_facets WHERE workspace=...

These tests drive ``classify.scan`` directly (the function ``cmd_scan`` calls)
and read ``report.to_dict()`` (the exact JSON ``gaia scan --json`` prints),
against temp fixture repos with REAL manifests and a temp DB -- so nothing
touches the real workspace tree or ~/.gaia/gaia.db.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from gaia.store.writer import declare_workspace
from tools.scan import classify as classify_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_dir = tmp_path / "gaia_data"
    db_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(db_dir))
    from gaia.paths import db_path
    return db_path()


def _mk_repo(base: Path, *segments: str) -> Path:
    """Create ``base/segments.../.git`` and return the repo dir."""
    repo = base.joinpath(*segments)
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    return repo


def _scan_declared(root: Path, workspace: str, db_path: Path):
    """Apply a scan of *workspace*, declared at *root*."""
    declare_workspace(workspace, root, db_path=db_path)
    return classify_mod.scan(root, workspace, db_path=db_path, apply=True)


def _write_python_helm_tf_repo(repo: Path) -> None:
    """Populate ``repo`` with manifests the scanners detect as a
    python + fastapi + terraform + helm stack."""
    (repo / "pyproject.toml").write_text(
        "[project]\n"
        'name = "svc"\n'
        'version = "0.1.0"\n'
        'dependencies = ["fastapi>=0.100.0"]\n'
        "\n[build-system]\n"
        'requires = ["setuptools"]\n',
        encoding="utf-8",
    )
    (repo / "main.tf").write_text(
        'provider "google" {\n  project = "demo"\n}\n',
        encoding="utf-8",
    )
    (repo / "Chart.yaml").write_text(
        "apiVersion: v2\nname: svc\nversion: 0.1.0\n",
        encoding="utf-8",
    )


def _facet_rows(db_path: Path, workspace: str, project: str):
    """Return ``[(scope, key, value), ...]`` for a project, or [] if none."""
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT scope, key, value FROM project_facets "
            "WHERE workspace = ? AND project = ? ORDER BY scope, key",
            (workspace, project),
        ).fetchall()
    finally:
        con.close()
    return [(r["scope"], r["key"], r["value"]) for r in rows]


def _facet_count(db_path: Path, workspace: str, project: str) -> int:
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM project_facets WHERE workspace = ? AND project = ?",
            (workspace, project),
        ).fetchone()[0]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# apply: the fingerprint is persisted as project_facets rows
# ---------------------------------------------------------------------------

def test_apply_persists_stack_fingerprint_as_facets(tmp_path, tmp_db):
    """A real scan of a python/fastapi/terraform/helm repo persists the stack
    fingerprint as scope/key/value rows in project_facets (AC-6)."""
    root = tmp_path / "myws"
    repo = _mk_repo(root, "svc")
    _write_python_helm_tf_repo(repo)

    report = _scan_declared(root, "myws", tmp_db)
    assert report.error is None, report.error
    assert report.facet_failures == [], report.facet_failures
    assert [p["repo"] for p in report.projects] == ["svc"], report.projects

    rows = _facet_rows(tmp_db, "myws", "svc")
    by_scope_key = {(s, k): v for (s, k, v) in rows}

    # language: python detected from pyproject.toml
    assert ("language", "python") in by_scope_key, rows
    # framework: fastapi WITH version (value carries the detail/version)
    assert ("framework", "fastapi") in by_scope_key, rows
    assert by_scope_key[("framework", "fastapi")] == "0.100.0", rows
    # infrastructure: terraform from *.tf
    assert ("infrastructure", "terraform") in by_scope_key, rows
    # orchestration: helm from Chart.yaml
    assert ("orchestration", "helm") in by_scope_key, rows


def test_report_projects_carry_facets_on_apply(tmp_path, tmp_db):
    """The report (and thus the CLI JSON) carries the persisted facets per
    project on an apply run too, not only in dry-run."""
    root = tmp_path / "myws"
    repo = _mk_repo(root, "svc")
    _write_python_helm_tf_repo(repo)

    report = _scan_declared(root, "myws", tmp_db)
    payload = report.to_dict()
    facets = payload["projects"][0]["facets"]
    scopes = {f["scope"] for f in facets}
    assert {"language", "framework", "infrastructure", "orchestration"} <= scopes, facets


# ---------------------------------------------------------------------------
# dry-run: facets are PREVIEWED in the JSON, nothing is written
# ---------------------------------------------------------------------------

def test_dry_run_previews_facets_and_writes_nothing(tmp_path, tmp_db):
    """A dry-run previews the fingerprint in report.to_dict()['projects'][*]
    ['facets'] -- the exact JSON `gaia scan --dry-run --json` emits -- and
    persists NOTHING (the DB is not even materialized)."""
    root = tmp_path / "myws"
    repo = _mk_repo(root, "svc")
    _write_python_helm_tf_repo(repo)

    report = classify_mod.scan(root, "myws", db_path=tmp_db, apply=False)
    payload = report.to_dict()

    # The facets are present in the JSON surface, per-project.
    assert len(payload["projects"]) == 1, payload["projects"]
    facets = payload["projects"][0]["facets"]
    assert facets, "dry-run did not preview any facets"
    seen = {(f["scope"], f["key"]) for f in facets}
    assert ("language", "python") in seen, facets
    assert ("framework", "fastapi") in seen, facets
    assert ("infrastructure", "terraform") in seen, facets
    assert ("orchestration", "helm") in seen, facets

    # Nothing was written: the dry-run must not materialize the DB at all.
    assert not tmp_db.exists(), (
        "dry-run persisted state -- the DB file was created; apply=False must "
        "preview facets without writing"
    )


# ---------------------------------------------------------------------------
# rescan: REFRESH without duplicating; prune stale facets
# ---------------------------------------------------------------------------

def test_rescan_refreshes_without_duplicating(tmp_path, tmp_db):
    """Scanning the same repo twice yields the SAME facet rows (no dup) --
    the (workspace, project, scope, key) PK + upsert makes it coalesce-safe."""
    root = tmp_path / "myws"
    repo = _mk_repo(root, "svc")
    _write_python_helm_tf_repo(repo)

    _scan_declared(root, "myws", tmp_db)
    count_after_first = _facet_count(tmp_db, "myws", "svc")
    _scan_declared(root, "myws", tmp_db)
    count_after_second = _facet_count(tmp_db, "myws", "svc")

    assert count_after_first > 0, "first scan persisted no facets"
    assert count_after_second == count_after_first, (
        f"rescan duplicated facets: {count_after_first} -> {count_after_second}"
    )


def test_rescan_prunes_stale_facets(tmp_path, tmp_db):
    """A facet that disappears from the repo between scans is pruned on rescan
    (100% scanner-owned refresh) -- removing Chart.yaml drops orchestration:helm."""
    root = tmp_path / "myws"
    repo = _mk_repo(root, "svc")
    _write_python_helm_tf_repo(repo)

    _scan_declared(root, "myws", tmp_db)
    first = dict(((s, k), v) for (s, k, v) in _facet_rows(tmp_db, "myws", "svc"))
    assert ("orchestration", "helm") in first, first

    # The chart is removed from the repo; rescan must drop the stale facet.
    (repo / "Chart.yaml").unlink()
    _scan_declared(root, "myws", tmp_db)
    second = dict(((s, k), v) for (s, k, v) in _facet_rows(tmp_db, "myws", "svc"))

    assert ("orchestration", "helm") not in second, (
        f"stale orchestration:helm facet was not pruned on rescan: {second}"
    )
    # The surviving fingerprint (language/framework/infra) is still present.
    assert ("language", "python") in second, second
    assert ("infrastructure", "terraform") in second, second


# ---------------------------------------------------------------------------
# regression guard: identity-collapse across workspaces (the T8 FK bug)
# ---------------------------------------------------------------------------

def test_identity_collapse_cross_workspace_facets(tmp_path, tmp_db):
    """A repo indexed under one workspace, then owned by a nested workspace
    declared later, is ONE projects row that moves to the nearest declared
    workspace, with its facets, and stays there when the outer one rescans."""
    from gaia.store.writer import _connect

    repo = _mk_repo(tmp_path, "aaxis", "aos", "aos-iac")
    shutil.rmtree(repo / ".git")
    subprocess.run(["git", "init", "--quiet"], cwd=str(repo), check=True)
    _write_python_helm_tf_repo(repo)

    r1 = _scan_declared(tmp_path / "aaxis", "aaxis", tmp_db)
    r2 = _scan_declared(tmp_path / "aaxis" / "aos", "aos", tmp_db)
    r3 = _scan_declared(tmp_path / "aaxis", "aaxis", tmp_db)

    # No facet write blew up (the FK regression manifested as an aborted scan).
    assert r1.facet_failures == [], r1.facet_failures
    assert r2.facet_failures == [], r2.facet_failures
    assert [w["kind"] for w in r2.warnings] == ["project_moved"], r2.warnings

    con = _connect(tmp_db)
    try:
        rows = con.execute(
            "SELECT workspace, name, status FROM projects WHERE project_identity IS NOT NULL"
        ).fetchall()
    finally:
        con.close()
    assert [tuple(r) for r in rows] == [("aos", "aos-iac", "active")], (
        f"the nearest declared workspace did not win: {[tuple(r) for r in rows]}"
    )
    assert r3.foreign_repos and _facet_rows(tmp_db, "aaxis", "aos-iac") == []

    rows = _facet_rows(tmp_db, "aos", "aos-iac")
    seen = {(s, k) for (s, k, v) in rows}
    assert ("language", "python") in seen, rows
    assert ("infrastructure", "terraform") in seen, rows


# ---------------------------------------------------------------------------
# primary_language must be DERIVED from the detected language facet
#
# Regression guard for the facet -> primary_language drift: the scanner detected
# a `language` facet (recursive, complete manifest list) but the projects row's
# scalar `primary_language` stayed NULL, because it was resolved by a SEPARATE
# top-level-only probe with an INCOMPLETE manifest list (no Gemfile, no
# composer.json, no subdir manifests). primary_language now comes from the SAME
# scanner sections that produce the facets, so the two can never disagree.
# ---------------------------------------------------------------------------

def _primary_language(db_path: Path, workspace: str, project: str) -> str | None:
    """Return projects.primary_language for one (workspace, project) row."""
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT primary_language FROM projects WHERE workspace = ? AND name = ?",
            (workspace, project),
        ).fetchone()
    finally:
        con.close()
    return row["primary_language"] if row is not None else None


def test_ruby_gemfile_resolves_primary_language(tmp_path, tmp_db):
    """A repo whose only language manifest is a Gemfile detects a ruby language
    facet AND resolves primary_language='ruby' (the exact metraton.github.io
    symptom: ruby facet detected, primary_language NULL)."""
    root = tmp_path / "myws"
    repo = _mk_repo(root, "site")
    (repo / "Gemfile").write_text(
        'source "https://rubygems.org"\ngem "jekyll"\n', encoding="utf-8"
    )

    report = _scan_declared(root, "myws", tmp_db)
    assert report.error is None, report.error

    # The language facet is detected ...
    seen = {(f["scope"], f["key"]) for f in report.projects[0]["facets"]}
    assert ("language", "ruby") in seen, report.projects[0]["facets"]
    # ... and primary_language follows it (no drift).
    assert _primary_language(tmp_db, "myws", "site") == "ruby"


def test_python_manifest_in_subdir_resolves_primary_language(tmp_path, tmp_db):
    """A repo whose python manifest lives in a SUBDIRECTORY (not at the repo
    root) still resolves primary_language='python' -- the recursive scanner
    detection finds it, unlike the historical top-level-only probe (the
    bildwiz-insights symptom: python facet detected, primary_language NULL)."""
    root = tmp_path / "myws"
    repo = _mk_repo(root, "svc")
    sub = repo / "backend"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "requirements.txt").write_text("fastapi>=0.100.0\n", encoding="utf-8")

    report = _scan_declared(root, "myws", tmp_db)
    assert report.error is None, report.error

    seen = {(f["scope"], f["key"]) for f in report.projects[0]["facets"]}
    assert ("language", "python") in seen, report.projects[0]["facets"]
    assert _primary_language(tmp_db, "myws", "svc") == "python"


def test_java_manifest_in_subdir_resolves_primary_language(tmp_path, tmp_db):
    """A repo whose java build file lives in a SUBDIRECTORY resolves
    primary_language='java' (the aos-keycloak symptom: java facet detected,
    primary_language NULL because pom.xml was not at the repo root)."""
    root = tmp_path / "myws"
    repo = _mk_repo(root, "keycloak")
    module = repo / "server"
    module.mkdir(parents=True, exist_ok=True)
    (module / "pom.xml").write_text(
        '<project><modelVersion>4.0.0</modelVersion>'
        '<groupId>x</groupId><artifactId>server</artifactId>'
        '<version>1.0</version></project>\n',
        encoding="utf-8",
    )

    report = _scan_declared(root, "myws", tmp_db)
    assert report.error is None, report.error

    seen = {(f["scope"], f["key"]) for f in report.projects[0]["facets"]}
    assert ("language", "java") in seen, report.projects[0]["facets"]
    assert _primary_language(tmp_db, "myws", "keycloak") == "java"


def test_javascript_package_json_still_resolves_primary_language(tmp_path, tmp_db):
    """The previously-working case must keep working: a package.json repo
    resolves primary_language='javascript' (balance / gaia)."""
    root = tmp_path / "myws"
    repo = _mk_repo(root, "app")
    (repo / "package.json").write_text(
        '{"name": "app", "version": "1.0.0"}\n', encoding="utf-8"
    )

    report = _scan_declared(root, "myws", tmp_db)
    assert report.error is None, report.error
    assert _primary_language(tmp_db, "myws", "app") == "javascript"


def test_iac_only_repo_has_no_primary_language(tmp_path, tmp_db):
    """An IaC-only repo (only *.tf / Chart.yaml, no language manifest)
    legitimately resolves primary_language=None -- the fix must NOT invent a
    language for infra-only repos (the 5-of-7 aaxis repos that correctly stay
    NULL)."""
    root = tmp_path / "myws"
    repo = _mk_repo(root, "infra")
    (repo / "main.tf").write_text(
        'provider "google" {\n  project = "demo"\n}\n', encoding="utf-8"
    )
    (repo / "Chart.yaml").write_text(
        "apiVersion: v2\nname: infra\nversion: 0.1.0\n", encoding="utf-8"
    )

    report = _scan_declared(root, "myws", tmp_db)
    assert report.error is None, report.error

    # There IS infra, but no language facet ...
    seen = {f["scope"] for f in report.projects[0]["facets"]}
    assert "infrastructure" in seen, report.projects[0]["facets"]
    assert "language" not in seen, report.projects[0]["facets"]
    # ... so primary_language is honestly NULL.
    assert _primary_language(tmp_db, "myws", "infra") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
