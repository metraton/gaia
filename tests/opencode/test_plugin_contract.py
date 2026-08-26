"""Static contract checks for the packaged OpenCode plugin."""

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def test_plugin_learns_identity_from_real_message_events():
    source = (PACKAGE_ROOT / "opencode" / "plugin.ts").read_text()

    assert 'event.type === "message.updated"' in source
    assert "agentBySession.set(info.sessionID, info.agent)" in source
    assert "call.agent" not in source


def test_plugin_uses_opencode_named_function_export_contract():
    """Mirrors the install guard's own predicate (_install_helpers.py::
    configure_opencode_plugin): a callable named GaiaOpenCodePlugin export is
    required, and a default object export is rejected -- per e9468d8 and the
    official OpenCode docs (opencode.ai/docs/plugins/), which document only
    the named-async-function export, never `export default`."""
    source = (PACKAGE_ROOT / "opencode" / "plugin.ts").read_text()

    assert "export const GaiaOpenCodePlugin = async" in source
    assert "export default" not in source


def test_opencode_loader_resolves_the_named_export_with_no_default_present():
    import json
    import subprocess

    plugin = PACKAGE_ROOT / "opencode" / "plugin.ts"
    script = f'''
      const module = await import({json.dumps(str(plugin))})
      console.log(JSON.stringify({{
        hasDefault: "default" in module,
        namedIsFunction: typeof module.GaiaOpenCodePlugin === "function",
      }}))
    '''
    result = subprocess.run(["bun", "-e", script], text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == {"hasDefault": False, "namedIsFunction": True}


def test_plugin_has_a_positive_process_liveness_signal():
    source = (PACKAGE_ROOT / "opencode" / "plugin.ts").read_text()

    assert 'const LIVENESS_PREFIX = "[gaia-opencode:liveness]"' in source
    assert "process.pid" in source
    assert "loaded_at" in source
    assert "host log failed" in source


def test_orchestrator_bash_is_restricted_to_gaia_cli():
    import json

    policy = json.loads((PACKAGE_ROOT / "opencode" / "agent-policy.json").read_text())

    assert policy["gaia-orchestrator"]["permission"]["bash"] == {
        "*": "deny",
        "gaia *": "allow",
    }


def test_plugin_preserves_bash_failure_signals_for_post_tool_policy():
    import json
    import subprocess
    plugin = PACKAGE_ROOT / "opencode" / "plugin.ts"
    script = f'import {{ toolResult }} from {json.dumps(str(plugin))}; console.log(JSON.stringify(toolResult({{output:"", metadata:{{exitCode:9}}}})))'
    result = subprocess.run(["bun", "-e", script], text=True, capture_output=True, check=True)
    assert json.loads(result.stdout)["exit_code"] == 9
