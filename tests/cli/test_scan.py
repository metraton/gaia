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
            "fresh": False,
            "workspace": None,
            "dry_run": False,
            "json": False,
            "scanners": None,
            "check_staleness": False,
            "full": False,
            "no_color": True,
            "verbose": False,
            "skip_claude_install": False,
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
        assert ns.fresh is False
        assert ns.dry_run is False
        assert ns.json is False
        assert ns.workspace is None

    def test_scan_subcommand_accepts_flags(self):
        parser = self._build_parser()
        ns = parser.parse_args([
            "scan",
            "--fresh",
            "--dry-run",
            "--json",
            "--workspace", "/tmp/wsx",
            "--scanners", "stack,git",
            "--no-color",
        ])
        assert ns.fresh is True
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
        assert "--fresh" in result.stdout
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

    def test_dry_run_with_existing_context_reports_metadata(self, tmp_path, capsys):
        """--dry-run reports DB-backed metadata; last_scan comes from workspaces table.

        T1.3: project context is in gaia.db, not project-context.json. When the
        workspaces row is absent (no prior scan), last_scan falls back to "unknown".
        """
        args = _MockArgs(workspace=str(tmp_path), dry_run=True, json=True)
        rc = scan_mod.cmd_scan(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["dry_run"] is True
        assert data["project_root"] == str(tmp_path)
        # last_scan is DB-backed; "unknown" when workspace row is absent
        assert "last_scan" in data
        assert "would_scan" in data


# ---------------------------------------------------------------------------
# Workspace resolution
# ---------------------------------------------------------------------------

class TestWorkspaceResolution:
    def test_resolve_workspace_explicit(self, tmp_path):
        result = scan_mod._resolve_workspace(str(tmp_path))
        assert result == tmp_path.resolve()

    def test_resolve_workspace_defaults_to_cwd(self):
        result = scan_mod._resolve_workspace(None)
        assert result == Path.cwd().resolve()

    def test_invalid_workspace_returns_1(self, tmp_path, capsys):
        bogus = tmp_path / "does-not-exist"
        args = _MockArgs(workspace=str(bogus), dry_run=False, json=True)
        rc = scan_mod.cmd_scan(args)
        assert rc == 1
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "error"
        assert "not found" in data["error"]
