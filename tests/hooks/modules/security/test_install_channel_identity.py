"""Every channel's published install layout runs its own bin/gaia free, and a change inside it asks a signature."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import data_heredoc, gaia_cli_only_guard  # noqa: E402
from modules.security import mutative_verbs as mv  # noqa: E402
from modules.security import tiers as tiers_module  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

LAYOUTS = {
    "claude-marketplace": ("home", ".claude", "plugins", "cache", "gaia-marketplace", "gaia", "5.7.1"),
    "claude-other-marketplace": ("home", ".claude", "plugins", "cache", "mirror", "gaia", "5.7.1"),
    "claude-marketplace-windows": ("Users", "x", ".claude", "plugins", "cache", "gaia-marketplace", "gaia", "5.7.1"),
    "npm-local": ("proj", "node_modules", "@jaguilar87", "gaia"),
    "npm-global-posix": ("prefix", "lib", "node_modules", "@jaguilar87", "gaia"),
    "npm-global-windows": ("Users", "x", "AppData", "Roaming", "npm", "node_modules", "@jaguilar87", "gaia"),
    "dev-plugin": None,
}
CALLS = ("--version", "now", "approvals question P-0123456789abcdef")
SESSION = "s-571"
SUBAGENT = {"agent_id": "a0123456789abcdef", "agent_type": "developer", "session_id": SESSION}
ORCHESTRATOR = {"agent_type": "gaia-orchestrator", "session_id": SESSION}


def _clear_caches() -> None:
    mv._detect_mutative_command.cache_clear()
    mv._root_is_install.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()


def _lay_out(root: Path) -> Path:
    (root / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy(_REPO_ROOT / "bin" / "gaia", root / "bin" / "gaia")
    (root / "package.json").write_text(
        '{"name": "@jaguilar87/gaia", "version": "5.7.1", "bin": {"gaia": "bin/gaia"}}', encoding="utf-8",
    )
    return root


@pytest.fixture(params=sorted(LAYOUTS))
def install(request, tmp_path, monkeypatch) -> Path:
    shape = LAYOUTS[request.param]
    if shape is None:
        from gaia.paths import cache_dir

        root = _lay_out(cache_dir() / "dev-plugin" / "ws")
    else:
        root = _lay_out(tmp_path.joinpath(*shape))
    monkeypatch.setattr(mv, "_HOOK_ROOT", root.resolve())
    _clear_caches()
    yield root.resolve()
    _clear_caches()


def _validate(command: str, payload: dict, cwd: Path):
    return BashValidator().validate(
        command, is_subagent="agent_id" in payload, session_id=SESSION,
        agent_type=payload["agent_type"], hook_payload=payload, cwd=str(cwd),
    )


@pytest.mark.parametrize("call", CALLS)
@pytest.mark.parametrize("payload", [SUBAGENT, ORCHESTRATOR], ids=["subagent", "orchestrator"])
def test_the_installed_cli_by_path_runs_free(install, tmp_path, call, payload):
    result = _validate(f"{install.as_posix()}/bin/gaia {call}", payload, tmp_path)
    assert result.allowed, result.reason
    assert gaia_cli_only_guard.is_trusted_gaia_binary(str(install / "bin" / "gaia"))
    assert data_heredoc.is_gaia_cli(str(install / "bin" / "gaia"))


def test_a_change_inside_the_install_asks_a_signature_naming_it(install, tmp_path):
    result = _validate(f"touch {install.as_posix()}/bin/planted.py", SUBAGENT, tmp_path)
    assert not result.allowed and result.approval_id, result.reason
    assert "installed Gaia" in str(result.block_response)


def test_a_write_to_a_claude_hook_tree_stays_refused(install, tmp_path):
    hooks = tmp_path / "home" / ".claude" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    result = _validate(f"touch {hooks.as_posix()}/pre_tool_use.py", SUBAGENT, tmp_path)
    assert not result.allowed and not result.approval_id, result.reason


def test_a_checkout_carrying_the_same_manifest_is_no_install(tmp_path, monkeypatch):
    root = _lay_out(tmp_path.joinpath(*LAYOUTS["claude-marketplace"]))
    (root / ".git").mkdir()
    monkeypatch.setattr(mv, "_HOOK_ROOT", root)
    _clear_caches()
    assert mv.detect_mutative_command(f"{root.as_posix()}/bin/gaia --version").is_mutative is True


@pytest.mark.parametrize("payload", [SUBAGENT, ORCHESTRATOR], ids=["subagent", "orchestrator"])
def test_a_bare_gaia_with_none_on_the_hook_path_resolves_to_this_install(install, tmp_path, monkeypatch, payload):
    git_only = os.path.dirname(shutil.which("git"))
    assert shutil.which("gaia", path=git_only) is None
    monkeypatch.setenv("PATH", git_only)
    result = _validate("gaia memory show user_pref_x", payload, tmp_path)
    assert result.allowed, result.reason


def _cmd_shim(prefix: Path, script: str) -> Path:
    shim = prefix / "gaia.cmd"
    shim.write_text(
        '@ECHO off\r\nSET dp0=%~dp0\r\nIF EXIST "%dp0%\\python3.exe" (SET "_prog=%dp0%\\python3.exe")\r\n'
        f'"%_prog%"  "%dp0%\\{script}" %*\r\n', encoding="utf-8",
    )
    return shim


def test_an_npm_launcher_wrapping_the_package_bin_is_the_trusted_cli(tmp_path):
    prefix = tmp_path / "npm"
    _lay_out(prefix / "node_modules" / "@jaguilar87" / "gaia")
    shim = _cmd_shim(prefix, "node_modules\\@jaguilar87\\gaia\\bin\\gaia")
    (prefix / "gaia.ps1").write_text(
        '$basedir=Split-Path $MyInvocation.MyCommand.Definition -Parent\n'
        '& "$basedir/python3$exe"  "$basedir/node_modules/@jaguilar87/gaia/bin/gaia" $args\n', encoding="utf-8",
    )
    assert gaia_cli_only_guard.is_trusted_gaia_binary(str(shim))
    assert gaia_cli_only_guard.is_trusted_gaia_binary(str(prefix / "gaia.ps1"))


@pytest.mark.parametrize("script", ["node_modules\\other\\bin\\gaia", "bin\\gaia\" & \"%dp0%\\evil.py"])
def test_a_launcher_running_anything_else_is_not_trusted(tmp_path, script):
    prefix = tmp_path / "npm"
    _lay_out(prefix / "node_modules" / "@jaguilar87" / "gaia")
    (prefix / "node_modules" / "other" / "bin").mkdir(parents=True)
    (prefix / "node_modules" / "other" / "bin" / "gaia").write_text("print()", encoding="utf-8")
    assert not gaia_cli_only_guard.is_trusted_gaia_binary(str(_cmd_shim(prefix, script)))


def _windows_spellings(path: Path) -> list:
    forward = str(path).replace("\\", "/")
    return [
        forward,
        "/" + forward[0].lower() + forward[2:],
        "'" + str(path).replace("/", "\\") + "'",
        forward.upper(),
    ]


@pytest.mark.skipif(os.name != "nt", reason="drive letters, backslashes and case folding are Windows path semantics")
@pytest.mark.parametrize("call", CALLS)
def test_every_windows_spelling_of_the_installed_cli_runs_free(install, tmp_path, call):
    for spelling in _windows_spellings(install / "bin" / "gaia"):
        for payload in (SUBAGENT, ORCHESTRATOR):
            _clear_caches()
            result = _validate(f"{spelling} {call}", payload, tmp_path)
            assert result.allowed, (spelling, result.reason)
        assert gaia_cli_only_guard.is_trusted_gaia_binary(spelling.strip("'"))
