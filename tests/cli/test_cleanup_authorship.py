"""gaia cleanup removes CLAUDE.md and .claude/settings.json only when Gaia authored them.

Authorship comes from the install manifest (``cli/_manifest.py``) when the
workspace has one, and from the hook ownership predicate
(``plugin_setup.is_gaia_hook_command``) when it does not.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli import _manifest  # noqa: E402
from cli.cleanup import _remove_claude_md, _remove_settings_json  # noqa: E402

USER_CLAUDE_MD = b"# My project\n\nHand-written notes.\n"
USER_SETTINGS = b'{\n  "model": "opus",\n  "env": {"FOO": "bar"}\n}\n'


def _install(workspace: Path, gaia_writes) -> None:
    """Simulate an install: record the manifest around *gaia_writes*."""
    baseline, _ = _manifest.baseline_for(workspace)
    gaia_writes()
    _manifest.record(workspace, baseline, channel="npm", version="0.0.0-test")


def _cleanup(workspace: Path) -> None:
    _remove_claude_md(workspace, dry_run=False)
    _remove_settings_json(workspace, dry_run=False)


class TestCleanupAuthorship(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)
        self.claude = self.ws / ".claude"
        self.claude.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_user_files_written_before_install_survive_byte_for_byte(self):
        (self.ws / "CLAUDE.md").write_bytes(USER_CLAUDE_MD)
        (self.claude / "settings.json").write_bytes(USER_SETTINGS)
        _install(self.ws, lambda: (self.claude / ".plugin-initialized").write_text("1\n"))
        recorded = {e["path"] for e in _manifest.load(self.ws)["entries"]}
        self.assertNotIn("CLAUDE.md", recorded)
        self.assertNotIn(".claude/settings.json", recorded)

        _cleanup(self.ws)

        self.assertEqual((self.ws / "CLAUDE.md").read_bytes(), USER_CLAUDE_MD)
        self.assertEqual((self.claude / "settings.json").read_bytes(), USER_SETTINGS)

    def test_files_gaia_created_and_recorded_are_removed(self):
        def gaia_writes():
            (self.ws / "CLAUDE.md").write_text("gaia identity\n")
            (self.claude / "settings.json").write_text("{}\n")

        _install(self.ws, gaia_writes)
        recorded = {e["path"] for e in _manifest.load(self.ws)["entries"]}
        self.assertIn("CLAUDE.md", recorded)
        self.assertIn(".claude/settings.json", recorded)

        _cleanup(self.ws)

        self.assertFalse((self.ws / "CLAUDE.md").exists())
        self.assertFalse((self.claude / "settings.json").exists())

    def test_gaia_created_file_the_user_edited_survives(self):
        _install(self.ws, lambda: (self.ws / "CLAUDE.md").write_text("gaia identity\n"))
        (self.ws / "CLAUDE.md").write_bytes(USER_CLAUDE_MD)

        _cleanup(self.ws)

        self.assertEqual((self.ws / "CLAUDE.md").read_bytes(), USER_CLAUDE_MD)

    def test_without_manifest_user_files_survive(self):
        (self.ws / "CLAUDE.md").write_bytes(USER_CLAUDE_MD)
        (self.claude / "settings.json").write_bytes(USER_SETTINGS)
        self.assertIsNone(_manifest.load(self.ws))

        _cleanup(self.ws)

        self.assertEqual((self.ws / "CLAUDE.md").read_bytes(), USER_CLAUDE_MD)
        self.assertEqual((self.claude / "settings.json").read_bytes(), USER_SETTINGS)

    def test_without_manifest_settings_holding_only_gaia_hooks_is_removed(self):
        gaia_only = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/pre_tool_use.py"},
        ]}]}}
        (self.claude / "settings.json").write_text(json.dumps(gaia_only))

        _cleanup(self.ws)

        self.assertFalse((self.claude / "settings.json").exists())

    def test_without_manifest_settings_mixing_user_hook_survives(self):
        mixed = json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/pre_tool_use.py"},
            {"type": "command", "command": "/usr/local/bin/my-audit.sh"},
        ]}]}}).encode()
        (self.claude / "settings.json").write_bytes(mixed)

        _cleanup(self.ws)

        self.assertEqual((self.claude / "settings.json").read_bytes(), mixed)


if __name__ == "__main__":
    unittest.main()
