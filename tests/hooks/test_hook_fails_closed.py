"""A PreToolUse hook that cannot start or that crashes blocks the tool call.

The host runs the tool unless the hook exits 2 or prints a deny, and treats a
hook that cannot start or exits any other code as a non-blocking error unless
the registration sets ``onFailure: "block"`` (code.claude.com/docs/en/hooks,
"Block the action when a hook fails"). :func:`_host_blocks` encodes that rule,
so each probe asserts what the host would do rather than an exit code alone.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from tests.conftest import require_tool

REPO_ROOT = Path(__file__).resolve().parents[2]
SH = require_tool("sh")
PAYLOAD = json.dumps(
    {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}, "session_id": "probe"}
)


def _host_blocks(handler: dict, returncode: int, stdout: str) -> bool:
    if returncode == 2:
        return True
    if returncode == 0:
        try:
            decision = json.loads(stdout).get("hookSpecificOutput", {}).get("permissionDecision")
        except (ValueError, AttributeError):
            return False
        return decision == "deny"
    return handler.get("onFailure") == "block"


def _generated_pre_tool_use_handlers() -> list[dict]:
    spec = importlib.util.spec_from_file_location("build_plugin", REPO_ROOT / "scripts" / "build-plugin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = json.loads((REPO_ROOT / "build" / "gaia.manifest.json").read_text())
    hooks = module.generate_hooks_json(manifest)["hooks"]["PreToolUse"]
    return [handler for entry in hooks for handler in entry["hooks"]]


def _hook_tree(tmp_path: Path, broken_module: str, body: str) -> Path:
    """A copy of the plugin's Python trees with one hooks module replaced by *body*."""
    root = tmp_path / "plugin"
    for tree in ("hooks", "gaia", "opencode"):
        shutil.copytree(REPO_ROOT / tree, root / tree, ignore=shutil.ignore_patterns("__pycache__", "node_modules"))
    hooks = root / "hooks"
    (hooks / broken_module).write_text(body)
    return hooks


def _run_entry(hooks: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "GAIA_DATA_DIR": str(tmp_path / "data"),
        "GAIA_HOOK_STDIN_TIMEOUT": "5",
    }
    return subprocess.run(
        [sys.executable, str(hooks / "pre_tool_use.py")],
        cwd=tmp_path, env=env, input=PAYLOAD, capture_output=True, text=True, timeout=60,
    )


def _assert_denied(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 2, (result.returncode, result.stderr[-800:])
    decision = json.loads(result.stdout.strip().splitlines()[-1])["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"


def test_every_pre_tool_use_registration_blocks_when_the_hook_fails():
    handlers = _generated_pre_tool_use_handlers()

    assert handlers
    assert all(handler.get("onFailure") == "block" for handler in handlers), handlers


def test_missing_interpreter_blocks_the_tool_call(tmp_path):
    handler = _generated_pre_tool_use_handlers()[0]
    root = tmp_path / "plugin root"
    (root / "hooks").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "hooks" / "launch.sh", root / "hooks" / "launch.sh")
    (root / "hooks" / "pre_tool_use.py").write_text("print('ran')\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "sh").symlink_to(SH)

    result = subprocess.run(
        [SH, "-c", handler["command"]],
        cwd=tmp_path, env={"PATH": str(bin_dir), "HOME": str(tmp_path), "CLAUDE_PLUGIN_ROOT": str(root)},
        input=PAYLOAD, capture_output=True, text=True, timeout=30,
    )

    assert result.returncode not in (0, 2), result
    assert _host_blocks(handler, result.returncode, result.stdout)


def test_import_error_in_the_gate_denies_the_tool_call(tmp_path):
    hooks = _hook_tree(tmp_path, "modules/core/stdin.py", "raise ImportError('forced by probe')\n")

    _assert_denied(_run_entry(hooks, tmp_path))


def test_over_long_path_failure_in_the_gate_denies_the_tool_call(tmp_path):
    long_path = "/" + "x" * 300
    hooks = _hook_tree(
        tmp_path,
        "adapters/registry.py",
        f"import errno\n\ndef get_adapter():\n"
        f"    raise OSError(errno.ENAMETOOLONG, 'File name too long', {long_path!r})\n",
    )

    _assert_denied(_run_entry(hooks, tmp_path))


def test_shipped_plugin_requires_a_host_that_honours_on_failure():
    plugin = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text())

    assert plugin["engines"]["claude-code"] == ">=2.1.295"


def test_opencode_bridge_denies_when_the_gate_cannot_load(tmp_path):
    """plugin.ts throws, denying the tool, on a non-zero bridge exit or unparseable output."""
    hooks = _hook_tree(tmp_path, "adapters/registry.py", "raise ImportError('forced by probe')\n")
    event = {"event": "tool.execute.before", "sessionID": "probe", "callID": "c1", "tool": "Bash", "args": {"command": "ls"}}

    result = subprocess.run(
        [sys.executable, str(hooks.parent / "opencode" / "bridge.py"), "--shell-env-v1"],
        cwd=tmp_path, env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "GAIA_DATA_DIR": str(tmp_path / "data")},
        input=json.dumps(event), capture_output=True, text=True, timeout=60,
    )

    if result.returncode == 0:
        assert json.loads(result.stdout)["action"] == "deny", result.stdout


@pytest.fixture
def windows_without_sh(monkeypatch):
    real_which = shutil.which
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(shutil, "which", lambda cmd, *a, **k: None if cmd in ("sh", "sh.exe") else real_which(cmd, *a, **k))
    for path in (REPO_ROOT / "bin", REPO_ROOT):
        if str(path) not in sys.path:
            monkeypatch.syspath_prepend(str(path))


def test_install_refuses_a_windows_host_without_sh(windows_without_sh, tmp_path, monkeypatch, capsys):
    from cli import install

    def _bootstrap_reached(**_kwargs):
        raise AssertionError("install proceeded past the hook shell precondition")

    monkeypatch.setattr(install, "_run_bootstrap", _bootstrap_reached)
    args = Namespace(workspace=str(tmp_path), db_path=str(tmp_path / "gaia.db"), quiet=True)

    assert install.install_channels(args, ()) == 1
    assert "sh" in capsys.readouterr().err


@pytest.fixture
def cli_on_path(monkeypatch):
    for path in (REPO_ROOT / "bin", REPO_ROOT):
        if str(path) not in sys.path:
            monkeypatch.syspath_prepend(str(path))


def test_host_minimum_matches_the_declared_engine_and_its_validator(cli_on_path):
    from gaia.hook_shell import MIN_CLAUDE_CODE_VERSION

    engine = ">=" + ".".join(map(str, MIN_CLAUDE_CODE_VERSION))
    plugin = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert plugin["engines"]["claude-code"] == engine
    assert f"claudeCodeEngine !== '{engine}'" in (REPO_ROOT / "bin" / "pre-publish-validate.js").read_text()


def _claude_reports(monkeypatch, version_line: str) -> None:
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda cmd, *a, **k: "/usr/bin/claude" if cmd == "claude" else real_which(cmd, *a, **k))
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=version_line, stderr="")
    )


def test_install_refuses_a_claude_code_older_than_the_minimum(cli_on_path, tmp_path, monkeypatch, capsys):
    from cli import install

    def _bootstrap_reached(**_kwargs):
        raise AssertionError("install proceeded on a host that ignores onFailure")

    monkeypatch.setattr(install, "_run_bootstrap", _bootstrap_reached)
    _claude_reports(monkeypatch, "2.1.294 (Claude Code)\n")
    args = Namespace(workspace=str(tmp_path), db_path=str(tmp_path / "gaia.db"), quiet=True, skip_workspace=True)

    assert install.install_channels(args, ("npm",)) == 1
    assert "claude update" in capsys.readouterr().err


def test_unreadable_claude_code_version_does_not_refuse(cli_on_path, monkeypatch):
    from gaia.hook_shell import outdated_claude_code

    _claude_reports(monkeypatch, "garbled\n")

    assert outdated_claude_code() is None


def test_keyboard_interrupt_at_import_denies_the_tool_call(tmp_path):
    hooks = _hook_tree(tmp_path, "modules/core/stdin.py", "raise KeyboardInterrupt\n")

    _assert_denied(_run_entry(hooks, tmp_path))


def test_settings_local_render_keeps_on_failure():
    hooks_dir = REPO_ROOT / "hooks"
    if str(hooks_dir) not in sys.path:
        sys.path.insert(0, str(hooks_dir))
    from modules.core.plugin_setup import render_gaia_hooks

    shipped = json.loads((hooks_dir / "hooks.json").read_text())["hooks"]
    rendered = render_gaia_hooks(shipped, hooks_dir)

    handlers = [h for entry in rendered["PreToolUse"] for h in entry["hooks"]]
    assert handlers and all(h.get("onFailure") == "block" for h in handlers), handlers


def test_doctor_flags_a_claude_code_older_than_the_minimum(cli_on_path, monkeypatch):
    from cli import doctor

    monkeypatch.setattr(doctor.shutil, "which", lambda cmd, *a, **k: "/usr/bin/claude" if cmd == "claude" else None)
    monkeypatch.setattr(
        doctor.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="2.1.294 (Claude Code)\n", stderr=""),
    )

    result = doctor.check_claude_code()

    assert result["severity"] == "error" and result["fix"] == "claude update", result


def test_doctor_flags_a_windows_host_without_sh(windows_without_sh):
    from cli import doctor

    checks = {name: fn for _order, name, fn in doctor._CHECKS}
    assert "Hook shell" in checks, sorted(checks)
    result = checks["Hook shell"]()
    assert result["severity"] == "error" and not result["ok"], result
