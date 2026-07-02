"""
Unit tests for scan_workspace_to_store's DETERMINISTIC classification (post
inference-removal).

The deterministic rule:
  * A **project** is a dir with ``.git`` (discovered by ``_list_repos``).
  * Every discovered repo is attributed to the SINGLE caller-provided
    ``workspace``. There is NO sub-workspace detection and NO
    nearest-installed-ancestor inference -- the caller (tools.scan.classify,
    driven by ``--workspace``) has already decided the workspace.
  * ``group_name`` = the immediate container of the repo when it is not directly
    under ``root``; ``None`` when it sits directly at ``root``.
  * ``_is_installed_gaia_workspace`` remains a LIVE signal helper (used by
    ``_scan_gaia_installations``) and is exercised here as a pure function.
    Its former companions -- ``_list_installed_workspaces``,
    ``_walk_for_installs``, ``_nearest_installed_ancestor`` -- were the
    installed-workspace attribution layer superseded by the deterministic
    ``--workspace`` classifier; they, along with ``resolve_identity``, have
    been removed as dead code.

Coverage:
  (1) a git dir is registered as a project WITH its path populated.
  (2) the live install-signal helper classifies a Gaia registry vs a
      third-party .claude correctly as a pure function.
  (3) a project under an intermediate folder is attributed to the caller
      workspace, with group_name = the repo's immediate container.
  (4) the real-world aaxis/ tree: all repos are owned by the caller workspace;
      no separate sub-workspace is registered.
  (5) a dir that is both git + install is a single project under the caller
      workspace (no self-attribution, no separate sub-workspace).

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

    Writes ``path/.claude/plugin-registry.json`` listing gaia as installed.
    """
    claude = path / ".claude"
    claude.mkdir(parents=True, exist_ok=True)
    (claude / "plugin-registry.json").write_text(
        json.dumps({"installed": [{"name": "gaia", "version": "5.0.0"}]})
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
    def test_registry_with_gaia_is_workspace(self, tmp_path):
        _make_gaia_install(tmp_path)
        assert _is_installed_gaia_workspace(tmp_path) is True

    def test_registry_with_unknown_name_is_not_workspace(self, tmp_path):
        # Only "gaia" is recognized; any other name is not a Gaia workspace.
        claude = tmp_path / ".claude"
        claude.mkdir()
        (claude / "plugin-registry.json").write_text(
            json.dumps({"installed": [{"name": "unrelated-plugin"}]})
        )
        assert _is_installed_gaia_workspace(tmp_path) is False

    def test_third_party_claude_is_not_workspace(self, tmp_path):
        _make_third_party_claude(tmp_path)
        assert _is_installed_gaia_workspace(tmp_path) is False

    def test_bare_claude_without_registry_is_not_workspace(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        assert _is_installed_gaia_workspace(tmp_path) is False

    def test_no_claude_is_not_workspace(self, tmp_path):
        assert _is_installed_gaia_workspace(tmp_path) is False


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
# (3) DETERMINISTIC attribution: every repo belongs to the caller-provided
# workspace (nearest-installed-ancestor inference was removed).
#
# scan_workspace_to_store no longer detects sub-workspaces or attributes a repo
# to a nearest installed ancestor. The caller (tools.scan.classify, driven by
# --workspace) has already decided the workspace; the populator records every
# discovered repo under it. group_name = the immediate container of the repo
# when it is not directly under root, else None.
# ---------------------------------------------------------------------------

class TestDeterministicAttribution:
    def test_all_repos_attributed_to_caller_workspace(self, tmp_db, tmp_path):
        # root "ws" / nfi (install, ignored as a signal) / group / proj
        nfi = tmp_path / "nfi"
        nfi.mkdir()
        _make_gaia_install(nfi)  # install signal is IGNORED now
        proj = nfi / "group" / "proj"
        _make_repo(proj)

        scan_workspace_to_store("ws", tmp_path, "scanner", db_path=tmp_db)

        # The project belongs to the caller-provided workspace "ws", regardless
        # of the intermediate install signal.
        rows = _project_rows(tmp_db, "ws")
        assert len(rows) == 1, f"expected 1 project under 'ws', got {rows}"
        name, group_name, path = rows[0]
        assert name == "proj"
        # group_name = the immediate container of the repo (its parent dir).
        assert group_name == "group", f"expected group_name='group', got {group_name!r}"
        assert path == str(proj)

    def test_project_directly_under_root_has_no_group(self, tmp_db, tmp_path):
        proj = tmp_path / "proj"
        _make_repo(proj)

        scan_workspace_to_store("ws", tmp_path, "scanner", db_path=tmp_db)

        rows = _project_rows(tmp_db, "ws")
        assert len(rows) == 1
        name, group_name, path = rows[0]
        assert name == "proj"
        assert group_name is None, f"expected None group_name, got {group_name!r}"


# ---------------------------------------------------------------------------
# (4) real-world aaxis/ tree, DETERMINISTIC: all repos under the caller
# workspace, no sub-workspace detection.
# ---------------------------------------------------------------------------

class TestAaxisTree:
    def test_all_repos_owned_by_caller_workspace(self, tmp_db, tmp_path):
        # aaxis/ (caller workspace)
        #   nfi/ (install signal, IGNORED)
        #     nfi-oro-com/ (.git)  -> project of aaxis, group_name='nfi'
        aaxis = tmp_path
        nfi = aaxis / "nfi"
        nfi.mkdir()
        _make_gaia_install(nfi)
        oro = nfi / "nfi-oro-com"
        _make_repo(oro)

        scan_workspace_to_store("aaxis", aaxis, "scanner", db_path=tmp_db)

        # "list projects of workspace aaxis" -> nfi-oro-com, container = nfi.
        aaxis_projects = _project_rows(tmp_db, "aaxis")
        assert len(aaxis_projects) == 1
        name, group_name, path = aaxis_projects[0]
        assert name == "nfi-oro-com"
        assert group_name == "nfi", f"expected group_name='nfi', got {group_name!r}"
        assert path == str(oro)

        # No separate 'nfi' workspace row is created (no sub-workspace detection).
        ws_names = _workspace_names(tmp_db)
        assert "aaxis" in ws_names


# ---------------------------------------------------------------------------
# (5) a dir that is BOTH git + install is a single project under the caller
# workspace (no self-attribution, no separate sub-workspace).
# ---------------------------------------------------------------------------

class TestDirIsBothProjectAndWorkspace:
    def test_git_and_install_dir_is_one_project(self, tmp_db, tmp_path):
        # root "ws" / both (.git AND install)
        both = tmp_path / "both"
        _make_repo(both)
        _make_gaia_install(both)

        scan_workspace_to_store("ws", tmp_path, "scanner", db_path=tmp_db)

        # projects table has a row for "both" under the caller workspace "ws".
        root_projects = _project_rows(tmp_db, "ws")
        names = {r[0] for r in root_projects}
        assert "both" in names, f"'both' project row missing; have {names}"
