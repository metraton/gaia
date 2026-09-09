"""Exercise env transport through the actual plugin, bridge, attestation and SQLite grants."""

import json
import sqlite3
import subprocess

import pytest

from tests.integration.test_opencode_consent_retry_e2e import (
    db_env, _request_set, _approve_set, _drive, _before, _grant,
    SESSION_ID, CALL_ID, DRIVER, DISPATCH_STEPS,
)

COMMAND = (
    "GHX_ACCOUNT=metraton /home/jorge/bin/ghx -C /home/jorge/ws/me/gaia "
    "pr create --repo metraton/gaia --head fix/attestation-cli-fixture "
    "--title 'fixture regression' --body 'diagnostic only'"
)


def _step(kind, label, call_id=CALL_ID):
    """Describe a host lifecycle event; publication commands are never executed."""
    return {"kind": kind, "label": label, "sessionID": SESSION_ID, "callID": call_id}


def _result(driven, label):
    """Read one uniquely labelled host observation."""
    return next(step for step in driven["steps"] if step["label"] == label)


def _assert_settled_callback(driven, label):
    """Assert callback and backend acceptance without dumping identity credentials."""
    keys = ("action", "reason", "error", "class")
    step = _result(driven, label)
    responses = [
        {key: item["received"][key] for key in keys if key in item["received"]}
        for item in driven["exchanges"]
        if item["sent"].get("event") == "tool.execute.after"
        and item["sent"].get("tool") == "bash"
    ]
    diagnostic = {"responses": responses, **{key: step[key] for key in keys if key in step}}
    assert step["allowed"], diagnostic
    assert responses and all(item.get("action") == "allow" for item in responses), diagnostic


def test_exact_command_env_child_and_single_settlement(db_env):
    env, db = db_env
    approval = _request_set(env, (COMMAND,))
    _approve_set(env, approval)
    driven = _drive(env, [
        _before("reserve", COMMAND),
        _step("shell-env", "identity"),
        {**_step("after", "settle"), "command": COMMAND, "output": "mock remote success"},
        _step("shell-env", "late-env"),
        _before("consumed", COMMAND, call_id="new-call"),
    ])
    first = _result(driven, "reserve")
    assert first["allowed"], first
    assert first["commandBefore"] == first["commandAfter"] == COMMAND
    assert _result(driven, "identity")["childIdentity"] == "gaia-system"
    _assert_settled_callback(driven, "settle")
    assert _result(driven, "settle")["commandAfter"] == COMMAND
    assert not _result(driven, "late-env")["allowed"]
    assert not _result(driven, "consumed")["allowed"]
    grant = _grant(db, approval)
    assert grant["status"] == "CONSUMED"
    assert json.loads(grant["consumed_indexes_json"]) == [0]
    assert grant["reservation_tool_use_id"] is None


@pytest.mark.parametrize("other_call", [CALL_ID, "different-call"])
def test_outstanding_reservation_is_not_cached_permission(db_env, other_call):
    env, db = db_env
    approval = _request_set(env, (COMMAND,))
    _approve_set(env, approval)
    driven = _drive(env, [
        _before("first", COMMAND),
        _before("repeat", COMMAND, call_id=other_call),
    ])
    assert _result(driven, "first")["allowed"]
    assert not _result(driven, "repeat")["allowed"]
    grant = _grant(db, approval)
    assert grant["reservation_tool_use_id"] == CALL_ID
    assert json.loads(grant["consumed_indexes_json"]) == []


@pytest.mark.parametrize("status", ["REVOKED", "EXPIRED", "CONSUMED", "deadline"])
def test_invalid_grant_cannot_acquire_env_delivery(db_env, status):
    env, db = db_env
    approval = _request_set(env, (COMMAND,))
    _approve_set(env, approval)
    # db_env verifies both Gaia path routing and PRAGMA database_list against this test's database.
    with sqlite3.connect(db) as con:
        if status == "deadline":
            con.execute("UPDATE approval_grants SET expires_at='2000-01-01T00:00:00Z' WHERE approval_id=?", (approval,))
        else:
            con.execute("UPDATE approval_grants SET status=? WHERE approval_id=?", (status, approval))
    driven = _drive(env, [_before("denied", COMMAND), _step("shell-env", "no-env")])
    assert not _result(driven, "denied")["allowed"]
    assert not _result(driven, "no-env")["allowed"]
    assert json.loads(_grant(db, approval)["consumed_indexes_json"]) == []


@pytest.mark.parametrize("command", [COMMAND + " --draft", "export GAIA_DISPATCH_AGENT=gaia-system; " + COMMAND])
def test_changed_bytes_and_user_compound_remain_denied(db_env, command):
    env, db = db_env
    approval = _request_set(env, (COMMAND,))
    _approve_set(env, approval)
    driven = _drive(env, [_before("denied", command)])
    assert not _result(driven, "denied")["allowed"]
    assert _grant(db, approval)["reservation_tool_use_id"] is None


def test_missing_attestation_does_not_reserve(db_env):
    env, db = db_env
    approval = _request_set(env, (COMMAND,))
    _approve_set(env, approval)
    scenario = {"missingAttestation": True, "steps": DISPATCH_STEPS + [_before("denied", COMMAND)]}
    result = subprocess.run(["bun", str(DRIVER), json.dumps(scenario)], cwd=env["WORKSPACE"], env=env,
                            capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr
    driven = json.loads(result.stdout.strip().splitlines()[-1])
    assert not _result(driven, "denied")["allowed"]
    assert _grant(db, approval)["reservation_tool_use_id"] is None


@pytest.mark.parametrize("session,call", [("unknown-session", CALL_ID), (SESSION_ID, "")])
def test_changed_or_missing_correlation_cannot_reserve(db_env, session, call):
    env, db = db_env
    approval = _request_set(env, (COMMAND,))
    _approve_set(env, approval)
    step = {**_before("denied", COMMAND), "sessionID": session, "callID": call}
    driven = _drive(env, [step])
    assert not _result(driven, "denied")["allowed"]
    assert _grant(db, approval)["reservation_tool_use_id"] is None


def test_failed_execution_settles_once_and_clears_identity(db_env):
    env, db = db_env
    approval = _request_set(env, (COMMAND,))
    _approve_set(env, approval)
    after = {**_step("after", "failed"), "command": COMMAND, "metadata": {"exit_code": 7}}
    driven = _drive(env, [_before("first", COMMAND), after, {**after, "label": "duplicate-after"},
                          _step("shell-env", "late-env")])
    assert _result(driven, "first")["allowed"]
    _assert_settled_callback(driven, "failed")
    assert not _result(driven, "late-env")["allowed"]
    grant = _grant(db, approval)
    assert grant["status"] == "FAILED"
    assert grant["failed_index"] == 0
    assert json.loads(grant["consumed_indexes_json"]) == []
    assert grant["reservation_tool_use_id"] is None


def test_session_failure_removes_pending_env_identity(db_env):
    env, _db = db_env
    approval = _request_set(env, (COMMAND,))
    _approve_set(env, approval)
    driven = _drive(env, [_before("first", COMMAND),
                          {**_step("lifecycle", "error"), "eventType": "session.error"},
                          _step("shell-env", "late-env")])
    assert _result(driven, "first")["allowed"]
    assert not _result(driven, "late-env")["allowed"]


def test_legacy_bridge_keeps_prefix_despite_raw_env_claims(db_env):
    env, _db = db_env
    command = "git status"
    scenario = {"legacyBridge": True, "injectCarrierClaim": True,
                "steps": DISPATCH_STEPS + [_before("legacy", command)]}
    result = subprocess.run(["bun", str(DRIVER), json.dumps(scenario)], cwd=env["WORKSPACE"], env=env,
                            capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr
    driven = json.loads(result.stdout.strip().splitlines()[-1])
    exchange = next(item for item in driven["exchanges"]
                    if item["sent"].get("event") == "tool.execute.before" and item["sent"].get("tool") == "bash")
    assert exchange["received"]["action"] == "allow"
    assert exchange["received"]["updated_input"]["command"] == "export GAIA_DISPATCH_AGENT=gaia-system; " + command
    assert "shell_env" not in exchange["received"]
    # The new plugin refuses a mismatched legacy backend instead of silently losing identity.
    assert not _result(driven, "legacy")["allowed"]
