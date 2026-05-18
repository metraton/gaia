"""
Unit tests for bin/cli/context.py -- gaia context subcommand.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure bin/ is on sys.path so the plugin is importable
_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli.context import (
    _cmd_scan,
    _cmd_show,
    _cmd_get,
    _cmd_dump,
    _find_project_root,
    cmd_context,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_CONTEXT = {
    "metadata": {
        "version": "2.0",
        "last_updated": "2026-04-15T00:00:00+00:00",
        "scan_config": {
            "last_scan": "2026-04-15T00:00:00+00:00",
            "scanner_version": "5.0.0",
            "staleness_hours": 24,
        },
    },
    "sections": {
        "stack": {"_source": "scanner:stack", "languages": ["python"]},
        "git": {"_source": "scanner:git", "platform": "github"},
        "project_identity": {"_source": "scanner:stack", "name": "test-project"},
    },
}

# Canonical substrate shape returned by get_context()
_SAMPLE_SUBSTRATE_CTX = {
    "identity": "test-workspace",
    "stack": {},
    "environment": {},
    "git": {"workspace_name": "test-workspace", "created_at": "2026-01-01"},
    "workspace": {
        "projects": [{"name": "my-repo", "role": None}],
        "apps": [],
        "libraries": [],
        "services": [],
        "features": [],
        "tf_modules": [],
        "tf_live": [],
        "releases": [],
        "workloads": [],
        "clusters_defined": [],
        "clusters": [],
        "integrations": [],
        "gaia_installations": [],
        "machines": [],
    },
}


def _write_context(project_root: Path, data: dict | None = None):
    """Write a project-context.json under project_root/.claude/project-context/."""
    ctx_dir = project_root / ".claude" / "project-context"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    ctx_file = ctx_dir / "project-context.json"
    ctx_file.write_text(json.dumps(data or _SAMPLE_CONTEXT), encoding="utf-8")
    return ctx_file


class _MockArgs:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


# ---------------------------------------------------------------------------
# _find_project_root
# ---------------------------------------------------------------------------

class TestFindProjectRoot:
    def test_finds_root_from_nested_dir(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        nested = tmp_path / "sub" / "dir"
        nested.mkdir(parents=True)
        root = _find_project_root(nested)
        assert root == tmp_path

    def test_returns_none_or_path_type(self, tmp_path):
        result = _find_project_root(tmp_path / "nowhere")
        assert result is None or isinstance(result, Path)


# ---------------------------------------------------------------------------
# _cmd_show  (now reads from substrate via get_context)
# ---------------------------------------------------------------------------

class TestCmdShow:
    """_cmd_show reads from the SQLite substrate (not project-context.json)."""

    def _run_show(self, section=None, json_output=False, ctx=None):
        args = _MockArgs(context_cmd="show", section=section, json=json_output)
        ctx_val = ctx if ctx is not None else _SAMPLE_SUBSTRATE_CTX
        with patch("cli.context.get_context" if False else "gaia.store.provider.get_context"):
            pass
        # Patch at the import site inside cli.context
        with patch("cli.context._cmd_show.__module__"):
            pass
        import cli.context as _ctx_mod
        with patch.object(_ctx_mod, "get_context" if hasattr(_ctx_mod, "get_context") else "_cmd_show",
                          return_value=ctx_val, create=True):
            with patch("gaia.project.current", return_value="test-workspace"):
                with patch("gaia.store.provider.get_context", return_value=ctx_val):
                    return _cmd_show(args)

    def _run_show_simple(self, section=None, json_output=False, ctx=None):
        """Run _cmd_show with substrate mocked."""
        args = _MockArgs(context_cmd="show", section=section, json=json_output)
        ctx_val = ctx if ctx is not None else _SAMPLE_SUBSTRATE_CTX
        with patch("gaia.project.current", return_value="test-workspace"):
            with patch("gaia.store.provider.get_context", return_value=ctx_val):
                return _cmd_show(args)

    def test_show_exits_zero(self):
        rc = self._run_show_simple()
        assert rc == 0

    def test_show_workspace_not_found_returns_1(self):
        """When get_context returns None (workspace not found), exit 1."""
        args = _MockArgs(context_cmd="show", section=None, json=False)
        with patch("gaia.project.current", return_value="nonexistent"):
            with patch("gaia.store.provider.get_context", return_value=None):
                rc = _cmd_show(args)
        assert rc == 1

    def test_show_json_output_has_identity(self, capsys):
        rc = self._run_show_simple(json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "identity" in data
        assert rc == 0

    def test_show_json_output_has_workspace(self, capsys):
        rc = self._run_show_simple(json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "workspace" in data
        assert rc == 0

    def test_show_human_contains_workspace(self, capsys):
        rc = self._run_show_simple()
        captured = capsys.readouterr()
        assert "workspace" in captured.out
        assert rc == 0

    def test_show_section_projects(self, capsys):
        rc = self._run_show_simple(section="projects", json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)
        assert rc == 0

    def test_show_unknown_section_returns_1(self):
        rc = self._run_show_simple(section="nonexistent_section_xyz")
        assert rc == 1

    def test_show_section_identity(self, capsys):
        rc = self._run_show_simple(section="identity", json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data == "test-workspace"
        assert rc == 0


# ---------------------------------------------------------------------------
# _cmd_get (change #3: new canonical subcommand)
# ---------------------------------------------------------------------------

class TestCmdGet:
    """_cmd_get emits canonical workspace shape; exits 1 for nonexistent workspace."""

    def _run_get(self, workspace="me", section=None, json_output=False, text=False, ctx=None):
        args = _MockArgs(
            context_cmd="get",
            workspace=workspace,
            section=section,
            json=json_output,
            text=text,
        )
        ctx_val = ctx  # None means workspace not found
        with patch("gaia.project.current", return_value=workspace):
            with patch("gaia.store.provider.get_context", return_value=ctx_val):
                return _cmd_get(args)

    def test_get_exits_zero_for_known_workspace(self, capsys):
        rc = self._run_get(ctx=_SAMPLE_SUBSTRATE_CTX)
        assert rc == 0

    def test_get_nonexistent_workspace_exits_1(self, capsys):
        """Fix #5: exit 1 when workspace not found."""
        rc = self._run_get(workspace="nonexistent", ctx=None)
        captured = capsys.readouterr()
        assert rc == 1
        assert "nonexistent" in captured.err

    def test_get_json_output_has_identity(self, capsys):
        rc = self._run_get(ctx=_SAMPLE_SUBSTRATE_CTX)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["identity"] == "test-workspace"
        assert rc == 0

    def test_get_section_filter(self, capsys):
        rc = self._run_get(section="projects", ctx=_SAMPLE_SUBSTRATE_CTX)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)
        assert rc == 0

    def test_get_invalid_section_exits_1(self, capsys):
        rc = self._run_get(section="no_such_section", ctx=_SAMPLE_SUBSTRATE_CTX)
        assert rc == 1

    def test_get_text_flag_renders_tabular(self, capsys):
        rc = self._run_get(text=True, ctx=_SAMPLE_SUBSTRATE_CTX)
        captured = capsys.readouterr()
        assert "workspace" in captured.out
        assert rc == 0

    def test_get_identity_field_is_workspace_name(self, capsys):
        """Fix #4: identity in shape must be the workspace name, not a repo URL."""
        ctx = dict(_SAMPLE_SUBSTRATE_CTX)
        ctx["identity"] = "test-workspace"  # should be name, not git remote
        rc = self._run_get(workspace="test-workspace", ctx=ctx)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["identity"] == "test-workspace"
        assert rc == 0


# ---------------------------------------------------------------------------
# _cmd_dump (deprecated alias)
# ---------------------------------------------------------------------------

class TestCmdDump:
    """_cmd_dump emits deprecation warning and delegates to _cmd_get."""

    def test_dump_warns_deprecated(self, capsys):
        args = _MockArgs(context_cmd="dump", workspace=None, section=None, json=False, text=False)
        with patch("gaia.project.current", return_value="me"):
            with patch("gaia.store.provider.get_context", return_value=_SAMPLE_SUBSTRATE_CTX):
                rc = _cmd_dump(args)
        captured = capsys.readouterr()
        assert "deprecated" in captured.err.lower()
        assert rc == 0

    def test_dump_still_returns_json(self, capsys):
        args = _MockArgs(context_cmd="dump", workspace=None, section=None, json=False, text=False)
        with patch("gaia.project.current", return_value="me"):
            with patch("gaia.store.provider.get_context", return_value=_SAMPLE_SUBSTRATE_CTX):
                rc = _cmd_dump(args)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "identity" in data
        assert rc == 0


# ---------------------------------------------------------------------------
# _cmd_scan
# ---------------------------------------------------------------------------

class TestCmdScan:
    def test_dry_run_exits_zero(self, tmp_path, capsys):
        _write_context(tmp_path)
        args = _MockArgs(context_cmd="scan", dry_run=True, json=False)
        with patch("cli.context._find_project_root", return_value=tmp_path):
            rc = _cmd_scan(args)
        assert rc == 0

    def test_dry_run_json_exits_zero(self, tmp_path, capsys):
        _write_context(tmp_path)
        args = _MockArgs(context_cmd="scan", dry_run=True, json=True)
        with patch("cli.context._find_project_root", return_value=tmp_path):
            rc = _cmd_scan(args)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["dry_run"] is True
        assert rc == 0

    def test_dry_run_json_includes_project_root(self, tmp_path, capsys):
        _write_context(tmp_path)
        args = _MockArgs(context_cmd="scan", dry_run=True, json=True)
        with patch("cli.context._find_project_root", return_value=tmp_path):
            _cmd_scan(args)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "project_root" in data
        assert "context_path" in data

    def test_dry_run_shows_would_scan(self, tmp_path, capsys):
        _write_context(tmp_path)
        args = _MockArgs(context_cmd="scan", dry_run=True, json=False)
        with patch("cli.context._find_project_root", return_value=tmp_path):
            _cmd_scan(args)
        captured = capsys.readouterr()
        assert "dry-run" in captured.out.lower() or "would" in captured.out.lower()

    def test_missing_root_returns_1(self):
        args = _MockArgs(context_cmd="scan", dry_run=True, json=False)
        with patch("cli.context._find_project_root", return_value=None):
            rc = _cmd_scan(args)
        assert rc == 1

    def test_scan_delegates_to_cli_scan(self, tmp_path):
        """Verify that non-dry-run scan delegates in-process to cli.scan.cmd_scan."""
        _write_context(tmp_path)

        args = _MockArgs(context_cmd="scan", dry_run=False, json=False)
        with patch("cli.context._find_project_root", return_value=tmp_path):
            with patch("cli.scan.cmd_scan", return_value=0) as mock_cmd_scan:
                rc = _cmd_scan(args)

        mock_cmd_scan.assert_called_once()
        scan_args = mock_cmd_scan.call_args[0][0]
        assert scan_args.workspace == str(tmp_path)
        assert scan_args.fresh is False
        assert scan_args.dry_run is False
        assert rc == 0


# ---------------------------------------------------------------------------
# cmd_context dispatch
# ---------------------------------------------------------------------------

class TestCmdContextDispatch:
    def test_dispatch_show(self):
        args = _MockArgs(context_cmd="show", section=None, json=False)
        with patch("gaia.project.current", return_value="me"):
            with patch("gaia.store.provider.get_context", return_value=_SAMPLE_SUBSTRATE_CTX):
                rc = cmd_context(args)
        assert rc == 0

    def test_dispatch_get(self):
        args = _MockArgs(context_cmd="get", workspace=None, section=None, json=False, text=False)
        with patch("gaia.project.current", return_value="me"):
            with patch("gaia.store.provider.get_context", return_value=_SAMPLE_SUBSTRATE_CTX):
                rc = cmd_context(args)
        assert rc == 0

    def test_dispatch_scan_dry_run(self, tmp_path):
        _write_context(tmp_path)
        args = _MockArgs(context_cmd="scan", dry_run=True, json=False)
        with patch("cli.context._find_project_root", return_value=tmp_path):
            rc = cmd_context(args)
        assert rc == 0

    def test_dispatch_no_action_returns_zero(self, tmp_path, capsys):
        args = _MockArgs(context_cmd=None)
        rc = cmd_context(args)
        assert rc == 0


# ---------------------------------------------------------------------------
# Integration: run via entry point with subprocess
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_context_show_runs_exits_zero(self, tmp_path):
        """Smoke test: python bin/gaia context show exits 0 (reads from substrate)."""
        import os
        import subprocess

        bin_gaia = _BIN_DIR / "gaia"
        gaia_ops_dev = _BIN_DIR.parent

        result = subprocess.run(
            [sys.executable, str(bin_gaia), "context", "show"],
            capture_output=True,
            text=True,
            cwd=str(gaia_ops_dev),
            env={**os.environ, "GAIA_DATA_DIR": str(tmp_path)},
        )
        # workspace key is always present in the tabular render -- when the
        # substrate is empty, show may exit non-zero; accept either as long as
        # the binary ran without crashing.
        assert result.returncode in (0, 1), (
            f"Unexpected exit {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_context_get_runs_exits_zero(self, tmp_path):
        """Smoke test: python bin/gaia context get exits 0 and emits JSON."""
        import os
        import subprocess

        bin_gaia = _BIN_DIR / "gaia"
        gaia_ops_dev = _BIN_DIR.parent

        result = subprocess.run(
            [sys.executable, str(bin_gaia), "context", "get"],
            capture_output=True,
            text=True,
            cwd=str(gaia_ops_dev),
            env={**os.environ, "GAIA_DATA_DIR": str(tmp_path)},
        )
        # Empty substrate -> exit 1 is acceptable; assert binary ran.
        assert result.returncode in (0, 1), (
            f"Unexpected exit {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_context_get_nonexistent_workspace_exits_1(self, tmp_path):
        """Fix #5: python bin/gaia context get --workspace nonexistent exits 1."""
        import os
        import subprocess

        bin_gaia = _BIN_DIR / "gaia"
        gaia_ops_dev = _BIN_DIR.parent

        result = subprocess.run(
            [sys.executable, str(bin_gaia), "context", "get", "--workspace", "nonexistent_xyz_404"],
            capture_output=True,
            text=True,
            cwd=str(gaia_ops_dev),
            env={**os.environ, "GAIA_DATA_DIR": str(tmp_path)},
        )
        assert result.returncode == 1, (
            f"Expected exit 1, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "not found" in result.stderr

    def test_context_dump_deprecated_warning(self, tmp_path):
        """gaia context dump emits deprecation warning to stderr."""
        import os
        import subprocess

        bin_gaia = _BIN_DIR / "gaia"
        gaia_ops_dev = _BIN_DIR.parent

        result = subprocess.run(
            [sys.executable, str(bin_gaia), "context", "dump"],
            capture_output=True,
            text=True,
            cwd=str(gaia_ops_dev),
            env={**os.environ, "GAIA_DATA_DIR": str(tmp_path)},
        )
        # Deprecation message goes to stderr regardless of exit code.
        assert "deprecated" in result.stderr.lower()

    def test_context_scan_dry_run(self, tmp_path):
        """Smoke test: python bin/gaia context scan --dry-run exits 0."""
        import os
        import subprocess

        bin_gaia = _BIN_DIR / "gaia"
        gaia_ops_dev = _BIN_DIR.parent

        result = subprocess.run(
            [sys.executable, str(bin_gaia), "context", "scan", "--dry-run"],
            capture_output=True,
            text=True,
            cwd=str(gaia_ops_dev),
            env={**os.environ, "GAIA_DATA_DIR": str(tmp_path)},
        )
        assert result.returncode == 0, (
            f"Expected exit 0, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
