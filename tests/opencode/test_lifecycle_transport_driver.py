"""Gate 1009 (task 536, T8): the plugin's ``event`` handler forwards the
lifecycle transport set to Gaia's bridge, not only ``message.updated``, and
preserves the parent binding fields (callID, state.metadata.sessionId).

Driven through the real plugin and issuer under bun, with recording stubs for
tool/lifecycle policy. This checks transport after an authorized dispatch;
bridge.py's lifecycle routing is tested in test_lifecycle_transport_gate.py.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.conftest import IsolatedRuntimeEnv

_ROOT = Path(__file__).resolve().parents[2]
DRIVER = _ROOT / "tests" / "opencode" / "lifecycle_transport_driver.ts"


def _drive(tmp_path) -> dict:
    env = IsolatedRuntimeEnv(tmp_path)
    env.prepare_hook_workspace()
    result = subprocess.run(
        ["bun", str(DRIVER)], cwd=env["WORKSPACE"], env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_event_handler_forwards_the_lifecycle_conjunto_not_only_message_updated(tmp_path):
    driven = _drive(tmp_path)
    types = [request["event"] for request in driven["requests"]]

    assert types == [
        "message.part.updated",
        "session.idle",
        "session.error",
        "session.deleted",
        "session.compacted",
    ]


def test_event_handler_preserves_the_parent_binding_fields(tmp_path):
    driven = _drive(tmp_path)
    binding = driven["requests"][0]

    assert binding["event"] == "message.part.updated"
    assert binding["sessionID"] == "ses-parent"
    assert binding["callID"] == "call-dispatch-1"
    assert binding["state"]["metadata"]["sessionId"] == "ses-child-1"
