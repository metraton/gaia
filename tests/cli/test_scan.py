"""
Unit tests for bin/cli/scan.py -- gaia scan subcommand.

Covers:
  * --help smoke test (parser registers cleanly)
  * register() actually adds the `scan` subparser
  * --dry-run does not touch the SQLite DB or project-context.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Ensure bin/ is on sys.path so the plugin is importable
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

import cli.scan as scan_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _MockArgs:
    def __init__(self, **kwargs):
        # Defaults matching the parser
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


# ---------------------------------------------------------------------------
# register() -- parser wiring
# ---------------------------------------------------------------------------

class TestRegister:
    def _build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="gaia")
        subparsers = parser.add_subparsers(dest="subcommand")
        scan_mod.register(subparsers)
        return parser

    def test_register_returns_subparser(self):
        parser = argparse.ArgumentParser(prog="gaia")
        subparsers = parser.add_subparsers(dest="subcommand")
        sp = scan_mod.register(subparsers)
        assert isinstance(sp, argparse.ArgumentParser)

    def test_scan_subcommand_parses(self):
        parser = self._build_parser()
        ns = parser.parse_args(["scan"])
        assert ns.subcommand == "scan"
        assert ns.path is None
        assert ns.dry_run is False
        assert ns.json is False
        assert ns.workspace is None

    def test_scan_subcommand_accepts_positional_target(self):
        parser = self._build_parser()
        ns = parser.parse_args(["scan", "/tmp/target"])
        assert ns.path == "/tmp/target"

    def test_scan_subcommand_accepts_flags(self):
        parser = self._build_parser()
        ns = parser.parse_args([
            "scan",
            "--dry-run",
            "--json",
            "--workspace", "/tmp/wsx",
            "--scanners", "stack,git",
            "--no-color",
        ])
        assert ns.dry_run is True
        assert ns.json is True
        assert ns.workspace == "/tmp/wsx"
        assert ns.scanners == "stack,git"
        assert ns.no_color is True


# ---------------------------------------------------------------------------
# --help smoke
# ---------------------------------------------------------------------------

class TestHelpSmoke:
    def test_gaia_scan_help_exit_zero(self):
        """Invoke `python bin/gaia scan --help` end-to-end and check exit 0."""
        env = dict(os.environ)
        env["NO_COLOR"] = "1"
        # Run from a tmp cwd so we don't accidentally trigger plugin discovery
        # against an unexpected workspace.
        result = subprocess.run(
            [sys.executable, str(_BIN_DIR / "gaia"), "scan", "--help"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        )
        # Help text must mention key flags so future regressions are visible
        assert "--workspace" in result.stdout
        assert "--dry-run" in result.stdout
        assert "--json" in result.stdout


# ---------------------------------------------------------------------------
# Dry-run behavior -- must not touch the DB or project-context.json
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_human_exits_zero(self, tmp_path, capsys):
        args = _MockArgs(workspace=str(tmp_path), dry_run=True)
        rc = scan_mod.cmd_scan(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "[dry-run]" in captured.out
        assert str(tmp_path) in captured.out

    def test_dry_run_json_exits_zero(self, tmp_path, capsys):
        args = _MockArgs(workspace=str(tmp_path), dry_run=True, json=True)
        rc = scan_mod.cmd_scan(args)
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["dry_run"] is True
        assert data["project_root"] == str(tmp_path)

    def test_dry_run_does_not_write_context_file(self, tmp_path):
        """--dry-run must not create or modify project-context.json."""
        args = _MockArgs(workspace=str(tmp_path), dry_run=True, json=True)
        rc = scan_mod.cmd_scan(args)
        assert rc == 0
        # project-context.json must not have been created
        ctx = tmp_path / ".claude" / "project-context" / "project-context.json"
        assert not ctx.exists()

    def test_dry_run_does_not_touch_gaia_db(self, tmp_path, monkeypatch):
        """--dry-run must not open or modify ~/.gaia/gaia.db.

        We point GAIA_DATA_DIR at a tmp path so any accidental write would land
        there, then assert the path stays empty.
        """
        gaia_dir = tmp_path / "gaia-data"
        gaia_dir.mkdir()
        monkeypatch.setenv("GAIA_DATA_DIR", str(gaia_dir))

        args = _MockArgs(workspace=str(tmp_path), dry_run=True, json=True)
        rc = scan_mod.cmd_scan(args)
        assert rc == 0

        # No DB file should have been created in the redirected dir
        assert list(gaia_dir.iterdir()) == [], (
            f"gaia-data dir was modified during --dry-run: "
            f"{[p.name for p in gaia_dir.iterdir()]}"
        )

    def test_dry_run_is_pure_preview(self, tmp_path, capsys):
        """--dry-run is a pure preview: it reports the target and what would
        run, and (post scan/install split) does NOT read or touch the DB."""
        args = _MockArgs(workspace=str(tmp_path), dry_run=True, json=True)
        rc = scan_mod.cmd_scan(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["dry_run"] is True
        assert data["project_root"] == str(tmp_path)
        assert "would_scan" in data


# ---------------------------------------------------------------------------
# Target resolution (explicit entry points)
# ---------------------------------------------------------------------------

class TestTargetResolution:
    def test_resolve_positional_path(self, tmp_path):
        args = _MockArgs(path=str(tmp_path))
        result = scan_mod._resolve_target(args)
        assert result == tmp_path.resolve()

    def test_resolve_workspace_flag(self, tmp_path):
        args = _MockArgs(workspace=str(tmp_path))
        result = scan_mod._resolve_target(args)
        assert result == tmp_path.resolve()

    def test_positional_wins_over_flag(self, tmp_path):
        other = tmp_path / "other"
        args = _MockArgs(path=str(tmp_path), workspace=str(other))
        result = scan_mod._resolve_target(args)
        assert result == tmp_path.resolve()

    def test_no_target_resolves_none(self):
        args = _MockArgs()
        assert scan_mod._resolve_target(args) is None

    def test_invalid_target_returns_1(self, tmp_path, capsys):
        bogus = tmp_path / "does-not-exist"
        args = _MockArgs(path=str(bogus), dry_run=False, json=True)
        rc = scan_mod.cmd_scan(args)
        assert rc == 1
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "error"
        assert "not found" in data["error"]


# ---------------------------------------------------------------------------
# Explicit entry points: outside-a-workspace + no target -> clean error
# ---------------------------------------------------------------------------

class TestOutsideWorkspaceError:
    def test_no_target_outside_workspace_is_clean_error(self, tmp_path, monkeypatch, capsys):
        """`gaia scan` with no target, run outside any Gaia workspace, errors
        cleanly -- it must NOT fall back to an install/bootstrap mode."""
        # cwd is a plain dir with no Gaia install -> not a workspace.
        monkeypatch.chdir(tmp_path)
        args = _MockArgs(json=True)  # no path, no workspace
        rc = scan_mod.cmd_scan(args)
        assert rc == 1
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "error"
        assert "not in a Gaia workspace" in data["error"]

    def test_no_target_inside_workspace_proceeds(self, tmp_path, monkeypatch):
        """Inside a workspace (plugin-registry signal), no-target scan proceeds
        past the workspace guard (reaches the scan-core call)."""
        claude = tmp_path / ".claude"
        claude.mkdir()
        (claude / "plugin-registry.json").write_text(
            '{"installed": [{"name": "gaia-ops"}]}'
        )
        monkeypatch.chdir(tmp_path)

        # Stub scan-core so we only verify the guard let us through.
        reached = {}
        def _fake_run_scan(project_root, cfg, args, version):
            reached["called"] = True
            return 0
        monkeypatch.setattr(scan_mod, "_run_scan", _fake_run_scan)

        args = _MockArgs()
        rc = scan_mod.cmd_scan(args)
        assert rc == 0
        assert reached.get("called") is True

    def test_npm_postinstall_bypasses_workspace_guard(self, tmp_path, monkeypatch):
        """--npm-postinstall scans the cwd without requiring the workspace
        signal (install owns the just-created workspace identity)."""
        monkeypatch.chdir(tmp_path)
        reached = {}
        def _fake_run_scan(project_root, cfg, args, version):
            reached["called"] = True
            return 0
        monkeypatch.setattr(scan_mod, "_run_scan", _fake_run_scan)

        args = _MockArgs(npm_postinstall=True)
        rc = scan_mod.cmd_scan(args)
        assert rc == 0
        assert reached.get("called") is True


# ---------------------------------------------------------------------------
# Install-decoupling: scan must not import install setup functions
# ---------------------------------------------------------------------------

class TestScanDoesNotInstall:
    def test_scan_module_has_no_install_symbols(self):
        """The removed install-mode functions must be gone from cli.scan."""
        for removed in ("_mode_fresh", "_mode_existing", "_mode_scan_only"):
            assert not hasattr(scan_mod, removed), (
                f"cli.scan still exposes removed install-mode function {removed!r}"
            )

    def test_scan_source_does_not_reference_setup_install(self):
        """cli/scan.py must not import the install-layer setup functions."""
        src = (Path(scan_mod.__file__)).read_text()
        for forbidden in (
            "ensure_gaia_ops_package",
            "create_claude_directory",
            "install_git_hooks",
            "ensure_claude_code",
        ):
            assert forbidden not in src, (
                f"cli/scan.py still references install-layer symbol {forbidden!r}"
            )


# ---------------------------------------------------------------------------
# M1-T1: --workspace-name flag (AC-1)
# ---------------------------------------------------------------------------

class TestWorkspaceNameFlag:
    """AC-1: --workspace-name overrides the remote-first identity derivation.

    With the flag the workspace written to the DB (and passed to scan-core) is
    the explicit NAME, NOT the git-remote-derived canonical form.
    Without the flag the existing remote-first behaviour is unchanged.
    """

    def test_register_exposes_workspace_name_flag(self):
        """Parser must accept --workspace-name."""
        parser = argparse.ArgumentParser(prog="gaia")
        subparsers = parser.add_subparsers(dest="subcommand")
        scan_mod.register(subparsers)
        ns = parser.parse_args(["scan", "--workspace-name", "my-explicit-ws"])
        assert ns.workspace_name == "my-explicit-ws"

    def test_workspace_name_default_is_none(self):
        """Without --workspace-name the attribute is None (no override)."""
        parser = argparse.ArgumentParser(prog="gaia")
        subparsers = parser.add_subparsers(dest="subcommand")
        scan_mod.register(subparsers)
        ns = parser.parse_args(["scan"])
        assert ns.workspace_name is None

    def test_run_scan_uses_workspace_name_override(self, tmp_path, monkeypatch):
        """When workspace_name is set, _run_scan passes that value to scan_workspace
        instead of calling gaia.project.current()."""
        captured = {}

        import tools.scan.core as _core
        monkeypatch.setattr(
            _core,
            "scan_workspace",
            lambda root, workspace, config=None, db_path=None: (
                captured.__setitem__("workspace", workspace)
                or _make_stub_result()
            ),
        )

        # Ensure gaia.project.current() is NOT called by patching it to raise.
        import gaia.project as _proj
        def _should_not_be_called(cwd=None):
            raise AssertionError(
                "gaia.project.current() must NOT be called when --workspace-name is set"
            )
        monkeypatch.setattr(_proj, "current", _should_not_be_called)

        args = _MockArgs(
            path=str(tmp_path),
            workspace_name="path-derived-name",
            json=True,
        )
        rc = scan_mod._run_scan(tmp_path, _DummyScanConfig(), args, "test")
        assert rc == 0
        assert captured.get("workspace") == "path-derived-name", (
            f"scan_workspace received workspace={captured.get('workspace')!r}, "
            "expected 'path-derived-name'"
        )

    def test_run_scan_without_flag_calls_project_current(self, tmp_path, monkeypatch):
        """Without --workspace-name, _run_scan falls back to gaia.project.current()
        (remote-first derivation) -- no-regression test."""
        captured = {}

        import tools.scan.core as _core
        monkeypatch.setattr(
            _core,
            "scan_workspace",
            lambda root, workspace, config=None, db_path=None: (
                captured.__setitem__("workspace", workspace)
                or _make_stub_result()
            ),
        )

        import gaia.project as _proj
        monkeypatch.setattr(_proj, "current", lambda cwd=None: "remote-first-identity")

        args = _MockArgs(
            path=str(tmp_path),
            # workspace_name intentionally absent -- tests getattr default of None
            json=True,
        )
        rc = scan_mod._run_scan(tmp_path, _DummyScanConfig(), args, "test")
        assert rc == 0
        assert captured.get("workspace") == "remote-first-identity", (
            f"expected remote-first-identity, got {captured.get('workspace')!r}"
        )

    def test_workspace_name_flag_end_to_end_with_json(self, tmp_path, monkeypatch):
        """End-to-end: cmd_scan with --workspace-name writes the named workspace
        into the scan summary (json output) rather than a git-remote derivation.

        This is the AC-1 evidence path: the workspace reported in the scan result
        matches the --workspace-name value, NOT a remote-derived identity.
        """
        captured = {}

        import tools.scan.core as _core
        monkeypatch.setattr(
            _core,
            "scan_workspace",
            lambda root, workspace, config=None, db_path=None: (
                captured.__setitem__("workspace", workspace)
                or _make_stub_result()
            ),
        )

        # Minimal git repo with a remote URL so that WITHOUT --workspace-name
        # the remote would win over the directory name.
        import subprocess
        subprocess.run(["git", "init", "--quiet"], cwd=str(tmp_path), check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/org/my-service.git"],
            cwd=str(tmp_path),
            check=True,
        )
        # Make the path look like an installed workspace.
        claude = tmp_path / ".claude"
        claude.mkdir()
        import json as _json
        (claude / "plugin-registry.json").write_text(
            _json.dumps({"installed": [{"name": "gaia-ops"}]})
        )

        args = _MockArgs(
            path=str(tmp_path),
            workspace_name="my-local-monorepo",
            json=True,
        )
        rc = scan_mod.cmd_scan(args)
        assert rc == 0

        # The workspace passed to scan_workspace must be the override, not
        # "github.com/org/my-service" (what current() would derive).
        assert captured.get("workspace") == "my-local-monorepo", (
            f"--workspace-name override not honoured: "
            f"scan_workspace received {captured.get('workspace')!r}, "
            "expected 'my-local-monorepo'"
        )

    def test_workspace_name_no_flag_uses_remote_first(self, tmp_path, monkeypatch):
        """No-regression: without --workspace-name the remote-first identity is used."""
        captured = {}

        import tools.scan.core as _core
        monkeypatch.setattr(
            _core,
            "scan_workspace",
            lambda root, workspace, config=None, db_path=None: (
                captured.__setitem__("workspace", workspace)
                or _make_stub_result()
            ),
        )

        # Repo with a clear remote -- remote-first should win.
        import subprocess
        subprocess.run(["git", "init", "--quiet"], cwd=str(tmp_path), check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/org/my-service.git"],
            cwd=str(tmp_path),
            check=True,
        )
        claude = tmp_path / ".claude"
        claude.mkdir()
        import json as _json
        (claude / "plugin-registry.json").write_text(
            _json.dumps({"installed": [{"name": "gaia-ops"}]})
        )

        args = _MockArgs(path=str(tmp_path), json=True)
        rc = scan_mod.cmd_scan(args)
        assert rc == 0

        ws = captured.get("workspace")
        assert ws == "github.com/org/my-service", (
            f"expected remote-first identity 'github.com/org/my-service', got {ws!r}"
        )


# ---------------------------------------------------------------------------
# Helpers for TestWorkspaceNameFlag stubs
# ---------------------------------------------------------------------------

class _DummyScanConfig:
    """Minimal scan config stub for unit-level _run_scan tests."""
    project_root = None
    verbose = False
    scanners = None
    staleness_hours = 24


def _make_stub_result():
    """Return a minimal ScanResult-like object that _run_scan can consume."""
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
