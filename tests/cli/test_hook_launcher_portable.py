"""The registered hook command runs on any Python 3 spelling and any path.

The command build-plugin writes into hooks.json -- and the npm channel renders
into settings.local.json -- must not assume ``python3`` (the python.org
Windows installer ships ``python`` and ``py``) and must survive a plugin root
or workspace path holding a space.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = REPO_ROOT / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from modules.core.plugin_setup import render_gaia_hooks  # noqa: E402

SH = shutil.which("sh")
pytestmark = pytest.mark.skipif(SH is None, reason="the launcher is a POSIX sh script")


def _generated_hooks() -> dict:
    spec = importlib.util.spec_from_file_location("build_plugin", REPO_ROOT / "scripts" / "build-plugin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = json.loads((REPO_ROOT / "build" / "gaia.manifest.json").read_text())
    return module.generate_hooks_json(manifest)["hooks"]


def _commands(hooks: dict) -> list[str]:
    return [h["command"] for entries in hooks.values() for entry in entries for h in entry["hooks"]]


def _plugin_root(tmp_path: Path, entrypoint: str) -> Path:
    """A plugin root with a space, holding the real launcher and a stub entrypoint."""
    root = tmp_path / "plugin root"
    (root / "hooks").mkdir(parents=True)
    shutil.copy2(_HOOKS_DIR / "launch.sh", root / "hooks" / "launch.sh")
    (root / "hooks" / entrypoint).write_text(
        "import sys\nprint('ran', sys.version_info[0], sys.argv[1:])\n"
    )
    return root


def _path_with(tmp_path: Path, **tools: str) -> str:
    """A PATH holding only ``sh`` plus the named interpreters, never ``python3``."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "sh").symlink_to(SH)
    for name, body in tools.items():
        tool = bin_dir / name
        if body == "python":
            tool.symlink_to(sys.executable)
        else:
            tool.write_text(body)
            tool.chmod(0o755)
    return str(bin_dir)


def _run(command: str, cwd: Path, path: str, plugin_root: Path | None = None):
    env = {"PATH": path, "HOME": str(cwd)}
    if plugin_root is not None:
        env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
    return subprocess.run(
        [SH, "-c", command], cwd=cwd, env=env, input="{}", capture_output=True, text=True, timeout=30
    )


def test_every_generated_command_goes_through_the_quoted_launcher():
    commands = _commands(_generated_hooks())

    assert commands
    for command in commands:
        assert not command.startswith("python3 "), command
        assert command.startswith('sh "${CLAUDE_PLUGIN_ROOT}/hooks/launch.sh" "${CLAUDE_PLUGIN_ROOT}/hooks/'), command


def test_shipped_hooks_json_carries_the_generated_commands():
    shipped = json.loads((_HOOKS_DIR / "hooks.json").read_text())["hooks"]
    assert _commands(shipped) == _commands(_generated_hooks())


def test_plugin_command_runs_from_a_spaced_workspace_with_only_python(tmp_path):
    command = _commands(_generated_hooks())[0]
    entrypoint = PurePosixPath(command.rsplit("/hooks/", 1)[1].rstrip('"')).name
    root = _plugin_root(tmp_path, entrypoint)
    workspace = tmp_path / "my workspace"
    workspace.mkdir()

    result = _run(command, workspace, _path_with(tmp_path, python="python"), plugin_root=root)

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("ran 3"), result.stdout


def test_npm_rendered_command_runs_with_py_launcher_only(tmp_path):
    hooks = _generated_hooks()
    root = _plugin_root(tmp_path, "pre_tool_use.py")
    rendered = render_gaia_hooks({"PreToolUse": hooks["PreToolUse"]}, root / "hooks")
    command = _commands(rendered)[0]
    py = f'#!{SH}\n[ "$1" = "-3" ] || exit 9\nshift\nexec "{sys.executable}" "$@"\n'
    workspace = tmp_path / "my workspace"
    workspace.mkdir()

    result = _run(command, workspace, _path_with(tmp_path, py=py))

    assert "${CLAUDE_PLUGIN_ROOT}" not in command
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("ran 3"), result.stdout


def test_launcher_skips_a_python_that_is_not_python3(tmp_path):
    root = _plugin_root(tmp_path, "pre_tool_use.py")
    fake_python3 = f"#!{SH}\necho 'Python was not found' >&2\nexit 9009\n"
    path = _path_with(tmp_path, python3=fake_python3, python="python")

    result = _run(
        f'sh "{root}/hooks/launch.sh" "{root}/hooks/pre_tool_use.py"', tmp_path, path
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("ran 3")


def test_launcher_names_what_it_tried_when_no_python_exists(tmp_path):
    root = _plugin_root(tmp_path, "pre_tool_use.py")

    result = _run(
        f'sh "{root}/hooks/launch.sh" "{root}/hooks/pre_tool_use.py"', tmp_path, _path_with(tmp_path)
    )

    assert result.returncode != 0
    assert "python3, python, py -3" in result.stderr
