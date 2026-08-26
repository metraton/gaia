"""Gate 929: one real protected-path request across every pending read surface."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"
BIN_DIR = REPO_ROOT / "bin"
for path in (REPO_ROOT, HOOKS_DIR, BIN_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _approval_request_envelope(approval_id: str, payload: dict) -> dict:
    return {
        "agent_status": {
            "agent_state": "APPROVAL_REQUEST",
            "agent_id": "a1234567890abcdef",
            "pending_steps": ["await consent"],
            "next_action": "awaiting user approval",
        },
        "evidence_report": {
            "patterns_checked": [],
            "files_checked": [],
            "commands_run": [],
            "key_outputs": [],
            "verbatim_outputs": [],
            "cross_layer_impacts": [],
            "open_gaps": [],
        },
        "consolidation_report": None,
        "approval_request": {
            "operation": payload["operation"],
            "exact_content": payload["exact_content"],
            "scope": payload["scope"],
            "risk_level": payload["risk_level"],
            "rollback": payload.get("rollback_hint"),
            "verification": payload.get("verification"),
            "approval_id": approval_id,
        },
    }


def _run_gaia(*args: str) -> dict | list:
    """Run the source-tree Gaia dispatcher and decode its JSON response."""
    completed = subprocess.run(
        [str(REPO_ROOT / "bin" / "gaia"), *args],
        cwd=REPO_ROOT,
        env=os.environ.copy(),
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _run_gaia_failure(*args: str) -> subprocess.CompletedProcess[str]:
    """Run a Gaia command expected to reject a noncanonical identity."""
    return subprocess.run(
        [str(REPO_ROOT / "bin" / "gaia"), *args],
        cwd=REPO_ROOT,
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )


def test_protected_path_pending_round_trips_as_one_opaque_machine_identity(
    tmp_path, monkeypatch
):
    """Producer, store, pending/list/show, and contract validation agree literally."""
    db_path = tmp_path / "gaia.db"
    monkeypatch.setenv("GAIA_DB", str(db_path))
    monkeypatch.delenv("GAIA_DATA_DIR", raising=False)

    from adapters.opencode import OpenCodeAdapter
    from gaia.approvals import store
    from gaia.contract.crosscheck import validate_crosscheck

    protected_path = REPO_ROOT / "hooks" / "modules" / "security" / "tiers.py"
    event = OpenCodeAdapter().parse_event(json.dumps({
        "event": "tool.execute.before",
        "sessionID": "session-gate-929",
        "callID": "call-gate-929",
        "agentID": "a1234567890abcdef",
        "agent": "gaia-system",
        "tool": "write",
        "args": {"file_path": str(protected_path), "content": "not executed"},
    }))
    refusal = OpenCodeAdapter().adapt_pre_tool_use(event)
    assert refusal.output["action"] == "deny"
    approval_id = refusal.output["approval_id"]
    assert approval_id.startswith("P-") and len(approval_id) == 34

    produced = store.get_by_id(approval_id)
    assert produced is not None and produced["status"] == "pending"
    payload = json.loads(produced["payload_json"])

    terminal_ids = []
    transitions = {
        "approved": store.approve,
        "rejected": store.reject,
        "revoked": store.revoke,
        "expired": store.expire,
    }
    for status, transition in transitions.items():
        other_id = store.insert_requested(
            {"operation": status, "exact_content": status, "scope": "negative"},
            session_id="session-gate-929",
        )
        transition(other_id, "session-gate-929")
        terminal_ids.append(other_id)

    before_statuses = {
        row_id: store.get_by_id(row_id)["status"]
        for row_id in [approval_id, *terminal_ids]
    }
    with sqlite3.connect(db_path) as con:
        grants_before = con.execute("SELECT COUNT(*) FROM approval_grants").fetchone()[0]

    pending_json = _run_gaia("approvals", "pending", "--all-sessions", "--json")
    assert [row["id"] for row in pending_json] == [approval_id]

    listed = _run_gaia("approvals", "list", "--json")
    assert listed["count"] == 1
    [machine] = listed["pending"]
    assert machine["approval_id"] == approval_id
    assert machine["display_label"] == f"P-{approval_id[2:10]}"
    assert machine["status"] == produced["status"] == "pending"
    assert machine["operation"] == payload["operation"]
    assert machine["exact_content"] == payload["exact_content"]
    assert machine["commands"] == payload["commands"]
    assert machine["scope"] == payload["scope"]
    assert machine["impact"] == payload.get("impact")
    assert machine["risk_level"] == payload["risk_level"]
    assert machine["rollback"] == payload.get("rollback_hint")
    assert machine["verification"] == payload.get("verification")
    assert machine["request_fingerprint"] == payload.get("request_fingerprint")
    assert machine["payload_fingerprint"] == produced["fingerprint"]
    assert machine["agent_id"] == produced["agent_id"]
    assert machine["session_id"] == produced["session_id"]
    assert machine["binding"] == {"session_id": produced["session_id"]}
    assert machine["correlation_id"] == payload.get("correlation_id")
    assert machine["created_at"] == produced["created_at"]
    assert machine["decided_at"] == produced["decided_at"]
    assert machine["sealed_payload"] == payload
    assert not set(terminal_ids) & {row["approval_id"] for row in listed["pending"]}

    shown = _run_gaia("approvals", "show", approval_id, "--json")
    assert shown["approval"]["id"] == approval_id
    assert shown["approval"]["payload_json"] == produced["payload_json"]

    for noncanonical in (machine["display_label"], approval_id[2:]):
        rejected_show = _run_gaia_failure(
            "approvals", "show", noncanonical, "--json"
        )
        assert rejected_show.returncode == 1
        assert "short display labels and raw nonces are not lookup keys" in rejected_show.stdout

    crosscheck = validate_crosscheck(
        _approval_request_envelope(approval_id, payload), db_path=db_path
    )
    assert crosscheck.ok and crosscheck.checked

    after_statuses = {
        row_id: store.get_by_id(row_id)["status"]
        for row_id in [approval_id, *terminal_ids]
    }
    with sqlite3.connect(db_path) as con:
        grants_after = con.execute("SELECT COUNT(*) FROM approval_grants").fetchone()[0]
    assert after_statuses == before_statuses
    assert grants_after == grants_before == 0
