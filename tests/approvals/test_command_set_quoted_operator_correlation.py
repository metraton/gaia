"""An approved COMMAND_SET item whose quoted payload carries a shell operator reserves on the real hook.

``BashValidator._has_operators`` is a quote-blind substring scan, so a single
command like ``kubectl exec ... -- /bin/bash -c 'a; b'`` takes the operator
branch, the quote-aware parser then returns it as ONE component, and that
branch is what used to drop the host's ``tool_use_id``: the reservation guard
saw an empty correlation id and refused the signed command (P-6fe252a6...,
2026-10-01). These tests drive ``ClaudeCodeAdapter.adapt_pre_tool_use`` with the
payload Claude Code sends for a resumed subagent's Bash call.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from gaia.approvals.command_set import request_fingerprint, validate_request_set
from gaia.store import writer

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "hooks") not in sys.path:
    sys.path.insert(0, str(_ROOT / "hooks"))

SESSION = "946f3943-b2d9-4bbb-a283-04189f3a279b"
AGENT_TYPE = "cloud-troubleshooter"
APPROVAL_ID = "P-quoted-operator"
QUOTED_OPERATOR = (
    "kubectl exec -n branchkinect-dev keycloak-7b9cb768cf-cm6d5 -c keycloak -- /bin/bash -c "
    "'K=/opt/keycloak/bin/kcadm.sh; A=(--no-config --server http://localhost:8080 --realm master "
    "--user \"$KC_BOOTSTRAP_ADMIN_USERNAME\" --password \"$KC_BOOTSTRAP_ADMIN_PASSWORD\"); "
    "\"$K\" get realms \"${A[@]}\" --fields realm,enabled,organizationsEnabled,registrationAllowed'"
)
NO_OPERATOR = "git push origin main"


@pytest.fixture
def substrate(tmp_path, monkeypatch):
    from modules.core.paths import clear_path_cache

    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DB", raising=False)
    con = sqlite3.connect(tmp_path / "gaia.db")
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()
    clear_path_cache()
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    monkeypatch.setattr("modules.core.state.find_claude_dir", lambda: claude_dir)
    workdir = tmp_path / "ws"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    return tmp_path / "gaia.db", str(workdir)


def _approve(command, db, cwd):
    commands = [command, "docker push registry/app:1"]
    items = validate_request_set(commands, cwd=cwd)
    result = writer.insert_plan_command_set(
        APPROVAL_ID, items, request_fingerprint=request_fingerprint(commands),
        agent_id=AGENT_TYPE, session_id=SESSION, db_path=db,
    )
    assert result["status"] == "applied"


def _pre_tool_use(command, cwd, **extra):
    """Return ``(decision, reason)``; a refusal is Claude Code's exit-2 stderr text, not JSON."""
    from adapters.claude_code import ClaudeCodeAdapter

    payload = {
        "hook_event_name": "PreToolUse", "session_id": SESSION, "cwd": cwd,
        "agent_id": "a5b9f990dcb26b1a1", "agent_type": AGENT_TYPE,
        "tool_name": "Bash", "tool_input": {"command": command, "description": "Read realms"},
        **extra,
    }
    adapter = ClaudeCodeAdapter()
    response = adapter.adapt_pre_tool_use(adapter.parse_event(json.dumps(payload)))
    if response.exit_code == 2:
        return "deny", response.output
    decision = response.output["hookSpecificOutput"]
    return decision["permissionDecision"], decision.get("permissionDecisionReason", "")


def _grant(db):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    row = dict(con.execute(
        "SELECT status, next_index, reservation_tool_use_id FROM approval_grants WHERE approval_id=?",
        (APPROVAL_ID,),
    ).fetchone())
    con.close()
    return row


@pytest.mark.parametrize("command", [QUOTED_OPERATOR, NO_OPERATOR], ids=["quoted-operator", "no-operator"])
def test_approved_item_reserves_under_the_host_tool_use_id(substrate, command):
    db, cwd = substrate
    _approve(command, db, cwd)

    decision, reason = _pre_tool_use(command, cwd, tool_use_id="toolu_01KeycloakRealms")

    assert decision == "allow", reason
    assert _grant(db)["reservation_tool_use_id"] == "toolu_01KeycloakRealms"


def test_missing_host_correlation_names_what_is_missing_and_keeps_the_grant(substrate):
    db, cwd = substrate
    _approve(QUOTED_OPERATOR, db, cwd)

    decision, reason = _pre_tool_use(QUOTED_OPERATOR, cwd)

    assert decision == "deny"
    assert APPROVAL_ID in reason
    assert "tool_use_id" in reason
    assert "not consumed" in reason
    assert _grant(db) == {"status": "PENDING", "next_index": 0, "reservation_tool_use_id": None}
