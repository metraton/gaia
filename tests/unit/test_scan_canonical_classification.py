"""
Unit tests for the CANONICAL classification rule of the scanner
(brief gaia-scan-overhaul).

The canonical rule:
  * A **project** is a dir with ``.git``.
  * A **workspace** is a dir with a Gaia installation, detected by the
    mode-agnostic signal ``.claude/plugin-registry.json`` whose
    ``installed[*].name`` includes ``gaia-ops`` / ``gaia-security``.
    A third-party ``.claude`` WITHOUT that registry must NOT register.
  * Intermediate folders (no ``.git``, no install) are not entities; their
    projects are attributed to the nearest installed-ancestor workspace, with
    ``group_name`` = the container between the workspace and the project.
  * A dir may be BOTH a project and a workspace -> rows in both tables.
  * When no installed ancestor exists in the tree, projects fall back to the
    CLI-root workspace.

Coverage:
  (1) a git dir is registered as a project WITH its path populated.
  (2) a dir with the Gaia registry IS a workspace; a third-party .claude is NOT.
  (3) a project under an intermediate folder is attributed to the nearest
      installed-ancestor workspace (not the root), with group_name = container.
  (4) the real-world aaxis/ tree: nfi is a workspace, nfi-oro-com is a project
      of nfi with its path; "list projects of nfi" returns nfi-oro-com.
  (5) a dir that is both git + install appears in BOTH tables.

Test isolation:
  * GAIA_DATA_DIR is redirected to a tmp dir so we never touch ~/.gaia/gaia.db.
  * gaia-system / scanner permissions are granted on the relevant tables.
  * The .claude/plugin-registry.json fixtures live entirely under pytest's
    tmp_path -- this is an isolated temp tree, NOT the protected workspace
    .claude/ directory.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tools.scan.store_populator import (
    _is_installed_gaia_workspace,
    _list_installed_workspaces,
    _nearest_installed_ancestor,
    scan_workspace_to_store,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCAN_TABLES = [
    "workspaces", "projects", "apps", "services", "libraries", "features",
    "integrations", "gaia_installations",
    "tf_modules", "tf_live", "releases", "workloads", "clusters_defined",
]


def _make_repo(path: Path) -> None:
    """Create a minimal git repo (a .git dir marker)."""
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()


def _make_gaia_install(path: Path) -> None:
    """Create the canonical Gaia install signal at `path`.

    Writes ``path/.claude/plugin-registry.json`` listing gaia-ops as installed.
    """
    claude = path / ".claude"
    claude.mkdir(parents=True, exist_ok=True)
    (claude / "plugin-registry.json").write_text(
        json.dumps({"installed": [{"name": "gaia-ops", "version": "5.0.0"}]})
    )


def _make_third_party_claude(path: Path) -> None:
    """Create a non-Gaia .claude (no plugin-registry, or unrelated plugins)."""
    claude = path / ".claude"
    claude.mkdir(parents=True, exist_ok=True)
    # A settings file but NO Gaia plugin-registry -> must not be detected.
    (claude / "settings.json").write_text(json.dumps({"foo": "bar"}))
    # And a registry that lists OTHER plugins only.
    (claude / "plugin-registry.json").write_text(
        json.dumps({"installed": [{"name": "some-other-plugin"}]})
    )


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Isolated DB + scanner write permissions for 'scanner' agent."""
    db_dir = tmp_path / "gaia_data"
    db_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(db_dir))
    from gaia.paths import db_path
    dbp = db_path()
    from gaia.store.writer import _connect
    con = _connect(dbp)
    try:
        for table in _SCAN_TABLES:
            con.execute(
                "INSERT OR REPLACE INTO agent_permissions "
                "(table_name, agent_name, allow_write) VALUES (?, ?, 1)",
                (table, "scanner"),
            )
        con.commit()
    finally:
        con.close()
    return dbp


def _project_rows(db_path: Path, workspace: str) -> list[tuple]:
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(
            "SELECT name, group_name, path FROM projects WHERE workspace = ?",
            (workspace,),
        ).fetchall()
    finally:
        con.close()


def _workspace_names(db_path: Path) -> set[str]:
    con = sqlite3.connect(str(db_path))
    try:
        return {r[0] for r in con.execute("SELECT name FROM workspaces").fetchall()}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# (2) workspace detection signal
# ---------------------------------------------------------------------------

class TestInstallSignal:
    def test_registry_with_gaia_ops_is_workspace(self, tmp_path):
        _make_gaia_install(tmp_path)
        assert _is_installed_gaia_workspace(tmp_path) is True

    def test_registry_with_gaia_security_is_workspace(self, tmp_path):
        claude = tmp_path / ".claude"
        claude.mkdir()
        (claude / "plugin-registry.json").write_text(
            json.dumps({"installed": [{"name": "gaia-security"}]})
        )
        assert _is_installed_gaia_workspace(tmp_path) is True

    def test_third_party_claude_is_not_workspace(self, tmp_path):
        _make_third_party_claude(tmp_path)
        assert _is_installed_gaia_workspace(tmp_path) is False

    def test_bare_claude_without_registry_is_not_workspace(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        assert _is_installed_gaia_workspace(tmp_path) is False

    def test_no_claude_is_not_workspace(self, tmp_path):
        assert _is_installed_gaia_workspace(tmp_path) is False


# ---------------------------------------------------------------------------
# _list_installed_workspaces / _nearest_installed_ancestor
# ---------------------------------------------------------------------------

class TestInstalledWorkspaceWalk:
    def test_finds_nested_installed_workspace(self, tmp_path):
        # aaxis/ (no install) -> nfi/ (install)
        nfi = tmp_path / "aaxis" / "nfi"
        nfi.mkdir(parents=True)
        _make_gaia_install(nfi)
        found = _list_installed_workspaces(tmp_path)
        assert nfi in found

    def test_third_party_claude_not_listed(self, tmp_path):
        clone = tmp_path / "some-clone"
        clone.mkdir()
        _make_third_party_claude(clone)
        found = _list_installed_workspaces(tmp_path)
        assert clone not in found

    def test_nearest_strict_ancestor(self, tmp_path):
        # root(install) / mid(install) / sub / proj
        root = tmp_path
        _make_gaia_install(root)
        mid = root / "mid"
        mid.mkdir()
        _make_gaia_install(mid)
        proj = mid / "sub" / "proj"
        proj.mkdir(parents=True)
        installed = {root, mid}
        # nearest strict ancestor of proj is mid, not root.
        assert _nearest_installed_ancestor(proj, installed, root) == mid

    def test_excludes_self(self, tmp_path):
        # A dir that is itself installed is NOT its own ancestor.
        root = tmp_path
        proj = root / "proj"
        proj.mkdir()
        installed = {root, proj}
        assert _nearest_installed_ancestor(proj, installed, root) == root


# ---------------------------------------------------------------------------
# (1) project path is populated
# ---------------------------------------------------------------------------

class TestProjectPathPopulated:
    def test_project_row_has_path(self, tmp_db, tmp_path):
        repo = tmp_path / "my-repo"
        _make_repo(repo)
        scan_workspace_to_store("ws", tmp_path, "scanner", db_path=tmp_db)
        rows = _project_rows(tmp_db, "ws")
        assert len(rows) == 1
        name, group_name, path = rows[0]
        assert name == "my-repo"
        assert path == str(repo), f"path not populated: {path!r}"


# ---------------------------------------------------------------------------
# (3) attribution to nearest installed-ancestor workspace
# ---------------------------------------------------------------------------

class TestAttributionToNearestWorkspace:
    def test_project_attributed_to_nearest_not_root(self, tmp_db, tmp_path):
        # root "ws" (CLI anchor, no install) / nfi (install) / group / proj
        nfi = tmp_path / "nfi"
        nfi.mkdir()
        _make_gaia_install(nfi)
        proj = nfi / "group" / "proj"
        _make_repo(proj)

        scan_workspace_to_store("ws", tmp_path, "scanner", db_path=tmp_db)

        from tools.scan.store_populator import resolve_identity
        nfi_ws = resolve_identity(nfi)

        # The project belongs to nfi, NOT to the root "ws".
        root_rows = _project_rows(tmp_db, "ws")
        nfi_rows = _project_rows(tmp_db, nfi_ws)
        assert root_rows == [], f"project should not be under root ws: {root_rows}"
        assert len(nfi_rows) == 1
        name, group_name, path = nfi_rows[0]
        assert name == "proj"
        # group_name = container between nfi and proj == "group"
        assert group_name == "group", f"expected group_name='group', got {group_name!r}"
        assert path == str(proj)

    def test_project_directly_under_workspace_has_no_group(self, tmp_db, tmp_path):
        nfi = tmp_path / "nfi"
        nfi.mkdir()
        _make_gaia_install(nfi)
        proj = nfi / "proj"
        _make_repo(proj)

        scan_workspace_to_store("ws", tmp_path, "scanner", db_path=tmp_db)

        from tools.scan.store_populator import resolve_identity
        nfi_ws = resolve_identity(nfi)
        rows = _project_rows(tmp_db, nfi_ws)
        assert len(rows) == 1
        name, group_name, path = rows[0]
        assert name == "proj"
        assert group_name is None, f"expected None group_name, got {group_name!r}"


# ---------------------------------------------------------------------------
# (4) real-world aaxis/ tree
# ---------------------------------------------------------------------------

class TestAaxisTree:
    def test_nfi_is_workspace_and_owns_nfi_oro_com(self, tmp_db, tmp_path):
        # aaxis/ (CLI root, no install)
        #   nfi/ (install)            -> workspace
        #     nfi-oro-com/ (.git)     -> project of nfi
        aaxis = tmp_path
        nfi = aaxis / "nfi"
        nfi.mkdir()
        _make_gaia_install(nfi)
        oro = nfi / "nfi-oro-com"
        _make_repo(oro)

        scan_workspace_to_store("aaxis", aaxis, "scanner", db_path=tmp_db)

        from tools.scan.store_populator import resolve_identity
        nfi_ws = resolve_identity(nfi)

        # nfi is a workspace row.
        ws_names = _workspace_names(tmp_db)
        assert nfi_ws in ws_names, f"nfi not registered as workspace; have {ws_names}"

        # "list projects of workspace nfi" -> nfi-oro-com, with its path + workspace.
        nfi_projects = _project_rows(tmp_db, nfi_ws)
        assert len(nfi_projects) == 1
        name, group_name, path = nfi_projects[0]
        assert name == "nfi-oro-com"
        assert group_name is None  # sits directly under nfi
        assert path == str(oro)

        # The root aaxis workspace owns NO projects (everything is under nfi).
        assert _project_rows(tmp_db, "aaxis") == []

        # gaia_installations row exists for the nfi workspace.
        con = sqlite3.connect(str(tmp_db))
        try:
            inst = con.execute(
                "SELECT COUNT(*) FROM gaia_installations WHERE workspace = ?",
                (nfi_ws,),
            ).fetchone()[0]
        finally:
            con.close()
        assert inst >= 1, "gaia_installations row missing for nfi"


# ---------------------------------------------------------------------------
# (5) dir that is BOTH a project and a workspace
# ---------------------------------------------------------------------------

class TestDirIsBothProjectAndWorkspace:
    def test_appears_in_both_tables(self, tmp_db, tmp_path):
        # root "ws" (CLI anchor) / both (.git AND install)
        both = tmp_path / "both"
        _make_repo(both)
        _make_gaia_install(both)

        scan_workspace_to_store("ws", tmp_path, "scanner", db_path=tmp_db)

        from tools.scan.store_populator import resolve_identity
        both_ws = resolve_identity(both)

        # workspaces table has a row for "both".
        ws_names = _workspace_names(tmp_db)
        assert both_ws in ws_names, f"'both' not in workspaces; have {ws_names}"

        # projects table has a row for "both" -- attributed to its nearest
        # installed ANCESTOR (the CLI root "ws"), since a dir is not its own
        # ancestor.
        root_projects = _project_rows(tmp_db, "ws")
        names = {r[0] for r in root_projects}
        assert "both" in names, f"'both' project row missing; have {names}"

        # And its own workspace owns no projects of itself.
        assert _project_rows(tmp_db, both_ws) == []
