"""A specialist is told the OS, the gaia CLI path and the tools on PATH, in a line that stays capped."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

from tests.conftest import copy_bootstrapped_db

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from modules.context import kernel_builder  # noqa: E402
from modules.session import session_manifest  # noqa: E402

CLI_PATH = "/opt/gaia/bin/gaia"


@pytest.fixture()
def env(tmp_path, monkeypatch, bootstrapped_db_template):
    data = tmp_path / "data"
    db = copy_bootstrapped_db(bootstrapped_db_template, data / "gaia.db")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(db))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(session_manifest, "_resolve_gaia_cli_path", lambda: CLI_PATH)
    return db


def _path_with(monkeypatch, *dirs: Path) -> None:
    monkeypatch.setenv("PATH", os.pathsep.join(str(d) for d in dirs))


def _make(directory: Path, *names: str, mode: int = 0o755) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        tool = directory / name
        tool.write_text("#!/bin/sh\n")
        tool.chmod(mode)
    return directory


def _tools(line: str) -> list[str]:
    return line.split("tools on PATH: ")[1].split(", ")


def _make_console_scripts(directory: Path, *names: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        script = directory / name
        script.write_text(
            "#!/usr/bin/python3\n# -*- coding: utf-8 -*-\nimport re\nimport sys\n"
            "from somepackage import main\nif __name__ == '__main__':\n"
            "    sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])\n"
            "    sys.exit(main())\n"
        )
        script.chmod(0o755)
    return directory


def test_line_lists_an_executable_from_a_directory_the_user_owns(env, tmp_path, monkeypatch):
    home = tmp_path / "home"
    _path_with(monkeypatch, _make(tmp_path / "usr", "git"), _make(home / "bin", "ghx"), _make(home / ".local" / "bin", "mytool"))

    line = session_manifest.build_environment_line()

    assert {"git", "ghx", "mytool"} <= set(_tools(line))
    assert CLI_PATH in line
    assert line.startswith("Linux")


def test_line_names_only_executable_files(env, tmp_path, monkeypatch):
    home = tmp_path / "home"
    _make(home / "bin", "runs")
    _make(home / "bin", "notes.txt", ".hidden", mode=0o644)
    (home / "bin" / "adir").mkdir()
    _path_with(monkeypatch, home / "bin")

    assert _tools(session_manifest.build_environment_line()) == ["runs"]


def test_line_skips_directories_outside_home_and_package_manager_trees(env, tmp_path, monkeypatch):
    home = tmp_path / "home"
    _path_with(
        monkeypatch,
        _make(tmp_path / "system", "systool"),
        _make(home / "proj" / "node_modules" / ".bin", "nodetool"),
        _make(home / ".cargo" / "bin", "cargotool"),
        _make(home / "go" / "bin", "gotool"),
        _make(home / ".venv" / "bin", "venvtool"),
        _make(home / "bin", "owned"),
    )

    assert _tools(session_manifest.build_environment_line()) == ["owned"]


def test_line_lists_a_name_once_across_directories_and_known_tools(env, tmp_path, monkeypatch):
    home = tmp_path / "home"
    _path_with(monkeypatch, _make(tmp_path / "usr", "git"), _make(home / "bin", "git", "twice"), _make(home / ".local" / "bin", "twice"))

    assert sorted(_tools(session_manifest.build_environment_line())) == ["git", "twice"]


def test_orchestrator_section_lists_more_user_executables_than_the_line_keeps(env, tmp_path, monkeypatch):
    names = [f"my-own-tool-{n:02d}" for n in range(60)]
    _path_with(monkeypatch, _make(tmp_path / "home" / "bin", *names))

    kept_by_line = _tools(session_manifest.build_environment_line())
    listed = session_manifest._scan_available_tools()

    assert listed == names[: session_manifest.USER_EXECUTABLES_MAX]
    assert 0 < len(kept_by_line) < len(listed)


def test_line_lists_the_users_own_tools_before_known_tools_and_pip_console_scripts(env, tmp_path, monkeypatch):
    home = tmp_path / "home"
    pip_scripts = [f"opentelemetry-tool-{n:02d}" for n in range(30)] + ["pytest", "py.test", "uvicorn"]
    own_python = home / "bin" / "mytask"
    _make(home / "bin", "ghx")
    own_python.write_text("#!/usr/bin/env python3\nprint('mine')\n")
    own_python.chmod(0o755)
    _path_with(
        monkeypatch,
        _make_console_scripts(home / ".local" / "bin", *pip_scripts),
        home / "bin",
        _make(tmp_path / "usr", "git"),
    )

    line = session_manifest.build_environment_line()
    tools = _tools(line)

    assert len(line) <= session_manifest.ENVIRONMENT_LINE_MAX
    assert tools[:3] == ["ghx", "mytask", "git"]
    assert set(tools[3:]) <= set(pip_scripts)
    assert 0 < len(tools[3:]) < len(pip_scripts)


def test_orchestrator_scan_orders_own_tools_then_known_tools_then_pip_console_scripts(env, tmp_path, monkeypatch):
    home = tmp_path / "home"
    _path_with(
        monkeypatch,
        _make_console_scripts(home / ".local" / "bin", "pytest"),
        _make(home / "bin", "ghx"),
        _make(tmp_path / "usr", "git"),
    )

    assert session_manifest._scan_available_tools() == ["ghx", "git", "pytest"]


def test_line_names_wsl_from_the_kernel_release(env, monkeypatch):
    monkeypatch.setattr(session_manifest.platform, "release", lambda: "6.6.1-microsoft-standard-WSL2")
    monkeypatch.setattr(session_manifest.platform, "system", lambda: "Linux")

    assert session_manifest.build_environment_line().startswith("Linux/WSL2;")


def test_line_is_capped_and_keeps_the_cli_path(env, monkeypatch):
    many = [f"tool{n:02d}" for n in range(80)]
    monkeypatch.setattr(session_manifest, "_scan_available_tools", lambda: many)

    line = session_manifest.build_environment_line()

    assert session_manifest.ENVIRONMENT_LINE_MAX == 400
    assert len(line) <= 400
    assert CLI_PATH in line
    assert "tool00" in line and "tool79" not in line


def test_kernel_places_the_line_after_user_rows_and_before_the_cli_index(env, tmp_path):
    con = sqlite3.connect(env)
    con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES ('_gaia_user', '_gaia_user')")
    con.execute(
        "INSERT INTO memory (workspace, name, type, description, body, class, audience, updated_at) "
        "VALUES ('_gaia_user', 'user_pref_x', 'user', 'd', 'a standard for every artifact', "
        "'anchor', 'executor', '2026-09-01T00:00:00Z')"
    )
    con.commit()
    con.close()

    kernel = kernel_builder.build_kernel_context(
        {
            "contract_id": "a0123456789abcdef.beefcafe0123",
            "agent_id": "a0123456789abcdef",
            "workspace": "me",
            "dispatch_prompt": "fix the build",
            "kernel_sections": '{"role": "primary", "surface": "app_ci"}',
        },
        agent_name="developer", agents_dir=tmp_path / "agents", db_path=env,
    )

    user = kernel.index(kernel_builder.MEMORY_HEADING)
    environment = kernel.index(kernel_builder.ENVIRONMENT_HEADING)
    cli = kernel.index(kernel_builder.CLI_HEADING)
    assert user < environment < cli
    assert CLI_PATH in kernel[environment:cli]
