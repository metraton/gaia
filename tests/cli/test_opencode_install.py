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
    plugin.write_text(
        "export const GaiaOpenCodePlugin = async () => ({})\n"
        'export default { id: "gaia", server: GaiaOpenCodePlugin }\n'
    )
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


def test_rejects_a_plugin_export_shape_that_would_fail_at_host_load(tmp_path):
    package = tmp_path / "package"
    plugin = package / "opencode" / "plugin.ts"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("export default { id: 'gaia' }\n")
    (plugin.parent / "agent-policy.json").write_text(
        '{"default": {"mode": "subagent"}}\n'
    )

    result = _install_helpers.configure_opencode_plugin(tmp_path, package)

    assert result["action"] == "error"
    assert "no callable GaiaOpenCodePlugin named export" in result["details"]
    assert not (tmp_path / "opencode.json").exists()


def test_preserves_existing_opencode_plugins(tmp_path):
    package = tmp_path / "package"
    plugin = package / "opencode" / "plugin.ts"
    plugin.parent.mkdir(parents=True)
    plugin.write_text(
        "export const GaiaOpenCodePlugin = async () => ({})\n"
        'export default { id: "gaia", server: GaiaOpenCodePlugin }\n'
    )
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
    plugin.write_text(
        "export const GaiaOpenCodePlugin = async () => ({})\n"
        'export default { id: "gaia", server: GaiaOpenCodePlugin }\n'
    )
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
    plugin.write_text(
        "export const GaiaOpenCodePlugin = async () => ({})\n"
        'export default { id: "gaia", server: GaiaOpenCodePlugin }\n'
    )
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
        "  - code-standards\n"
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


def test_gaia_system_alone_reaches_only_canonical_scratch(tmp_path, monkeypatch):
    data_root = tmp_path / "gaia-data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_root))
    package = _install_helpers._PACKAGE_ROOT
    policy = json.loads((package / "opencode" / "agent-policy.json").read_text())

    generated = _install_helpers._opencode_agents(package, policy, None)
    scratch = str((data_root / "scratch").resolve())

    assert generated["gaia-system"]["permission"]["external_directory"] == {
        "*": "deny",
        scratch: "allow",
        f"{scratch}/*": "allow",
    }
    for name, agent in generated.items():
        if name != "gaia-system":
            assert "external_directory" not in agent["permission"], name


def test_canonical_scratch_rules_fail_closed_for_escape_shapes(tmp_path, monkeypatch):
    data_root = tmp_path / "gaia-data"
    scratch = data_root / "scratch"
    sibling = data_root / "sibling"
    unrelated = tmp_path / "external"
    scratch.mkdir(parents=True)
    sibling.mkdir()
    unrelated.mkdir()
    (scratch / "escape").symlink_to(unrelated, target_is_directory=True)
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_root))

    rules = _install_helpers._canonical_scratch_permission()
    allowed_root = Path(next(path for path, action in rules.items() if action == "allow"))

    def reaches_scratch(candidate: Path) -> bool:
        resolved = candidate.resolve(strict=False)
        return resolved == allowed_root or allowed_root in resolved.parents

    matrix = {
        "exact": reaches_scratch(scratch),
        "child": reaches_scratch(scratch / "contract.file"),
        "sibling": reaches_scratch(sibling),
        "parent": reaches_scratch(data_root),
        "traversal": reaches_scratch(scratch / ".." / "sibling"),
        "symlink": reaches_scratch(scratch / "escape" / "payload"),
        "unrelated": reaches_scratch(unrelated),
    }
    print("OPENCODE_EXTERNAL_DIRECTORY_MATRIX=" + json.dumps(matrix, sort_keys=True))
    assert matrix == {
        "exact": True,
        "child": True,
        "sibling": False,
        "parent": False,
        "traversal": False,
        "symlink": False,
        "unrelated": False,
    }


def test_host_short_circuit_is_recorded_without_a_false_gaia_verdict():
    policy = json.loads(
        (_install_helpers._PACKAGE_ROOT / "opencode" / "agent-policy.json").read_text()
    )
    gap = policy["authority"]["host_short_circuit_gap"]

    print("HOST_DENY_OWNER=" + gap["owner"])
    print("HOST_DENY_GAIA_CONSULTED=" + str(gap["gaia_consulted"]).lower())
    print("HOST_DENY_GAIA_VERDICT=" + str(gap["gaia_verdict"]).lower())
    assert gap == {
        "status": "ACCEPTED_DOCUMENTED_GAP",
        "owner": "HOST",
        "gaia_consulted": False,
        "gaia_verdict": None,
        "surfaces": ["external_directory"],
    }


def test_generated_scratch_policy_reconciles_idempotently(tmp_path, monkeypatch):
    data_root = tmp_path / "gaia-data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_root))
    package = _install_helpers._PACKAGE_ROOT

    first = _install_helpers.configure_opencode_plugin(tmp_path, package)
    first_text = (tmp_path / "opencode.json").read_text()
    second = _install_helpers.configure_opencode_plugin(tmp_path, package)

    assert first["action"] == "updated"
    assert second["action"] == "noop"
    assert (tmp_path / "opencode.json").read_text() == first_text
    config = json.loads(first_text)
    scratch = str((data_root / "scratch").resolve())
    assert config["agent"]["gaia-system"]["permission"]["external_directory"] == {
        "*": "deny",
        scratch: "allow",
        f"{scratch}/*": "allow",
    }


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
    plugin.write_text(
        "export const GaiaOpenCodePlugin = async () => ({})\n"
        'export default { id: "gaia", server: GaiaOpenCodePlugin }\n'
    )
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
