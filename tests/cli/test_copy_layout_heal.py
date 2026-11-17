"""A same-version .claude copy, left where symlink_to failed, turns into a link once links work; doctor warns meanwhile."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for entry in (REPO_ROOT / "bin", REPO_ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from cli import _install_helpers as helpers  # noqa: E402
from cli import doctor  # noqa: E402

VERSION = "5.8.0-rc.3"


@pytest.fixture
def copied_workspace(tmp_path):
    """A workspace wired from its node_modules package while symlinks failed: stamped copies at VERSION."""
    package = tmp_path / "node_modules" / "@jaguilar87" / "gaia"
    for name in ("agents", "hooks"):
        (package / name).mkdir(parents=True)
        (package / name / "a.py").write_text("# v1\n")
    (package / "package.json").write_text(json.dumps({"name": "@jaguilar87/gaia", "version": VERSION}))
    (tmp_path / ".claude").mkdir()
    with mock.patch.object(helpers.Path, "symlink_to", side_effect=OSError("WinError 1314")):
        helpers.manage_symlinks(tmp_path, plugin_root=package)
    assert not (tmp_path / ".claude" / "hooks").is_symlink()
    return tmp_path, package


def test_update_turns_a_same_version_copy_into_a_link(copied_workspace):
    workspace, package = copied_workspace

    result = helpers.manage_symlinks(workspace, plugin_root=package)

    hooks = workspace / ".claude" / "hooks"
    assert hooks.is_symlink() and hooks.resolve() == (package / "hooks").resolve()
    assert "hooks (copy to link)" in result["fixed"]
    assert helpers._read_stamps(workspace / ".claude") == {}
    assert not (workspace / ".claude" / helpers._LINK_PROBE).exists()
    assert doctor.check_symlinks_freshness(workspace)["severity"] != "warning"


def test_dry_run_lists_the_copy_to_link_and_changes_nothing(copied_workspace):
    workspace, package = copied_workspace

    result = helpers.manage_symlinks(workspace, plugin_root=package, dry_run=True)

    assert "hooks (copy to link)" in result["fixed"]
    assert not (workspace / ".claude" / "hooks").is_symlink()
    assert not (workspace / ".claude" / helpers._LINK_PROBE).is_symlink()


def test_copy_stays_while_links_fail_and_doctor_names_gaia_update(copied_workspace):
    workspace, package = copied_workspace

    with mock.patch.object(helpers.Path, "symlink_to", side_effect=OSError("WinError 1314")):
        result = helpers.manage_symlinks(workspace, plugin_root=package)

    hooks = workspace / ".claude" / "hooks"
    assert not hooks.is_symlink() and (hooks / "a.py").is_file()
    assert result["action"] == "noop" and "hooks" in result["valid"]
    finding = doctor.check_symlinks_freshness(workspace)
    assert finding["severity"] == "warning"
    assert "gaia update" in finding["fix"]
