"""
Track B gap fix tests: populate_apps / populate_services / populate_libraries
/ populate_gaia_installations.

These cover the populators wired into ``scan_workspace_to_store`` to satisfy
the B2 / B5 acceptance criteria that were left open after the original
brief closures (apps/services/libraries tables stayed empty;
``gaia_installations`` had no scanner backfill at all).

Test pattern mirrors ``test_populate_features.py``:

  * ``tmp_db`` fixture isolates ``~/.gaia/gaia.db`` per test via
    ``GAIA_DATA_DIR``.
  * ``_setup_permissions`` grants the scanner agent write access on the
    tables it needs.
  * Each fixture builds a minimal repo on disk (with ``git init`` + remote)
    and then asserts on rows in the relevant table.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


def _grant(con, table, agent):
    con.execute(
        "INSERT OR REPLACE INTO agent_permissions (table_name, agent_name, allow_write) "
        "VALUES (?, ?, 1)",
        (table, agent),
    )
    con.commit()


def _setup_permissions(db_path, tables, agent="developer"):
    from gaia.store.writer import _connect
    con = _connect(db_path)
    for t in tables:
        _grant(con, t, agent)
    con.close()


def _init_repo(repo_path: Path, remote: str = "https://example.com/x/y.git") -> None:
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet"], cwd=str(repo_path), check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", remote],
        cwd=str(repo_path),
        check=True,
    )


# ---------------------------------------------------------------------------
# _scan_apps
# ---------------------------------------------------------------------------

class TestScanApps:
    def test_apps_directory_emits_one_per_subdir(self, tmp_path):
        from tools.scan.store_populator import _scan_apps

        repo = tmp_path / "monorepo"
        (repo / "apps" / "nova").mkdir(parents=True)
        (repo / "apps" / "lighthouse").mkdir(parents=True)

        result = _scan_apps(repo)
        names = {a["name"] for a in result}
        assert names == {"nova", "lighthouse"}

    def test_apps_kind_inferred_from_dockerfile(self, tmp_path):
        from tools.scan.store_populator import _scan_apps

        repo = tmp_path / "monorepo"
        (repo / "apps" / "api").mkdir(parents=True)
        (repo / "apps" / "api" / "Dockerfile").write_text("FROM node:20\n")
        (repo / "apps" / "frontend").mkdir(parents=True)

        result = _scan_apps(repo)
        kinds = {a["name"]: a["kind"] for a in result}
        assert kinds["api"] == "service"
        assert kinds["frontend"] == "app"

    def test_single_repo_with_package_json_emits_one_app(self, tmp_path):
        from tools.scan.store_populator import _scan_apps

        repo = tmp_path / "single-app"
        repo.mkdir()
        (repo / "package.json").write_text(json.dumps({"name": "my-app", "version": "1.0.0"}))

        result = _scan_apps(repo)
        names = [a["name"] for a in result]
        assert names == ["my-app"]

    def test_workspace_root_skipped_as_app(self, tmp_path):
        """A package.json with a workspaces field is a monorepo aggregator,
        not an app -- libraries/apps populators handle the children."""
        from tools.scan.store_populator import _scan_apps

        repo = tmp_path / "monorepo-root"
        repo.mkdir()
        (repo / "package.json").write_text(
            json.dumps({"name": "monorepo", "workspaces": ["packages/*"]})
        )

        result = _scan_apps(repo)
        # Without an apps/ dir AND with a workspaces field, nothing emits
        assert result == []

    def test_no_marker_returns_empty(self, tmp_path):
        from tools.scan.store_populator import _scan_apps

        repo = tmp_path / "empty"
        repo.mkdir()
        result = _scan_apps(repo)
        assert result == []


# ---------------------------------------------------------------------------
# _scan_services
# ---------------------------------------------------------------------------

class TestScanServices:
    def test_services_directory_emits_one_per_subdir(self, tmp_path):
        from tools.scan.store_populator import _scan_services

        repo = tmp_path / "infra"
        (repo / "services" / "auth").mkdir(parents=True)
        (repo / "services" / "billing").mkdir(parents=True)

        result = _scan_services(repo)
        names = {s["name"] for s in result}
        assert names == {"auth", "billing"}

    def test_docker_compose_top_level_services(self, tmp_path):
        from tools.scan.store_populator import _scan_services

        repo = tmp_path / "compose"
        repo.mkdir()
        (repo / "docker-compose.yml").write_text(
            "version: '3'\n"
            "services:\n"
            "  postgres:\n"
            "    image: postgres:15\n"
            "  redis:\n"
            "    image: redis:7\n"
            "  api:\n"
            "    image: my-org/api:latest\n"
        )

        result = _scan_services(repo)
        kinds = {s["name"]: s["kind"] for s in result}
        assert kinds["postgres"] == "database"
        assert kinds["redis"] == "cache"
        assert kinds["api"] == "api"

    def test_no_marker_returns_empty(self, tmp_path):
        from tools.scan.store_populator import _scan_services

        repo = tmp_path / "empty"
        repo.mkdir()
        result = _scan_services(repo)
        assert result == []


# ---------------------------------------------------------------------------
# _scan_libraries
# ---------------------------------------------------------------------------

class TestScanLibraries:
    def test_packages_directory_with_package_json(self, tmp_path):
        from tools.scan.store_populator import _scan_libraries

        repo = tmp_path / "monorepo"
        pkg_a = repo / "packages" / "shared"
        pkg_a.mkdir(parents=True)
        (pkg_a / "package.json").write_text(
            json.dumps({"name": "@org/shared", "version": "1.2.3"})
        )

        result = _scan_libraries(repo)
        names = {l["name"]: l for l in result}
        assert "@org/shared" in names
        assert names["@org/shared"]["version"] == "1.2.3"
        assert names["@org/shared"]["language"] == "javascript"

    def test_libs_directory_alternative_convention(self, tmp_path):
        from tools.scan.store_populator import _scan_libraries

        repo = tmp_path / "alt"
        (repo / "libs" / "utils").mkdir(parents=True)

        result = _scan_libraries(repo)
        names = {l["name"] for l in result}
        assert "utils" in names

    def test_workspaces_field_globs_directories(self, tmp_path):
        from tools.scan.store_populator import _scan_libraries

        repo = tmp_path / "ws"
        repo.mkdir()
        (repo / "package.json").write_text(
            json.dumps({"name": "ws-root", "workspaces": ["modules/*"]})
        )
        mod = repo / "modules" / "core"
        mod.mkdir(parents=True)
        (mod / "package.json").write_text(json.dumps({"name": "@ws/core", "version": "0.1.0"}))

        result = _scan_libraries(repo)
        names = {l["name"]: l for l in result}
        assert "@ws/core" in names
        assert names["@ws/core"]["version"] == "0.1.0"

    def test_no_libraries_returns_empty(self, tmp_path):
        from tools.scan.store_populator import _scan_libraries

        repo = tmp_path / "empty"
        repo.mkdir()
        result = _scan_libraries(repo)
        assert result == []


# ---------------------------------------------------------------------------
# _scan_gaia_installations
# ---------------------------------------------------------------------------

class TestScanGaiaInstallations:
    def test_node_modules_marker_emits_row_with_version(self, tmp_path):
        from tools.scan.store_populator import _scan_gaia_installations

        ws = tmp_path / "ws"
        gaia_dir = ws / "node_modules" / "@jaguilar87" / "gaia"
        gaia_dir.mkdir(parents=True)
        (gaia_dir / "package.json").write_text(
            json.dumps({"name": "@jaguilar87/gaia", "version": "5.0.0-rc.3"})
        )

        result = _scan_gaia_installations(ws)
        assert len(result) == 1
        assert result[0]["version"] == "5.0.0-rc.3"
        assert result[0]["install_mode"] == "npm"
        assert result[0]["machine"]  # hostname is non-empty

    def test_claude_footprint_marker(self, tmp_path):
        from tools.scan.store_populator import _scan_gaia_installations

        ws = tmp_path / "ws"
        (ws / ".claude" / "skills").mkdir(parents=True)
        (ws / ".claude" / "agents").mkdir(parents=True)

        result = _scan_gaia_installations(ws)
        assert len(result) == 1
        assert result[0]["install_mode"] == "unknown"
        assert result[0]["version"] is None

    def test_no_marker_returns_empty(self, tmp_path):
        from tools.scan.store_populator import _scan_gaia_installations

        ws = tmp_path / "empty"
        ws.mkdir()
        result = _scan_gaia_installations(ws)
        assert result == []


# ---------------------------------------------------------------------------
# Integration: populate_apps writes to store
# ---------------------------------------------------------------------------

class TestPopulateApps:
    def test_apps_table_populated(self, tmp_db, tmp_path):
        from gaia.store.writer import _connect
        from tools.scan.store_populator import populate_apps, populate_repo

        _setup_permissions(tmp_db, ["repos", "apps"], agent="developer")

        repo_path = tmp_path / "qxo-monorepo"
        _init_repo(repo_path, "https://bitbucket.org/aaxisdigital/qxo.git")
        for name in ("nova", "lighthouse"):
            (repo_path / "apps" / name).mkdir(parents=True)
        # nova has Dockerfile -> kind=service
        (repo_path / "apps" / "nova" / "Dockerfile").write_text("FROM node:20\n")

        populate_repo("ws-qxo", repo_path, "developer", db_path=tmp_db)
        result = populate_apps("ws-qxo", "qxo-monorepo", repo_path, "developer", db_path=tmp_db)

        assert "apps" in result
        upsert = result["apps"].get("upsert", {})
        assert upsert.get("applied", 0) >= 2

        con = _connect(tmp_db)
        rows = con.execute(
            "SELECT name, kind FROM apps WHERE project = ? AND repo = ?",
            ("ws-qxo", "qxo-monorepo"),
        ).fetchall()
        con.close()

        kinds = {r["name"]: r["kind"] for r in rows}
        assert kinds.get("nova") == "service"
        assert kinds.get("lighthouse") == "app"


# ---------------------------------------------------------------------------
# Integration: populate_services writes to store
# ---------------------------------------------------------------------------

class TestPopulateServices:
    def test_services_table_populated_from_compose(self, tmp_db, tmp_path):
        from gaia.store.writer import _connect
        from tools.scan.store_populator import populate_repo, populate_services

        _setup_permissions(tmp_db, ["repos", "services"], agent="developer")

        repo_path = tmp_path / "platform"
        _init_repo(repo_path)
        (repo_path / "docker-compose.yml").write_text(
            "version: '3'\nservices:\n"
            "  postgres:\n    image: postgres:15\n"
            "  api:\n    image: org/api:1.0\n"
        )

        populate_repo("ws-test", repo_path, "developer", db_path=tmp_db)
        populate_services("ws-test", "platform", repo_path, "developer", db_path=tmp_db)

        con = _connect(tmp_db)
        rows = con.execute(
            "SELECT name, kind FROM services WHERE project = ? AND repo = ?",
            ("ws-test", "platform"),
        ).fetchall()
        con.close()

        kinds = {r["name"]: r["kind"] for r in rows}
        assert kinds.get("postgres") == "database"
        assert kinds.get("api") == "api"


# ---------------------------------------------------------------------------
# Integration: populate_libraries writes to store
# ---------------------------------------------------------------------------

class TestPopulateLibraries:
    def test_libraries_table_populated(self, tmp_db, tmp_path):
        from gaia.store.writer import _connect
        from tools.scan.store_populator import populate_libraries, populate_repo

        _setup_permissions(tmp_db, ["repos", "libraries"], agent="developer")

        repo_path = tmp_path / "platform"
        _init_repo(repo_path)
        pkg_dir = repo_path / "packages" / "shared"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "package.json").write_text(
            json.dumps({"name": "@bw/shared", "version": "0.5.0"})
        )

        populate_repo("ws-test", repo_path, "developer", db_path=tmp_db)
        populate_libraries("ws-test", "platform", repo_path, "developer", db_path=tmp_db)

        con = _connect(tmp_db)
        rows = con.execute(
            "SELECT name, version, language FROM libraries WHERE project = ? AND repo = ?",
            ("ws-test", "platform"),
        ).fetchall()
        con.close()

        assert len(rows) == 1
        assert rows[0]["name"] == "@bw/shared"
        assert rows[0]["version"] == "0.5.0"
        assert rows[0]["language"] == "javascript"


# ---------------------------------------------------------------------------
# Integration: populate_gaia_installations writes to store
# ---------------------------------------------------------------------------

class TestPopulateGaiaInstallations:
    def test_gaia_installations_table_populated(self, tmp_db, tmp_path):
        from gaia.store.writer import _connect, _ensure_project_row
        from tools.scan.store_populator import populate_gaia_installations

        _setup_permissions(tmp_db, ["gaia_installations"], agent="gaia-system")

        # Pre-seed the project row -- gaia_installations has FK -> projects
        con = _connect(tmp_db)
        _ensure_project_row(con, "ws-test")
        con.commit()
        con.close()

        ws = tmp_path / "ws"
        gaia_dir = ws / "node_modules" / "@jaguilar87" / "gaia"
        gaia_dir.mkdir(parents=True)
        (gaia_dir / "package.json").write_text(
            json.dumps({"name": "@jaguilar87/gaia", "version": "5.0.0-rc.3"})
        )

        result = populate_gaia_installations("ws-test", ws, "gaia-system", db_path=tmp_db)
        upsert = result["gaia_installations"].get("upsert", {})
        assert upsert.get("applied", 0) == 1

        con = _connect(tmp_db)
        rows = con.execute(
            "SELECT machine, version, install_mode FROM gaia_installations WHERE project = ?",
            ("ws-test",),
        ).fetchall()
        con.close()

        assert len(rows) == 1
        assert rows[0]["version"] == "5.0.0-rc.3"
        assert rows[0]["install_mode"] == "npm"


# ---------------------------------------------------------------------------
# scan_workspace_to_store integration: new tables populate end-to-end
# ---------------------------------------------------------------------------

class TestScanWorkspaceWiring:
    def test_apps_services_libraries_gaia_installations_emit_rows(self, tmp_db, tmp_path):
        """Single-pass scan_workspace_to_store should populate all four
        new tables in one call (B2 + B5 gap closure)."""
        from gaia.store.writer import _connect
        from tools.scan.store_populator import scan_workspace_to_store

        _setup_permissions(
            tmp_db,
            ["repos", "apps", "services", "libraries", "gaia_installations",
             "features", "tf_modules", "tf_live", "releases", "workloads",
             "clusters_defined"],
            agent="developer",
        )

        # Workspace with a single repo that has all four signals
        ws_root = tmp_path / "ws"
        repo_path = ws_root / "platform"
        _init_repo(repo_path)
        # apps/ with one Dockerfile-backed service
        (repo_path / "apps" / "api").mkdir(parents=True)
        (repo_path / "apps" / "api" / "Dockerfile").write_text("FROM node:20\n")
        # services/ subdir
        (repo_path / "services" / "auth").mkdir(parents=True)
        # packages/ with a package.json
        pkg = repo_path / "packages" / "shared"
        pkg.mkdir(parents=True)
        (pkg / "package.json").write_text(json.dumps({"name": "@x/shared", "version": "0.0.1"}))

        # Gaia installation marker at workspace root
        gaia_dir = ws_root / "node_modules" / "@jaguilar87" / "gaia"
        gaia_dir.mkdir(parents=True)
        (gaia_dir / "package.json").write_text(
            json.dumps({"name": "@jaguilar87/gaia", "version": "5.0.0-rc.3"})
        )

        results = scan_workspace_to_store("ws-test", ws_root, "developer", db_path=tmp_db)

        # Sanity: per-repo result has new keys
        assert "platform" in results
        assert "apps" in results["platform"]
        assert "services" in results["platform"]
        assert "libraries" in results["platform"]
        # Workspace-scoped result
        assert "__workspace__" in results
        assert "gaia_installations" in results["__workspace__"]

        con = _connect(tmp_db)
        try:
            apps_count = con.execute(
                "SELECT COUNT(*) FROM apps WHERE project = ?", ("ws-test",)
            ).fetchone()[0]
            services_count = con.execute(
                "SELECT COUNT(*) FROM services WHERE project = ?", ("ws-test",)
            ).fetchone()[0]
            libs_count = con.execute(
                "SELECT COUNT(*) FROM libraries WHERE project = ?", ("ws-test",)
            ).fetchone()[0]
            gaia_count = con.execute(
                "SELECT COUNT(*) FROM gaia_installations WHERE project = ?", ("ws-test",)
            ).fetchone()[0]
        finally:
            con.close()

        assert apps_count >= 1, "apps row missing for api"
        assert services_count >= 1, "services row missing for auth"
        assert libs_count >= 1, "libraries row missing for @x/shared"
        assert gaia_count >= 1, "gaia_installations row missing"
