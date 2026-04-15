"""
Tests for bin/cli/update.py -- gaia update subcommand.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli.update import (
    _find_project_root,
    _read_package_version,
    _detect_versions,
    _check_settings_json,
    _check_symlinks,
    _run_verification,
    register,
    cmd_update,
)


class TestFindProjectRoot(unittest.TestCase):
    def test_finds_claude_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            with patch("os.getcwd", return_value=str(root)):
                result = _find_project_root()
            self.assertEqual(result, root)


class TestReadPackageVersion(unittest.TestCase):
    def test_reads_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "package.json"
            pkg.write_text(json.dumps({"version": "5.2.0"}))
            self.assertEqual(_read_package_version(pkg), "5.2.0")

    def test_missing_returns_unknown(self):
        result = _read_package_version(Path("/nonexistent/package.json"))
        self.assertEqual(result, "unknown")


class TestDetectVersions(unittest.TestCase):
    def test_detects_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            pkg_root = Path(tmp) / "pkg"
            pkg_root.mkdir()
            (pkg_root / "package.json").write_text(json.dumps({"version": "5.3.0"}))
            result = _detect_versions(cwd, pkg_root)
            self.assertEqual(result["current"], "5.3.0")
            self.assertIsNone(result["previous"])


class TestCheckSettingsJson(unittest.TestCase):
    def test_skipped_when_no_claude_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = Path(tmp) / ".claude"
            result = _check_settings_json(claude_dir, dry_run=False)
            self.assertEqual(result["status"], "skipped")

    def test_ok_when_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = Path(tmp) / ".claude"
            claude_dir.mkdir()
            (claude_dir / "settings.json").write_text("{}\n")
            result = _check_settings_json(claude_dir, dry_run=False)
            self.assertEqual(result["status"], "ok")

    def test_creates_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = Path(tmp) / ".claude"
            claude_dir.mkdir()
            result = _check_settings_json(claude_dir, dry_run=False)
            self.assertEqual(result["status"], "created")
            self.assertTrue((claude_dir / "settings.json").exists())

    def test_dry_run_does_not_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = Path(tmp) / ".claude"
            claude_dir.mkdir()
            result = _check_settings_json(claude_dir, dry_run=True)
            self.assertEqual(result["status"], "created")
            self.assertFalse((claude_dir / "settings.json").exists())


class TestCheckSymlinks(unittest.TestCase):
    def test_skipped_when_no_claude_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = Path(tmp) / ".claude"
            pkg_root = Path(tmp) / "pkg"
            pkg_root.mkdir()
            result = _check_symlinks(claude_dir, pkg_root, dry_run=False)
            self.assertEqual(result["status"], "skipped")

    def test_marks_missing_as_fixed_in_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = Path(tmp) / ".claude"
            claude_dir.mkdir()
            pkg_root = Path(tmp) / "pkg"
            pkg_root.mkdir()
            # Create some target dirs that would be symlinked
            for name in ["agents", "tools", "hooks"]:
                (pkg_root / name).mkdir()
            result = _check_symlinks(claude_dir, pkg_root, dry_run=True)
            # Missing symlinks should be reported as "would fix"
            self.assertIn("agents", result["fixed"])

    def test_creates_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = Path(tmp) / ".claude"
            claude_dir.mkdir()
            pkg_root = Path(tmp) / "pkg"
            pkg_root.mkdir()
            (pkg_root / "agents").mkdir()
            result = _check_symlinks(claude_dir, pkg_root, dry_run=False)
            self.assertIn("agents", result["fixed"])
            link = claude_dir / "agents"
            self.assertTrue(link.is_symlink())


class TestRunVerification(unittest.TestCase):
    def test_returns_checks_and_issues(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = Path(tmp) / ".claude"
            claude_dir.mkdir()
            result = _run_verification(claude_dir)
            self.assertIn("checks", result)
            self.assertIn("issues", result)
            self.assertIn("passed", result)
            self.assertIn("total", result)

    def test_missing_hooks_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = Path(tmp) / ".claude"
            claude_dir.mkdir()
            result = _run_verification(claude_dir)
            hook_issues = [i for i in result["issues"] if "Hook missing" in i]
            self.assertTrue(len(hook_issues) > 0)

    def test_valid_project_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = Path(tmp) / ".claude"
            claude_dir.mkdir()
            ctx_dir = claude_dir / "project-context"
            ctx_dir.mkdir()
            ctx = {"sections": {f"s{i}": {} for i in range(4)}}
            (ctx_dir / "project-context.json").write_text(json.dumps(ctx))
            result = _run_verification(claude_dir)
            ctx_check = next((c for c in result["checks"] if c["name"] == "project-context.json"), None)
            self.assertIsNotNone(ctx_check)
            self.assertTrue(ctx_check["ok"])


class TestRegisterSubcommand(unittest.TestCase):
    def test_register_creates_parser(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        # Should parse without error
        args = parser.parse_args(["update", "--dry-run"])
        self.assertTrue(args.dry_run)

    def test_verbose_flag(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["update", "--verbose"])
        self.assertTrue(args.verbose)

    def test_json_flag(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["update", "--json"])
        self.assertTrue(args.json)


class TestCmdUpdate(unittest.TestCase):
    def _make_args(self, dry_run=False, verbose=False, as_json=False):
        import argparse
        ns = argparse.Namespace()
        ns.dry_run = dry_run
        ns.verbose = verbose
        ns.json = as_json
        return ns

    def test_dry_run_json_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_dir = root / ".claude"
            claude_dir.mkdir()
            pkg_root = Path(tmp) / "pkg"
            pkg_root.mkdir()
            (pkg_root / "package.json").write_text(json.dumps({"version": "5.0.0"}))

            args = self._make_args(dry_run=True, as_json=True)
            with patch("cli.update._find_project_root", return_value=root):
                with patch("cli.update._find_package_root", return_value=pkg_root):
                    import io
                    from contextlib import redirect_stdout
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        rc = cmd_update(args)
                    output = buf.getvalue()

            self.assertEqual(rc, 0)
            data = json.loads(output)
            self.assertTrue(data["dry_run"])
            self.assertIn("settings_json", data)
            self.assertIn("symlinks", data)
            self.assertIn("verification", data)

    def test_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            pkg_root = Path(tmp) / "pkg"
            pkg_root.mkdir()
            (pkg_root / "package.json").write_text(json.dumps({"version": "5.0.0"}))

            args = self._make_args()
            with patch("cli.update._find_project_root", return_value=root):
                with patch("cli.update._find_package_root", return_value=pkg_root):
                    import io
                    from contextlib import redirect_stdout
                    with redirect_stdout(io.StringIO()):
                        rc = cmd_update(args)
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
