"""
Tests for the scan path wiring of scan_workspace_to_store (store population).

Scan-core lives in ``tools.scan.core``; the CLI (``cli.scan``) is a thin
front-end over it. These tests target the core for unit coverage and the CLI
for integration coverage.

Covers:
  * core.ensure_scan_permissions grants write access for gaia-system on all scanner tables.
  * core.populate_store calls scan_workspace_to_store and persists project rows.
  * CLI scan (--json): invokes scan-core, projects row present after run.
  * scanner_ts is refreshed on re-scan (row exists, scanner_ts changes).
  * Store population is non-fatal: scan still returns 0 when populator fails.

Test isolation:
  * GAIA_DATA_DIR is redirected to tmp_path per test via monkeypatch so we
    never touch ~/.gaia/gaia.db.
  * core.run_scanners is patched to return a lightweight ScanOutput, avoiding
    the need for a full scanner environment (tools, env, git history, etc.).
  * Each workspace has a minimal git repo (git init + remote) so
    scan_workspace_to_store._list_repos can discover it.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

# Ensure bin/ is on sys.path
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

import cli.scan as scan_mod
from tools.scan import core as scan_core


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _MockArgs:
    """Minimal argparse.Namespace substitute with scan-flag defaults."""

    def __init__(self, **kwargs):
        defaults = {
            "path": None,
            "workspace": None,
            "dry_run": False,
            "json": False,
            "scanners": None,
            "check_staleness": False,
            "full": False,
            "no_color": True,
            "verbose": False,
            "npm_postinstall": False,
        }
        defaults.update(kwargs)
        self.__dict__.update(defaults)


def _make_dummy_scan_output():
    """Return a minimal ScanOutput-like object with no errors."""
    from tools.scan.orchestrator import ScanOutput
    return ScanOutput(
        sections_updated=["stack"],
        sections_preserved=[],
        warnings=[],
        errors=[],
        duration_ms=1.0,
        scanner_results={},
    )


def _init_git_repo(path: Path, remote: str = "https://github.com/test/repo.git") -> None:
    """Create a minimal git repo with an origin remote."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet"], cwd=str(path), check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", remote],
        cwd=str(path),
        check=True,
    )


def _count_projects(db_path: Path, workspace: str) -> int:
    """Return the number of project rows for a given workspace."""
    import sqlite3
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute(
            "SELECT COUNT(*) FROM projects WHERE workspace = ?", (workspace,)
        ).fetchone()
        return row[0] if row else 0
    finally:
        con.close()


def _get_project_scanner_ts(db_path: Path, workspace: str, name: str) -> str | None:
    """Fetch scanner_ts for a named project row."""
    import sqlite3
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute(
            "SELECT scanner_ts FROM projects WHERE workspace = ? AND name = ?",
            (workspace, name),
        ).fetchone()
        return row[0] if row else None
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Fixture: isolated DB + patched _run_scan
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Redirect GAIA_DATA_DIR to a temp dir and return the isolated db path."""
    db_dir = tmp_path / "gaia_data"
    db_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(db_dir))
    from gaia.paths import db_path
    return db_path()


@pytest.fixture()
def patch_run_scan(monkeypatch):
    """Patch tools.scan.core.run_scanners to return a dummy ScanOutput.

    This avoids requiring a real scanner environment (tools, env, CI context)
    while still exercising all the post-scan logic (populate + soft-prune).
    """
    monkeypatch.setattr(scan_core, "run_scanners", lambda root, cfg: _make_dummy_scan_output())


# ---------------------------------------------------------------------------
# ensure_scan_permissions (scan-core)
# ---------------------------------------------------------------------------

class TestEnsureScanPermissions:
    def test_grants_write_for_gaia_system_on_projects(self, tmp_db):
        """After ensure_scan_permissions, gaia-system can write projects."""
        from gaia.store.writer import _connect
        scan_core.ensure_scan_permissions(db_path=tmp_db)
        con = _connect(tmp_db)
        try:
            row = con.execute(
                "SELECT allow_write FROM agent_permissions "
                "WHERE table_name = 'projects' AND agent_name = ?",
                (scan_core.SCAN_AGENT,),
            ).fetchone()
        finally:
            con.close()
        assert row is not None, "permission row not inserted"
        assert row[0] == 1

    def test_grants_all_scanner_tables(self, tmp_db):
        """All SCAN_TABLES have a write grant for gaia-system."""
        from gaia.store.writer import _connect
        scan_core.ensure_scan_permissions(db_path=tmp_db)
        con = _connect(tmp_db)
        try:
            for table in scan_core.SCAN_TABLES:
                row = con.execute(
                    "SELECT allow_write FROM agent_permissions "
                    "WHERE table_name = ? AND agent_name = ?",
                    (table, scan_core.SCAN_AGENT),
                ).fetchone()
                assert row is not None, f"missing permission row for table={table}"
                assert row[0] == 1, f"allow_write != 1 for table={table}"
        finally:
            con.close()

    def test_idempotent(self, tmp_db):
        """Calling ensure_scan_permissions twice does not raise or duplicate rows."""
        scan_core.ensure_scan_permissions(db_path=tmp_db)
        scan_core.ensure_scan_permissions(db_path=tmp_db)  # second call must not fail
        from gaia.store.writer import _connect
        con = _connect(tmp_db)
        try:
            count = con.execute(
                "SELECT COUNT(*) FROM agent_permissions WHERE agent_name = ?",
                (scan_core.SCAN_AGENT,),
            ).fetchone()[0]
        finally:
            con.close()
        assert count == len(scan_core.SCAN_TABLES), (
            "idempotent grant should not insert duplicate rows"
        )


# ---------------------------------------------------------------------------
# populate_store (scan-core unit)
# ---------------------------------------------------------------------------

class TestPopulateStore:
    def test_persists_project_row(self, tmp_db, tmp_path):
        """populate_store writes a project row for a git repo under the workspace."""
        repo = tmp_path / "my-repo"
        _init_git_repo(repo)

        # The workspace identity passed to scan_workspace_to_store is resolved
        # from the workspace ROOT (tmp_path), not from the individual repo.
        from gaia.project import current as _current
        ws = _current(cwd=tmp_path)

        scan_core.populate_store(ws, tmp_path, db_path=tmp_db)

        count = _count_projects(tmp_db, ws)
        assert count >= 1, f"Expected >= 1 project row for workspace={ws!r}, got {count}"

    def test_refreshes_scanner_ts_on_rescan(self, tmp_db, tmp_path):
        """A second call to populate_store updates scanner_ts on the project row."""
        repo = tmp_path / "rescan-repo"
        _init_git_repo(repo)

        # Workspace identity from the workspace root (same as CLI perspective)
        from gaia.project import current as _current
        ws = _current(cwd=tmp_path)

        # First scan
        scan_core.populate_store(ws, tmp_path, db_path=tmp_db)
        first_ts = _get_project_scanner_ts(tmp_db, ws, "rescan-repo")
        assert first_ts is not None, "project row not written on first scan"

        # Wait a moment so scanner_ts can differ (granularity = 1 second)
        time.sleep(1.1)

        # Second scan
        scan_core.populate_store(ws, tmp_path, db_path=tmp_db)
        second_ts = _get_project_scanner_ts(tmp_db, ws, "rescan-repo")
        assert second_ts is not None, "project row missing after second scan"
        assert second_ts >= first_ts, "scanner_ts must not go backwards"
        # For a genuine refresh we expect strict inequality (different seconds)
        assert second_ts != first_ts, (
            "scanner_ts should be refreshed on re-scan "
            f"(first={first_ts!r}, second={second_ts!r})"
        )


# ---------------------------------------------------------------------------
# CLI integration: scan persists rows through scan-core
# ---------------------------------------------------------------------------

class TestCliScanPopulatesStore:
    """`gaia scan <target>` runs scan-core and persists project rows."""

    def test_scan_persists_project_row(self, tmp_db, tmp_path, patch_run_scan):
        """After a --json scan of a target with a git repo, projects has a row."""
        repo = tmp_path / "ws-repo"
        _init_git_repo(repo)

        # The CLI resolves workspace identity from project_root (= tmp_path),
        # not from the individual repo.  Match that identity here.
        from gaia.project import current as _current
        ws = _current(cwd=tmp_path)

        # Explicit target path -> on-demand entry point (no workspace guard).
        args = _MockArgs(path=str(tmp_path), json=True)
        rc = scan_mod.cmd_scan(args)
        assert rc == 0

        count = _count_projects(tmp_db, ws)
        assert count >= 1, (
            f"scan should persist project rows; got {count} for ws={ws!r}"
        )

    def test_populator_skipped_on_scanner_errors(self, tmp_db, tmp_path,
                                                 patch_run_scan, monkeypatch):
        """When ScanOutput has errors, populate_store must NOT be called."""
        from tools.scan.orchestrator import ScanOutput
        monkeypatch.setattr(
            scan_core,
            "run_scanners",
            lambda root, cfg: ScanOutput(errors=["scanner blew up"]),
        )
        populate_calls = []
        monkeypatch.setattr(
            scan_core,
            "populate_store",
            lambda ws, root, agent=scan_core.SCAN_AGENT, db_path=None: (
                populate_calls.append(1) or ({}, 0, [])
            ),
        )

        repo = tmp_path / "err-repo"
        _init_git_repo(repo)
        args = _MockArgs(path=str(tmp_path), json=True)
        rc = scan_mod.cmd_scan(args)
        assert rc == 1
        assert populate_calls == [], "populate_store must not be called when scan has errors"


# ---------------------------------------------------------------------------
# Non-fatal: populate failure must not break scan exit code
# ---------------------------------------------------------------------------

class TestPopulateStoreNonFatal:
    def test_populate_failure_does_not_break_scan(self, tmp_db, tmp_path,
                                                  patch_run_scan, monkeypatch):
        """If populate_store raises, cmd_scan still returns 0 (non-fatal)."""
        def _boom(ws, root, agent=scan_core.SCAN_AGENT, db_path=None):
            raise RuntimeError("simulated DB failure")

        monkeypatch.setattr(scan_core, "populate_store", _boom)

        repo = tmp_path / "nonfatal-repo"
        _init_git_repo(repo)

        args = _MockArgs(path=str(tmp_path), json=True)
        rc = scan_mod.cmd_scan(args)
        assert rc == 0, "scan must succeed even when store population fails"


# ---------------------------------------------------------------------------
# AC-4: prune stale project rows in re-scan
# ---------------------------------------------------------------------------

def _list_project_names(db_path: Path, workspace: str) -> set:
    """Return the set of project names in the projects table for a workspace."""
    import sqlite3
    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute(
            "SELECT name FROM projects WHERE workspace = ?", (workspace,)
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        con.close()


def _upsert_project_direct(db_path: Path, workspace: str, name: str) -> None:
    """Directly insert a project row (simulating a previous scan that wrote a now-stale row)."""
    import sqlite3
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        con.execute(
            "INSERT INTO workspaces (name, identity, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO NOTHING",
            (workspace, workspace, "2026-01-01T00:00:00Z"),
        )
        con.execute(
            "INSERT INTO projects (workspace, name, scanner_ts) VALUES (?, ?, ?) "
            "ON CONFLICT(workspace, name) DO NOTHING",
            (workspace, name, "2026-01-01T00:00:00Z"),
        )
        con.commit()
    finally:
        con.close()


def _write_app_agent_column(db_path: Path, workspace: str, project: str,
                             app_name: str, description: str) -> None:
    """Write an agent-owned description to an apps row (simulating an agent annotation)."""
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        # Ensure workspace + project rows exist first.
        con.execute(
            "INSERT INTO workspaces (name, identity, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO NOTHING",
            (workspace, workspace, "2026-01-01T00:00:00Z"),
        )
        con.execute(
            "INSERT INTO projects (workspace, name, scanner_ts) VALUES (?, ?, ?) "
            "ON CONFLICT(workspace, name) DO NOTHING",
            (workspace, project, "2026-01-01T00:00:00Z"),
        )
        # Upsert the apps row with an agent-written description.
        con.execute(
            "INSERT INTO apps (workspace, project, name, description, scanner_ts) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(workspace, project, name) DO UPDATE SET description = excluded.description",
            (workspace, project, app_name, description, "2026-01-01T00:00:00Z"),
        )
        con.commit()
    finally:
        con.close()


def _get_app_description(db_path: Path, workspace: str, project: str,
                          app_name: str) -> str | None:
    """Fetch the agent-owned 'description' column for an app row."""
    import sqlite3
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute(
            "SELECT description FROM apps "
            "WHERE workspace = ? AND project = ? AND name = ?",
            (workspace, project, app_name),
        ).fetchone()
        return row[0] if row else None
    finally:
        con.close()


def _get_project_status(db_path: Path, workspace: str, name: str):
    """Return (status, missing_since) for a project row, or (None, None)."""
    import sqlite3
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute(
            "SELECT status, missing_since FROM projects "
            "WHERE workspace = ? AND name = ?",
            (workspace, name),
        ).fetchone()
        return (row[0], row[1]) if row else (None, None)
    finally:
        con.close()


class TestSoftDeleteMissingProjectRows:
    """AC-4: missing project rows are soft-deleted (marked) on re-scan, never
    hard-deleted.

    Covers:
      (a) a project row that disappeared from disk is marked status='missing'
          with a missing_since timestamp -- NOT deleted.
      (b) live project rows are preserved (and active).
      (c) sub-rows belonging to a surviving project are not deleted.
      (d) soft-delete does not cross to other workspaces.
    """

    def test_missing_row_is_marked_not_deleted(self, tmp_db, tmp_path):
        """(a) A project that no longer exists on disk is marked missing, not deleted."""
        from gaia.project import current as _current

        # Set up a live repo that will appear in the scan.
        live_repo = tmp_path / "live-repo"
        _init_git_repo(live_repo)

        # Resolve workspace identity the way the CLI does.
        ws = _current(cwd=tmp_path)

        # Pre-populate a project row (simulating a previous scan of a now-deleted dir).
        _upsert_project_direct(tmp_db, ws, "me-briefs")

        # Sanity: row exists and is active before the scan.
        before = _list_project_names(tmp_db, ws)
        assert "me-briefs" in before, "row not seeded correctly"

        # Run populate_store: only 'live-repo' survives the discovery pass.
        scan_core.populate_store(ws, tmp_path, db_path=tmp_db)

        # The row must STILL EXIST (soft-delete, not hard-delete).
        after = _list_project_names(tmp_db, ws)
        assert "me-briefs" in after, (
            "soft-delete must NOT remove the row -- 'me-briefs' should still exist"
        )
        status, missing_since = _get_project_status(tmp_db, ws, "me-briefs")
        assert status == "missing", (
            f"vanished project should be status='missing', got {status!r}"
        )
        assert missing_since, (
            "missing_since timestamp must be set when a project is marked missing"
        )

    def test_missing_since_not_rebumped_on_repeated_scan(self, tmp_db, tmp_path):
        """A row already missing keeps its original missing_since across re-scans."""
        from gaia.project import current as _current

        live_repo = tmp_path / "live-repo"
        _init_git_repo(live_repo)
        ws = _current(cwd=tmp_path)
        _upsert_project_direct(tmp_db, ws, "gone")

        scan_core.populate_store(ws, tmp_path, db_path=tmp_db)
        _, first_missing_since = _get_project_status(tmp_db, ws, "gone")
        assert first_missing_since

        time.sleep(1.1)
        scan_core.populate_store(ws, tmp_path, db_path=tmp_db)
        _, second_missing_since = _get_project_status(tmp_db, ws, "gone")
        assert second_missing_since == first_missing_since, (
            "missing_since must NOT be re-bumped on a subsequent scan "
            f"(first={first_missing_since!r}, second={second_missing_since!r})"
        )

    def test_reappeared_project_reactivated(self, tmp_db, tmp_path):
        """A project that reappears on disk is reactivated: status='active', missing_since=NULL."""
        from gaia.project import current as _current

        ws = _current(cwd=tmp_path)

        # Seed a project row that is already marked missing (a prior scan
        # soft-deleted it). It is NOT on disk yet.
        _upsert_project_direct(tmp_db, ws, "comeback")
        from gaia.store.writer import _connect as _wconnect
        con = _wconnect(tmp_db)
        try:
            con.execute(
                "UPDATE projects SET status = 'missing', missing_since = ? "
                "WHERE workspace = ? AND name = ?",
                ("2026-01-01T00:00:00Z", ws, "comeback"),
            )
            con.commit()
        finally:
            con.close()
        status, _ = _get_project_status(tmp_db, ws, "comeback")
        assert status == "missing", "precondition: row seeded as missing"

        # Now the repo reappears on disk.
        comeback_repo = tmp_path / "comeback"
        _init_git_repo(comeback_repo)

        scan_core.populate_store(ws, tmp_path, db_path=tmp_db)

        status, missing_since = _get_project_status(tmp_db, ws, "comeback")
        assert status == "active", (
            f"reappeared project must be reactivated to 'active', got {status!r}"
        )
        assert missing_since is None, (
            f"reactivated project must clear missing_since, got {missing_since!r}"
        )

    def test_live_rows_preserved(self, tmp_db, tmp_path):
        """(b) Project rows for living repos are NOT pruned."""
        from gaia.project import current as _current

        live_repo = tmp_path / "keep-me"
        _init_git_repo(live_repo)

        ws = _current(cwd=tmp_path)

        # First scan: 'keep-me' is discovered and written.
        scan_core.populate_store(ws, tmp_path, db_path=tmp_db)

        # Second scan: re-run with the same repo present.
        scan_core.populate_store(ws, tmp_path, db_path=tmp_db)

        after = _list_project_names(tmp_db, ws)
        assert "keep-me" in after, (
            "'keep-me' repo still exists on disk but was pruned erroneously"
        )

    def test_live_project_sub_rows_not_pruned(self, tmp_db, tmp_path):
        """(c) Sub-rows belonging to surviving projects are NOT deleted by the prune.

        The prune targets the ``projects`` table (removing stale top-level rows).
        ON DELETE CASCADE would cascade-delete the sub-rows if a project row were
        deleted.  This test verifies that sub-rows for a SURVIVING project
        (one that is still on disk) remain after a re-scan.
        """
        from gaia.project import current as _current
        import sqlite3

        # Create a live repo with an apps/ subdirectory so the apps scanner
        # discovers an app row for it.
        live_repo = tmp_path / "my-service"
        _init_git_repo(live_repo)
        apps_dir = live_repo / "apps" / "web"
        apps_dir.mkdir(parents=True)

        ws = _current(cwd=tmp_path)

        # First scan: scanner writes the project row AND the apps row.
        scan_core.populate_store(ws, tmp_path, db_path=tmp_db)

        # Verify the app sub-row was written.
        con = sqlite3.connect(str(tmp_db))
        try:
            app_count_before = con.execute(
                "SELECT COUNT(*) FROM apps WHERE workspace = ? AND project = ?",
                (ws, "my-service"),
            ).fetchone()[0]
        finally:
            con.close()
        assert app_count_before >= 1, "apps sub-row not written on first scan"

        # Second scan (same repos present -- no prune expected).
        scan_core.populate_store(ws, tmp_path, db_path=tmp_db)

        # The project and its apps sub-rows must still exist.
        after_names = _list_project_names(tmp_db, ws)
        assert "my-service" in after_names, (
            "surviving project 'my-service' was erroneously pruned"
        )
        con = sqlite3.connect(str(tmp_db))
        try:
            app_count_after = con.execute(
                "SELECT COUNT(*) FROM apps WHERE workspace = ? AND project = ?",
                (ws, "my-service"),
            ).fetchone()[0]
        finally:
            con.close()
        assert app_count_after >= 1, (
            "apps sub-rows for surviving project 'my-service' were cascade-deleted "
            "by an erroneous prune"
        )

    def test_prune_does_not_cross_workspaces(self, tmp_db, tmp_path):
        """(d) Pruning one workspace does NOT delete rows belonging to other workspaces."""
        from gaia.project import current as _current

        # Workspace A: has a live repo.
        ws_a_root = tmp_path / "ws-a"
        ws_a_root.mkdir()
        live_repo_a = ws_a_root / "repo-a"
        _init_git_repo(live_repo_a)
        ws_a = _current(cwd=ws_a_root)

        # Workspace B: is a separate workspace; inject a project row directly.
        ws_b = "other-workspace-xyzzy"
        _upsert_project_direct(tmp_db, ws_b, "project-b")

        # Verify project-b row is present before running ws_a scan.
        before_b = _list_project_names(tmp_db, ws_b)
        assert "project-b" in before_b, "cross-workspace row not seeded"

        # Run populate_store for workspace A.
        scan_core.populate_store(ws_a, ws_a_root, db_path=tmp_db)

        # Workspace B's rows must be untouched.
        after_b = _list_project_names(tmp_db, ws_b)
        assert "project-b" in after_b, (
            "prune crossed workspace boundary: 'project-b' in workspace B "
            "was deleted by a scan of workspace A"
        )


# ---------------------------------------------------------------------------
# AC-4: reader filtering (provider.get_context) on soft-deleted projects
# ---------------------------------------------------------------------------

def _seed_missing_project_with_child(db_path: Path, workspace: str,
                                     project: str, app_name: str) -> None:
    """Seed a workspace + a MISSING project + one child apps row."""
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        con.execute(
            "INSERT INTO workspaces (name, identity, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO NOTHING",
            (workspace, workspace, "2026-01-01T00:00:00Z"),
        )
        con.execute(
            "INSERT INTO projects (workspace, name, scanner_ts, status, missing_since) "
            "VALUES (?, ?, ?, 'missing', ?) "
            "ON CONFLICT(workspace, name) DO UPDATE SET "
            "status='missing', missing_since=excluded.missing_since",
            (workspace, project, "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"),
        )
        con.execute(
            "INSERT INTO apps (workspace, project, name, scanner_ts) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(workspace, project, name) DO NOTHING",
            (workspace, project, app_name, "2026-01-01T00:00:00Z"),
        )
        con.commit()
    finally:
        con.close()


def _seed_active_project_with_child(db_path: Path, workspace: str,
                                    project: str, app_name: str) -> None:
    """Seed a workspace + an ACTIVE project + one child apps row."""
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        con.execute(
            "INSERT INTO workspaces (name, identity, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO NOTHING",
            (workspace, workspace, "2026-01-01T00:00:00Z"),
        )
        con.execute(
            "INSERT INTO projects (workspace, name, scanner_ts, status) "
            "VALUES (?, ?, ?, 'active') "
            "ON CONFLICT(workspace, name) DO UPDATE SET status='active'",
            (workspace, project, "2026-01-01T00:00:00Z"),
        )
        con.execute(
            "INSERT INTO apps (workspace, project, name, scanner_ts) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(workspace, project, name) DO NOTHING",
            (workspace, project, app_name, "2026-01-01T00:00:00Z"),
        )
        con.commit()
    finally:
        con.close()


class TestProviderFiltersMissing:
    """AC-4: get_context filters status='missing' projects (and their children)
    by default, but exposes them with include_missing=True."""

    def test_active_view_excludes_missing_project(self, tmp_db):
        from gaia.store.provider import get_context
        ws = "ws-filter"
        _seed_active_project_with_child(tmp_db, ws, "alive", "alive-app")
        _seed_missing_project_with_child(tmp_db, ws, "vanished", "vanished-app")

        ctx = get_context(ws, db_path=tmp_db)
        names = {p["name"] for p in ctx["workspace"]["projects"]}
        assert "alive" in names
        assert "vanished" not in names, (
            "missing project must be filtered out of the default active view"
        )

    def test_active_view_excludes_child_rows_of_missing_project(self, tmp_db):
        from gaia.store.provider import get_context
        ws = "ws-filter-children"
        _seed_active_project_with_child(tmp_db, ws, "alive", "alive-app")
        _seed_missing_project_with_child(tmp_db, ws, "vanished", "vanished-app")

        ctx = get_context(ws, db_path=tmp_db)
        app_names = {a["name"] for a in ctx["workspace"]["apps"]}
        assert "alive-app" in app_names, "child of active project must remain"
        assert "vanished-app" not in app_names, (
            "child rows of a missing project must NOT contaminate active queries"
        )

    def test_include_missing_returns_everything(self, tmp_db):
        from gaia.store.provider import get_context
        ws = "ws-include-missing"
        _seed_active_project_with_child(tmp_db, ws, "alive", "alive-app")
        _seed_missing_project_with_child(tmp_db, ws, "vanished", "vanished-app")

        ctx = get_context(ws, db_path=tmp_db, include_missing=True)
        names = {p["name"] for p in ctx["workspace"]["projects"]}
        app_names = {a["name"] for a in ctx["workspace"]["apps"]}
        assert {"alive", "vanished"} <= names, (
            "include_missing=True must return missing projects too "
            "('existed but no longer' is consultable)"
        )
        assert {"alive-app", "vanished-app"} <= app_names, (
            "include_missing=True must return children of missing projects too"
        )

    def test_missing_project_row_carries_status_when_included(self, tmp_db):
        from gaia.store.provider import get_context
        ws = "ws-status-visible"
        _seed_missing_project_with_child(tmp_db, ws, "vanished", "vanished-app")

        ctx = get_context(ws, db_path=tmp_db, include_missing=True)
        proj = next(p for p in ctx["workspace"]["projects"] if p["name"] == "vanished")
        assert proj["status"] == "missing"
        assert proj["missing_since"], "missing_since must be exposed in the row"


# ---------------------------------------------------------------------------
# AC-4: per-project isolation -- one bad repo does not abort the whole scan
# ---------------------------------------------------------------------------

class TestPerProjectIsolation:
    """A repo whose population raises must not tear down the rest of the scan;
    the other repos still populate and the failure is collected."""

    def test_one_failing_repo_does_not_abort_others(self, tmp_db, tmp_path,
                                                    monkeypatch):
        from tools.scan import store_populator as sp

        good_a = tmp_path / "good-a"
        bad = tmp_path / "bad-repo"
        good_b = tmp_path / "good-b"
        for r in (good_a, bad, good_b):
            (r / ".git").mkdir(parents=True)

        from gaia.project import current as _current
        ws = _current(cwd=tmp_path)

        real_populate_project = sp.populate_project

        def _flaky_populate_project(workspace, project_path, agent, **kwargs):
            if project_path.name == "bad-repo":
                raise RuntimeError("simulated bad column / permissions error")
            return real_populate_project(workspace, project_path, agent, **kwargs)

        monkeypatch.setattr(sp, "populate_project", _flaky_populate_project)

        scan_core.ensure_scan_permissions(db_path=tmp_db)
        results = sp.scan_workspace_to_store(
            workspace=ws, root=tmp_path, agent=scan_core.SCAN_AGENT,
            db_path=tmp_db,
        )

        # The two good repos populated despite the bad one raising.
        names = _list_project_names(tmp_db, ws)
        assert "good-a" in names and "good-b" in names, (
            "a single failing repo must NOT prevent the others from populating"
        )

        # The failure was collected, not swallowed silently.
        failures = results.get("__failures__")
        assert failures, "the failing repo must be recorded in __failures__"
        failed_projects = {f["project"] for f in failures}
        assert "bad-repo" in failed_projects

    def test_failing_repo_not_marked_missing(self, tmp_db, tmp_path, monkeypatch):
        """A repo that is on disk but fails to populate must NOT be soft-deleted
        on account of that transient failure (it stays as-is)."""
        from tools.scan import store_populator as sp
        from gaia.project import current as _current

        live = tmp_path / "live"
        flaky = tmp_path / "flaky"
        for r in (live, flaky):
            (r / ".git").mkdir(parents=True)

        ws = _current(cwd=tmp_path)
        # Pre-seed 'flaky' as an existing active row from a prior clean scan.
        _upsert_project_direct(tmp_db, ws, "flaky")

        real_populate_project = sp.populate_project

        def _flaky_populate_project(workspace, project_path, agent, **kwargs):
            if project_path.name == "flaky":
                raise RuntimeError("transient failure")
            return real_populate_project(workspace, project_path, agent, **kwargs)

        monkeypatch.setattr(sp, "populate_project", _flaky_populate_project)

        scan_core.populate_store(ws, tmp_path, db_path=tmp_db)

        # 'flaky' is on disk; its population failed. It must NOT be marked missing.
        status, _ = _get_project_status(tmp_db, ws, "flaky")
        assert status != "missing", (
            "a failed-but-on-disk repo must not be soft-deleted due to a "
            f"transient population failure (status={status!r})"
        )


# Note: setup-stubbing helpers were removed -- scan no longer invokes install
# functions (setup.py), so there is nothing to stub. Scan-core is patched via
# the patch_run_scan fixture (tools.scan.core.run_scanners).
