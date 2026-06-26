"""
Tests for bin/cli/uninstall.py -- gaia uninstall subcommand.

All tests use isolated tmp directories. The real ~/.gaia/gaia.db is never
touched because every test patches the DB path or operates on a tmp DB
file. Tests never invoke `gaia uninstall` against the live machine.
"""

import argparse
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

# Ensure bin/ is on the path so the plugin can be imported
_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli.uninstall import (  # noqa: E402
    _purge_db,
    _resolve_workspace,
    _snapshot_db,
    cmd_uninstall,
    register,
)


def _make_args(**overrides) -> argparse.Namespace:
    """Build a Namespace with all uninstall flags defaulted."""
    ns = argparse.Namespace()
    ns.preuninstall = False
    ns.purge = False
    ns.workspace = None
    ns.dry_run = False
    ns.quiet = False
    ns.json = False
    ns.db_path = None
    ns.snapshot_dir = None
    ns.no_snapshot = False
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


class TestRegisterSubcommand(unittest.TestCase):
    def test_register_creates_parser(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["uninstall"])
        self.assertEqual(args.subcommand, "uninstall")
        self.assertFalse(args.purge)
        self.assertFalse(args.dry_run)
        self.assertFalse(args.preuninstall)

    def test_purge_flag(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["uninstall", "--purge"])
        self.assertTrue(args.purge)

    def test_preuninstall_flag(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["uninstall", "--preuninstall"])
        self.assertTrue(args.preuninstall)

    def test_dry_run_flag(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["uninstall", "--dry-run"])
        self.assertTrue(args.dry_run)

    def test_workspace_flag(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["uninstall", "--workspace", "/tmp/foo"])
        self.assertEqual(args.workspace, "/tmp/foo")


class TestResolveWorkspace(unittest.TestCase):
    def test_explicit_workspace_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self.assertEqual(_resolve_workspace(str(root)), root)

    def test_default_falls_back_to_finder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            with patch("cli.uninstall._find_project_root", return_value=root):
                self.assertEqual(_resolve_workspace(None), root)


class TestPurgeDb(unittest.TestCase):
    def test_missing_db_returns_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "ghost.db"
            r = _purge_db(db, dry_run=False)
            self.assertFalse(r["found"])
            self.assertFalse(r["removed"])

    def test_purge_actually_deletes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "fake.db"
            db.write_bytes(b"SQLite\x00")
            r = _purge_db(db, dry_run=False)
            self.assertTrue(r["found"])
            self.assertTrue(r["removed"])
            self.assertFalse(db.exists())

    def test_dry_run_preserves_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "fake.db"
            db.write_bytes(b"SQLite\x00")
            r = _purge_db(db, dry_run=True)
            self.assertTrue(r["found"])
            self.assertFalse(r["removed"])
            self.assertTrue(db.exists())  # CRITICAL: dry-run never deletes


class TestCmdUninstallDryRun(unittest.TestCase):
    def test_dry_run_does_not_touch_filesystem(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            (root / "CLAUDE.md").write_text("identity\n")
            (root / ".claude" / "settings.json").write_text("{}\n")
            fake_db = root / "fake.db"
            fake_db.write_bytes(b"SQLite\x00")

            args = _make_args(
                workspace=str(root),
                dry_run=True,
                purge=True,  # even with purge, dry-run preserves
                json=True,
                db_path=str(fake_db),
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_uninstall(args)

            self.assertEqual(rc, 0)
            # Files must still exist after dry-run
            self.assertTrue((root / "CLAUDE.md").exists())
            self.assertTrue((root / ".claude" / "settings.json").exists())
            self.assertTrue(fake_db.exists())

            data = json.loads(buf.getvalue())
            self.assertTrue(data["dry_run"])
            self.assertTrue(data["purge_requested"])

    def test_returns_zero_even_with_no_workspace_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            args = _make_args(workspace=str(root), dry_run=True, json=True)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_uninstall(args)
            self.assertEqual(rc, 0)


class TestCmdUninstallPurgeGate(unittest.TestCase):
    """The --purge flag is the ONLY way the user DB gets deleted."""

    def test_default_preserves_db_even_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            fake_db = root / "fake.db"
            fake_db.write_bytes(b"SQLite\x00")

            args = _make_args(
                workspace=str(root),
                json=True,
                db_path=str(fake_db),
                # purge=False (default)
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_uninstall(args)

            self.assertEqual(rc, 0)
            self.assertTrue(fake_db.exists(), "DB must be preserved without --purge")
            data = json.loads(buf.getvalue())
            self.assertTrue(data["db"]["preserved"])

    def test_purge_deletes_db_when_flag_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            fake_db = root / "fake.db"
            fake_db.write_bytes(b"SQLite\x00")

            args = _make_args(
                workspace=str(root),
                purge=True,
                json=True,
                db_path=str(fake_db),
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_uninstall(args)

            self.assertEqual(rc, 0)
            self.assertFalse(fake_db.exists(), "DB should be deleted with --purge")
            data = json.loads(buf.getvalue())
            self.assertTrue(data["db"]["removed"])


class TestCmdUninstallPreuninstall(unittest.TestCase):
    def test_preuninstall_mode_marker_in_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            args = _make_args(
                workspace=str(root),
                preuninstall=True,
                dry_run=True,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_uninstall(args)
            self.assertEqual(rc, 0)
            data = json.loads(buf.getvalue())
            self.assertEqual(data["mode"], "preuninstall")


class TestCmdUninstallQuiet(unittest.TestCase):
    def test_quiet_suppresses_human_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            args = _make_args(workspace=str(root), quiet=True)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_uninstall(args)
            self.assertEqual(rc, 0)
            self.assertEqual(buf.getvalue(), "")


class TestSnapshotDb(unittest.TestCase):
    """The snapshot helper guards the user against accidental DB loss on --purge."""

    def test_snapshot_creates_gzip_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "fake.db"
            db.write_bytes(b"SQLite\x00fake-content-here")
            snapshot_dir = Path(tmp) / "snapshots"
            res = _snapshot_db(db, snapshot_dir, dry_run=False)
            self.assertTrue(res["created"])
            self.assertIsNotNone(res["path"])
            snapshot_path = Path(res["path"])
            self.assertTrue(snapshot_path.exists())
            self.assertEqual(snapshot_path.suffix, ".gz")
            # Verify gzip content matches original
            import gzip
            with gzip.open(snapshot_path, "rb") as f:
                self.assertEqual(f.read(), b"SQLite\x00fake-content-here")

    def test_snapshot_dry_run_does_not_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "fake.db"
            db.write_bytes(b"x")
            snapshot_dir = Path(tmp) / "snapshots"
            res = _snapshot_db(db, snapshot_dir, dry_run=True)
            self.assertFalse(res["created"])
            self.assertFalse(snapshot_dir.exists())

    def test_snapshot_when_db_missing_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "ghost.db"
            snapshot_dir = Path(tmp) / "snapshots"
            res = _snapshot_db(db, snapshot_dir, dry_run=False)
            self.assertFalse(res["created"])
            self.assertNotIn("error", res)


class TestPurgeWithSnapshot(unittest.TestCase):
    """--purge MUST snapshot before deletion (default behavior)."""

    def test_purge_creates_snapshot_then_deletes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            db = root / "fake.db"
            db.write_bytes(b"SQLite\x00")
            snapshot_dir = root / "snapshots"

            args = _make_args(
                workspace=str(root),
                purge=True,
                json=True,
                db_path=str(db),
                snapshot_dir=str(snapshot_dir),
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_uninstall(args)

            self.assertEqual(rc, 0)
            data = json.loads(buf.getvalue())
            self.assertTrue(data["snapshot"]["created"])
            self.assertTrue(data["db"]["removed"])
            self.assertFalse(db.exists())
            # Snapshot file exists
            snapshot_path = Path(data["snapshot"]["path"])
            self.assertTrue(snapshot_path.exists())

    def test_purge_with_no_snapshot_flag_skips_snapshot(self):
        """--no-snapshot bypasses safety net (advanced; documented as DANGEROUS)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            db = root / "fake.db"
            db.write_bytes(b"SQLite\x00")
            snapshot_dir = root / "snapshots"

            args = _make_args(
                workspace=str(root),
                purge=True,
                no_snapshot=True,
                json=True,
                db_path=str(db),
                snapshot_dir=str(snapshot_dir),
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_uninstall(args)

            self.assertEqual(rc, 0)
            data = json.loads(buf.getvalue())
            self.assertFalse(data["snapshot"].get("created", False))
            self.assertFalse(data["snapshot"].get("requested", True))
            self.assertTrue(data["db"]["removed"])
            self.assertFalse(snapshot_dir.exists())  # no snapshot dir created

    def test_purge_aborts_when_snapshot_fails(self):
        """If snapshot creation fails, purge MUST be aborted (DB preserved)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            db = root / "fake.db"
            db.write_bytes(b"SQLite\x00")

            args = _make_args(
                workspace=str(root),
                purge=True,
                json=True,
                db_path=str(db),
                # snapshot_dir will be patched to fail
            )

            from unittest.mock import patch as _patch
            with _patch(
                "cli.uninstall._snapshot_db",
                return_value={
                    "requested": True,
                    "source": str(db),
                    "path": "/fake/snapshot.db.gz",
                    "created": False,
                    "dry_run": False,
                    "error": "permission denied",
                },
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cmd_uninstall(args)

            self.assertEqual(rc, 0)
            data = json.loads(buf.getvalue())
            self.assertTrue(db.exists(), "DB must be preserved when snapshot fails")
            self.assertTrue(data["db"]["preserved"])
            self.assertEqual(data["db"].get("error"), "permission denied")

    def test_purge_dry_run_does_not_create_snapshot_or_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            db = root / "fake.db"
            db.write_bytes(b"SQLite\x00")
            snapshot_dir = root / "snapshots"

            args = _make_args(
                workspace=str(root),
                purge=True,
                dry_run=True,
                json=True,
                db_path=str(db),
                snapshot_dir=str(snapshot_dir),
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_uninstall(args)

            self.assertEqual(rc, 0)
            self.assertTrue(db.exists())
            self.assertFalse(snapshot_dir.exists())


class TestCmdUninstallFootprint(unittest.TestCase):
    """End-to-end: the 4 footprint artifacts are cleaned and the DB is preserved."""

    def _make_full_workspace(self, root: Path) -> Path:
        """Build a workspace mirroring what `gaia install` writes."""
        claude_dir = root / ".claude"
        claude_dir.mkdir()

        # skills symlink (install creates it via manage_symlinks)
        skills_target = root / "real_skills"
        skills_target.mkdir()
        (claude_dir / "skills").symlink_to(skills_target)

        # .plugin-initialized marker
        (claude_dir / ".plugin-initialized").write_text(
            json.dumps({"initialized_at": "2026-01-01", "mode": "ops"})
        )

        # plugin-registry.json with a co-installed third-party plugin
        (claude_dir / "plugin-registry.json").write_text(
            json.dumps({
                "installed": [
                    {"name": "gaia-ops", "version": "4.4.0"},
                    {"name": "third-party", "version": "2.0.0"},
                ],
                "source": "cli-install",
            }, indent=2) + "\n"
        )

        # settings.local.json with both Gaia + user content
        (claude_dir / "settings.local.json").write_text(
            json.dumps({
                "agent": "gaia-orchestrator",
                "env": {
                    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
                    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                    "USER_VAR": "preserve",
                },
                "permissions": {
                    "allow": ["Bash(*)", "Read", "UserTool(x)"],
                    "deny": [],
                    "ask": [],
                },
            }, indent=2) + "\n"
        )
        return claude_dir

    def test_footprint_cleaned_db_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_dir = self._make_full_workspace(root)
            db = root / "fake.db"
            db.write_bytes(b"SQLite\x00")

            args = _make_args(workspace=str(root), json=True, db_path=str(db))
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_uninstall(args)

            self.assertEqual(rc, 0)
            data = json.loads(buf.getvalue())

            # skills symlink removed
            self.assertIn(".claude/skills", data["symlinks"]["removed"])
            self.assertFalse((claude_dir / "skills").exists())

            # .plugin-initialized removed
            self.assertTrue(data["plugin_initialized"]["removed"])
            self.assertFalse((claude_dir / ".plugin-initialized").exists())

            # plugin-registry.json: Gaia entry gone, third-party preserved
            self.assertEqual(data["plugin_registry"]["removed_entries"], ["gaia-ops"])
            self.assertTrue((claude_dir / "plugin-registry.json").exists())
            reg = json.loads((claude_dir / "plugin-registry.json").read_text())
            names = [e["name"] for e in reg["installed"]]
            self.assertEqual(names, ["third-party"])

            # settings.local.json: Gaia keys gone, user keys preserved
            self.assertTrue(data["settings_local_json"]["found"])
            local = json.loads((claude_dir / "settings.local.json").read_text())
            self.assertNotIn("agent", local)
            self.assertEqual(local["env"]["USER_VAR"], "preserve")
            self.assertIn("UserTool(x)", local["permissions"]["allow"])
            self.assertNotIn("Bash(*)", local["permissions"]["allow"])

            # DB preserved (no --purge)
            self.assertTrue(db.exists())
            self.assertTrue(data["db"]["preserved"])

    def test_footprint_idempotent(self):
        """Running uninstall twice is safe and a no-op the second time."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_full_workspace(root)
            db = root / "fake.db"
            db.write_bytes(b"SQLite\x00")

            for _ in range(2):
                args = _make_args(workspace=str(root), json=True, db_path=str(db))
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cmd_uninstall(args)
                self.assertEqual(rc, 0)

            data = json.loads(buf.getvalue())
            # Second pass: nothing Gaia-owned left to clean.
            self.assertFalse(data["plugin_initialized"].get("found"))
            self.assertFalse(data["plugin_registry"].get("found"))
            self.assertFalse(data["settings_local_json"].get("found"))
            self.assertTrue(db.exists())

    def test_footprint_dry_run_touches_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_dir = self._make_full_workspace(root)
            db = root / "fake.db"
            db.write_bytes(b"SQLite\x00")

            args = _make_args(
                workspace=str(root), json=True, dry_run=True, db_path=str(db),
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_uninstall(args)

            self.assertEqual(rc, 0)
            self.assertTrue((claude_dir / "skills").exists())
            self.assertTrue((claude_dir / ".plugin-initialized").exists())
            self.assertTrue((claude_dir / "plugin-registry.json").exists())
            reg = json.loads((claude_dir / "plugin-registry.json").read_text())
            self.assertEqual(len(reg["installed"]), 2)
            local = json.loads((claude_dir / "settings.local.json").read_text())
            self.assertIn("agent", local)
            self.assertTrue(db.exists())


if __name__ == "__main__":
    unittest.main()
