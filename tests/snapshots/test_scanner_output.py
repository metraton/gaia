"""
AC-4: Snapshot tests over real-shape fixtures (bildwiz, rnd, qxo).

For each workspace, the test:
 1. Copies the fixture to a tmpdir.
 2. Runs scan_workspace_to_store with a temp DB.
 3. Builds a deterministic snapshot (sorted lines, repo + role + counts +
    representative names) by SELECTing from the provider.
 4. Compares against the matching golden file. Mismatch = regression.

Generating golden files: the first run writes the golden file when the env
var ``GAIA_REGENERATE_SNAPSHOTS=1`` is set. CI runs with the env var unset
and asserts equality.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest


FIXTURES_DIR = Path(__file__).parents[1] / "fixtures" / "workspaces"
GOLDEN_DIR = Path(__file__).parent / "golden"


def _grant_all(con, agent: str) -> None:
    for table in (
        "repos", "apps", "tf_modules", "tf_live", "releases", "workloads",
        "clusters_defined",
    ):
        con.execute(
            "INSERT OR REPLACE INTO agent_permissions (table_name, agent_name, allow_write) "
            "VALUES (?, ?, 1)",
            (table, agent),
        )
    con.commit()


def _build_snapshot(workspace: str, db_path: Path) -> str:
    """Build a deterministic text snapshot from the provider output."""
    from gaia.store import get_context

    ctx = get_context(workspace, db_path=db_path)
    ws = ctx["workspace"]

    lines: list[str] = []
    lines.append(f"# workspace: {workspace}")

    repos = sorted(ws["repos"], key=lambda r: r["name"])
    lines.append(f"repos: {len(repos)}")
    for r in repos:
        lines.append(
            f"  repo name={r['name']} role={r.get('role') or '-'} "
            f"language={r.get('primary_language') or '-'}"
        )

    tf_modules = sorted(ws["tf_modules"], key=lambda m: (m["repo"], m["name"]))
    lines.append(f"tf_modules: {len(tf_modules)}")
    for m in tf_modules:
        lines.append(f"  tf_module repo={m['repo']} name={m['name']}")

    clusters_defined = sorted(
        ws["clusters_defined"], key=lambda c: (c["repo"], c["name"])
    )
    lines.append(f"clusters_defined: {len(clusters_defined)}")
    for c in clusters_defined:
        lines.append(
            f"  cluster_defined repo={c['repo']} name={c['name']} "
            f"provider={c.get('provider') or '-'}"
        )

    releases = sorted(ws["releases"], key=lambda r: (r["repo"], r["name"]))
    lines.append(f"releases: {len(releases)}")
    for r in releases:
        lines.append(f"  release repo={r['repo']} name={r['name']}")

    workloads = sorted(ws["workloads"], key=lambda w: (w["repo"], w["name"]))
    lines.append(f"workloads: {len(workloads)}")
    for w in workloads:
        lines.append(
            f"  workload repo={w['repo']} name={w['name']} "
            f"kind={w.get('kind') or '-'} ns={w.get('namespace') or '-'}"
        )

    return "\n".join(lines) + "\n"


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / ".gaia"))
    from gaia.paths import db_path
    return db_path()


def _scan_and_snapshot(
    workspace: str,
    fixture_name: str,
    tmp_path: Path,
    tmp_db: Path,
    monkeypatch,
) -> str:
    """Copy the fixture, run scan_workspace_to_store, build snapshot."""
    src = FIXTURES_DIR / fixture_name
    assert src.is_dir(), f"missing fixture: {src}"
    dest = tmp_path / fixture_name
    shutil.copytree(src, dest)

    # Force a deterministic identity to avoid host-dependent git probes.
    import gaia.project as project_mod
    monkeypatch.setattr(project_mod, "current", lambda cwd=None: workspace)

    from gaia.store.writer import _connect
    con = _connect(tmp_db)
    _grant_all(con, "developer")
    _grant_all(con, "terraform-architect")
    _grant_all(con, "gitops-operator")
    con.close()

    from tools.scan.store_populator import scan_workspace_to_store

    scan_workspace_to_store(
        workspace=workspace,
        root=dest,
        agent="developer",
        db_path=tmp_db,
    )
    # Run infra + orch populators per repo with their domain agents.
    from tools.scan.store_populator import (
        populate_infrastructure, populate_orchestration,
    )
    for repo_dir in sorted(d for d in dest.iterdir() if d.is_dir()):
        populate_infrastructure(
            workspace=workspace,
            repo=repo_dir.name,
            repo_path=repo_dir,
            agent="terraform-architect",
            db_path=tmp_db,
        )
        populate_orchestration(
            workspace=workspace,
            repo=repo_dir.name,
            repo_path=repo_dir,
            agent="gitops-operator",
            db_path=tmp_db,
        )

    return _build_snapshot(workspace, tmp_db)


def _compare_or_record(name: str, snapshot: str) -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    golden = GOLDEN_DIR / f"{name}.txt"
    if os.environ.get("GAIA_REGENERATE_SNAPSHOTS") == "1" or not golden.is_file():
        golden.write_text(snapshot)
        # First-run: record but pass to bootstrap golden files
        return
    expected = golden.read_text()
    assert snapshot == expected, (
        f"snapshot mismatch for {name}.\n"
        f"--- expected ({golden}) ---\n{expected}\n"
        f"--- actual ---\n{snapshot}"
    )


def test_bildwiz_snapshot(tmp_path, tmp_db, monkeypatch):
    snap = _scan_and_snapshot("bildwiz", "bildwiz", tmp_path, tmp_db, monkeypatch)
    _compare_or_record("bildwiz", snap)


def test_rnd_snapshot(tmp_path, tmp_db, monkeypatch):
    snap = _scan_and_snapshot("rnd", "rnd", tmp_path, tmp_db, monkeypatch)
    _compare_or_record("rnd", snap)


def test_qxo_snapshot(tmp_path, tmp_db, monkeypatch):
    snap = _scan_and_snapshot("qxo", "qxo", tmp_path, tmp_db, monkeypatch)
    _compare_or_record("qxo", snap)
