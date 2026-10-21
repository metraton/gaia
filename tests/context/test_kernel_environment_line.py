"""A specialist is told the machine facts it cannot discover cheaply.

What the user feels: a specialist knows it runs under WSL, which tools exist
and where the gaia CLI lives without spending turns probing, and the line never
grows past its cap however many tools the machine has. Runs against a
temporary HOME, GAIA_DATA_DIR and GAIA_DB.
"""

from __future__ import annotations

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
    monkeypatch.delenv("GAIA_CLI_ALIASES", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(session_manifest, "_resolve_gaia_cli_path", lambda: CLI_PATH)
    return db


def _path_with(tmp_path: Path, monkeypatch, *binaries: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in binaries:
        tool = bin_dir / name
        tool.write_text("#!/bin/sh\n")
        tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))


def _tools(line: str) -> list[str]:
    return line.split("tools on PATH: ")[1].split(", ")


def test_line_lists_a_declared_wrapper_that_is_on_path(env, tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_CLI_ALIASES", "ghx=gh")
    _path_with(tmp_path, monkeypatch, "ghx", "git")

    line = session_manifest.build_environment_line()

    assert "ghx" in _tools(line)
    assert CLI_PATH in line
    assert line.startswith("Linux")


def test_line_omits_a_wrapper_that_is_on_path_but_not_declared(env, tmp_path, monkeypatch):
    _path_with(tmp_path, monkeypatch, "ghx", "git")

    assert "ghx" not in session_manifest.build_environment_line()


def test_line_omits_a_declared_wrapper_that_is_not_on_path(env, tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_CLI_ALIASES", "ghx=gh")
    _path_with(tmp_path, monkeypatch, "git")

    assert "ghx" not in session_manifest.build_environment_line()


def test_line_names_wsl_from_the_kernel_release(env, monkeypatch):
    monkeypatch.setattr(session_manifest.platform, "release", lambda: "6.6.1-microsoft-standard-WSL2")
    monkeypatch.setattr(session_manifest.platform, "system", lambda: "Linux")

    assert session_manifest.build_environment_line().startswith("Linux/WSL2;")


def test_line_is_capped_and_keeps_the_cli_path(env, monkeypatch):
    many = [f"tool{n:02d}" for n in range(80)]
    monkeypatch.setattr(session_manifest, "_scan_available_tools", lambda: many)

    line = session_manifest.build_environment_line()

    assert session_manifest.ENVIRONMENT_LINE_MAX == 300
    assert len(line) <= 300
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
