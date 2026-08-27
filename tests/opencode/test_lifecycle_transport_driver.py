"""Gate 1009 (task 536, T8): the plugin's ``event`` handler forwards the
lifecycle transport set to Gaia's bridge, not only ``message.updated``, and
preserves the parent binding fields (callID, state.metadata.sessionId).

Driven through the real ``GaiaOpenCodePlugin`` closure under bun, with a
recording ``gaiaBridge`` stub -- this checks what the plugin sends, never
bridge.py's own routing (see test_lifecycle_transport_gate.py for that).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
DRIVER = _ROOT / "tests" / "opencode" / "lifecycle_transport_driver.ts"


def _drive() -> dict:
    result = subprocess.run(
        ["bun", str(DRIVER)], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_event_handler_forwards_the_lifecycle_conjunto_not_only_message_updated():
    driven = _drive()
    types = [request["event"] for request in driven["requests"]]

    assert types == [
        "message.part.updated",
        "session.idle",
        "session.error",
        "session.deleted",
        "session.compacted",
    ]


def test_event_handler_preserves_the_parent_binding_fields():
    driven = _drive()
    binding = driven["requests"][0]

    assert binding["event"] == "message.part.updated"
    assert binding["sessionID"] == "ses-parent"
    assert binding["callID"] == "call-dispatch-1"
    assert binding["state"]["metadata"]["sessionId"] == "ses-child-1"
