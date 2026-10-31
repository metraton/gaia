#!/usr/bin/env python3
"""Tests for session_manifest -- the SessionStart birth block.

The builders are fail-safe and the assembler joins the non-empty parts. The
whole-block behaviour (four sections, budget, user rows whole, data home) is
pinned by tests/hooks/test_session_birth_block.py; this file keeps what a
user feels in the parts: the version and CLI path the Environment names and
that a failure degrades instead of stopping the session. The workspaces
section is pinned by test_workspaces_i_manage.py.
"""

import json
import re
import sys
from pathlib import Path

import pytest


HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.session import session_manifest
from modules.session.session_manifest import (
    build_environment_section,
    build_session_context,
)


# ---------------------------------------------------------------------------
# build_environment_section
# ---------------------------------------------------------------------------

class TestBuildEnvironmentSection:
    @pytest.fixture(autouse=True)
    def _quiet(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        monkeypatch.setattr(session_manifest, "_read_workspace_identity", lambda: None)
        monkeypatch.setattr(session_manifest.platform, "node", lambda: "host")
        monkeypatch.setattr(session_manifest.platform, "system", lambda: "Linux")
        monkeypatch.setattr(session_manifest.platform, "machine", lambda: "x86_64")
        monkeypatch.setattr(session_manifest.platform, "release", lambda: "6.6.1-generic")
        monkeypatch.setattr(session_manifest, "_scan_live_gaia_installation", lambda: None)
        monkeypatch.setattr(session_manifest, "_read_gaia_version", lambda: "5.3.0")
        monkeypatch.setattr(session_manifest, "_resolve_gaia_cli_path", lambda: None)
        monkeypatch.setattr(session_manifest, "_scan_available_tools", lambda **_: [])

    def test_the_machine_and_the_folder_are_values_not_prose(self):
        """Replaces "a folder outside every workspace says so": the section is values only, no sentence about the workspace."""
        result = build_environment_section()

        assert result.startswith("## Environment")
        assert "machine=host os=Linux arch=x86_64 wsl=no host=claude-code" in result.splitlines()
        assert f"cwd={Path.cwd()}" in result.splitlines()
        assert "not inside a declared workspace" not in result

    def test_the_workspace_identity_keys_the_due_section_and_is_not_an_environment_value(self, monkeypatch):
        """Replaces "names the workspace": the identity now selects whose reminders are due."""
        seen = []
        monkeypatch.setattr(session_manifest, "_read_workspace_identity", lambda: "my-workspace")
        monkeypatch.setattr(session_manifest, "build_due_section", lambda workspace: seen.append(workspace) or "")
        monkeypatch.setattr(session_manifest, "orchestrator_user_rows", lambda: [])

        assert "my-workspace" not in build_environment_section()
        session_manifest.build_session_context()

        assert seen == ["my-workspace"]

    def test_version_comes_from_the_live_scan_not_the_package_json(self, monkeypatch):
        monkeypatch.setattr(
            session_manifest,
            "_scan_live_gaia_installation",
            lambda: {"version": "5.5.0-rc.1", "install_mode": "npm"},
        )
        monkeypatch.setattr(session_manifest, "_read_gaia_version", lambda: "5.0.0-rc.3")

        result = build_environment_section()

        assert "gaia=5.5.0-rc.1 channel=npm" in result.splitlines()
        assert "5.0.0-rc.3" not in result

    def test_version_falls_back_to_the_package_json_when_the_scan_finds_nothing(self):
        assert "gaia=5.3.0" in build_environment_section().splitlines()

    def test_version_carries_the_local_dev_build_count(self):
        """A `gaia dev` build ships the base semver; the count tells it from the release."""
        from gaia.dev_builds import record_build

        record_build("5.3.0", "fb27693c")

        assert "gaia=5.3.0 (dev.1, build fb27693c)" in build_environment_section()

    def test_version_degrades_to_bare_when_the_counter_raises(self, monkeypatch):
        """SessionStart must not be breakable by the dev-build counter."""
        import gaia.dev_builds as dev_builds

        def _boom(_version):
            raise RuntimeError("simulated counter failure")

        monkeypatch.setattr(dev_builds, "describe_version", _boom)

        result = build_environment_section()

        assert "gaia=5.3.0" in result.splitlines()
        assert "dev." not in result

    def test_the_channel_is_plugin_when_the_plugin_root_is_declared(self, monkeypatch, tmp_path):
        """Replaces "gaia root is the declared plugin root": the root is no longer a value, its channel and the cli path are."""
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "plugin"))
        monkeypatch.setattr(session_manifest, "_resolve_gaia_cli_path", lambda: str(tmp_path / "plugin" / "bin" / "gaia"))

        lines = build_environment_section().splitlines()

        assert "gaia=5.3.0 channel=plugin" in lines
        assert f"cli={tmp_path / 'plugin' / 'bin' / 'gaia'}" in lines

    def test_no_cli_line_when_no_binary_resolves(self):
        """Replaces "falls back to the own package": the fallback lives in _resolve_gaia_cli_path, pinned below; here its absence leaves no line."""
        assert not [line for line in build_environment_section().splitlines() if line.startswith("cli=")]

    def test_tools_on_path_are_one_values_line_so_a_missing_tool_can_be_refused(self, monkeypatch):
        monkeypatch.setattr(session_manifest, "_scan_available_tools", lambda **_: ["git", "acli"])

        assert "tools=git,acli" in build_environment_section().splitlines()

    def test_no_tools_line_when_none_resolves(self):
        assert not [line for line in build_environment_section().splitlines() if line.startswith("tools=")]

    def test_pending_work_is_the_due_section_and_never_an_environment_line(self, monkeypatch):
        """Replaces "recurring work is one line": due reminders and routines are their own ``## Due`` section."""
        monkeypatch.setattr(session_manifest, "orchestrator_user_rows", lambda: [])
        assert "Recurring work" not in build_environment_section()
        monkeypatch.setattr(session_manifest, "build_due_section", lambda _ws: "")
        assert "## Due" not in session_manifest.build_session_context()

        monkeypatch.setattr(session_manifest, "build_due_section", lambda _ws: "## Due\noverdue=1\n'x'  id=1")
        context = session_manifest.build_session_context()

        assert "## Due\noverdue=1" in context
        assert "## Due" not in build_environment_section()

    def test_names_the_local_zone_but_never_a_time(self, monkeypatch):
        monkeypatch.setenv("TZ", "America/Santiago")

        result = build_environment_section()

        zone_lines = [line for line in result.splitlines() if "America/Santiago" in line]
        assert zone_lines == ["zone=America/Santiago"], result
        assert not re.search(r"\d{1,2}:\d{2}", zone_lines[0]), zone_lines[0]
        assert len(session_manifest.build_session_context()) <= session_manifest.BIRTH_BUDGET

    def test_a_failing_due_read_does_not_drop_the_environment(self, monkeypatch):
        """Replaces "a failing recurring line does not drop the section"."""
        import gaia.store.reader as reader

        def _boom(**_kwargs):
            raise RuntimeError("simulated scheduler failure")

        monkeypatch.setattr(reader, "list_unread_notifications", _boom)

        assert session_manifest.build_due_section("ws") == ""
        assert build_environment_section().startswith("## Environment")


# ---------------------------------------------------------------------------
# _scan_live_gaia_installation / _resolve_gaia_cli_path / _scan_available_tools
# ---------------------------------------------------------------------------

def _declare_workspaces(tmp_path, monkeypatch, roots: dict) -> None:
    """A data home whose schema'd database records *roots*; a None root is a history-only row."""
    import sqlite3

    from gaia.store.writer import _connect

    data_home = tmp_path / "gaia-data"
    data_home.mkdir(exist_ok=True)
    database = data_home / "gaia.db"
    _connect(database).close()
    con = sqlite3.connect(database)
    con.executemany(
        "INSERT INTO workspaces (name, root_path) VALUES (?, ?)",
        [(name, str(root) if root else None) for name, root in roots.items()],
    )
    con.commit()
    con.close()
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_home))
    monkeypatch.setenv("GAIA_DB", str(database))
    for key in ("GAIA_DISPATCH_WORKSPACE", "GAIA_WORKSPACE"):
        monkeypatch.delenv(key, raising=False)


class TestReadWorkspaceIdentity:
    """The Folder line names a workspace only when a declared root holds the cwd."""

    def test_outside_every_declared_root_there_is_no_workspace(self, monkeypatch, tmp_path):
        declared, outside = tmp_path / "declared", tmp_path / "outside"
        declared.mkdir()
        outside.mkdir()
        _declare_workspaces(tmp_path, monkeypatch, {"global": None, "mine": declared})
        monkeypatch.chdir(outside)

        assert session_manifest._read_workspace_identity() is None

    def test_inside_a_declared_root_the_workspace_is_named(self, monkeypatch, tmp_path):
        nested = tmp_path / "declared" / "repo"
        nested.mkdir(parents=True)
        _declare_workspaces(tmp_path, monkeypatch, {"global": None, "mine": tmp_path / "declared"})
        monkeypatch.chdir(nested)

        assert session_manifest._read_workspace_identity() == "mine"


class TestScanLiveGaiaInstallation:
    def test_delegates_to_the_pure_scanner_for_the_declared_install(self, monkeypatch, tmp_path):
        """The live scan calls the real, table-free detector against the
        declared install root -- never the gaia_installations table."""
        captured = {}

        def _fake_scan(workspace_root):
            captured["root"] = workspace_root
            return [{"machine": "host", "version": "9.9.9", "install_mode": "npm"}]

        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        monkeypatch.setattr(session_manifest, "_declared_install_root", lambda: tmp_path / "ws")
        import tools.scan.store_populator as store_populator

        monkeypatch.setattr(store_populator, "_scan_gaia_installations", _fake_scan)

        result = session_manifest._scan_live_gaia_installation()
        assert result == {"machine": "host", "version": "9.9.9", "install_mode": "npm"}
        assert captured["root"] == tmp_path / "ws"

    def test_returns_none_on_any_failure(self, monkeypatch):
        def _boom():
            raise RuntimeError("registry unreadable")

        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        monkeypatch.setattr(session_manifest, "_declared_install_root", _boom)
        assert session_manifest._scan_live_gaia_installation() is None


class TestResolveGaiaCliPath:
    @pytest.fixture(autouse=True)
    def _npm_channel(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        monkeypatch.setattr(session_manifest, "_declared_install_root", lambda: tmp_path / "ws")

    def _make_npm_layout(self, tmp_path, bin_rel="bin/gaia"):
        gaia_dir = tmp_path / "ws" / "node_modules" / "@jaguilar87" / "gaia"
        gaia_dir.mkdir(parents=True)
        (gaia_dir / "package.json").write_text(
            json.dumps({"name": "@jaguilar87/gaia", "bin": {"gaia": bin_rel}})
        )
        bin_path = gaia_dir / bin_rel
        bin_path.parent.mkdir(parents=True, exist_ok=True)
        bin_path.write_text("#!/usr/bin/env node\n")
        return gaia_dir, bin_path

    def test_returns_none_when_no_candidate_package_exists(self, monkeypatch, tmp_path):
        monkeypatch.setattr(session_manifest, "_own_package_root", lambda: tmp_path / "empty")
        assert session_manifest._resolve_gaia_cli_path() is None

    def test_returns_none_when_guard_rejects_the_candidate(self, monkeypatch, tmp_path):
        """A candidate that resolves but fails the real trust guard is
        never published -- the guard is consulted, not merely trusted."""
        self._make_npm_layout(tmp_path)
        import modules.security.gaia_cli_only_guard as guard

        monkeypatch.setattr(guard, "is_trusted_gaia_binary", lambda _token: False)

        assert session_manifest._resolve_gaia_cli_path() is None

    def test_returns_the_candidate_when_the_guard_accepts_it(self, monkeypatch, tmp_path):
        _gaia_dir, bin_path = self._make_npm_layout(tmp_path)
        import modules.security.gaia_cli_only_guard as guard

        monkeypatch.setattr(guard, "is_trusted_gaia_binary", lambda _token: True)

        assert session_manifest._resolve_gaia_cli_path() == str(bin_path)


def _make_gaia_package(root: Path, version: str = "5.5.0") -> Path:
    """A package the real trust guard accepts: same name as ours, bin.gaia declared."""
    root.mkdir(parents=True)
    (root / "package.json").write_text(
        json.dumps({"name": "@jaguilar87/gaia", "version": version, "bin": {"gaia": "bin/gaia"}})
    )
    bin_path = root / "bin" / "gaia"
    bin_path.parent.mkdir()
    bin_path.write_text("#!/usr/bin/env python3\n")
    bin_path.chmod(0o755)
    return bin_path


_ALIAS = Path("node_modules") / "@jaguilar87" / "gaia"


class TestResolveGaiaCliPathByInstallLayout:
    """Each supported layout publishes a path the real guard accepts; PATH is never read.

    The session opens in ``ws/repo``, a repository inside the declared
    workspace ``ws`` that carries its own ``.claude`` and a stale nested copy.
    """

    @pytest.fixture
    def layout(self, monkeypatch, tmp_path):
        import modules.core.paths as core_paths_mod

        workspace = tmp_path / "ws"
        repo = workspace / "repo"
        (repo / ".claude").mkdir(parents=True)
        _declare_workspaces(tmp_path, monkeypatch, {"ws": workspace})
        monkeypatch.chdir(repo)
        monkeypatch.setattr(core_paths_mod, "find_claude_dir", lambda: repo / ".claude")
        monkeypatch.setattr(session_manifest, "_own_package_root", lambda: tmp_path / "no-package")
        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        empty_path_dir = tmp_path / "empty-path"
        empty_path_dir.mkdir()
        monkeypatch.setenv("PATH", str(empty_path_dir))
        return workspace, tmp_path

    def _nested_stale_copy(self, workspace: Path) -> Path:
        return _make_gaia_package(workspace / "repo" / _ALIAS, version="5.0.0-rc.7")

    def _plugin(self, monkeypatch, tmp_path) -> Path:
        plugin_root = tmp_path / "plugins" / "cache" / "gaia-marketplace" / "gaia" / "5.5.0"
        bin_path = _make_gaia_package(plugin_root)
        monkeypatch.setattr(session_manifest, "_own_package_root", lambda: plugin_root)
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        return bin_path

    def test_plugin_only_publishes_the_plugin_package_bin(self, layout, monkeypatch):
        from modules.security.gaia_cli_only_guard import is_trusted_gaia_binary

        workspace, tmp_path = layout
        bin_path = self._plugin(monkeypatch, tmp_path)

        cli_path = session_manifest._resolve_gaia_cli_path()

        assert not (workspace / "node_modules").exists()
        assert cli_path == str(bin_path)
        assert is_trusted_gaia_binary(cli_path)

    def test_plugin_only_environment_carries_the_cli_line(self, layout, monkeypatch):
        _workspace, tmp_path = layout
        bin_path = self._plugin(monkeypatch, tmp_path)

        block = build_environment_section()

        assert f"cli={bin_path}" in block.splitlines()

    def test_plugin_channel_publishes_the_plugin_beside_any_workspace_copy(self, layout, monkeypatch):
        """The guard accepts every genuine copy, so it cannot be what picks the running one."""
        from modules.security.gaia_cli_only_guard import is_trusted_gaia_binary

        workspace, tmp_path = layout
        bin_path = self._plugin(monkeypatch, tmp_path)
        stale_bin = self._nested_stale_copy(workspace)
        _make_gaia_package(workspace / _ALIAS)

        results = {session_manifest._resolve_gaia_cli_path() for _ in range(3)}

        assert results == {str(bin_path)}
        assert is_trusted_gaia_binary(str(bin_path))
        assert is_trusted_gaia_binary(str(stale_bin))

    def test_plugin_channel_version_is_the_plugins_not_a_nested_copy(self, layout, monkeypatch):
        workspace, tmp_path = layout
        self._plugin(monkeypatch, tmp_path)
        self._nested_stale_copy(workspace)
        monkeypatch.setattr(session_manifest, "_read_gaia_version", lambda: "5.5.0")

        block = build_environment_section()

        assert "gaia=5.5.0 channel=plugin" in block.splitlines()
        assert "5.0.0-rc.7" not in block

    def _run_from(self, monkeypatch, root: Path) -> Path:
        bin_path = _make_gaia_package(root)
        monkeypatch.setattr(session_manifest, "_own_package_root", lambda: root)
        monkeypatch.setattr(session_manifest, "_read_gaia_version", lambda: "5.5.0")
        return bin_path

    def test_marketplace_cache_cli_without_plugin_root_reports_the_plugin_channel(
        self, layout, monkeypatch
    ):
        workspace, tmp_path = layout
        _make_gaia_package(workspace / _ALIAS, version="5.6.0")
        cache_root = tmp_path / "home" / ".claude" / "plugins" / "cache" / "gaia-marketplace" / "gaia" / "5.5.0"
        bin_path = self._run_from(monkeypatch, cache_root)

        block = build_environment_section()

        assert "gaia=5.5.0 channel=plugin" in block.splitlines()
        assert session_manifest._resolve_gaia_cli_path() == str(bin_path)

    @pytest.mark.parametrize(
        "parts",
        [
            ("plugins", "cache", "other-marketplace", "gaia", "5.5.0"),
            ("plugins", "cache", "gaia-marketplace", "other", "5.5.0"),
            ("plugins", "gaia-marketplace", "gaia", "5.5.0"),
            ("plugins", "cache", "gaia-marketplace", "gaia"),
        ],
    )
    def test_a_path_that_only_resembles_the_marketplace_cache_is_not_the_plugin_channel(
        self, layout, monkeypatch, parts
    ):
        _workspace, tmp_path = layout
        self._run_from(monkeypatch, tmp_path.joinpath("home", ".claude", *parts))

        assert session_manifest._on_plugin_channel() is False

    def test_npm_only_publishes_the_workspace_alias(self, layout):
        from modules.security.gaia_cli_only_guard import is_trusted_gaia_binary

        workspace, _tmp_path = layout
        alias_bin = _make_gaia_package(workspace / _ALIAS)

        cli_path = session_manifest._resolve_gaia_cli_path()

        assert cli_path == str(alias_bin)
        assert is_trusted_gaia_binary(cli_path)

    def test_npm_publishes_the_declared_install_not_a_nested_copy(self, layout):
        workspace, _tmp_path = layout
        alias_bin = _make_gaia_package(workspace / _ALIAS, version="5.6.0")
        self._nested_stale_copy(workspace)

        block = build_environment_section()

        assert session_manifest._resolve_gaia_cli_path() == str(alias_bin)
        assert "gaia=5.6.0 channel=npm" in block.splitlines()
        assert "5.0.0-rc.7" not in block

    def test_npm_outside_every_declared_workspace_never_publishes_a_local_copy(
        self, layout, monkeypatch
    ):
        import modules.core.paths as core_paths_mod

        _workspace, tmp_path = layout
        outside = tmp_path / "outside"
        (outside / ".claude").mkdir(parents=True)
        _make_gaia_package(outside / _ALIAS)
        monkeypatch.chdir(outside)
        monkeypatch.setattr(core_paths_mod, "find_claude_dir", lambda: outside / ".claude")

        assert session_manifest._resolve_gaia_cli_path() is None

    def test_untrusted_alias_falls_through_to_the_own_package(self, layout, monkeypatch):
        workspace, tmp_path = layout
        own_root = tmp_path / "own"
        bin_path = _make_gaia_package(own_root)
        monkeypatch.setattr(session_manifest, "_own_package_root", lambda: own_root)
        impostor = workspace / _ALIAS
        _make_gaia_package(impostor)
        (impostor / "package.json").write_text(
            json.dumps({"name": "not-gaia", "bin": {"gaia": "bin/gaia"}})
        )

        assert session_manifest._resolve_gaia_cli_path() == str(bin_path)

    def test_a_trusted_gaia_on_path_alone_is_never_published(self, layout, monkeypatch):
        _workspace, tmp_path = layout
        on_path = _make_gaia_package(tmp_path / "elsewhere" / "gaia")
        monkeypatch.setenv("PATH", str(on_path.parent))

        assert session_manifest._resolve_gaia_cli_path() is None


class TestScanAvailableTools:
    @pytest.fixture(autouse=True)
    def _no_user_executables(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("PATH", "")

    def test_a_registered_binary_follows_the_candidates_and_an_unregistered_executable_is_not_listed(
        self, monkeypatch, tmp_path
    ):
        import shutil as _shutil

        user_bin = tmp_path / "bin"
        user_bin.mkdir()
        (user_bin / "mywrap").write_text("#!/bin/sh\n")
        (user_bin / "mywrap").chmod(0o755)
        monkeypatch.setenv("PATH", str(user_bin))
        monkeypatch.setattr(
            _shutil, "which", lambda name: "/usr/bin/" + name if name in ("git", "mywrap") else None
        )
        assert session_manifest._scan_available_tools(("git",)) == ["git"]
        assert session_manifest._scan_available_tools(("git",), user_binaries=["mywrap"]) == ["git", "mywrap"]

    def test_only_resolvable_candidates_are_returned_in_order(self, monkeypatch):
        import shutil as _shutil

        monkeypatch.setattr(
            _shutil, "which", lambda name: "/usr/bin/" + name if name in ("git", "acli") else None
        )
        result = session_manifest._scan_available_tools(("playwright", "git", "acli"))
        assert result == ["git", "acli"]

    def test_a_lookup_failure_is_skipped_not_fatal(self, monkeypatch):
        import shutil as _shutil

        def _boom(_name):
            raise OSError("simulated PATH lookup failure")

        monkeypatch.setattr(_shutil, "which", _boom)
        assert session_manifest._scan_available_tools(("git",)) == []


# ---------------------------------------------------------------------------
# build_session_context (assembler)
# ---------------------------------------------------------------------------

class TestBuildSessionContext:
    @pytest.fixture(autouse=True)
    def _no_user_rows_or_schema_notice(self, monkeypatch):
        monkeypatch.setattr(session_manifest, "orchestrator_user_rows", lambda: [])
        monkeypatch.setattr(session_manifest, "build_schema_direction_block", lambda: "")

    def test_alarms_lead_and_empty_sections_leave_no_gap(self, monkeypatch):
        monkeypatch.setattr(session_manifest, "projects_renderer", lambda: lambda _max: "")
        monkeypatch.setattr(session_manifest, "build_environment_section", lambda session_id="": "ENV")

        result = build_session_context(alarms=["ALARM"])

        assert result == "# Gaia session context (source: startup)\n\nALARM\n\nENV"
        assert "\n\n\n" not in result

    def test_alarms_still_ship_when_assembling_the_rest_fails(self, monkeypatch):
        def _boom():
            raise RuntimeError("simulated builder failure")

        monkeypatch.setattr(session_manifest, "build_environment_section", _boom)

        assert build_session_context(alarms=["ALARM"]) == "ALARM"

    def test_returns_empty_when_there_is_nothing_to_say(self, monkeypatch):
        monkeypatch.setattr(session_manifest, "projects_renderer", lambda: lambda _max: "")
        monkeypatch.setattr(session_manifest, "build_environment_section", lambda session_id="": "")

        assert build_session_context() == ""


