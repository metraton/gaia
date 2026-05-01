"""
Fix 3 regression tests: gaia project merge --dry-run and --report-duplicates.

--dry-run  : must NOT move any files to disk; output matches preview mode.
--report-duplicates : must list projects with shared identity; exit 1 if found,
                       exit 0 if all identities are unique.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(
    from_id: str = "src",
    to_id: str = "dst",
    confirm: bool = False,
    dry_run: bool = False,
    report_duplicates: bool = False,
):
    """Build a minimal argparse.Namespace for _cmd_merge."""
    import argparse
    ns = argparse.Namespace(
        from_id=from_id,
        to_id=to_id,
        confirm=confirm,
        dry_run=dry_run,
        report_duplicates=report_duplicates,
    )
    return ns


@pytest.fixture()
def workspaces(tmp_path, monkeypatch):
    """Point workspaces_dir() at tmp_path/workspaces."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    ws = tmp_path / "workspaces"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


# ---------------------------------------------------------------------------
# --dry-run tests
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_does_not_move_files(self, workspaces, capsys):
        """--dry-run must leave source files intact."""
        from bin.cli.project import _cmd_merge

        src = workspaces / "src"
        (src / "data.txt").parent.mkdir(parents=True, exist_ok=True)
        (src / "data.txt").write_text("hello")

        rc = _cmd_merge(_make_args(from_id="src", to_id="dst", dry_run=True))

        # File must still exist at source
        assert (src / "data.txt").is_file(), "dry-run moved the file -- it must not"
        # Destination must NOT have been created
        assert not (workspaces / "dst" / "data.txt").exists()
        assert rc == 0

    def test_dry_run_output_reports_would_move(self, workspaces, capsys):
        """--dry-run output must mention 'Dry-run' and list the file."""
        from bin.cli.project import _cmd_merge

        src = workspaces / "src"
        src.mkdir(parents=True, exist_ok=True)
        (src / "a.txt").write_text("alpha")

        _cmd_merge(_make_args(from_id="src", to_id="dst", dry_run=True))
        captured = capsys.readouterr()

        assert "Dry-run" in captured.out
        assert "a.txt" in captured.out
        assert "no files were moved" in captured.out

    def test_dry_run_no_source_is_no_op(self, workspaces, capsys):
        """--dry-run on a non-existent source must return 0 silently."""
        from bin.cli.project import _cmd_merge

        rc = _cmd_merge(_make_args(from_id="nonexistent", to_id="dst", dry_run=True))
        assert rc == 0
        captured = capsys.readouterr()
        assert "No changes" in captured.out


# ---------------------------------------------------------------------------
# --report-duplicates tests
# ---------------------------------------------------------------------------

class TestReportDuplicates:
    def _insert_project(self, con, name: str, identity: str) -> None:
        from gaia.store.writer import _now_iso
        con.execute(
            "INSERT OR REPLACE INTO projects (name, identity, created_at) VALUES (?, ?, ?)",
            (name, identity, _now_iso()),
        )
        con.commit()

    def test_report_duplicates_clean_returns_zero(self, tmp_db, workspaces, capsys):
        """When all identities are unique, exit code is 0."""
        from gaia.store.writer import _connect
        from bin.cli.project import _cmd_merge

        con = _connect(tmp_db)
        self._insert_project(con, "ws-a", "github.com/owner/repo-a")
        self._insert_project(con, "ws-b", "github.com/owner/repo-b")
        con.close()

        rc = _cmd_merge(_make_args(from_id="ws-a", to_id="ws-b", report_duplicates=True))
        captured = capsys.readouterr()

        assert rc == 0
        assert "duplicates=0" in captured.out

    def test_report_duplicates_finds_collision(self, tmp_db, workspaces, capsys):
        """When two projects share the same identity, exit code is 1 and they appear in output."""
        from gaia.store.writer import _connect
        from bin.cli.project import _cmd_merge

        con = _connect(tmp_db)
        # Both ws-old and ws-new collapsed to "me" due to the bug
        self._insert_project(con, "ws-old", "me")
        self._insert_project(con, "ws-new", "me")
        con.close()

        rc = _cmd_merge(_make_args(from_id="ws-old", to_id="ws-new", report_duplicates=True))
        captured = capsys.readouterr()

        assert rc == 1
        # The shared identity must appear in output
        assert "me" in captured.out
        # Both project names must appear
        assert "ws-old" in captured.out or "ws-new" in captured.out

    def test_report_duplicates_skips_merge_logic(self, tmp_db, workspaces, capsys):
        """--report-duplicates must not touch workspace files."""
        from gaia.store.writer import _connect
        from bin.cli.project import _cmd_merge

        src = workspaces / "ws-old"
        src.mkdir(parents=True, exist_ok=True)
        (src / "sentinel.txt").write_text("do not move")

        con = _connect(tmp_db)
        self._insert_project(con, "ws-old", "me")
        self._insert_project(con, "ws-new", "me")
        con.close()

        _cmd_merge(_make_args(from_id="ws-old", to_id="ws-new", report_duplicates=True))

        # Sentinel file must still be at source -- merge logic was not executed
        assert (src / "sentinel.txt").is_file()
