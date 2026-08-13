"""Tests for the non-invasive OpenCode installation surface."""

from __future__ import annotations

import json
import sys
from pathlib import Path


_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli import _install_helpers


def test_registers_the_packaged_plugin_without_creating_claude_config(tmp_path):
    package = tmp_path / "package"
    plugin = package / "opencode" / "plugin.ts"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("export {}\n")
    (plugin.parent / "agent-policy.json").write_text(
        '{"default": {"mode": "subagent"}, "gaia-orchestrator": {"mode": "primary"}}\n'
    )
    agent = package / "agents" / "gaia-orchestrator.md"
    agent.parent.mkdir()
    agent.write_text("---\nname: gaia-orchestrator\ndescription: Routes work\n---\nPrompt\n")
    skill = package / "skills" / "sample" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: sample\ndescription: Sample\n---\n")

    result = _install_helpers.configure_opencode_plugin(tmp_path, package)

    assert result["action"] == "updated"
    assert not (tmp_path / ".claude").exists()
    config = json.loads((tmp_path / "opencode.json").read_text())
    assert str(plugin.resolve()) in config["plugin"]
    assert config["default_agent"] == "gaia-orchestrator"
    assert config["agent"]["gaia-orchestrator"]["mode"] == "primary"
    assert (tmp_path / ".opencode" / "skills" / "sample").is_symlink()


def test_preserves_existing_opencode_plugins(tmp_path):
    package = tmp_path / "package"
    plugin = package / "opencode" / "plugin.ts"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("export {}\n")
    (plugin.parent / "agent-policy.json").write_text('{"default": {"mode": "subagent"}}\n')
    agent = package / "agents" / "gaia-orchestrator.md"
    agent.parent.mkdir()
    agent.write_text("---\nname: gaia-orchestrator\ndescription: Routes work\n---\nPrompt\n")
    skill = package / "skills" / "sample" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: sample\ndescription: Sample\n---\n")
    config = tmp_path / "opencode.json"
    config.write_text('{"plugin": ["./user-plugin.ts"]}\n')

    _install_helpers.configure_opencode_plugin(tmp_path, package)

    text = config.read_text()
    assert "./user-plugin.ts" in text
    assert str(plugin.resolve()) in text


def test_second_install_is_idempotent(tmp_path):
    package = tmp_path / "package"
    plugin = package / "opencode" / "plugin.ts"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("export {}\n")
    (plugin.parent / "agent-policy.json").write_text('{"default": {"mode": "subagent"}}\n')
    agent = package / "agents" / "gaia-orchestrator.md"
    agent.parent.mkdir()
    agent.write_text("---\nname: gaia-orchestrator\ndescription: Routes work\n---\nPrompt\n")
    skill = package / "skills" / "sample" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: sample\ndescription: Sample\n---\n")

    _install_helpers.configure_opencode_plugin(tmp_path, package)
    result = _install_helpers.configure_opencode_plugin(tmp_path, package)

    assert result["action"] == "noop"


def test_uses_stable_workspace_package_link_instead_of_pnpm_store(tmp_path):
    store_package = (
        tmp_path
        / "node_modules"
        / ".pnpm"
        / "gaia-v1"
        / "node_modules"
        / "@jaguilar87"
        / "gaia"
    )
    plugin = store_package / "opencode" / "plugin.ts"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("export {}\n")
    (plugin.parent / "agent-policy.json").write_text(
        '{"default": {"mode": "subagent"}}\n'
    )
    agent = store_package / "agents" / "gaia-orchestrator.md"
    agent.parent.mkdir()
    agent.write_text(
        "---\nname: gaia-orchestrator\ndescription: Routes work\n---\nPrompt\n"
    )
    skill = store_package / "skills" / "sample" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: sample\ndescription: Sample\n---\n")
    stable_package = tmp_path / "node_modules" / "@jaguilar87" / "gaia"
    stable_package.parent.mkdir(parents=True)
    stable_package.symlink_to(store_package, target_is_directory=True)

    _install_helpers.configure_opencode_plugin(tmp_path, store_package)

    config = json.loads((tmp_path / "opencode.json").read_text())
    stable_root = str(stable_package.absolute())
    assert config["plugin"] == [f"{stable_root}/opencode/plugin.ts"]
    assert config["agent"]["gaia-orchestrator"]["prompt"] == (
        f"{{file:{stable_root}/agents/gaia-orchestrator.md}}"
    )
    assert ".pnpm" not in config["plugin"][0]
    assert ".pnpm" not in config["agent"]["gaia-orchestrator"]["prompt"]
    assert (tmp_path / ".opencode" / "skills" / "sample").readlink() == (
        stable_package / "skills" / "sample"
    )


def test_translates_agent_tools_disallowed_tools_and_skills(tmp_path):
    package = tmp_path / "package"
    agent = package / "agents" / "developer.md"
    agent.parent.mkdir(parents=True)
    agent.write_text(
        "---\n"
        "name: developer\n"
        "description: Builds applications\n"
        "tools: Read, Edit, Bash, Skill\n"
        "disallowedTools: [Bash]\n"
        "skills:\n"
        "  - agent-protocol\n"
        "  - coding-standards\n"
        "---\nPrompt\n"
    )

    generated = _install_helpers._opencode_agents(
        package, {"default": {"mode": "subagent"}}, None
    )["developer"]

    assert generated["permission"] == {
        "*": "deny",
        "read": "allow",
        "edit": "allow",
        "bash": "deny",
        "skill": {"*": "allow"},
    }


def test_host_policy_overrides_frontmatter_permissions(tmp_path):
    package = tmp_path / "package"
    agent = package / "agents" / "gaia-orchestrator.md"
    agent.parent.mkdir(parents=True)
    agent.write_text(
        "---\nname: gaia-orchestrator\ndescription: Routes work\n"
        "tools: Read, Bash\n---\nPrompt\n"
    )

    generated = _install_helpers._opencode_agents(
        package,
        {
            "default": {"mode": "subagent"},
            "gaia-orchestrator": {
                "mode": "primary",
                "permission": {"bash": {"*": "deny", "gaia *": "allow"}},
            },
        },
        None,
    )["gaia-orchestrator"]

    assert generated["permission"]["read"] == "allow"
    assert generated["permission"]["bash"] == {"*": "deny", "gaia *": "allow"}


def test_orchestrator_task_policy_is_closed_and_nominal():
    policy = json.loads((_install_helpers._PACKAGE_ROOT / "opencode" / "agent-policy.json").read_text())
    task = policy["gaia-orchestrator"]["permission"]["task"]
    assert task["*"] == "deny"
    assert task["gaia-system"] == "allow"
    assert task["developer"] == "allow"
    assert "gaia-orchestrator" not in task


def test_replaces_stale_gaia_plugin_but_preserves_foreign_plugin(tmp_path):
    package = tmp_path / "package"
    plugin = package / "opencode" / "plugin.ts"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("export {}\n")
    (plugin.parent / "agent-policy.json").write_text('{"default": {"mode": "subagent"}}\n')
    agent = package / "agents" / "gaia-orchestrator.md"
    agent.parent.mkdir()
    agent.write_text("---\nname: gaia-orchestrator\ndescription: Routes work\n---\n")
    skill = package / "skills" / "sample" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: sample\ndescription: Sample\n---\n")
    config = tmp_path / "opencode.json"
    config.write_text(json.dumps({"plugin": [
        "/old/node_modules/@jaguilar87/gaia/opencode/plugin.ts",
        "./user-plugin.ts",
    ]}))

    _install_helpers.configure_opencode_plugin(tmp_path, package)

    plugins = json.loads(config.read_text())["plugin"]
    assert plugins == ["./user-plugin.ts", str(plugin.resolve())]


def test_only_portable_provider_model_is_emitted(tmp_path):
    package = tmp_path / "package"
    agents = package / "agents"
    agents.mkdir(parents=True)
    (agents / "portable.md").write_text("---\nname: portable\ndescription: P\nmodel: openai/gpt-5\n---\n")
    (agents / "alias.md").write_text("---\nname: alias\ndescription: A\nmodel: sonnet\neffort: high\npermissionMode: acceptEdits\n---\n")
    generated = _install_helpers._opencode_agents(package, {"default": {"mode": "subagent"}}, None)
    assert generated["portable"]["model"] == "openai/gpt-5"
    assert "model" not in generated["alias"]
    assert "options" not in generated["alias"]
