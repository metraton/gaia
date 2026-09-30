"""Explicit install channels: `gaia install` and `gaia dev` name one, npm and plugin exclude each other."""

import argparse
import ast
import json
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT / "bin"), str(_ROOT / "hooks")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cli import _manifest  # noqa: E402
from cli import dev as dev_mod  # noqa: E402
from cli import doctor as doctor_mod  # noqa: E402
from cli import install as install_mod  # noqa: E402
from cli import update as update_mod  # noqa: E402

_SETTLED = 7


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch, _isolate_gaia_data_dir):
    """Keep the user's settings out and fail any step that runs before the channel is settled."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.setattr(install_mod, "_run_bootstrap",
                        lambda **_: pytest.fail("install bootstrapped before settling the channel"))
    monkeypatch.setattr(dev_mod, "_run_pack_mode",
                        lambda *_, **__: pytest.fail("dev packed before settling the channel"))
    monkeypatch.setattr(dev_mod, "_run_plugin_channel",
                        lambda *_, **__: pytest.fail("dev served the plugin before settling the channel"))


def _parse(module, argv):
    parser = argparse.ArgumentParser()
    module.register(parser.add_subparsers(dest="subcommand"))
    return parser.parse_args(argv)


def _install(argv, capsys):
    rc = install_mod.cmd_install(_parse(install_mod, ["install", *argv]))
    return rc, capsys.readouterr().err


def _dev(argv, capsys):
    rc = dev_mod.cmd_dev(_parse(dev_mod, ["dev", *argv]))
    return rc, capsys.readouterr().err


def _empty_workspace(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


def _plugin_workspace(tmp_path, settings=".claude/settings.local.json"):
    workspace = _empty_workspace(tmp_path)
    target = tmp_path / settings if settings.startswith("home/") else workspace / settings
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"enabledPlugins": {"gaia@gaia-dev": True}}))
    return workspace


def _npm_workspace(tmp_path):
    workspace = _empty_workspace(tmp_path)
    claude = workspace / ".claude"
    claude.mkdir()
    command = f"python3 {claude.as_posix()}/hooks/pre_tool_use.py"
    handler = {"type": "command", "command": command}
    settings = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [handler]}]}}
    (claude / "settings.local.json").write_text(json.dumps(settings))
    return workspace


def _record_settled(monkeypatch, module, name, calls):
    def settled(*args, **kwargs):
        calls.append(kwargs.get("host"))
        return {"rc": _SETTLED} if module is install_mod else 0
    monkeypatch.setattr(module, name, settled)


def test_install_without_a_channel_fails_listing_the_channels(tmp_path, capsys):
    rc, err = _install(["--workspace", str(_empty_workspace(tmp_path))], capsys)
    assert rc == 1
    assert "--channel" in err
    assert "npm" in err and "opencode" in err
    assert "claude plugin install gaia@gaia-marketplace" in err


def test_dev_without_a_channel_fails_listing_the_channels(tmp_path, capsys):
    rc, err = _dev(["--workspace", str(_empty_workspace(tmp_path))], capsys)
    assert rc == 1
    assert "--channel" in err
    assert all(channel in err for channel in ("npm", "plugin", "opencode"))


@pytest.mark.parametrize("module,flag", [
    (install_mod, "--channel"), (install_mod, "--host"), (dev_mod, "--channel"), (dev_mod, "--host"),
])
def test_all_is_not_a_channel(module, flag, capsys):
    name = "install" if module is install_mod else "dev"
    with pytest.raises(SystemExit):
        _parse(module, [name, flag, "all"])
    assert "invalid choice: 'all'" in capsys.readouterr().err


@pytest.mark.parametrize("settings,scope", [
    (".claude/settings.local.json", "local"),
    (".claude/settings.json", "project"),
    ("home/.claude/settings.json", "user"),
])
@pytest.mark.parametrize("flag", [["--channel", "npm"], ["--host", "claude_code"]])
def test_npm_install_where_the_plugin_channel_is_fails_naming_it(tmp_path, capsys, settings, scope, flag):
    workspace = _plugin_workspace(tmp_path, settings)
    rc, err = _install([*flag, "--workspace", str(workspace)], capsys)
    assert rc == 1
    assert "plugin channel" in err
    assert "gaia@gaia-dev" in err
    assert f"claude plugin uninstall gaia@gaia-dev --scope {scope}" in err
    assert "--channel opencode" in err
    assert not (workspace / ".claude" / "gaia-manifest.json").exists()


def test_npm_channel_skipping_the_workspace_ignores_the_plugin_channel(tmp_path, capsys, monkeypatch):
    installs = []
    _record_settled(monkeypatch, install_mod, "_run_bootstrap", installs)
    argv = ["--channel", "npm", "--skip-workspace", "--workspace", str(_plugin_workspace(tmp_path))]
    assert _install(argv, capsys)[0] == _SETTLED


def test_npm_dev_where_the_plugin_channel_is_fails_before_packing(tmp_path, capsys):
    rc, err = _dev(["--channel", "npm", "--workspace", str(_plugin_workspace(tmp_path))], capsys)
    assert rc == 1
    assert "claude plugin uninstall gaia@gaia-dev --scope local" in err


def test_plugin_dev_where_the_npm_channel_is_fails_naming_it(tmp_path, capsys):
    workspace = _npm_workspace(tmp_path)
    rc, err = _dev(["--channel", "plugin", "--workspace", str(workspace)], capsys)
    assert rc == 1
    assert "npm channel" in err
    assert f"gaia uninstall --workspace {workspace}" in err
    assert "--channel opencode" in err


@pytest.mark.parametrize("build", [_empty_workspace, _plugin_workspace, _npm_workspace])
def test_opencode_channel_joins_either_channel(tmp_path, capsys, monkeypatch, build):
    workspace = build(tmp_path)
    installs, devs = [], []
    _record_settled(monkeypatch, install_mod, "_run_bootstrap", installs)
    _record_settled(monkeypatch, dev_mod, "_run_pack_mode", devs)

    assert _install(["--channel", "opencode", "--workspace", str(workspace)], capsys)[0] == _SETTLED
    assert _dev(["--channel", "opencode", "--workspace", str(workspace)], capsys)[0] == 0
    assert devs == ["opencode"]


def test_npm_channel_installs_where_no_plugin_is(tmp_path, capsys, monkeypatch):
    workspace = _npm_workspace(tmp_path)
    installs, devs = [], []
    _record_settled(monkeypatch, install_mod, "_run_bootstrap", installs)
    _record_settled(monkeypatch, dev_mod, "_run_pack_mode", devs)

    assert _install(["--channel", "npm", "--workspace", str(workspace)], capsys)[0] == _SETTLED
    assert _dev(["--channel", "npm", "--workspace", str(workspace)], capsys)[0] == 0
    assert devs == ["claude_code"]


def _update(argv, capsys):
    rc = update_mod.cmd_update(_parse(update_mod, ["update", *argv]))
    return rc, capsys.readouterr().err


def test_update_without_a_recorded_channel_fails_naming_install(tmp_path, capsys):
    workspace = _empty_workspace(tmp_path)
    rc, err = _update(["--workspace", str(workspace)], capsys)
    assert rc == 1
    assert "no install channel is recorded" in err
    assert "gaia install --channel npm|opencode" in err


def test_update_rewires_the_recorded_channels(tmp_path, capsys, monkeypatch):
    workspace = _empty_workspace(tmp_path)
    (workspace / ".claude").mkdir()
    _manifest.record(workspace, {}, channel="npm", version="0", package_channels=["opencode"])
    _manifest.record(workspace, {}, channel="plugin", version="0")
    _manifest.record(workspace, {}, channel="npm", version="0", package_channels=["npm"])
    wired = []
    monkeypatch.setattr(install_mod, "install_channels",
                        lambda args, channels, **_: wired.append(tuple(channels)) or 0)

    assert _update(["--workspace", str(workspace)], capsys)[0] == 0
    assert wired == [("npm", "opencode")]


def test_update_with_a_channel_installs_that_channel(tmp_path, capsys, monkeypatch):
    workspace = _plugin_workspace(tmp_path)
    rc, err = _update(["--channel", "npm", "--workspace", str(workspace)], capsys)
    assert rc == 1
    assert "claude plugin uninstall gaia@gaia-dev --scope local" in err


def test_install_skipping_the_workspace_needs_no_channel(tmp_path, capsys, monkeypatch):
    installs = []
    _record_settled(monkeypatch, install_mod, "_run_bootstrap", installs)
    assert _install(["--skip-workspace", "--workspace", str(_empty_workspace(tmp_path))], capsys)[0] == _SETTLED


def _doctor_strings():
    """Every string literal in doctor.py that is not a docstring, f-string fields rendered as {}."""
    tree = ast.parse(Path(doctor_mod.__file__).read_text())
    scopes = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    skipped = {id(node.body[0].value) for node in ast.walk(tree)
               if isinstance(node, scopes) and ast.get_docstring(node) is not None}
    skipped |= {id(part) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr) for part in node.values}
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            yield "".join(part.value if isinstance(part, ast.Constant) else "{}" for part in node.values)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skipped:
            yield node.value


def test_doctor_hints_name_a_channel_and_hold_no_pipe():
    commands = [match.group(1) for text in _doctor_strings()
                for match in re.finditer(r"(?:`|Run: )(gaia (?:install|dev)\b[^`\n]*)", text)]
    assert commands
    for command in commands:
        assert "|" not in command, command
        assert any(flag in command for flag in ("--channel", "--skip-workspace", "--help")), command


def test_doctor_dev_hint_is_one_runnable_line_per_recorded_channel(tmp_path):
    workspace = _empty_workspace(tmp_path)
    (workspace / ".claude").mkdir()
    assert "`gaia dev --help` lists the channels" in doctor_mod._package_dev_command(workspace)

    _manifest.record(workspace, {}, channel="npm", version="0", package_channels=["opencode", "npm"])
    hint = doctor_mod._package_dev_command(workspace)
    assert hint == (f"`gaia dev --workspace {workspace} --channel npm` and "
                    f"`gaia dev --workspace {workspace} --channel opencode`")
