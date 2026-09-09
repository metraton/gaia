"""Probe wrappers also execute the real issuer under the private state guard."""

import json
from pathlib import Path
import subprocess

import pytest

from tests.conftest import IsolatedRuntimeEnv


@pytest.mark.parametrize("wrapper", [
    "prompt_mutation_probe_wrapper.ts", "t9_kernel_prepend_probe_wrapper.ts",
])
def test_probe_wrapper_private_bridge(tmp_path, wrapper):
    env = IsolatedRuntimeEnv(tmp_path)
    env.prepare_hook_workspace()
    module = (Path(__file__).resolve().parent / wrapper).as_uri()
    script = (
        f"const {{default: wrapper}} = await import({json.dumps(module)});"
        "const plugin = await wrapper.server({directory: process.env.WORKSPACE});"
        "await plugin.event({event: {type: 'message.updated', properties: {info: {"
        "role: 'assistant', sessionID: 'ses-private-probe', agent: 'gaia-orchestrator'"
        "}}}});"
    )
    result = subprocess.run(
        ["bun", "-e", script], cwd=env["WORKSPACE"], env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, "private probe wrapper failed"
    assert len(list((tmp_path / "ledger").glob("*.json"))) == 1
