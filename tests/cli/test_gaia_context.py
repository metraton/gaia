"""
Unit tests for bin/cli/context.py -- gaia context subcommand.
"""

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
# _cmd_show
# ---------------------------------------------------------------------------

class TestCmdShow:
    def _run_show(self, tmp_path, section=None, json_output=False):
        args = _MockArgs(context_cmd="show", section=section, json=json_output)
        with patch("cli.context._find_project_root", return_value=tmp_path):
            return _cmd_show(args)

    def test_show_exits_zero(self, tmp_path):
        _write_context(tmp_path)
        rc = self._run_show(tmp_path)
        assert rc == 0

    def test_show_missing_root_returns_1(self):
        args = _MockArgs(context_cmd="show", section=None, json=False)
        with patch("cli.context._find_project_root", return_value=None):
            rc = _cmd_show(args)
        assert rc == 1

    def test_show_missing_context_file_returns_1(self, tmp_path):
        # No project-context.json written
        (tmp_path / ".claude" / "project-context").mkdir(parents=True)
        rc = self._run_show(tmp_path)
        assert rc == 1

    def test_show_json_summary_has_sections(self, tmp_path, capsys):
        _write_context(tmp_path)
        rc = self._run_show(tmp_path, json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "sections" in data
        assert isinstance(data["sections"], list)
        assert rc == 0

    def test_show_json_summary_contains_stack(self, tmp_path, capsys):
        _write_context(tmp_path)
        self._run_show(tmp_path, json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "stack" in data["sections"]

    def test_show_human_contains_stack(self, tmp_path, capsys):
        _write_context(tmp_path)
        rc = self._run_show(tmp_path)
        captured = capsys.readouterr()
        assert "stack" in captured.out
        assert rc == 0

    def test_show_section_returns_section_data(self, tmp_path, capsys):
        _write_context(tmp_path)
        rc = self._run_show(tmp_path, section="stack", json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data.get("_source") == "scanner:stack"
        assert rc == 0

    def test_show_unknown_section_returns_1(self, tmp_path):
        _write_context(tmp_path)
        rc = self._run_show(tmp_path, section="nonexistent")
        assert rc == 1

    def test_show_invalid_json_returns_1(self, tmp_path):
        ctx_dir = tmp_path / ".claude" / "project-context"
        ctx_dir.mkdir(parents=True)
        (ctx_dir / "project-context.json").write_text("not-valid-json{{{", encoding="utf-8")
        rc = self._run_show(tmp_path)
        assert rc == 1


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

    def test_scan_shelled_out(self, tmp_path):
        """Verify that non-dry-run scan shells out to gaia-scan.py (not imports it)."""
        _write_context(tmp_path)

        # Create a fake gaia-scan.py in bin/ next to context.py's parent
        fake_scan = _BIN_DIR / "gaia-scan.py"
        # It exists in the real repo; patch subprocess.run to avoid side effects
        args = _MockArgs(context_cmd="scan", dry_run=False, json=False)
        with patch("cli.context._find_project_root", return_value=tmp_path):
            with patch("cli.context.subprocess.run") as mock_run:
                mock_proc = MagicMock()
                mock_proc.returncode = 0
                mock_run.return_value = mock_proc
                rc = _cmd_scan(args)

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        # The command must be a list containing gaia-scan.py, not an import
        assert any("gaia-scan.py" in str(arg) for arg in call_args)
        assert rc == 0

    def test_scan_missing_scan_script_returns_1(self, tmp_path):
        _write_context(tmp_path)
        args = _MockArgs(context_cmd="scan", dry_run=False, json=False)
        with patch("cli.context._find_project_root", return_value=tmp_path):
            with patch("pathlib.Path.exists", return_value=False):
                rc = _cmd_scan(args)
        assert rc == 1


# ---------------------------------------------------------------------------
# cmd_context dispatch
# ---------------------------------------------------------------------------

class TestCmdContextDispatch:
    def test_dispatch_show(self, tmp_path):
        _write_context(tmp_path)
        args = _MockArgs(context_cmd="show", section=None, json=False)
        with patch("cli.context._find_project_root", return_value=tmp_path):
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
    def test_context_show_runs_and_contains_stack(self):
        """Smoke test: python bin/gaia context show exits 0 and contains 'stack'."""
        import subprocess

        bin_gaia = _BIN_DIR / "gaia"
        gaia_ops_dev = _BIN_DIR.parent

        result = subprocess.run(
            [sys.executable, str(bin_gaia), "context", "show"],
            capture_output=True,
            text=True,
            cwd=str(gaia_ops_dev),
        )
        assert result.returncode == 0, (
            f"Expected exit 0, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "stack" in result.stdout

    def test_context_scan_dry_run(self):
        """Smoke test: python bin/gaia context scan --dry-run exits 0."""
        import subprocess

        bin_gaia = _BIN_DIR / "gaia"
        gaia_ops_dev = _BIN_DIR.parent

        result = subprocess.run(
            [sys.executable, str(bin_gaia), "context", "scan", "--dry-run"],
            capture_output=True,
            text=True,
            cwd=str(gaia_ops_dev),
        )
        assert result.returncode == 0, (
            f"Expected exit 0, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
