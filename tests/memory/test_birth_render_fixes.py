"""The birth block and the kernel render what a reader can act on: human recurrence and local times in Due, a filtered Environment, a visible scope-read failure.

Each test is a probe the verifier of feat/57-memory-model-injection refuted on real data.
Runs in-process against the temporary HOME and GAIA_DATA_DIR the suite's conftest provides.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "bin", _REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from gaia import notifications_time  # noqa: E402
from gaia.store.writer import USER_WORKSPACE, _connect, upsert_memory  # noqa: E402
from modules.context import kernel_builder  # noqa: E402
from modules.session import session_manifest  # noqa: E402


@pytest.fixture(autouse=True)
def _direct_caller(monkeypatch):
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("GAIA_HOST", raising=False)


@pytest.fixture
def santiago(monkeypatch):
    monkeypatch.setenv("TZ", "America/Santiago")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _spec(cron: str | None = None, every: str | None = None) -> str:
    return json.dumps(notifications_time.cron_spec(cron) if cron else notifications_time.interval_spec(every))


@pytest.mark.parametrize("cron, label", [
    ("15 9 * * 1-5", "mon-fri 09:15"),
    ("30 8 * * 1", "mon 08:30"),
    ("0 9 * * 1,3,5", "mon,wed,fri 09:00"),
    ("0 9 * * 1,2", "mon,tue 09:00"),
    ("0 10 * * 0,6", "sat,sun 10:00"),
    ("0 9 * * *", "daily 09:00"),
    ("0 9,17 * * 1-5", "mon-fri 09:00,17:00"),
    ("0 18 30 * *", "day 30 18:00"),
    ("0 8 1,15 * *", "day 1,15 08:00"),
    ("0 9 1 1,7 *", "jan,jul day 1 09:00"),
])
def test_a_cron_recurrence_reads_as_days_and_a_time(cron, label):
    assert notifications_time.describe_recurrence(_spec(cron=cron)) == label


@pytest.mark.parametrize("every, label", [
    ("6h", "every 6h"), ("90m", "every 90m"), ("2d", "every 2d"), ("1w", "every 1w"), ("45m", "every 45m"),
])
def test_an_interval_recurrence_reads_as_every_n_units(every, label):
    assert notifications_time.describe_recurrence(_spec(every=every)) == label


def test_an_unreadable_recurrence_is_shown_as_stored_never_dropped():
    assert notifications_time.describe_recurrence("not json") == "not json"


def _due_rows():
    return [
        {"id": 78, "kind": "routine", "headline": "Brief diario", "due_at": "2026-10-23T12:15:00Z",
         "recurrence": _spec(cron="15 9 * * 1-5"), "pointer_kind": "memory", "pointer_workspace": "century-inc",
         "pointer_ref": "atom_brief"},
        {"id": 79, "kind": "routine", "headline": "Sueldo", "due_at": "2026-10-30T21:00:00Z",
         "recurrence": _spec(cron="0 18 30 * *")},
        {"id": 81, "kind": "routine", "headline": "Revisar PRs", "due_at": "2026-10-30T15:00:00Z",
         "recurrence": _spec(every="6h")},
        {"id": 80, "kind": "reminder", "headline": "Call the bank", "due_at": "2020-01-02T15:00:00Z"},
    ]


def test_due_prints_repeats_as_a_short_human_form_and_times_in_the_session_zone(monkeypatch, santiago):
    import gaia.store.reader as reader

    monkeypatch.setattr(reader, "list_unread_notifications", lambda workspace=None: _due_rows())

    lines = session_manifest.build_due_section("me").splitlines()

    assert lines[:2] == ["## Due", "overdue=4"]
    assert lines[2] == "'Brief diario'  repeat='mon-fri 09:15'  missed=2026-10-23 09:15  ref=memory:century-inc/atom_brief  id=78"
    assert lines[3] == "'Sueldo'  repeat='day 30 18:00'  missed=2026-10-30 18:00  id=79"
    assert lines[4] == "'Revisar PRs'  repeat='every 6h'  missed=2026-10-30 12:00  id=81"
    assert lines[5].startswith("'Call the bank'  due=2020-01-02 12:00  late=") and lines[5].endswith("id=80")
    assert not any("{" in line or line.count("Z") for line in lines[2:] if "late=" not in line)


def _tool(name: str, binary: str, **extra) -> None:
    upsert_memory(USER_WORKSPACE, name, type="user", body="why", description="run it for every push",
                  kind="tool", binary=binary, **extra)


def _executable(directory: Path, name: str, body: str = "#!/bin/sh\n") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(body)
    (directory / name).chmod(0o755)


@pytest.fixture
def path_world(tmp_path, monkeypatch):
    home = Path.home()
    system, own, scripts = tmp_path / "usr-bin", home / "bin", home / ".local" / "bin"
    for name in ("git", "ghx", "gone-tool"):
        _executable(system, name)
    _executable(system, "kubectl")
    _executable(own, "orphan")
    _executable(scripts, "pytest", "#!/usr/bin/python3\nimport re\nsys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', x)\n")
    monkeypatch.setenv("PATH", os.pathsep.join(str(d) for d in (scripts, own, system)))
    monkeypatch.setattr(session_manifest, "_resolve_gaia_cli_path", lambda: str(home / "ws" / "bin" / "gaia"))
    return system


def test_tools_are_the_catalog_plus_the_users_registered_binaries_on_both_surfaces(path_world):
    _tool("user_tool_ghx", binary="ghx")
    _executable(path_world, "not-installed")
    _tool("user_tool_absent", binary="not-installed")
    (path_world / "not-installed").unlink()

    birth = session_manifest.build_environment_section().splitlines()
    kernel = kernel_builder.build_environment_block()

    assert "tools=git,kubectl,ghx" in birth
    assert "tools on PATH: git, kubectl, ghx" in kernel
    tools_values = [line for line in birth if line.startswith("tools=")] + [kernel.split("tools on PATH: ")[1].splitlines()[0]]
    for surface in tools_values:
        assert "orphan" not in surface and "pytest" not in surface and "not-installed" not in surface


def test_a_registered_binary_that_is_in_the_catalog_is_listed_once(path_world):
    _tool("user_tool_git", binary="git")

    assert "tools=git,kubectl" in session_manifest.build_environment_section().splitlines()


def test_doctor_still_lists_an_authored_executable_that_has_no_tool_row(path_world):
    import cli.doctor as doctor

    _tool("user_tool_ghx", binary="ghx")

    assert "orphan" in str(doctor.check_user_tools())


def test_host_comes_from_the_hook_host_adapter_on_both_surfaces(path_world, monkeypatch):
    def birth_tokens() -> list:
        return session_manifest.build_environment_section().split()

    assert "host=claude-code" in birth_tokens()
    assert "host claude-code" in kernel_builder.build_environment_block()

    monkeypatch.setenv("GAIA_HOST", "opencode")

    assert "host=opencode" in birth_tokens()
    assert "host opencode" in kernel_builder.build_environment_block()


def test_cwd_shows_home_as_a_tilde_while_cli_stays_absolute(path_world, monkeypatch):
    home = Path.home()
    (home / "ws").mkdir(exist_ok=True)
    monkeypatch.chdir(home / "ws")
    lines = session_manifest.build_environment_section().splitlines()

    assert "cwd=~/ws" in lines
    assert f"cli={home}/ws/bin/gaia" in lines

    monkeypatch.chdir(home)
    assert "cwd=~" in session_manifest.build_environment_section().splitlines()

    monkeypatch.chdir(path_world)
    assert f"cwd={path_world}" in session_manifest.build_environment_section().splitlines()


def test_the_environment_section_renders_verbatim(path_world, monkeypatch, santiago):
    from gaia.paths import data_dir, db_path

    home = Path.home()
    (home / "ws").mkdir(exist_ok=True)
    monkeypatch.chdir(home / "ws")
    monkeypatch.setattr(session_manifest.platform, "node", lambda: "aaxis-env")
    monkeypatch.setattr(session_manifest.platform, "system", lambda: "Linux")
    monkeypatch.setattr(session_manifest.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(session_manifest.platform, "release", lambda: "6.6.1-microsoft-standard-WSL2")
    monkeypatch.setattr(session_manifest, "_scan_live_gaia_installation", lambda: None)
    monkeypatch.setattr(session_manifest, "_read_gaia_version", lambda: "5.6.0")
    _tool("user_tool_ghx", binary="ghx", replaces="gh")

    assert session_manifest.build_environment_section("sess-1234") == "\n".join([
        "## Environment",
        "machine=aaxis-env os=Linux arch=x86_64 wsl=yes host=claude-code",
        "gaia=5.6.0",
        f"cli={home}/ws/bin/gaia",
        "cwd=~/ws",
        "session=sess-1234",
        "zone=America/Santiago",
        f"data={data_dir()} db={db_path()}",
        "tools=git,kubectl,ghx",
        'tool=ghx "run it for every push" replaces=gh ref=memory:user/user_tool_ghx',
    ])


def test_session_preview_carries_the_host_session_when_none_is_named(path_world, monkeypatch, capsys):
    import cli.session as session_cli

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-1234")
    session_cli._cmd_preview(argparse.Namespace(session_id=None))

    assert "session=sess-1234" in capsys.readouterr().out.splitlines()


def test_a_named_session_wins_over_the_host_session(path_world, monkeypatch, capsys):
    import cli.session as session_cli

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "from-env")
    session_cli._cmd_preview(argparse.Namespace(session_id="named"))

    out = capsys.readouterr().out
    assert "session=named" in out and "from-env" not in out


def _package(root: Path, version: str = "5.6.0") -> Path:
    (root / "bin").mkdir(parents=True)
    (root / "package.json").write_text(
        json.dumps({"name": "@jaguilar87/gaia", "version": version, "bin": {"gaia": "bin/gaia"}})
    )
    (root / "bin" / "gaia").write_text("#!/usr/bin/env python3\n")
    (root / "bin" / "gaia").chmod(0o755)
    return root / "bin" / "gaia"


def test_a_cli_run_from_the_plugin_cache_reports_the_plugin_not_the_workspace_npm_install(tmp_path, monkeypatch):
    """`gaia session preview` has no CLAUDE_PLUGIN_ROOT; the package it runs from says which channel runs."""
    from gaia.paths import cache_dir

    workspace = tmp_path / "ws"
    npm_bin = _package(workspace / "node_modules" / "@jaguilar87" / "gaia", "5.5.0")
    plugin_root = cache_dir() / "dev-plugin" / "ws"
    plugin_bin = _package(plugin_root)
    con = _connect()
    con.execute("INSERT INTO workspaces (name, root_path, status) VALUES ('ws', ?, 'active')", (str(workspace),))
    con.commit()
    con.close()
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(session_manifest, "_own_package_root", lambda: plugin_root)
    monkeypatch.setattr(session_manifest, "_read_gaia_version", lambda: "5.6.0")

    lines = session_manifest.build_environment_section().splitlines()

    assert f"cli={plugin_bin}" in lines
    assert "gaia=5.6.0 channel=plugin" in lines
    assert str(npm_bin) not in "\n".join(lines)
    assert session_manifest._resolve_gaia_cli_path() == str(plugin_bin)


def _seed_scope() -> dict:
    con = _connect()
    con.execute("INSERT OR IGNORE INTO workspaces (name, root_path, status) VALUES ('me', '/tmp/me', 'active')")
    con.execute(
        "INSERT OR IGNORE INTO projects (workspace, name, path, status, group_name, project_identity) "
        "VALUES ('me', 'gaia', '/tmp/me/gaia', 'active', 'tools', 'github.com/metraton/gaia')"
    )
    con.commit()
    con.close()
    return {"workspace": "me", "dispatch_project": "gaia"}


def test_a_failed_scope_read_is_logged_and_marked_not_rendered_as_an_empty_scope(caplog):
    row = _seed_scope()
    from gaia.paths import db_path

    caplog.set_level(logging.WARNING)

    block = kernel_builder.build_memory_block(row, db_path=str(db_path()))

    assert block == "scope memory unavailable: AttributeError"
    assert any(r.levelno >= logging.WARNING and "scope memory" in r.getMessage() for r in caplog.records)


def test_a_failed_scope_chain_read_is_marked_too(monkeypatch, caplog):
    row = _seed_scope()
    caplog.set_level(logging.WARNING)

    def _boom(*_args, **_kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr(kernel_builder, "_connect", _boom)

    assert kernel_builder.build_memory_block(row) == "scope memory unavailable: OSError"
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_an_empty_scope_stays_empty_and_silent(caplog):
    row = _seed_scope()
    caplog.set_level(logging.WARNING)

    assert kernel_builder.build_memory_block(row) == ""
    assert not caplog.records
