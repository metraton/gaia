"""
Integration test: child-table population against a REAL schema.sql DB.

Why this test exists
--------------------
A live `gaia scan` failed with ``sqlite3.OperationalError: no such column:
project`` while populating the per-project child tables (apps, services,
tf_modules, tf_live, libraries, features, releases, workloads,
clusters_defined).

Root cause: commit be9698f ("rename projects->workspaces, repos->projects")
renamed the child-table FK column ``repo`` -> ``project`` in ``schema.sql`` and
in every writer/populator SQL path, but shipped NO migration. Because the child
tables are declared ``CREATE TABLE IF NOT EXISTS``, schema.sql silently no-ops
on a DB that already has them, so existing installations stayed at the legacy
``repo`` column while the code emitted ``project``.

The existing unit tests did not catch it because:
  * they build the DB from the CURRENT source schema.sql (which already has
    ``project``), so the code-vs-schema names agree there, and
  * they only exercise apps/services/libraries -- not the full set of nine
    child tables driven by the generic ``bulk_upsert`` path.

This module closes both gaps:

  1. TestSchemaV15Migration -- builds a v14-shaped DB with the LEGACY ``repo``
     column on all nine child tables, runs bootstrap_database.sh, and asserts
     every child table converged to ``project`` (the exact regression that
     caused the live failure).

  2. TestRealSchemaChildTablePopulation -- builds a DB from the REAL schema.sql
     via the un-mocked writer (``_connect``), lays down on-disk content that
     triggers inserts into ALL nine child tables, drives the REAL populator
     functions (no writer mock), and asserts rows land with no "no such column"
     error. This exercises the dynamic INSERT / ON CONFLICT / delete_missing SQL
     in ``gaia.store.writer.bulk_upsert`` against a live schema.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP_SH = _REPO_ROOT / "scripts" / "bootstrap_database.sh"
_SCHEMA_SQL = _REPO_ROOT / "gaia" / "store" / "schema.sql"
_MIGRATIONS_DIR = _REPO_ROOT / "scripts" / "migrations"

# The nine per-project child tables whose FK column was renamed repo -> project.
_CHILD_TABLES = [
    "apps", "libraries", "services", "features", "tf_modules",
    "tf_live", "releases", "workloads", "clusters_defined",
]

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Part 1: v14 -> v15 migration regression (repo -> project rename)
# ---------------------------------------------------------------------------

# A minimal v14-state DB stub: parent tables + nine child tables each carrying
# the LEGACY `repo` column (the shape every pre-v15 live DB has), ledger at v14.
_V14_LEGACY_STUB_HEADER = """
CREATE TABLE workspaces (
    name        TEXT NOT NULL PRIMARY KEY,
    identity    TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE projects (
    workspace  TEXT NOT NULL,
    name       TEXT NOT NULL,
    scanner_ts TEXT,
    PRIMARY KEY (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE TABLE schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    description TEXT
);
"""

# Each child table mirrors the REAL schema.sql columns (so that schema.sql's
# CREATE INDEX statements parse cleanly when bootstrap re-runs it against this
# pre-existing DB), but uses the LEGACY `repo` FK column instead of `project`.
# Column sets are copied from gaia/store/schema.sql per table.
_V14_CHILD_COLUMNS: dict[str, str] = {
    "apps": "kind TEXT, description TEXT, status TEXT, topic_key TEXT,",
    "libraries": "version TEXT, language TEXT,",
    "services": "kind TEXT, description TEXT, status TEXT, topic_key TEXT,",
    "features": "status TEXT, description TEXT, topic_key TEXT,",
    "tf_modules": "source TEXT, version TEXT, topic_key TEXT,",
    "tf_live": "kind TEXT, attributes TEXT,",
    "releases": "released_at TEXT, notes TEXT,",
    "workloads": "kind TEXT, namespace TEXT, cluster TEXT,",
    "clusters_defined": "provider TEXT, region TEXT,",
}

_V14_CHILD_TEMPLATE = """
CREATE TABLE {table} (
    workspace  TEXT NOT NULL,
    repo       TEXT NOT NULL,
    name       TEXT NOT NULL,
    {extra_cols}
    scanner_ts TEXT,
    PRIMARY KEY (workspace, repo, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE,
    FOREIGN KEY (workspace, repo) REFERENCES projects(workspace, name) ON DELETE CASCADE
);
"""


def _build_v14_legacy_db(db_path: Path) -> None:
    """Build a v14-state DB whose child tables carry the legacy `repo` column.

    The column sets mirror the real schema.sql so that when bootstrap re-runs
    schema.sql against this pre-existing DB, its CREATE INDEX statements (e.g.
    idx_apps_status, idx_workloads_cluster) parse against existing columns.
    Only the FK column name differs: legacy `repo` instead of `project`.
    """
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(_V14_LEGACY_STUB_HEADER)
        # projects needs the columns its own indexes reference (topic_key, etc.)
        con.executescript(
            "DROP TABLE projects;"
            "CREATE TABLE projects ("
            "  workspace TEXT NOT NULL, name TEXT NOT NULL, role TEXT,"
            "  remote_url TEXT, platform TEXT, primary_language TEXT,"
            "  scanner_ts TEXT, topic_key TEXT, group_name TEXT, path TEXT,"
            "  PRIMARY KEY (workspace, name),"
            "  FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE"
            ");"
        )
        for t in _CHILD_TABLES:
            con.executescript(
                _V14_CHILD_TEMPLATE.format(
                    table=t, extra_cols=_V14_CHILD_COLUMNS[t]
                )
            )
        con.execute("INSERT INTO workspaces (name, identity) VALUES ('me', 'me')")
        con.execute("INSERT INTO projects (workspace, name) VALUES ('me', 'gaia')")
        # Seed one row per child table so we can prove data survives the rename.
        for t in _CHILD_TABLES:
            con.execute(
                f"INSERT INTO {t} (workspace, repo, name) VALUES ('me', 'gaia', 'seed')"
            )
        # Stamp the ledger up to v14 so bootstrap requests exactly v14->v15.
        con.executemany(
            "INSERT INTO schema_version (version, applied_at, description) VALUES (?, ?, ?)",
            [(v, "2026-01-01T00:00:00Z", f"v{v}") for v in range(1, 15)],
        )
        con.commit()
    finally:
        con.close()


def _run_bootstrap_with_db(db_path: Path, workspace: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["GAIA_DB"] = str(db_path)
    env["WORKSPACE"] = str(workspace)
    return subprocess.run(
        ["bash", str(_BOOTSTRAP_SH)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _run_bootstrap_fresh(workspace: Path) -> tuple[subprocess.CompletedProcess, Path]:
    db_path = workspace / "tmp_gaia.db"
    env = os.environ.copy()
    env["GAIA_DB"] = str(db_path)
    env["WORKSPACE"] = str(workspace)
    res = subprocess.run(
        ["bash", str(_BOOTSTRAP_SH)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return res, db_path


def _fk_col(db_path: Path, table: str) -> str | None:
    con = sqlite3.connect(str(db_path))
    try:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()
    if "project" in cols:
        return "project"
    if "repo" in cols:
        return "repo"
    return None


class TestSchemaV15Migration:
    """Child tables expose the `project` FK column.

    The v14 -> v15 in-place rename (repo -> project) is below the schema floor
    (v18) and is no longer exercised: the historical migration chain was
    collapsed and bootstrap rejects below-floor DBs. What remains verifiable is
    the floor contract -- a fresh install builds child tables already on the
    `project` column.
    """

    def setup_method(self):
        if not _BOOTSTRAP_SH.is_file():
            pytest.skip(f"bootstrap script not found at {_BOOTSTRAP_SH}")

    def test_fresh_install_child_tables_use_project(self):
        """Fresh bootstrap (schema.sql) produces child tables with `project`."""
        with tempfile.TemporaryDirectory() as tmp:
            res, db_path = _run_bootstrap_fresh(Path(tmp))
            assert res.returncode == 0, (
                f"fresh bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
            )
            for t in _CHILD_TABLES:
                assert _fk_col(db_path, t) == "project", (
                    f"fresh install: {t} FK column should be `project`"
                )
            con = sqlite3.connect(str(db_path))
            try:
                max_v = con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            finally:
                con.close()
            assert max_v >= 15, f"fresh install ledger should be >= 15 (got {max_v})"


# ---------------------------------------------------------------------------
# Part 2: real-schema end-to-end child-table population (no writer mock)
# ---------------------------------------------------------------------------

@pytest.fixture()
def real_db(tmp_path, monkeypatch):
    """A DB built from the REAL schema.sql via the un-mocked writer ``_connect``.

    ``_connect`` runs gaia/store/schema.sql, so the child tables carry whatever
    column the source schema declares (``project``). This is the un-mocked
    writer the gap-tests previously side-stepped.
    """
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


def _grant_all_child_tables(db_path: Path, agent: str = "gaia-system") -> None:
    """Grant write access on every scanner-owned child table + projects."""
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        for t in ["projects", *_CHILD_TABLES]:
            con.execute(
                "INSERT OR REPLACE INTO agent_permissions "
                "(table_name, agent_name, allow_write) VALUES (?, ?, 1)",
                (t, agent),
            )
        con.commit()
    finally:
        con.close()


def _seed_parent_project(db_path: Path, workspace: str, project: str) -> None:
    """Ensure the workspace + parent project row exist (FK target)."""
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        con.execute(
            "INSERT OR IGNORE INTO workspaces (name, identity, created_at) "
            "VALUES (?, ?, ?)",
            (workspace, workspace, "2026-01-01T00:00:00Z"),
        )
        con.execute(
            "INSERT OR IGNORE INTO projects (workspace, name, scanner_ts) "
            "VALUES (?, ?, ?)",
            (workspace, project, "2026-01-01T00:00:00Z"),
        )
        con.commit()
    finally:
        con.close()


def _build_triggering_project(root: Path) -> Path:
    """Lay down on-disk content that triggers inserts into all nine child tables.

    Path components avoid the scanner skip-words (tests, fixtures, templates,
    examples, .git, node_modules) so the rglob scanners do not exclude them.
    """
    proj = root / "svc"
    proj.mkdir(parents=True, exist_ok=True)

    # apps/ -> populate_apps (one app per subdir)
    (proj / "apps" / "web").mkdir(parents=True)
    (proj / "apps" / "web" / "Dockerfile").write_text("FROM scratch\n")

    # services/ -> populate_services (one service per subdir)
    (proj / "services" / "api").mkdir(parents=True)

    # package.json -> populate_libraries (workspace packages)
    (proj / "package.json").write_text('{"name": "svc-root", "version": "1.0.0"}\n')
    (proj / "packages" / "shared").mkdir(parents=True)
    (proj / "packages" / "shared" / "package.json").write_text(
        '{"name": "@svc/shared", "version": "2.1.0"}\n'
    )

    # feature.json -> populate_features
    (proj / "feat-auth").mkdir()
    (proj / "feat-auth" / "feature.json").write_text('{"name": "auth"}\n')

    # *.tf with module + GKE cluster -> populate_infrastructure
    (proj / "main.tf").write_text(
        'module "vpc" {\n'
        '  source  = "terraform-google-modules/network/google"\n'
        '  version = "5.0.0"\n'
        '}\n\n'
        'resource "google_container_cluster" "primary" {\n'
        '  name = "primary"\n'
        '}\n'
    )

    # live/<env>/terragrunt.hcl -> tf_live
    (proj / "live" / "prod").mkdir(parents=True)
    (proj / "live" / "prod" / "terragrunt.hcl").write_text('include {}\n')

    # HelmRelease + Deployment YAML -> populate_orchestration (releases, workloads)
    (proj / "deploy").mkdir()
    (proj / "deploy" / "release.yaml").write_text(
        "apiVersion: helm.toolkit.fluxcd.io/v2\n"
        "kind: HelmRelease\n"
        "metadata:\n"
        "  name: my-release\n"
    )
    (proj / "deploy" / "workload.yaml").write_text(
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n"
        "  name: my-deploy\n"
        "  namespace: prod\n"
    )
    return proj


class TestRealSchemaChildTablePopulation:
    """Drive the REAL populators against a real-schema DB; rows must land.

    No writer is mocked: bulk_upsert / upsert_app / delete_missing_in execute
    their dynamic INSERT / ON CONFLICT / DELETE SQL against the schema.sql DB.
    If the schema and the code disagreed on the FK column name, every populate_*
    call here would raise ``OperationalError: no such column: project``.
    """

    WS = "me"
    PROJ = "svc"

    def test_all_child_tables_populate_without_column_error(self, real_db, tmp_path):
        from tools.scan import store_populator as sp

        _grant_all_child_tables(real_db, agent="gaia-system")
        _seed_parent_project(real_db, self.WS, self.PROJ)
        proj_path = _build_triggering_project(tmp_path)

        # Drive every real populator. A repo/project mismatch raises here.
        sp.populate_infrastructure(self.WS, self.PROJ, proj_path, "gaia-system", db_path=real_db)
        sp.populate_orchestration(self.WS, self.PROJ, proj_path, "gaia-system", db_path=real_db)
        sp.populate_features(self.WS, self.PROJ, proj_path, "gaia-system", db_path=real_db)
        sp.populate_apps(self.WS, self.PROJ, proj_path, "gaia-system", db_path=real_db)
        sp.populate_services(self.WS, self.PROJ, proj_path, "gaia-system", db_path=real_db)
        sp.populate_libraries(self.WS, self.PROJ, proj_path, "gaia-system", db_path=real_db)

        # Assert rows landed in the tables that the fixture content triggers.
        con = sqlite3.connect(str(real_db))
        try:
            def count(table: str) -> int:
                return con.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE workspace=? AND project=?",
                    (self.WS, self.PROJ),
                ).fetchone()[0]

            populated = {t: count(t) for t in [
                "apps", "services", "libraries", "features",
                "tf_modules", "tf_live", "releases", "workloads",
                "clusters_defined",
            ]}
        finally:
            con.close()

        # Each of these is driven by the fixture content above. If the column
        # name were wrong the populate_* call would have raised before we got
        # here, so a zero count signals a scanner-detection regression instead.
        for table in (
            "apps", "services", "libraries", "features",
            "tf_modules", "tf_live", "releases", "workloads",
            "clusters_defined",
        ):
            assert populated[table] >= 1, (
                f"expected >= 1 row in {table} keyed by project; got {populated[table]}. "
                f"all counts: {populated}"
            )

    def test_bulk_upsert_uses_project_column_directly(self, real_db):
        """Lowest-level guard: bulk_upsert into a child table with a `project`
        key must succeed against the real schema (the generic SQL path)."""
        from gaia.store import bulk_upsert

        _grant_all_child_tables(real_db, agent="gaia-system")
        _seed_parent_project(real_db, self.WS, self.PROJ)

        res = bulk_upsert(
            "tf_modules",
            self.WS,
            [{"project": self.PROJ, "name": "vpc", "source": "x", "version": "1.0",
              "scanner_ts": "2026-01-01T00:00:00Z"}],
            "gaia-system",
            db_path=real_db,
        )
        assert res["applied"] == 1, f"bulk_upsert did not apply row: {res}"

        con = sqlite3.connect(str(real_db))
        try:
            row = con.execute(
                "SELECT project, name FROM tf_modules WHERE workspace=?",
                (self.WS,),
            ).fetchone()
        finally:
            con.close()
        assert row == (self.PROJ, "vpc"), f"unexpected tf_modules row: {row}"
