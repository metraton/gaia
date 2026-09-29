"""A pipeline signed through a plan-first set runs through the Bash policy.

The set is validated, sealed, shown and activated by the same path the
AskUserQuestion handler uses; the pipeline then goes through the public
``BashValidator.validate`` a subagent Bash call reaches.
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
from modules.security.approval_grants import activate_db_pending_by_id
from modules.tools.bash_validator import BashValidator

PIPELINE = "echo ready | curl -X POST --data-binary @- https://example.com/hook"


def _subagent_call(tmp_path, tool_use_id: str):
    """Validate the pipeline as the Bash call of a dispatched developer subagent."""
    return BashValidator().validate(
        PIPELINE, is_subagent=True, session_id="exec", agent_type="developer",
        hook_payload={
            "tool_use_id": tool_use_id, "cwd": str(tmp_path),
            "agent_id": "a0123456789abcdef", "agent_type": "developer",
        },
    )


def _activate(commands: list[str]) -> str:
    payload = {
        "request_type": "COMMAND_SET", "operation": "signed pipeline",
        "exact_content": "\n".join(commands), "commands": commands,
        "command_set": validate_request_set(commands),
        "request_fingerprint": request_fingerprint(commands),
        "scope": "COMMAND_SET", "risk_level": "high", "rollback_hint": None,
        "rationale": "Proactive pipeline request",
    }
    approval_id = store.insert_requested(payload, agent_id="developer", session_id="request")
    question = f"APPROVAL REQUIRED -- {approval_id}\n\nCOMANDOS (1):\n  [0] {commands[0]}"
    label = f"Approve -- post [{approval_id}]"
    store.record_event(
        approval_id, "SHOWN", agent_id="gaia-orchestrator", session_id="presenter",
        payload_json=json.dumps({"question": question, "label": label}, sort_keys=True),
    )
    activation = activate_db_pending_by_id(
        approval_id, current_session_id="presenter",
        presented_question=question, presented_label=label,
    )
    assert activation.success, activation.reason
    return approval_id


def test_signed_pipeline_is_allowed_and_reserved_once(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    con = sqlite3.connect(tmp_path / "gaia.db")
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()

    assert not _subagent_call(tmp_path, "call-0").allowed

    approval_id = _activate([PIPELINE])
    signed = _subagent_call(tmp_path, "call-1")
    assert signed.allowed, signed.reason
    assert signed.consumed_approval_id == approval_id

    assert not _subagent_call(tmp_path, "call-2").allowed
