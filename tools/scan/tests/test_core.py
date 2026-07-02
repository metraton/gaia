"""
Tests for tools/scan/core.py -- the pure scan núcleo.

These tests assert the module boundary that the scan/install separation
introduced:

  * scan-core scans + populates + soft-prunes and NEVER installs (no
    package.json created, no npm run, no .claude/ built, no git hooks).
  * is_gaia_workspace reflects the canonical plugin-registry signal.
  * ScanResult carries populate + soft-prune outcomes.

Isolation: GAIA_DATA_DIR is redirected to a tmp dir; run_scanners is patched to
a lightweight ScanOutput so no real scanner environment is needed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.scan import core as scan_core


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_dir = tmp_path / "gaia_data"
    db_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(db_dir))
    from gaia.paths import db_path
    return db_path()


def _dummy_output(errors=None):
    from tools.scan.orchestrator import ScanOutput
    return ScanOutput(
        sections_updated=["stack"],
        sections_preserved=[],
        warnings=[],
        errors=errors or [],
        duration_ms=1.0,
        scanner_results={},
    )


def _init_git_repo(path: Path, remote="https://github.com/test/repo.git") -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet"], cwd=str(path), check=True)
    subprocess.run(["git", "remote", "add", "origin", remote], cwd=str(path), check=True)


def _make_config(project_root: Path):
    from tools.scan.config import load_scan_config
    cfg = load_scan_config(project_root)
    cfg.project_root = project_root
    return cfg


# ---------------------------------------------------------------------------
# is_gaia_workspace
# ---------------------------------------------------------------------------

class TestIsGaiaWorkspace:
    def test_plain_dir_is_not_a_workspace(self, tmp_path):
        assert scan_core.is_gaia_workspace(tmp_path) is False

    def test_registry_with_gaia_is_a_workspace(self, tmp_path):
        claude = tmp_path / ".claude"
        claude.mkdir()
        (claude / "plugin-registry.json").write_text(
            json.dumps({"installed": [{"name": "gaia"}]})
        )
        assert scan_core.is_gaia_workspace(tmp_path) is True

    def test_registry_with_other_plugin_is_not_a_workspace(self, tmp_path):
        claude = tmp_path / ".claude"
        claude.mkdir()
        (claude / "plugin-registry.json").write_text(
            json.dumps({"installed": [{"name": "some-other-plugin"}]})
        )
        assert scan_core.is_gaia_workspace(tmp_path) is False


# ---------------------------------------------------------------------------
# scan_workspace: pure, no install side-effects
# ---------------------------------------------------------------------------

class TestScanWorkspaceIsPure:
    def test_scan_does_not_create_package_json(self, tmp_db, tmp_path, monkeypatch):
        """scan_workspace must NEVER write a package.json (install is separate)."""
        monkeypatch.setattr(scan_core, "run_scanners", lambda r, c: _dummy_output())
        _init_git_repo(tmp_path / "repo")

        from gaia.project import current as _current
        ws = _current(cwd=tmp_path)
        scan_core.scan_workspace(tmp_path, ws, config=_make_config(tmp_path), db_path=tmp_db)

        assert not (tmp_path / "package.json").exists(), (
            "scan-core must not create package.json"
        )

    def test_scan_does_not_build_claude_dir(self, tmp_db, tmp_path, monkeypatch):
        """scan_workspace must NOT create a .claude/ directory or symlinks."""
        monkeypatch.setattr(scan_core, "run_scanners", lambda r, c: _dummy_output())
        _init_git_repo(tmp_path / "repo")

        from gaia.project import current as _current
        ws = _current(cwd=tmp_path)
        scan_core.scan_workspace(tmp_path, ws, config=_make_config(tmp_path), db_path=tmp_db)

        assert not (tmp_path / ".claude").exists(), (
            "scan-core must not build the .claude/ directory"
        )

    def test_scan_does_not_run_subprocess_npm(self, tmp_db, tmp_path, monkeypatch):
        """scan_workspace must never shell out (no npm/git-hook install)."""
        monkeypatch.setattr(scan_core, "run_scanners", lambda r, c: _dummy_output())
        _init_git_repo(tmp_path / "repo")

        calls = []
        real_run = subprocess.run

        def _tracking_run(*a, **kw):
            calls.append(a[0] if a else kw.get("args"))
            return real_run(*a, **kw)

        monkeypatch.setattr(subprocess, "run", _tracking_run)

        from gaia.project import current as _current
        ws = _current(cwd=tmp_path)
        scan_core.scan_workspace(tmp_path, ws, config=_make_config(tmp_path), db_path=tmp_db)

        npm_calls = [c for c in calls if c and "npm" in (c[0] if isinstance(c, (list, tuple)) else str(c))]
        assert npm_calls == [], f"scan-core must not invoke npm; saw {npm_calls!r}"

    def test_scan_populates_and_returns_result(self, tmp_db, tmp_path, monkeypatch):
        monkeypatch.setattr(scan_core, "run_scanners", lambda r, c: _dummy_output())
        _init_git_repo(tmp_path / "repo")

        # The CLI-root must be an INSTALLED Gaia workspace for scan_workspace to
        # take the normal populate path. Since the v17 demote/soft-delete change,
        # a directory with no install footprint is demoted (populated=None)
        # rather than populated. Mount the canonical install signal -- a
        # .claude/plugin-registry.json listing "gaia" -- the same fixture
        # TestIsGaiaWorkspace uses. This is scoped to THIS test only: the
        # no-side-effect tests above must NOT carry a .claude/ footprint.
        claude = tmp_path / ".claude"
        claude.mkdir()
        (claude / "plugin-registry.json").write_text(
            json.dumps({"installed": [{"name": "gaia"}]})
        )

        from gaia.project import current as _current
        ws = _current(cwd=tmp_path)
        result = scan_core.scan_workspace(
            tmp_path, ws, config=_make_config(tmp_path), db_path=tmp_db
        )

        assert isinstance(result, scan_core.ScanResult)
        assert result.has_errors is False
        assert result.populated is not None

    def test_scan_skips_populate_on_errors(self, tmp_db, tmp_path, monkeypatch):
        monkeypatch.setattr(
            scan_core, "run_scanners", lambda r, c: _dummy_output(errors=["boom"])
        )
        called = []
        monkeypatch.setattr(
            scan_core, "populate_store",
            lambda *a, **kw: (called.append(1) or ({}, 0, [])),
        )
        from gaia.project import current as _current
        ws = _current(cwd=tmp_path)
        result = scan_core.scan_workspace(
            tmp_path, ws, config=_make_config(tmp_path), db_path=tmp_db
        )
        assert result.has_errors is True
        assert called == [], "populate must be skipped when scanners report errors"
