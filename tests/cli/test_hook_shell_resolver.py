"""One Git Bash resolver decides the hook shell for install, update and doctor; Windows is faked under tmp_path."""

from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for entry in (REPO_ROOT / "bin", REPO_ROOT / "hooks", REPO_ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from cli import doctor  # noqa: E402
from cli import install as install_mod  # noqa: E402

import gaia.hook_shell as hook_shell  # noqa: E402

_WINDOWS_VARIABLES = ("CLAUDE_CODE_GIT_BASH_PATH", "ProgramFiles", "ProgramW6432", "ProgramFiles(x86)",
                      "LOCALAPPDATA")
_CLAUDE_HELPERS = ("configure_settings_json", "merge_local_permissions", "merge_local_hooks",
                   "merge_worktree_settings", "manage_symlinks", "register_plugin")


@pytest.fixture
def windows_without_sh(tmp_path, monkeypatch):
    """A win32 host whose PATH resolves nothing, with HOME and the Windows folders under tmp_path."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(hook_shell.shutil, "which", lambda name: None)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for variable in _WINDOWS_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    return tmp_path


def _git_for_windows(root: Path) -> Path:
    for folder in ("usr/bin", "bin"):
        (root / folder).mkdir(parents=True, exist_ok=True)
        (root / folder / "sh.exe").write_text("")
    (root / "bin" / "bash.exe").write_text("")
    return root / "usr" / "bin" / "sh.exe"


def test_program_files_git_is_found_when_sh_is_off_path(windows_without_sh, monkeypatch):
    sh = _git_for_windows(windows_without_sh / "Program Files" / "Git")
    monkeypatch.setenv("ProgramFiles", str(windows_without_sh / "Program Files"))

    assert hook_shell.resolve_hook_shell() == (str(sh), "Git for Windows under %ProgramFiles%")
    assert hook_shell.missing_hook_shell() is None


def test_settings_env_override_names_the_sh_beside_bash(windows_without_sh):
    git = windows_without_sh / "Tools" / "Git"
    _git_for_windows(git)
    settings = windows_without_sh / "home" / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"env": {"CLAUDE_CODE_GIT_BASH_PATH": str(git / "bin" / "bash.exe")}}))

    assert hook_shell.resolve_hook_shell() == (str(git / "bin" / "sh.exe"),
                                               f"CLAUDE_CODE_GIT_BASH_PATH in {settings}")


def test_override_with_a_basename_claude_code_ignores_is_skipped(windows_without_sh, monkeypatch):
    git = windows_without_sh / "Git"
    _git_for_windows(git)
    monkeypatch.setenv("CLAUDE_CODE_GIT_BASH_PATH", str(git / "bin" / "git-bash.exe"))

    assert hook_shell.resolve_hook_shell() is None


def test_non_windows_answers_path_sh_only(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    _git_for_windows(tmp_path / "Git")
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setattr(hook_shell.shutil, "which", lambda name: "/usr/bin/sh" if name == "sh" else None)
    assert hook_shell.resolve_hook_shell() == ("/usr/bin/sh", "PATH")

    monkeypatch.setattr(hook_shell.shutil, "which", lambda name: None)
    assert hook_shell.resolve_hook_shell() is None
    assert hook_shell.missing_hook_shell() is None


def _update(workspace: Path, channels: tuple[str, ...]) -> tuple[int, str, str, list[str]]:
    ns = argparse.Namespace(postinstall=False, quiet=False, verbose=False, db_path=None,
                            workspace=str(workspace), skip_workspace=False, no_path=True, strict_wiring=False)
    calls: list[str] = []

    def step(name, result):
        def call(*_args, **_kwargs):
            calls.append(name)
            return result
        return call

    done = {"action": "created", "path": "x", "details": "ok"}
    patches = [
        patch.object(install_mod, "_run_bootstrap", step("bootstrap", {"rc": 0})),
        patch.object(install_mod, "_seed_contract_permissions", step("seed", done)),
        patch.object(install_mod, "_seed_surface_routing", step("routing", done)),
        patch.object(install_mod._install_helpers, "configure_opencode_plugin", step("opencode", done)),
        patch.object(install_mod, "_clear_install_error_marker", lambda *a, **k: None),
        patch.object(install_mod, "_write_install_error_marker", lambda *a, **k: None),
        patch.object(install_mod, "_is_windows", lambda: True),
        patch.object(install_mod.subprocess, "run", lambda *a, **k: pytest.fail("unexpected external runner")),
        patch("gaia.hook_shell.claude_code_version_finding", lambda: None),
        *(patch.object(install_mod._install_helpers, name, step(name, done)) for name in _CLAUDE_HELPERS),
    ]
    out, err = io.StringIO(), io.StringIO()
    for p in patches:
        p.start()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = install_mod.install_channels(ns, channels, command="gaia update")
    finally:
        for p in reversed(patches):
            p.stop()
    return rc, out.getvalue(), err.getvalue(), calls


@pytest.fixture
def workspace(windows_without_sh, monkeypatch):
    ws = windows_without_sh / "ws"
    (ws / ".claude").mkdir(parents=True)
    monkeypatch.setenv("GAIA_DATA_DIR", str(windows_without_sh / "data"))
    monkeypatch.setenv("GAIA_DB", str(windows_without_sh / "data" / "gaia.db"))
    return ws


def test_update_and_doctor_print_the_same_shell_and_source(workspace, windows_without_sh, monkeypatch):
    _git_for_windows(windows_without_sh / "Program Files" / "Git")
    monkeypatch.setenv("ProgramFiles", str(windows_without_sh / "Program Files"))
    line = hook_shell.describe_hook_shell(hook_shell.resolve_hook_shell(workspace))

    rc, out, err, calls = _update(workspace, ("npm",))

    assert rc == 0, err
    assert "bootstrap" in calls
    assert f"hook shell: {line}" in out
    result = doctor.check_hook_shell(workspace)
    assert result["severity"] == "pass"
    assert line in result["detail"]


def test_claude_code_without_any_git_bash_is_refused_before_work(workspace):
    rc, _, err, calls = _update(workspace, ("npm",))

    assert rc == 1
    assert "refused" in err and "CLAUDE_CODE_GIT_BASH_PATH" in err
    assert calls == []
    assert doctor.check_hook_shell(workspace)["severity"] == "error"


def test_opencode_only_host_without_sh_is_not_refused(workspace):
    rc, out, err, calls = _update(workspace, ("opencode",))

    assert rc == 0, err
    assert "opencode" in calls
    assert "hook shell:" not in out
