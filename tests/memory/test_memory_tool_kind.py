"""kind=tool: a program built for the user, verified at write, rendered in Environment, read by the classifier as an alias.

Runs in-process against the temporary HOME and GAIA_DATA_DIR the suite's
conftest provides; PATH is replaced per test so a binary is "detected" only
when the test put it there.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "bin", _REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from gaia.store import memory_placement as placement  # noqa: E402
from gaia.store.writer import USER_WORKSPACE, _connect, upsert_memory  # noqa: E402

_SCHEMA = _REPO_ROOT / "gaia" / "store" / "schema.sql"
_MIGRATION = _REPO_ROOT / "scripts" / "migrations" / "v71_to_v72.sql"
_KIND_LIST = re.compile(r"WHEN NEW\.kind NOT IN \(([^)]*)\)")
_USER_LIST = re.compile(r"NEW\.owner_level = 'user' AND NEW\.kind NOT IN \(([^)]*)\)")


@pytest.fixture(autouse=True)
def _direct_caller(monkeypatch):
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)


@pytest.fixture
def bin_dir(tmp_path, monkeypatch) -> Path:
    directory = tmp_path / "bin"
    directory.mkdir()
    monkeypatch.setenv("PATH", str(directory))
    return directory


def _executable(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def _tool(name: str, binary: str, title: str = "run it for every push", **extra) -> dict:
    return upsert_memory(USER_WORKSPACE, name, type="user", body="why it exists",
                         description=title, kind="tool", binary=binary, **extra)


def _lists(sql: str) -> set[tuple[str, ...]]:
    return {tuple(w.strip(" '") for w in m.group(1).split(",")) for m in _KIND_LIST.finditer(sql)}


def test_the_kind_enum_is_in_lockstep_in_code_schema_and_migration():
    assert "tool" in placement.MEMORY_KINDS
    assert placement.KINDS_BY_OWNER["user"] == ("fact", "preference", "tool")
    for sql in (_SCHEMA.read_text(encoding="utf-8"), _MIGRATION.read_text(encoding="utf-8")):
        assert _lists(sql) == {placement.MEMORY_KINDS}
        user_lists = {tuple(w.strip(" '") for w in m.group(1).split(",")) for m in _USER_LIST.finditer(sql)}
        assert user_lists == {placement.KINDS_BY_OWNER["user"]}
    for owner, kinds in placement.KINDS_BY_OWNER.items():
        assert ("tool" in kinds) == (owner == "user")


def test_a_tool_names_a_binary_on_path_and_replaces_only_a_modelled_cli(bin_dir):
    with pytest.raises(ValueError, match="names its program"):
        _tool("user_tool_nobinary", binary=None)
    with pytest.raises(ValueError, match="not on PATH"):
        _tool("user_tool_ghost", binary="ghost")
    _executable(bin_dir, "ghwrap")
    with pytest.raises(ValueError, match="classifier models"):
        _tool("user_tool_ghwrap", binary="ghwrap", replaces="no-such-cli-anywhere")
    res = _tool("user_tool_ghwrap", binary="ghwrap", replaces="gh")
    con = _connect()
    stored = con.execute("SELECT kind, owner_level, binary, replaces FROM memory WHERE name = ?",
                         ("user_tool_ghwrap",)).fetchone()
    con.close()
    assert (res["binary"], res["replaces"]) == ("ghwrap", "gh")
    assert tuple(stored) == ("tool", "user", "ghwrap", "gh")
    with pytest.raises(ValueError, match="kind=tool only"):
        upsert_memory(USER_WORKSPACE, "user_fact_with_binary", type="user", body="b", kind="fact",
                      binary="ghwrap")


def test_a_tool_title_is_capped_at_80_and_one_row_per_binary(bin_dir):
    _executable(bin_dir, "mytool")
    with pytest.raises(ValueError, match="at most 80 characters"):
        _tool("user_tool_long", binary="mytool", title="x" * 81)
    _tool("user_tool_mytool", binary="mytool", title="x" * 80)
    with pytest.raises(ValueError, match="already has a tool row"):
        _tool("user_tool_mytool_again", binary="mytool")
    with pytest.raises(ValueError, match="belongs to the user"):
        upsert_memory("me", "project_tool", type="project", body="b", kind="tool", binary="mytool",
                      project_ref="github.com/x/gaia")


def test_environment_renders_detected_tools_on_both_surfaces_from_one_renderer(bin_dir, tmp_path):
    from modules.context import kernel_builder, user_sections
    from modules.session import session_manifest

    for n in range(7):
        _executable(bin_dir, f"tool{n}")
        _tool(f"user_tool_tool{n}", binary=f"tool{n}", title=f"use tool{n} for step {n}",
              replaces="gh" if n == 0 else None)
    _executable(bin_dir, "gone")
    _tool("user_tool_gone", binary="gone", title="a tool whose binary left")
    (bin_dir / "gone").unlink()

    expected = 'tool=tool0 "use tool0 for step 0" replaces=gh ref=memory:user/user_tool_tool0'
    birth = session_manifest.build_environment_section()
    kernel = kernel_builder.build_environment_block()
    assert expected in birth.splitlines() and expected in kernel.splitlines()
    assert "gone" not in birth and "gone" not in kernel
    assert len([line for line in birth.splitlines() if line.startswith("tool=")]) == 7
    assert len([line for line in kernel.splitlines() if line.startswith("tool=")]) == kernel_builder.KERNEL_TOOL_LINES == 5
    assert user_sections.tool_lines is session_manifest.tool_lines is kernel_builder.tool_lines


def test_doctor_lists_absent_binaries_and_authored_executables_without_a_row(bin_dir, monkeypatch):
    import cli.doctor as doctor

    home_bin = Path.home() / "bin"
    home_bin.mkdir(parents=True, exist_ok=True)
    _executable(home_bin, "orphan")
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), str(home_bin)]))
    _executable(bin_dir, "ghost")
    _tool("user_tool_ghost", binary="ghost")
    (bin_dir / "ghost").unlink()

    result = str(doctor.check_user_tools())

    assert "warning" in result
    assert "ghost" in result and "user_tool_ghost" in result
    assert "orphan" in result


def test_the_cli_warns_when_a_user_fact_names_an_authored_executable():
    import cli.memory as memory_mod

    warnings = memory_mod._add_warnings(
        mem_type="user", description="Use orphan for every push.", body="b", owned=False,
        previous_body=None, kind="fact", authored=("orphan", "other"),
    )
    codes = {w["code"]: w["message"] for w in warnings}
    assert "tool_without_kind" in codes
    assert "--kind=tool --binary=orphan" in codes["tool_without_kind"]
    assert "tool_without_kind" not in {w["code"] for w in memory_mod._add_warnings(
        mem_type="user", description="Use orphan for every push.", body="b", owned=False,
        previous_body=None, kind="tool", authored=("orphan",),
    )}


def test_the_classifier_alias_comes_from_tool_rows_never_from_the_environment(bin_dir, monkeypatch):
    from modules.security import cli_aliases

    assert not hasattr(cli_aliases, "ENV_VAR")
    monkeypatch.setenv("GAIA_CLI_ALIASES", "envwrap=gh")
    _executable(bin_dir, "ghwrap")
    _tool("user_tool_ghwrap", binary="ghwrap", replaces="gh")
    cli_aliases.reset_cache()

    assert cli_aliases.aliased_cli("ghwrap") == "gh"
    assert cli_aliases.aliased_cli("envwrap") is None
    assert cli_aliases.declared_wrapper("FOO=1 ghwrap pr view 1") == ("ghwrap", "FOO=1 gh pr view 1")

    monkeypatch.setenv("GAIA_DB", str(bin_dir / "missing" / "no.db"))
    cli_aliases.reset_cache()
    assert cli_aliases.aliased_cli("ghwrap") is None


def _add_rc(capsys, *flags: str) -> tuple[int, str]:
    import cli.memory as memory_mod

    parser = argparse.ArgumentParser()
    memory_mod.register(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args(["memory", "add", "--type=user", "--name=user_fact_k", "--body=b", *flags])
    rc = args.func(args)
    captured = capsys.readouterr()
    return rc, captured.out + captured.err


def test_kind_is_required_and_closed_at_the_cli(capsys):
    rc, text = _add_rc(capsys)
    assert rc == 1
    assert "--kind is required" in text and "gaia map memory" in text
    for kind in placement.MEMORY_KINDS:
        assert kind in text
    rc, text = _add_rc(capsys, "--kind=rule")
    assert rc == 1
    assert "'rule' is not a kind" in text and "closed" in text


def test_map_memory_page_and_add_help_render_the_same_matrix():
    import cli.map as gaia_map
    import cli.memory as memory_mod

    page = gaia_map.PAGES["memory"]()
    parser = argparse.ArgumentParser()
    memory_mod.register(parser.add_subparsers(dest="cmd"))
    add_help = parser.parse_args(["memory", "add", "--help"]) if False else None  # noqa: F841
    help_text = memory_mod._add_parser_for_help().format_help()
    matrix = placement.owner_matrix_text()
    assert matrix in page and matrix in help_text
    for kind in placement.MEMORY_KINDS:
        assert kind in page
    for word in ("supersedes", "until", "tool=", "## User memory", "# Scope memory", "closed"):
        assert word in page, word
