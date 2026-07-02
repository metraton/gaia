"""
Tests for bin/cli/_pack_helpers.py -- the shared `npm pack` primitive used
by `gaia dev` (Phase 1) and reused by `gaia release check` (Phase 2).

Hygiene: every test writes tarballs into a `tempfile.TemporaryDirectory()`
via `dest_dir=` -- never into the real source tree and never relying on
`~/.gaia`. `pack_tarball()` itself never touches ~/.gaia (it only shells
out to `npm pack`), so no GAIA_DATA_DIR isolation is required here.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli import _pack_helpers  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _npm_available() -> bool:
    return shutil.which("npm") is not None


class TestPackTarballMocked(unittest.TestCase):
    """Unit-level tests -- subprocess.run is mocked, no real npm invoked."""

    def test_success_returns_created_action_with_tarball_fields(self):
        fake_stdout = (
            "> @jaguilar87/gaia@5.0.11 prepack\n"
            "> npm run clean && npm run generate:plugin-root\n"
            "\n"
            '[{"name":"@jaguilar87/gaia","version":"5.0.11",'
            '"filename":"jaguilar87-gaia-5.0.11.tgz"}]\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            tarball_path = dest / "jaguilar87-gaia-5.0.11.tgz"
            tarball_path.write_bytes(b"fake tarball content")

            with patch(
                "cli._pack_helpers.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=["npm", "pack"], returncode=0, stdout=fake_stdout, stderr="",
                ),
            ):
                res = _pack_helpers.pack_tarball(_REPO_ROOT, dest_dir=dest)

            self.assertEqual(res["action"], "created")
            self.assertEqual(res["name"], "@jaguilar87/gaia")
            self.assertEqual(res["version"], "5.0.11")
            self.assertEqual(res["tarball"], tarball_path)

    def test_nonzero_exit_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "cli._pack_helpers.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=["npm", "pack"], returncode=1, stdout="", stderr="npm ERR! boom",
                ),
            ):
                res = _pack_helpers.pack_tarball(_REPO_ROOT, dest_dir=Path(tmp))
            self.assertEqual(res["action"], "error")
            self.assertIn("npm ERR! boom", res["details"])

    def test_unparsable_json_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "cli._pack_helpers.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=["npm", "pack"], returncode=0, stdout="not json at all", stderr="",
                ),
            ):
                res = _pack_helpers.pack_tarball(_REPO_ROOT, dest_dir=Path(tmp))
            self.assertEqual(res["action"], "error")

    def test_missing_tarball_file_returns_error(self):
        """npm pack reports success but the file is not actually on disk."""
        fake_stdout = '[{"name":"@jaguilar87/gaia","version":"5.0.11","filename":"missing.tgz"}]'
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "cli._pack_helpers.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=["npm", "pack"], returncode=0, stdout=fake_stdout, stderr="",
                ),
            ):
                res = _pack_helpers.pack_tarball(_REPO_ROOT, dest_dir=Path(tmp))
            self.assertEqual(res["action"], "error")
            self.assertIn("not found", res["details"])

    def test_oserror_invoking_npm_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "cli._pack_helpers.subprocess.run",
                side_effect=OSError("npm not found"),
            ):
                res = _pack_helpers.pack_tarball(_REPO_ROOT, dest_dir=Path(tmp))
            self.assertEqual(res["action"], "error")
            self.assertIn("npm not found", res["details"])

    def test_dest_dir_created_when_missing(self):
        fake_stdout = '[{"name":"@jaguilar87/gaia","version":"5.0.11","filename":"jaguilar87-gaia-5.0.11.tgz"}]'
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "nested" / "dest"
            self.assertFalse(dest.exists())

            def fake_run(cmd, **kwargs):
                # Simulate npm writing the tarball into --pack-destination.
                (dest / "jaguilar87-gaia-5.0.11.tgz").write_bytes(b"x")
                return subprocess.CompletedProcess(cmd, 0, fake_stdout, "")

            with patch("cli._pack_helpers.subprocess.run", side_effect=fake_run):
                res = _pack_helpers.pack_tarball(_REPO_ROOT, dest_dir=dest)

            self.assertEqual(res["action"], "created")
            self.assertTrue(dest.is_dir())


@unittest.skipUnless(_npm_available(), "npm not available in this environment")
class TestPackTarballReal(unittest.TestCase):
    """Integration: run the REAL `npm pack` against the actual source tree.

    Writes the tarball only into a `tempfile.TemporaryDirectory()` --
    never into the repo root, never into ~/.gaia. This is the same pack
    primitive `gaia dev --mode pack` and (Phase 2) `gaia release check`
    will invoke, so proving it works for real here is the load-bearing
    check for AC-1's "reflects a real shippable version" requirement.
    """

    def test_real_npm_pack_produces_tarball(self):
        with tempfile.TemporaryDirectory(prefix="gaia-pack-real-") as tmp:
            dest = Path(tmp)
            res = _pack_helpers.pack_tarball(_REPO_ROOT, dest_dir=dest, timeout=120)

            self.assertEqual(res["action"], "created", res.get("details"))
            self.assertEqual(res["name"], "@jaguilar87/gaia")
            tarball = res["tarball"]
            self.assertTrue(tarball.is_file())
            self.assertTrue(tarball.name.startswith("jaguilar87-gaia-"))
            self.assertGreater(tarball.stat().st_size, 1000)
            # The tarball must NOT have been written into the source tree.
            self.assertNotEqual(tarball.parent, _REPO_ROOT)


if __name__ == "__main__":
    unittest.main()
