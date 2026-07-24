"""Unit tests for bin/cli/_converge.py -- the shared drift-free convergence
inspector for `gaia dev` and `gaia release`.

Covers the 3 idempotency cases (aligned / stale / absent) per surface, the
schema-direction verdict (forward / reverse / aligned / absent), and the
aggregate `converge_report`. Pure/read-only: every external dependency (PATH,
npm bin dir, DB path) is injected or built on a tmp path, so no test touches
the real machine state.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli import _converge  # noqa: E402


def _make_pkg_tree(root: Path, version: str) -> Path:
    """Build <root>/bin/gaia + <root>/package.json so a `gaia` shim resolving to
    <root>/bin/gaia yields package version *version* (root = shim.parent.parent)."""
    (root / "bin").mkdir(parents=True, exist_ok=True)
    (root / "bin" / "gaia").write_text("#!/usr/bin/env python3\n")
    (root / "package.json").write_text(f'{{"name": "@jaguilar87/gaia", "version": "{version}"}}\n')
    return root / "bin" / "gaia"


def _make_schema_db(path: Path, version: int | None) -> None:
    """Build a DB with a schema_version table stamped at *version* (None = table
    present but empty; use -1 sentinel to omit the table entirely)."""
    con = sqlite3.connect(str(path))
    try:
        if version == -1:
            con.execute("CREATE TABLE other (x INTEGER)")
        else:
            con.execute(
                "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, "
                "applied_at TEXT NOT NULL, description TEXT)"
            )
            if version is not None:
                con.execute(
                    "INSERT INTO schema_version VALUES (?, '2026-01-01T00:00:00Z', 't')",
                    (version,),
                )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Surface 1 -- PATH gaia
# ---------------------------------------------------------------------------

class TestInspectPathGaia(unittest.TestCase):
    def test_absent_when_not_on_path(self):
        with patch("cli._converge.shutil.which", return_value=None):
            r = _converge.inspect_path_gaia("5.2.0")
        self.assertEqual(r["state"], _converge.STATE_ABSENT)

    def test_aligned_when_version_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            shim = _make_pkg_tree(Path(tmp) / "pkg", "5.2.0")
            with patch("cli._converge.shutil.which", return_value=str(shim)):
                r = _converge.inspect_path_gaia("5.2.0")
        self.assertEqual(r["state"], _converge.STATE_ALIGNED)
        self.assertEqual(r["version"], "5.2.0")

    def test_stale_when_version_differs(self):
        with tempfile.TemporaryDirectory() as tmp:
            shim = _make_pkg_tree(Path(tmp) / "pkg", "5.1.0")
            with patch("cli._converge.shutil.which", return_value=str(shim)):
                r = _converge.inspect_path_gaia("5.2.0")
        self.assertEqual(r["state"], _converge.STATE_STALE)

    def test_aligned_when_origin_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            shim = _make_pkg_tree(Path(tmp) / "pkg", "5.1.0")
            with patch("cli._converge.shutil.which", return_value=str(shim)):
                r = _converge.inspect_path_gaia(None)
        # No origin to diverge from -> cannot prove skew.
        self.assertEqual(r["state"], _converge.STATE_ALIGNED)


# ---------------------------------------------------------------------------
# Surface 2 -- hooks in settings.local.json
# ---------------------------------------------------------------------------

class TestInspectHooksSettings(unittest.TestCase):
    def test_absent_when_no_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _converge.inspect_hooks_settings(Path(tmp))
        self.assertEqual(r["state"], _converge.STATE_ABSENT)

    def test_aligned_when_hooks_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude = Path(tmp) / ".claude"
            claude.mkdir()
            (claude / "settings.local.json").write_text('{"hooks": {"PreToolUse": []}}')
            r = _converge.inspect_hooks_settings(Path(tmp))
        self.assertEqual(r["state"], _converge.STATE_ALIGNED)

    def test_stale_when_no_hooks_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude = Path(tmp) / ".claude"
            claude.mkdir()
            (claude / "settings.local.json").write_text('{"permissions": {}}')
            r = _converge.inspect_hooks_settings(Path(tmp))
        self.assertEqual(r["state"], _converge.STATE_STALE)

    def test_unknown_when_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude = Path(tmp) / ".claude"
            claude.mkdir()
            (claude / "settings.local.json").write_text("{not json")
            r = _converge.inspect_hooks_settings(Path(tmp))
        self.assertEqual(r["state"], _converge.STATE_UNKNOWN)


# ---------------------------------------------------------------------------
# Surface 3 -- workspace node_modules
# ---------------------------------------------------------------------------

class TestInspectWorkspaceNodeModules(unittest.TestCase):
    def _install(self, workspace: Path, version: str) -> None:
        nm = workspace / "node_modules" / "@jaguilar87" / "gaia"
        nm.mkdir(parents=True)
        (nm / "package.json").write_text(f'{{"version": "{version}"}}')

    def test_absent_when_not_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _converge.inspect_workspace_node_modules(Path(tmp), "5.2.0")
        self.assertEqual(r["state"], _converge.STATE_ABSENT)

    def test_aligned_when_version_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._install(Path(tmp), "5.2.0")
            r = _converge.inspect_workspace_node_modules(Path(tmp), "5.2.0")
        self.assertEqual(r["state"], _converge.STATE_ALIGNED)

    def test_stale_when_version_differs(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._install(Path(tmp), "5.0.0")
            r = _converge.inspect_workspace_node_modules(Path(tmp), "5.2.0")
        self.assertEqual(r["state"], _converge.STATE_STALE)


# ---------------------------------------------------------------------------
# Surface 4 -- global npm
# ---------------------------------------------------------------------------

class TestInspectGlobalNpm(unittest.TestCase):
    def test_absent_when_no_bin_dir(self):
        r = _converge.inspect_global_npm(None, "5.2.0")
        self.assertEqual(r["state"], _converge.STATE_ABSENT)

    def test_absent_when_no_shim(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _converge.inspect_global_npm(Path(tmp), "5.2.0")
        self.assertEqual(r["state"], _converge.STATE_ABSENT)

    def test_aligned_when_version_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "pkg"
            _make_pkg_tree(pkg, "5.2.0")
            npm_bin = Path(tmp) / "npm-bin"
            npm_bin.mkdir()
            (npm_bin / "gaia").symlink_to(pkg / "bin" / "gaia")
            r = _converge.inspect_global_npm(npm_bin, "5.2.0")
        self.assertEqual(r["state"], _converge.STATE_ALIGNED)

    def test_stale_when_version_differs(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "pkg"
            _make_pkg_tree(pkg, "4.9.0")
            npm_bin = Path(tmp) / "npm-bin"
            npm_bin.mkdir()
            (npm_bin / "gaia").symlink_to(pkg / "bin" / "gaia")
            r = _converge.inspect_global_npm(npm_bin, "5.2.0")
        self.assertEqual(r["state"], _converge.STATE_STALE)


# ---------------------------------------------------------------------------
# Surface 5 -- DB schema + direction
# ---------------------------------------------------------------------------

class TestInspectDbSchema(unittest.TestCase):
    def test_absent_when_no_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _converge.inspect_db_schema(37, Path(tmp) / "nope.db")
        self.assertEqual(r["state"], _converge.STATE_ABSENT)
        self.assertEqual(r["direction"], _converge.DIR_ABSENT)

    def test_aligned_when_equal(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "gaia.db"
            _make_schema_db(db, 37)
            r = _converge.inspect_db_schema(37, db)
        self.assertEqual(r["state"], _converge.STATE_ALIGNED)
        self.assertEqual(r["direction"], _converge.DIR_ALIGNED)

    def test_forward_when_code_ahead(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "gaia.db"
            _make_schema_db(db, 35)
            r = _converge.inspect_db_schema(37, db)
        self.assertEqual(r["state"], _converge.STATE_STALE)
        self.assertEqual(r["direction"], _converge.DIR_FORWARD)

    def test_reverse_when_code_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "gaia.db"
            _make_schema_db(db, 40)
            r = _converge.inspect_db_schema(37, db)
        self.assertEqual(r["state"], _converge.STATE_STALE)
        self.assertEqual(r["direction"], _converge.DIR_REVERSE)

    def test_unknown_when_no_schema_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "gaia.db"
            _make_schema_db(db, -1)  # table omitted
            r = _converge.inspect_db_schema(37, db)
        self.assertEqual(r["state"], _converge.STATE_UNKNOWN)


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

class TestConvergeReport(unittest.TestCase):
    def test_fully_aligned_is_converged(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            (ws / ".claude").mkdir(parents=True)
            (ws / ".claude" / "settings.local.json").write_text('{"hooks": {"PreToolUse": []}}')
            nm = ws / "node_modules" / "@jaguilar87" / "gaia"
            nm.mkdir(parents=True)
            (nm / "package.json").write_text('{"version": "5.2.0"}')
            db = Path(tmp) / "gaia.db"
            _make_schema_db(db, 37)
            pkg = Path(tmp) / "pkg"
            shim = _make_pkg_tree(pkg, "5.2.0")
            npm_bin = Path(tmp) / "npm-bin"
            npm_bin.mkdir()
            (npm_bin / "gaia").symlink_to(pkg / "bin" / "gaia")
            with patch("cli._converge.shutil.which", return_value=str(shim)):
                report = _converge.converge_report(
                    ws, origin_version="5.2.0", expected_version=37,
                    db_path=db, npm_global_bin=npm_bin,
                )
        self.assertTrue(report["converged"])
        self.assertFalse(report["reverse_direction"])
        self.assertEqual(len(report["surfaces"]), 5)

    def test_absent_surfaces_count_as_converged_input(self):
        """The not-installed case: every surface ABSENT is still 'converged'
        (a reconcile creates cleanly)."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            with patch("cli._converge.shutil.which", return_value=None):
                report = _converge.converge_report(
                    ws, origin_version="5.2.0", expected_version=37,
                    db_path=Path(tmp) / "nope.db", npm_global_bin=None,
                )
        self.assertTrue(report["converged"])

    def test_stale_surface_breaks_convergence(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            nm = ws / "node_modules" / "@jaguilar87" / "gaia"
            nm.mkdir(parents=True)
            (nm / "package.json").write_text('{"version": "5.0.0"}')  # stale
            with patch("cli._converge.shutil.which", return_value=None):
                report = _converge.converge_report(
                    ws, origin_version="5.2.0", expected_version=37,
                    db_path=Path(tmp) / "nope.db", npm_global_bin=None,
                )
        self.assertFalse(report["converged"])

    def test_reverse_direction_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            db = Path(tmp) / "gaia.db"
            _make_schema_db(db, 99)  # DB newer than code
            with patch("cli._converge.shutil.which", return_value=None):
                report = _converge.converge_report(
                    ws, origin_version="5.2.0", expected_version=37,
                    db_path=db, npm_global_bin=None,
                )
        self.assertTrue(report["reverse_direction"])
        self.assertFalse(report["converged"])


# ---------------------------------------------------------------------------
# Shared driver -- default_db_path / format_convergence_report / run_convergence_report
# ---------------------------------------------------------------------------

class TestDefaultDbPath(unittest.TestCase):
    def test_env_override_wins(self):
        with patch.dict("os.environ", {"GAIA_DB": "/tmp/custom-gaia.db"}, clear=False):
            self.assertEqual(_converge.default_db_path(), Path("/tmp/custom-gaia.db"))

    def test_falls_back_to_home_default(self):
        env = {k: v for k, v in __import__("os").environ.items() if k != "GAIA_DB"}
        with patch.dict("os.environ", env, clear=True):
            self.assertEqual(
                _converge.default_db_path(),
                Path("~/.gaia/gaia.db").expanduser(),
            )


class TestFormatConvergenceReport(unittest.TestCase):
    def test_converged_header_and_no_reverse_line(self):
        report = {
            "surfaces": [{"surface": "path_gaia", "state": "aligned", "detail": "PATH gaia v5.2.0"}],
            "converged": True,
            "reverse_direction": False,
        }
        lines = _converge.format_convergence_report(report, "5.2.0")
        self.assertIn("CONVERGED", lines[0])
        self.assertIn("origin v5.2.0", lines[0])
        self.assertTrue(any("path_gaia" in line for line in lines))
        self.assertFalse(any("reverse-direction" in line for line in lines))

    def test_skew_header_and_reverse_warning(self):
        report = {
            "surfaces": [{"surface": "db_schema", "state": "stale", "detail": "schema v99 > v37"}],
            "converged": False,
            "reverse_direction": True,
        }
        lines = _converge.format_convergence_report(report, None)
        self.assertIn("SKEW", lines[0])
        self.assertIn("origin v?", lines[0])
        self.assertTrue(any("reverse-direction" in line for line in lines))


class TestRunConvergenceReport(unittest.TestCase):
    def _fully_aligned(self, tmp: Path):
        ws = tmp / "ws"
        (ws / ".claude").mkdir(parents=True)
        (ws / ".claude" / "settings.local.json").write_text('{"hooks": {"PreToolUse": []}}')
        nm = ws / "node_modules" / "@jaguilar87" / "gaia"
        nm.mkdir(parents=True)
        (nm / "package.json").write_text('{"version": "5.2.0"}')
        db = tmp / "gaia.db"
        _make_schema_db(db, 37)
        return ws, db

    def test_returns_report_and_emits_when_not_quiet(self):
        emitted = []
        with tempfile.TemporaryDirectory() as tmp:
            ws, db = self._fully_aligned(Path(tmp))
            with patch("cli._converge.shutil.which", return_value=None):
                report = _converge.run_convergence_report(
                    ws, origin_version="5.2.0", expected_version=37,
                    db_path=db, npm_global_bin=None, quiet=False,
                    emit=emitted.append,
                )
        self.assertTrue(report["converged"])
        self.assertTrue(emitted)  # printed the report lines
        self.assertTrue(any("CONVERGED" in line for line in emitted))

    def test_quiet_suppresses_emit(self):
        emitted = []
        with tempfile.TemporaryDirectory() as tmp:
            ws, db = self._fully_aligned(Path(tmp))
            with patch("cli._converge.shutil.which", return_value=None):
                report = _converge.run_convergence_report(
                    ws, origin_version="5.2.0", expected_version=37,
                    db_path=db, npm_global_bin=None, quiet=True,
                    emit=emitted.append,
                )
        self.assertTrue(report["converged"])
        self.assertEqual(emitted, [])

    def test_inspection_failure_degrades_not_raises(self):
        emitted = []
        with patch("cli._converge.converge_report", side_effect=RuntimeError("boom")):
            report = _converge.run_convergence_report(
                Path("/nonexistent"), origin_version="5.2.0", expected_version=37,
                db_path=Path("/nope.db"), npm_global_bin=None, quiet=False,
                emit=emitted.append,
            )
        self.assertIn("error", report)
        self.assertEqual(report["surfaces"], [])
        self.assertFalse(report["converged"])
        self.assertTrue(any("inspection failed" in line for line in emitted))


if __name__ == "__main__":
    unittest.main()
