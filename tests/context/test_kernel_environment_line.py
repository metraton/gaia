"""A specialist is told the OS, host, the gaia CLI path and the detected tools, in a line that stays capped."""

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

from gaia.store.writer import USER_WORKSPACE, upsert_memory  # noqa: E402
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
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
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


def _register(binary: str) -> None:
    upsert_memory(USER_WORKSPACE, f"user_tool_{binary}", type="user", body="why it exists",
                  description="run it for every push", kind="tool", binary=binary)


def test_line_lists_the_catalog_tools_that_resolve_in_catalog_order(env, tmp_path, monkeypatch):
    _path_with(monkeypatch, _make(tmp_path / "usr", "kubectl", "git", "not-in-the-catalog"))

    line = session_manifest.build_environment_line()

    assert _tools(line) == ["git", "kubectl"]
    assert CLI_PATH in line
    assert line.startswith("Linux")


def test_line_names_only_executable_files(env, tmp_path, monkeypatch):
    _path_with(monkeypatch, _make(tmp_path / "usr", "git"), _make(tmp_path / "plain", "node", mode=0o644))

    assert _tools(session_manifest.build_environment_line()) == ["git"]


def _make_compiled(directory: Path, *names: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        binary = directory / name
        binary.write_bytes(b"\x7fELF\x02\x01\x01" + bytes(64))
        binary.chmod(0o755)
    return directory


def test_line_lists_the_users_executables_after_the_catalog_and_drops_console_scripts(env, tmp_path, monkeypatch):
    home = tmp_path / "home"
    _make_compiled(home / ".local" / "bin", "actionlint", "uv")
    _make_console_scripts(home / ".local" / "bin", "pytest", "uvicorn")
    _path_with(
        monkeypatch,
        home / ".local" / "bin",
        _make(home / "bin", "ghx", "mytask"),
        _make(tmp_path / "usr", "git"),
    )

    expected = ["git", "pytest", "actionlint", "uv", "ghx", "mytask"]
    assert _tools(session_manifest.build_environment_line()) == expected
    assert session_manifest._scan_available_tools() == expected
    authored, packages = session_manifest._scan_user_executables()
    assert authored == ["ghx", "mytask"] and packages == ["pytest", "uvicorn"]


def test_line_puts_registered_tools_first_then_the_catalog_then_the_rest_listing_each_once(env, tmp_path, monkeypatch):
    home = tmp_path / "home"
    _path_with(monkeypatch, _make(home / "bin", "ghx", "mytask"), _make(tmp_path / "usr", "git", "kubectl"))
    _register("ghx")
    _register("git")

    line = session_manifest.build_environment_line()

    assert _tools(line) == ["ghx", "git", "kubectl", "mytask"]
    assert session_manifest._scan_available_tools(user_binaries=["ghx", "git"]) == ["ghx", "git", "kubectl", "mytask"]


def test_a_registered_or_catalog_console_script_is_listed_though_other_ones_are_not(env, tmp_path, monkeypatch):
    home = tmp_path / "home"
    _path_with(monkeypatch, _make_console_scripts(home / ".local" / "bin", "pytest", "uvicorn", "httpx"))
    _register("uvicorn")

    assert _tools(session_manifest.build_environment_line()) == ["uvicorn", "pytest"]


def test_a_long_rest_is_truncated_before_any_row_or_catalog_name(env, tmp_path, monkeypatch):
    home = tmp_path / "home"
    rest = [f"loose-script-number-{n:03d}" for n in range(60)]
    _path_with(monkeypatch, _make(home / "bin", "ghx", *rest), _make(tmp_path / "usr", "git", "kubectl", "terraform"))
    _register("ghx")

    line = session_manifest.build_environment_line()

    assert len(line) <= 400
    assert _tools(line)[:4] == ["ghx", "git", "terraform", "kubectl"]
    assert "loose-script-number-000" in line and rest[-1] not in line


def test_line_names_wsl_from_the_kernel_release(env, monkeypatch):
    monkeypatch.setattr(session_manifest.platform, "release", lambda: "6.6.1-microsoft-standard-WSL2")
    monkeypatch.setattr(session_manifest.platform, "system", lambda: "Linux")

    assert session_manifest.build_environment_line().startswith("Linux/WSL2;")


def test_line_is_capped_and_keeps_the_cli_path(env, monkeypatch):
    many = [f"tool{n:02d}" for n in range(80)]
    monkeypatch.setattr(session_manifest, "_scan_available_tools", lambda **_: many)

    line = session_manifest.build_environment_line()

    assert session_manifest.ENVIRONMENT_LINE_MAX == 400
    assert len(line) <= 400
    assert CLI_PATH in line
    assert "tool00" in line and "tool79" not in line


def test_kernel_places_the_line_after_scope_memory_and_before_the_cli_index(env, tmp_path):
    """Replaces "after user rows": the user's rows left the kernel, the scope memory heads the same slot."""
    con = sqlite3.connect(env)
    con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES ('me', 'me')")
    con.execute(
        "INSERT INTO memory (workspace, name, type, description, body, class, kind, owner_level, updated_at) "
        "VALUES ('me', 'decision_me_standard', 'decision', 'Preference: a standard for every artifact', "
        "'the why', 'anchor', 'preference', 'workspace', '2026-09-01T00:00:00Z')"
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
