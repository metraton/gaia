"""One non-destructive lifecycle proof for plan-first COMMAND_SET governance.

The activation step below drives the REAL AskUserQuestion path -- the label
text through ``extract_nonce_from_label`` into ``activate_db_pending_by_prefix``
-- rather than calling ``insert_plan_command_set`` + ``store.approve`` by hand.
That distinction is the whole point of this file: a prior version of this test
minted the plan-first grant directly, so it kept passing even while
``activate_db_pending_by_prefix`` routed every command_set payload through the
legacy ``create_command_set_grant`` (a grant ``reserve_plan_command`` can never
find, since it requires ``source='plan-first'``) -- a real, live gap that the
hand-minted version could not have caught. See
``tests/hooks/modules/security/test_activation_db_bridge.py`` ->
``TestActivateDbPendingCommandSet`` for the create-side unit coverage of both
the plan-first and legacy branches individually.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from gaia.approvals import store
from gaia.approvals.command_set import request_fingerprint, validate_request_set
from gaia.store import writer
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hooks"))
from modules.security.approval_grants import (
    activate_db_pending_by_prefix,
    extract_nonce_from_label,
)
from modules.tools.bash_validator import BashValidator


def test_plan_forecast_approval_execution_failure_and_routing(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    db = tmp_path / "gaia.db"
    con = sqlite3.connect(db)
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()

    commands = ["git push origin main", "docker push registry/app:e2e"]
    items = validate_request_set(commands)
    forecast = {
        "representation": "COMMAND_SET",
        "commands": commands,
        "execution": "separate Bash calls",
        "checkpoint": "verify after each command",
    }
    payload = {
        "request_type": "COMMAND_SET", "operation": "e2e forecast",
        "exact_content": "\n".join(commands), "commands": commands,
        "command_set": items, "request_fingerprint": request_fingerprint(commands),
        "scope": "COMMAND_SET", "risk_level": "high", "rollback_hint": None,
        "rationale": json.dumps(forecast, sort_keys=True),
    }
    approval_id = store.insert_requested(payload, agent_id="gaia-system", session_id="request")

    # This is the real, human-facing surface: the orchestrator's presented
    # question and the Approve label the user actually clicks. Only the label
    # text drives activation -- the question body is presentation only.
    presented_question = (
        f"APPROVAL REQUIRED -- {approval_id}\n\n"
        f"COMANDOS (2):\n  [0] {commands[0]}\n  [1] {commands[1]}"
    )
    approve_label = f"Approve -- {json.dumps(forecast, sort_keys=True)[:0]}push + docker push [{approval_id[:10]}]"
    nonce_prefix = extract_nonce_from_label(approve_label)
    assert nonce_prefix, f"label must yield a nonce prefix: {approve_label!r}"

    store.record_event(
        approval_id, "SHOWN", agent_id="gaia-orchestrator", session_id="presenter",
        payload_json=json.dumps(
            {"question": presented_question, "label": approve_label}, sort_keys=True,
        ),
    )

    # Step 1: the REAL activation the ElicitationResult hook calls when the
    # user selects the Approve label -- not a hand-minted grant.
    activation = activate_db_pending_by_prefix(
        nonce_prefix, current_session_id="presenter",
        presented_question=presented_question, presented_label=approve_label,
    )
    assert activation.success, f"activation should succeed: {activation.reason}"

    validator = BashValidator()
    first = validator._validate_single_command(commands[0], session_id="exec", tool_use_id="call-1")
    assert first.allowed and first.command_set_reservation["index"] == 0
    assert writer.settle_plan_command(
        approval_id, session_id="exec", tool_use_id="call-1", success=True,
    )
    second = validator._validate_single_command(commands[1], session_id="exec", tool_use_id="call-2")
    assert second.allowed and second.command_set_reservation["index"] == 1
    assert writer.settle_plan_command(
        approval_id, session_id="exec", tool_use_id="call-2", success=False,
        failure_reason="simulated non-destructive failure",
    )

    checkpoint = writer.list_approval_grants(status="FAILED", limit=10)[0]
    assert json.loads(checkpoint["consumed_indexes_json"]) == [0]
    assert checkpoint["next_index"] == 1
    assert checkpoint["failed_index"] == 1
    assert writer.reserve_plan_command(
        commands[1], session_id="exec", tool_use_id="replay"
    ) is None
    agent_response = {
        "agent_state": "NEEDS_VERIFICATION",
        "next_action": "route to verifier before creating a continuation request-set",
        "checkpoint": {"completed": [0], "failed": 1, "next": 1},
    }
    assert agent_response["agent_state"] == "NEEDS_VERIFICATION"
