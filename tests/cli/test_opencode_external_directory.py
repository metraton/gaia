"""Pin what OpenCode subagents reach outside the project, and the search rule that keeps it cheap."""

from __future__ import annotations

import inspect
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin"))

from cli import _install_helpers  # noqa: E402
from tests.cli.test_opencode_orchestrator_access import _permission_decision  # noqa: E402

# OpenCode 1.18.32 (agent.ts) starts every agent from an "ask" wildcard and
# merges the agent's configured rules over it; this is that first layer.
_HOST_DEFAULT = {"external_directory": {"*": "ask"}}


def _generated() -> dict:
    policy = json.loads((ROOT / "opencode" / "agent-policy.json").read_text())
    return _install_helpers._opencode_agents(ROOT, policy, None)


def _subagent_rules(generated: dict) -> dict[str, dict]:
    rules = {
        name: agent["permission"]["external_directory"]
        for name, agent in generated.items()
        if agent["mode"] == "subagent"
    }
    assert rules, "the shipped inventory generates no subagent"
    return rules


def _isolate_storage(tmp_path: Path, monkeypatch, relocate: bool) -> Path:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("GAIA_DB", str(tmp_path / "db" / "gaia.db"))
    if relocate:
        monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "relocated"))
        return tmp_path / "relocated"
    monkeypatch.delenv("GAIA_DATA_DIR", raising=False)
    return home / ".gaia"


def _set_os_tmp(monkeypatch, value: str) -> None:
    for name in ("TMP", "TEMP"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TMPDIR", value)


def test_opencode_external_directory_subagents_list_read_and_search_anywhere_without_a_prompt():
    home = Path.home().as_posix()
    # The ask pattern OpenCode builds is "<directory>/*" for the path a tool touches.
    asked = [
        "/home/jorge/*",
        f"{home}/*",
        f"{home}/.config/*",
        f"{home}/.config/opencode/*",
        f"{home}/ws/century-inc/platform/*",
        "/home/jorge/ws/century-inc/gitops/clusters/*",
    ]
    for name, rules in _subagent_rules(_generated()).items():
        for pattern in asked:
            layer = {"external_directory": rules}
            assert _permission_decision("external_directory", pattern, _HOST_DEFAULT, layer) == "allow", (
                name, pattern,
            )


@pytest.mark.parametrize("relocate", [False, True])
def test_opencode_external_directory_names_gaia_roots_from_gaia_paths(tmp_path, monkeypatch, relocate):
    data = _isolate_storage(tmp_path, monkeypatch, relocate)
    _set_os_tmp(monkeypatch, str(tmp_path / "os-tmp"))

    for name, rules in _subagent_rules(_generated()).items():
        named = {pattern: action for pattern, action in rules.items() if pattern != "*"}
        for folder in ("scratch", "evidence", "tmp", "worktrees"):
            root = (data / folder).resolve().as_posix()
            assert named.get(f"{root}/**") == "allow", (name, folder, named)
            assert _permission_decision(
                "external_directory", f"{root}/deep/dir/*", _HOST_DEFAULT, {"external_directory": named},
            ) == "allow"
        assert "~/.gaia/scratch/**" not in rules
        if relocate:
            stale = (tmp_path / "home" / ".gaia").as_posix()
            assert not any(pattern.startswith(stale) for pattern in named), named


def test_opencode_external_directory_gives_back_opencodes_own_temp(tmp_path, monkeypatch):
    _isolate_storage(tmp_path, monkeypatch, relocate=False)
    os_tmp = tmp_path / "os-tmp"
    _set_os_tmp(monkeypatch, str(os_tmp))

    for name, rules in _subagent_rules(_generated()).items():
        named = {pattern: action for pattern, action in rules.items() if pattern != "*"}
        assert _permission_decision(
            "external_directory", f"{os_tmp.as_posix()}/opencode/tool-output/*",
            _HOST_DEFAULT, {"external_directory": named},
        ) == "allow", (name, named)


def test_opencode_external_directory_ignores_the_dispatch_tmpdir_of_an_installing_agent(tmp_path, monkeypatch):
    data = _isolate_storage(tmp_path, monkeypatch, relocate=True)
    dispatch_tmp = data / "tmp" / "0123456789ab"
    _set_os_tmp(monkeypatch, str(dispatch_tmp))

    for name, rules in _subagent_rules(_generated()).items():
        assert rules.get("/tmp/opencode/**") == "allow", (name, rules)
        assert f"{dispatch_tmp.as_posix()}/opencode/**" not in rules


def test_opencode_external_directory_orchestrator_policy_is_unchanged(tmp_path, monkeypatch):
    data = _isolate_storage(tmp_path, monkeypatch, relocate=True)
    generated = _generated()
    orchestrator = generated["gaia-orchestrator"]["permission"]["external_directory"]

    assert orchestrator == {
        "*": "deny",
        f"{(data / 'scratch').resolve().as_posix()}/**": "allow",
        f"{(data / 'evidence').resolve().as_posix()}/**": "allow",
    }
    outside = f"{(tmp_path / 'home').as_posix()}/ws/other/*"
    assert _permission_decision(
        "external_directory", outside, _HOST_DEFAULT, {"external_directory": orchestrator},
    ) == "deny"
    for name, rules in _subagent_rules(generated).items():
        assert _permission_decision(
            "external_directory", outside, _HOST_DEFAULT, {"external_directory": rules},
        ) == "allow", name


def test_opencode_external_directory_installer_comment_states_the_prompt_is_native():
    source = inspect.getsource(_install_helpers._opencode_agents)
    prose = " ".join(line.strip().lstrip("#").strip() for line in source.splitlines())

    assert "permission.ask" not in prose, "the installer still credits the plugin's permission.ask hook"
    assert not re.search(r"(?<!never )reaches the plugin", prose)
    assert "never reaches the plugin" in prose
    assert "1.18.32" in prose


@pytest.mark.parametrize("relative", [
    "skills/investigation/SKILL.md",
    "skills/command-execution/SKILL.md",
    "agents/gaia-system.md",
])
def test_opencode_external_directory_search_never_starts_from_home_or_root(relative):
    paragraphs = [
        re.sub(r"\s+", " ", paragraph)
        for paragraph in re.split(r"\n\s*\n", (ROOT / relative).read_text())
    ]
    rule = [
        paragraph for paragraph in paragraphs
        if re.search(r"\bnever search\b", paragraph, re.IGNORECASE)
        and "(`~`)" in paragraph
        and "`/`" in paragraph
        and "`gaia paths`" in paragraph
        and "`which`" in paragraph
    ]
    assert rule, f"{relative} does not forbid searching from ~ or / and name where to look instead"
