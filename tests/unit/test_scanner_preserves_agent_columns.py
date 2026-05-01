"""
AC-2 & AC-3: Reconciliation policy -- scanners preserve agent-owned columns.

The store API (B1) guarantees that scanner-side upserts only write the
scanner-owned column set. Columns annotated agent-owned in the schema
(repos.role contested, apps.description, apps.status, releases.notes,
services.description, etc.) are NEVER touched by the scanner path.

These tests instantiate a fixture row with an agent-owned column populated,
run the store_populator, and assert SELECT diff = 0 on that column.

Note on AC text vs. schema: the brief mentions ``notes`` and ``labels`` as
the canonical agent-owned columns. In the v1 DDL these map to
``releases.notes`` (agent-owned) and ``apps.description`` (agent-owned --
no `labels` column exists yet). Tests use the columns that actually exist
and demonstrate the preservation guarantee.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


def _grant_all(con, agent: str) -> None:
    """Grant write on the tables a scanner needs to populate."""
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


def test_infrastructure_preserves_notes(tmp_db, tmp_path, monkeypatch):
    """Setup: app with agent-owned ``description`` populated. Run
    infrastructure populator. Assert ``description`` unchanged.

    (The brief's literal "notes" maps to apps.description in v1 DDL.)
    """
    from gaia.store import upsert_repo, upsert_app
    from gaia.store.writer import _connect

    workspace = "ws-infra-preserve"

    con = _connect(tmp_db)
    _grant_all(con, "developer")
    _grant_all(con, "terraform-architect")
    con.close()

    # Seed: repo + app with agent-owned `description`
    upsert_repo(
        workspace=workspace,
        name="bildwiz-iac",
        fields={"role": "iac"},
        agent="developer",
        db_path=tmp_db,
    )
    upsert_app(
        workspace=workspace,
        repo="bildwiz-iac",
        name="orchestrator",
        fields={
            "kind": "service",
            "description": "hand-written by terraform-architect",
            "status": "active",
        },
        agent="developer",
        db_path=tmp_db,
    )

    # Snapshot before
    con = _connect(tmp_db)
    before = con.execute(
        "SELECT description, status FROM apps WHERE project = ? AND repo = ? AND name = ?",
        (workspace, "bildwiz-iac", "orchestrator"),
    ).fetchone()
    con.close()
    assert before["description"] == "hand-written by terraform-architect"

    # Run the infrastructure populator (mock a repo path with a tf file)
    repo_path = tmp_path / "bildwiz-iac"
    repo_path.mkdir()
    (repo_path / "main.tf").write_text(
        'module "vpc" {\n  source = "./modules/vpc"\n  version = "1.0.0"\n}\n'
    )

    # Force identity resolution to avoid surprises
    import gaia.project as project_mod
    monkeypatch.setattr(project_mod, "current", lambda cwd=None: workspace)

    from tools.scan.store_populator import populate_infrastructure

    res = populate_infrastructure(
        workspace=workspace,
        repo="bildwiz-iac",
        repo_path=repo_path,
        agent="terraform-architect",
        db_path=tmp_db,
    )

    # Snapshot after
    con = _connect(tmp_db)
    after = con.execute(
        "SELECT description, status FROM apps WHERE project = ? AND repo = ? AND name = ?",
        (workspace, "bildwiz-iac", "orchestrator"),
    ).fetchone()
    con.close()

    assert after is not None, "app row was deleted by scanner -- regression!"
    assert after["description"] == before["description"], (
        "scanner clobbered agent-owned `description` column"
    )
    assert after["status"] == before["status"]

    # Sanity: the scanner DID populate tf_modules
    con = _connect(tmp_db)
    tm_count = con.execute(
        "SELECT COUNT(*) FROM tf_modules WHERE project = ? AND repo = ?",
        (workspace, "bildwiz-iac"),
    ).fetchone()[0]
    con.close()
    assert tm_count >= 1, f"expected tf_modules row, got {tm_count}; res={res}"


def test_orchestration_preserves_labels(tmp_db, tmp_path, monkeypatch):
    """Setup: release with agent-owned ``notes`` populated. Run
    orchestration populator. Assert ``notes`` unchanged.

    (The brief's literal "labels" maps to releases.notes in v1 DDL.)
    """
    from gaia.store import upsert_repo
    from gaia.store.writer import _connect

    workspace = "ws-orch-preserve"

    con = _connect(tmp_db)
    _grant_all(con, "developer")
    _grant_all(con, "gitops-operator")
    con.close()

    # Seed: repo + release with agent-owned `notes`
    upsert_repo(
        workspace=workspace,
        name="bildwiz-gitops",
        fields={"role": "gitops"},
        agent="developer",
        db_path=tmp_db,
    )

    con = _connect(tmp_db)
    con.execute(
        "INSERT INTO releases (project, repo, name, released_at, notes, scanner_ts) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (workspace, "bildwiz-gitops", "v1.0.0", "2026-04-01",
         '{"team":"platform","approved_by":"jorge"}',
         "2026-04-01T00:00:00Z"),
    )
    con.commit()
    con.close()

    # Snapshot before
    con = _connect(tmp_db)
    before = con.execute(
        "SELECT notes FROM releases WHERE project = ? AND repo = ? AND name = ?",
        (workspace, "bildwiz-gitops", "v1.0.0"),
    ).fetchone()
    con.close()
    assert before["notes"] == '{"team":"platform","approved_by":"jorge"}'

    # Build a fake repo with a HelmRelease YAML (so scanner will upsert
    # the same v1.0.0 release)
    repo_path = tmp_path / "bildwiz-gitops"
    repo_path.mkdir()
    (repo_path / "release.yaml").write_text(
        "apiVersion: helm.toolkit.fluxcd.io/v2beta1\n"
        "kind: HelmRelease\n"
        "metadata:\n"
        "  name: v1.0.0\n"
        "  namespace: default\n"
    )

    import gaia.project as project_mod
    monkeypatch.setattr(project_mod, "current", lambda cwd=None: workspace)

    from tools.scan.store_populator import populate_orchestration

    res = populate_orchestration(
        workspace=workspace,
        repo="bildwiz-gitops",
        repo_path=repo_path,
        agent="gitops-operator",
        db_path=tmp_db,
    )

    # Snapshot after
    con = _connect(tmp_db)
    after = con.execute(
        "SELECT notes FROM releases WHERE project = ? AND repo = ? AND name = ?",
        (workspace, "bildwiz-gitops", "v1.0.0"),
    ).fetchone()
    con.close()

    assert after is not None, "release row was deleted by scanner -- regression!"
    assert after["notes"] == before["notes"], (
        "scanner clobbered agent-owned `notes` column on releases"
    )
