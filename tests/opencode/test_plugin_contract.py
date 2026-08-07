"""Static contract checks for the packaged OpenCode plugin."""

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def test_plugin_learns_identity_from_real_message_events():
    source = (PACKAGE_ROOT / "opencode" / "plugin.ts").read_text()

    assert 'event.type === "message.updated"' in source
    assert "agentBySession.set(info.sessionID, info.agent)" in source
    assert "call.agent" not in source


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
