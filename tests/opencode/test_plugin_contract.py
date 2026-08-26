"""Static contract checks for the packaged OpenCode plugin."""

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def test_plugin_learns_identity_from_real_message_events():
    source = (PACKAGE_ROOT / "opencode" / "plugin.ts").read_text()

    assert 'event.type === "message.updated"' in source
    assert "agentBySession.set(info.sessionID, info.agent)" in source
    assert "call.agent" not in source


def test_plugin_uses_opencode_named_function_export_contract():
    source = (PACKAGE_ROOT / "opencode" / "plugin.ts").read_text()

    assert "export const GaiaOpenCodePlugin = async" in source
    assert 'export default {\n  id: "gaia",\n  server: GaiaOpenCodePlugin,\n}' in source


def test_opencode_loader_prefers_usable_default_and_has_a_narrow_named_fallback():
    import json
    import subprocess

    plugin = PACKAGE_ROOT / "opencode" / "plugin.ts"
    script = f'''
      const module = await import({json.dumps(str(plugin))})
      function selectLoaderExport(candidate) {{
        if (candidate.default && typeof candidate.default.server === "function") {{
          return {{ path: "default", server: candidate.default.server }}
        }}
        if (typeof candidate.GaiaOpenCodePlugin === "function") {{
          return {{ path: "named", server: candidate.GaiaOpenCodePlugin }}
        }}
        throw new Error("no usable Gaia OpenCode plugin export")
      }}
      const preferred = selectLoaderExport(module)
      const fallback = selectLoaderExport({{ GaiaOpenCodePlugin: async () => ({{}}) }})
      let rejected = false
      try {{
        selectLoaderExport({{ default: {{ id: "gaia", server: "not-callable" }}, GaiaOpenCodePlugin: {{}} }})
      }} catch {{
        rejected = true
      }}
      console.log(JSON.stringify({{
        preferred: {{ path: preferred.path, sameServer: preferred.server === module.GaiaOpenCodePlugin }},
        fallback: fallback.path,
        rejected,
      }}))
    '''
    result = subprocess.run(["bun", "-e", script], text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == {
        "preferred": {"path": "default", "sameServer": True},
        "fallback": "named",
        "rejected": True,
    }


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
