"""
Tests for M1-T3 workspace-identity: scan anchors at workspace root + install
passes --workspace-name.

AC-3 acceptance criterion:
  ``gaia install`` from a directory results in a workspace with name by path (the
  scan triggered by install uses naming by path, not remote-first).

Two behaviours are tested:

1. SCAN ANCHORS AT WORKSPACE ROOT (Objective 1)
   When ``gaia scan`` is invoked with no target from a subdirectory inside an
   installed workspace, it should walk up to the nearest installed Gaia workspace
   ancestor and scan from there (not from the subdirectory).

2. INSTALL PASSES NAME-BY-PATH (Objective 2)
   ``_maybe_run_fresh_scan`` must add ``--workspace-name <basename>`` to the
   ``gaia scan`` command it invokes, so the resulting workspace row is named by
   the directory path (basename), not by the git remote URL.

Test isolation:
  - Never touches ~/.gaia/gaia.db (GAIA_DATA_DIR redirected to tmp_path).
  - Never runs ``gaia install`` for real.
  - ``_run_scan`` and ``scan_workspace`` are stubbed where needed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure bin/ is on sys.path so cli.scan and cli.install are importable.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

import cli.scan as scan_mod
import cli.install as install_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_gaia_install(path: Path) -> None:
    """Write the canonical Gaia installation signal under ``path``."""
    claude = path / ".claude"
    claude.mkdir(parents=True, exist_ok=True)
    (claude / "plugin-registry.json").write_text(
        json.dumps({"installed": [{"name": "gaia-ops", "version": "5.0.0"}]})
    )


def _make_scan_args(**kwargs) -> argparse.Namespace:
    """Build a minimal argparse.Namespace for scan tests."""
    defaults = {
        "path": None,
        "workspace": None,
        "dry_run": False,
        "json": True,
        "scanners": None,
        "check_staleness": False,
        "full": False,
        "no_color": True,
        "verbose": False,
        "npm_postinstall": False,
        "workspace_name": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _make_stub_result():
    """Return a minimal ScanResult-like object for scan stubs."""
    from tools.scan.orchestrator import ScanOutput
    output = ScanOutput(
        sections_updated=[],
        sections_preserved=[],
        warnings=[],
        errors=[],
        duration_ms=1.0,
        scanner_results={},
    )

    class _StubResult:
        pass

    r = _StubResult()
    r.output = output
    r.demoted = False
    r.marked_missing = []
    return r


# ---------------------------------------------------------------------------
# Tests: _find_workspace_root
# ---------------------------------------------------------------------------

class TestFindWorkspaceRoot:
    """Unit tests for the ``_find_workspace_root`` helper in cli.scan."""

    def test_returns_cwd_when_no_ancestor_installed(self, tmp_path):
        """When no ancestor has a Gaia install signal, falls back to cwd."""
        cwd = tmp_path / "some-subdir"
        cwd.mkdir()
        result = scan_mod._find_workspace_root(cwd)
        assert result == cwd

    def test_finds_immediate_parent_as_workspace_root(self, tmp_path):
        """When the immediate parent is an installed workspace, returns parent."""
        workspace_root = tmp_path
        _make_gaia_install(workspace_root)
        subdir = workspace_root / "projects" / "my-repo"
        subdir.mkdir(parents=True)

        result = scan_mod._find_workspace_root(subdir)
        assert result == workspace_root

    def test_finds_grandparent_as_workspace_root(self, tmp_path):
        """Walks multiple levels up to find the installed workspace ancestor."""
        workspace_root = tmp_path
        _make_gaia_install(workspace_root)
        deep_subdir = workspace_root / "a" / "b" / "c"
        deep_subdir.mkdir(parents=True)

        result = scan_mod._find_workspace_root(deep_subdir)
        assert result == workspace_root

    def test_nearest_workspace_wins_over_farther_one(self, tmp_path):
        """The nearest installed ancestor is returned, not a higher-level one."""
        outer_root = tmp_path
        _make_gaia_install(outer_root)
        inner_root = outer_root / "inner-workspace"
        _make_gaia_install(inner_root)
        subdir = inner_root / "repos" / "some-repo"
        subdir.mkdir(parents=True)

        # The nearest installed ancestor of subdir is inner_root, not outer_root.
        result = scan_mod._find_workspace_root(subdir)
        assert result == inner_root, (
            f"Expected nearest ancestor {inner_root}, got {result}"
        )

    def test_workspace_root_itself_is_returned_when_installed(self, tmp_path):
        """If cwd itself is the installed workspace, it is returned."""
        _make_gaia_install(tmp_path)
        result = scan_mod._find_workspace_root(tmp_path)
        assert result == tmp_path


# ---------------------------------------------------------------------------
# Tests: cmd_scan anchors at workspace root (Objective 1)
# ---------------------------------------------------------------------------

class TestScanAnchorsAtWorkspaceRoot:
    """When invoked with no target from a subdirectory, scan anchors at the
    nearest installed Gaia workspace ancestor (not at the nested cwd)."""

    def test_scan_from_subdirectory_uses_workspace_root(
        self, tmp_path, monkeypatch
    ):
        """cmd_scan with no target, invoked from a nested subdirectory, passes
        the workspace ROOT (not the subdirectory) to scan-core."""
        workspace_root = tmp_path
        _make_gaia_install(workspace_root)

        # Nested subdirectory simulating a repo inside the workspace.
        subdir = workspace_root / "projects" / "my-service"
        subdir.mkdir(parents=True)
        monkeypatch.chdir(subdir)

        captured = {}

        def _fake_run_scan(project_root, cfg, args, version):
            captured["project_root"] = project_root
            return 0

        monkeypatch.setattr(scan_mod, "_run_scan", _fake_run_scan)

        args = _make_scan_args()
        rc = scan_mod.cmd_scan(args)
        assert rc == 0, f"expected exit 0, got {rc}"

        assert captured.get("project_root") == workspace_root, (
            f"scan should anchor at workspace root {workspace_root}, "
            f"not at nested cwd {subdir}. "
            f"Got: {captured.get('project_root')}"
        )

    def test_scan_from_workspace_root_itself_unchanged(
        self, tmp_path, monkeypatch
    ):
        """When already at the workspace root, scan uses that root (no-op change)."""
        workspace_root = tmp_path
        _make_gaia_install(workspace_root)
        monkeypatch.chdir(workspace_root)

        captured = {}

        def _fake_run_scan(project_root, cfg, args, version):
            captured["project_root"] = project_root
            return 0

        monkeypatch.setattr(scan_mod, "_run_scan", _fake_run_scan)

        args = _make_scan_args()
        rc = scan_mod.cmd_scan(args)
        assert rc == 0
        assert captured.get("project_root") == workspace_root

    def test_explicit_target_not_affected_by_anchor_logic(
        self, tmp_path, monkeypatch
    ):
        """When an explicit target path is given, the workspace-root anchor
        logic is bypassed -- the explicit target is used as-is."""
        workspace_root = tmp_path
        _make_gaia_install(workspace_root)

        # A completely separate directory (no Gaia install).
        other_dir = tmp_path / "other"
        other_dir.mkdir()

        captured = {}

        def _fake_run_scan(project_root, cfg, args, version):
            captured["project_root"] = project_root
            return 0

        monkeypatch.setattr(scan_mod, "_run_scan", _fake_run_scan)

        # Pass the other directory as the explicit target.
        args = _make_scan_args(path=str(other_dir))
        rc = scan_mod.cmd_scan(args)
        assert rc == 0
        assert captured.get("project_root") == other_dir, (
            f"explicit target must be honoured; got {captured.get('project_root')}"
        )

    def test_outside_workspace_no_target_still_errors(
        self, tmp_path, monkeypatch
    ):
        """Without a target and outside any workspace, scan still errors cleanly
        (workspace-root anchor does not invent a workspace where there is none)."""
        monkeypatch.chdir(tmp_path)  # tmp_path has no Gaia install
        args = _make_scan_args(json=True)
        rc = scan_mod.cmd_scan(args)
        assert rc == 1

    def test_npm_postinstall_does_not_walk_up(self, tmp_path, monkeypatch):
        """The npm-postinstall path bypasses the ancestor walk (install created
        the workspace at cwd; scan should scan from cwd, not walk up)."""
        # Outer directory with a Gaia install -- the scan must NOT walk up here.
        outer = tmp_path / "outer-workspace"
        _make_gaia_install(outer)

        # Fresh workspace at a nested path: install just set it up.
        fresh_ws = outer / "fresh"
        fresh_ws.mkdir()
        monkeypatch.chdir(fresh_ws)

        captured = {}

        def _fake_run_scan(project_root, cfg, args, version):
            captured["project_root"] = project_root
            return 0

        monkeypatch.setattr(scan_mod, "_run_scan", _fake_run_scan)

        args = _make_scan_args(npm_postinstall=True)
        rc = scan_mod.cmd_scan(args)
        assert rc == 0
        assert captured.get("project_root") == fresh_ws, (
            f"--npm-postinstall must scan from cwd={fresh_ws}, "
            f"not from outer workspace {outer}. "
            f"Got: {captured.get('project_root')}"
        )


# ---------------------------------------------------------------------------
# Tests: install passes --workspace-name (Objective 2)
# ---------------------------------------------------------------------------

class TestInstallPassesWorkspaceNameByPath:
    """_maybe_run_fresh_scan must add --workspace-name <basename> to the scan
    command, so the workspace is named by path (not by git remote)."""

    def test_scan_command_includes_workspace_name_flag(self, tmp_path, monkeypatch):
        """The subprocess command built by _maybe_run_fresh_scan must include
        '--workspace-name' with the workspace directory basename."""
        workspace = tmp_path / "my-project"
        workspace.mkdir()

        # Ensure the workspace is seen as "not already scanned".
        monkeypatch.setattr(install_mod, "_workspace_already_scanned", lambda ws: False)

        # Stub bin/gaia to exist.
        fake_gaia = install_mod._PACKAGE_ROOT / "bin" / "gaia"
        monkeypatch.setattr(install_mod, "_PACKAGE_ROOT", tmp_path)
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir(exist_ok=True)
        (fake_bin / "gaia").write_text("#!/usr/bin/env python3\nprint('ok')\n")

        captured_cmd = {}

        def _fake_subprocess_run(cmd, **kwargs):
            captured_cmd["cmd"] = list(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        monkeypatch.setattr(install_mod.subprocess, "run", _fake_subprocess_run)

        result = install_mod._maybe_run_fresh_scan(workspace, verbose=False, quiet=True)

        assert result.get("action") == "created", (
            f"expected action='created', got {result}"
        )
        cmd = captured_cmd.get("cmd", [])
        assert "--workspace-name" in cmd, (
            f"scan command must include '--workspace-name'; got: {cmd}"
        )
        name_idx = cmd.index("--workspace-name") + 1
        assert name_idx < len(cmd), "--workspace-name must be followed by a value"
        passed_name = cmd[name_idx]
        expected_name = workspace.name.lower()
        assert passed_name == expected_name, (
            f"--workspace-name must be the workspace basename '{expected_name}', "
            f"got '{passed_name}'"
        )

    def test_workspace_name_is_path_not_remote(self, tmp_path, monkeypatch):
        """The name passed via --workspace-name is the directory basename,
        NOT a git-remote-derived string (the AC-3 invariant)."""
        import subprocess as real_subprocess

        workspace = tmp_path / "aos"  # basename 'aos', not 'bitbucket.org/aaxis/aos'
        workspace.mkdir()

        monkeypatch.setattr(install_mod, "_workspace_already_scanned", lambda ws: False)
        monkeypatch.setattr(install_mod, "_PACKAGE_ROOT", tmp_path)
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir(exist_ok=True)
        (fake_bin / "gaia").write_text("")

        captured_cmd = {}

        def _fake_run(cmd, **kwargs):
            captured_cmd["cmd"] = list(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        monkeypatch.setattr(install_mod.subprocess, "run", _fake_run)

        install_mod._maybe_run_fresh_scan(workspace, verbose=False, quiet=True)

        cmd = captured_cmd.get("cmd", [])
        assert "--workspace-name" in cmd

        name_idx = cmd.index("--workspace-name") + 1
        passed_name = cmd[name_idx]

        # The passed name must be the simple basename, NOT a host/owner/repo form.
        assert "/" not in passed_name, (
            f"--workspace-name must be path-based (no '/'), got '{passed_name}'"
        )
        assert passed_name == "aos", (
            f"expected 'aos' (basename of {workspace}), got '{passed_name}'"
        )

    def test_already_scanned_workspace_skips_scan_entirely(self, tmp_path, monkeypatch):
        """When the workspace is already scanned, _maybe_run_fresh_scan returns
        noop WITHOUT invoking subprocess (nothing about --workspace-name matters
        in this case, but the guard must still be honoured)."""
        workspace = tmp_path / "already-done"
        workspace.mkdir()

        monkeypatch.setattr(install_mod, "_workspace_already_scanned", lambda ws: True)

        run_called = {}
        monkeypatch.setattr(
            install_mod.subprocess, "run",
            lambda *a, **kw: run_called.__setitem__("called", True),
        )

        result = install_mod._maybe_run_fresh_scan(workspace, verbose=False, quiet=True)
        assert result.get("action") == "noop"
        assert "called" not in run_called, (
            "subprocess.run must NOT be called when workspace is already scanned"
        )


# ---------------------------------------------------------------------------
# AC-3 Evidence: end-to-end integration test (no real DB, no real install)
# ---------------------------------------------------------------------------

class TestAC3InstallScanUsesPathBasedName:
    """AC-3: gaia install from a directory results in a workspace named by path
    (scan triggered by install uses --workspace-name, not remote-first).

    This is the evidence-class test. It verifies the full seam: cmd_install
    calls _maybe_run_fresh_scan, which invokes gaia scan with --workspace-name
    set to the workspace basename. No real DB is touched; no real scan executes.
    """

    def _make_install_args(self, workspace: Path) -> argparse.Namespace:
        ns = argparse.Namespace()
        ns.postinstall = True
        ns.quiet = True
        ns.verbose = False
        ns.db_path = None
        ns.workspace = str(workspace)
        ns.skip_workspace = False
        ns.no_path = True
        return ns

    def test_install_scan_uses_workspace_name_by_path(self, tmp_path, monkeypatch):
        """Full seam: cmd_install -> _maybe_run_fresh_scan -> scan command includes
        --workspace-name with the workspace directory basename."""
        workspace = tmp_path / "my-workspace"
        workspace.mkdir()
        (workspace / ".claude").mkdir()

        # Capture what subprocess command scan receives.
        captured_scan_cmd = {}

        def _fake_run(cmd, **kwargs):
            # Only intercept the scan call; let bootstrap be handled by the
            # bootstrap mock below.
            if "scan" in cmd:
                captured_scan_cmd["cmd"] = list(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stdout = ""
            r.stderr = ""
            return r

        monkeypatch.setattr(install_mod.subprocess, "run", _fake_run)
        monkeypatch.setattr(install_mod, "_workspace_already_scanned", lambda ws: False)
        monkeypatch.setattr(install_mod, "_run_bootstrap", lambda **kw: {"rc": 0, "detail": ""})
        monkeypatch.setattr(install_mod, "_seed_contract_permissions", lambda **kw: {"action": "noop", "details": ""})
        monkeypatch.setattr(
            install_mod._install_helpers, "configure_settings_json",
            lambda ws: {"action": "noop", "path": str(ws), "details": ""},
        )
        monkeypatch.setattr(
            install_mod._install_helpers, "merge_local_permissions",
            lambda ws, **kw: {"action": "noop", "path": str(ws), "details": ""},
        )
        monkeypatch.setattr(
            install_mod._install_helpers, "merge_local_hooks",
            lambda ws: {"action": "noop", "path": str(ws), "details": ""},
        )
        monkeypatch.setattr(
            install_mod._install_helpers, "manage_symlinks",
            lambda ws: {"action": "noop", "path": str(ws), "details": ""},
        )
        monkeypatch.setattr(
            install_mod._install_helpers, "register_plugin",
            lambda ws, source="": {"action": "noop", "path": str(ws), "details": ""},
        )
        # Ensure gaia entry point is found.
        fake_gaia = install_mod._PACKAGE_ROOT / "bin" / "gaia"
        monkeypatch.setattr(install_mod, "_PACKAGE_ROOT", tmp_path)
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir(exist_ok=True)
        (fake_bin / "gaia").write_text("")

        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            rc = install_mod.cmd_install(self._make_install_args(workspace))

        assert rc == 0, f"cmd_install should exit 0 (postinstall swallows errors), got {rc}"

        cmd = captured_scan_cmd.get("cmd", [])
        assert cmd, "scan subprocess was not called by cmd_install"
        assert "--workspace-name" in cmd, (
            "scan command triggered by install must include --workspace-name; "
            f"full cmd: {cmd}"
        )
        name_idx = cmd.index("--workspace-name") + 1
        passed_name = cmd[name_idx]
        expected_name = workspace.name.lower()
        assert passed_name == expected_name, (
            f"AC-3 FAIL: --workspace-name should be '{expected_name}' (path-based), "
            f"not '{passed_name}' (which would be remote-first). "
            f"Full cmd: {cmd}"
        )
