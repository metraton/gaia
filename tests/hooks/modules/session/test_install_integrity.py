"""At session start, drift of Gaia's recorded install links and settings is a notice, never a block."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "hooks"))

from modules.session.install_integrity import integrity_notice
from modules.session.plugin_upgrade import _cli_module

manifest = _cli_module("_manifest")


def _installed(tmp_path):
    workspace = tmp_path / "ws"
    claude = workspace / ".claude"
    claude.mkdir(parents=True)
    hooks = tmp_path / "install" / "hooks"
    hooks.mkdir(parents=True)
    baseline = manifest.capture(workspace)
    (claude / "hooks").symlink_to(hooks)
    (claude / "settings.local.json").write_text(json.dumps({"env": {"GAIA_PLUGIN": "1"}}))
    manifest.record(workspace, baseline, channel="plugin", version="test")
    return workspace


def test_untouched_install_and_unrelated_user_settings_say_nothing(tmp_path):
    workspace = _installed(tmp_path)
    assert integrity_notice(workspace) == ""
    settings = workspace / ".claude" / "settings.local.json"
    settings.write_text(json.dumps({"env": {"GAIA_PLUGIN": "1"}, "permissions": {"allow": ["Bash(ls)"]}}))
    assert integrity_notice(workspace) == ""


def test_changed_gaia_setting_and_repointed_link_are_named(tmp_path):
    workspace = _installed(tmp_path)
    (workspace / ".claude" / "settings.local.json").write_text(json.dumps({"env": {"GAIA_PLUGIN": "0"}}))
    link = workspace / ".claude" / "hooks"
    link.unlink()
    link.symlink_to(tmp_path)
    notice = integrity_notice(workspace)
    assert "settings.local.json" in notice and ".claude/hooks" in notice


def test_missing_or_broken_manifest_never_raises(tmp_path):
    assert integrity_notice(tmp_path) == ""
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "gaia-manifest.json").write_text('{"entries": [{"path": 1}]}')
    assert integrity_notice(tmp_path) == ""
