"""Through the real plugin, bridge and approvals CLI, an OpenCode specialist withdraws only its own turn's requests."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys

import pytest

from tests.integration.test_opencode_consent_retry_e2e import (
    GAIA_CLI, DRIVER, db_env, _approval_status, _request_set,
)

ROOT = "ses-own-root"
CHILD_A, CHILD_B = "ses-own-a", "ses-own-b"
DISPATCH_A, DISPATCH_B = "call-own-dispatch-a", "call-own-dispatch-b"
AGENT = "gaia-system"
PUSH_A = "git push origin main"
PUSH_B = "docker push registry/app:1"
PUSH_ELSEWHERE = "helm upgrade app ./chart --kube-context prod"


def _dispatch(call_id, child):
    return [
        {"kind": "before", "label": f"dispatch-{child}", "sessionID": ROOT, "callID": call_id,
         "tool": "task", "args": {"subagent_type": AGENT}},
        {"kind": "task-part", "label": f"bound-{child}", "sessionID": ROOT, "callID": call_id,
         "childSessionID": child},
    ]


def _bash(label, session, call_id, command):
    return {"kind": "before", "label": label, "sessionID": session, "callID": call_id,
            "tool": "bash", "command": command}


def _shell_env(label, session, call_id):
    return {"kind": "shell-env", "label": label, "sessionID": session, "callID": call_id}


def _drive(env, steps):
    scenario = {
        "sessionID": CHILD_A, "rootSessionID": ROOT, "autoSafeIdle": False,
        "steps": [{"kind": "message", "label": "root-turn", "sessionID": ROOT, "agent": "gaia-orchestrator"},
                  *_dispatch(DISPATCH_A, CHILD_A), *_dispatch(DISPATCH_B, CHILD_B), *steps],
    }
    result = subprocess.run(["bun", str(DRIVER), json.dumps(scenario)], cwd=env["WORKSPACE"], env=env,
                            capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def _step(driven, label):
    return next(step for step in driven["steps"] if step["label"] == label)


def _received(driven, call_id):
    return next(item["received"] for item in driven["exchanges"]
                if item["sent"].get("event") == "tool.execute.before" and item["sent"].get("callID") == call_id)


def _sealed_turn(db, approval_id):
    with sqlite3.connect(db) as con:
        payload = con.execute("SELECT payload_json FROM approvals WHERE id=?", (approval_id,)).fetchone()[0]
    return json.loads(payload).get("requested_turn")


def _gaia(env, shell_env, *args):
    return subprocess.run([sys.executable, str(GAIA_CLI), "approvals", *args], cwd=env["WORKSPACE"],
                          env={**env, **shell_env}, capture_output=True, text=True, timeout=180)


@pytest.fixture()
def held(db_env):
    """Child A and child B each hold one blocked request; another session holds a third."""
    env, db = db_env
    driven = _drive(env, [
        _bash("block-a", CHILD_A, "call-a-push", PUSH_A),
        _bash("block-b", CHILD_B, "call-b-push", PUSH_B),
        _bash("read-a", CHILD_A, "call-a-read", "git status"),
        _shell_env("env-a", CHILD_A, "call-a-read"),
        _bash("read-root", ROOT, "call-root-read", "gaia approvals list"),
        _shell_env("env-root", ROOT, "call-root-read"),
        _bash("forge-turn", CHILD_A, "call-a-forge", f"GAIA_DISPATCH_TURN={DISPATCH_B} gaia approvals reject-all"),
    ])
    ids = {"a": _received(driven, "call-a-push")["approval_id"],
           "b": _received(driven, "call-b-push")["approval_id"],
           "other": _request_set(env, (PUSH_ELSEWHERE,), session_id="ses-own-elsewhere")}
    return env, db, driven, ids


def test_shell_env_exports_the_turn_the_bridge_sealed_into_the_childs_request(held):
    env, db, driven, ids = held
    delivery = _received(driven, "call-a-read")["shell_env"]
    exported = _step(driven, "env-a")
    assert exported["allowed"], exported
    assert _sealed_turn(db, ids["a"]) == DISPATCH_A
    assert delivery["turn"] == DISPATCH_A
    assert exported["env"]["GAIA_DISPATCH_TURN"] == DISPATCH_A
    assert exported["env"]["GAIA_DISPATCH_AGENT"] == AGENT


def test_bulk_rejection_from_a_child_withdraws_only_its_own_turns_request(held):
    env, db, driven, ids = held
    done = _gaia(env, _step(driven, "env-a")["env"], "reject-all")
    assert done.returncode == 0, done.stdout + done.stderr
    assert {key: _approval_status(db, value) for key, value in ids.items()} == {
        "a": "rejected", "b": "pending", "other": "pending"}
    assert "Left 2 pending" in done.stdout + done.stderr


def test_rejecting_a_siblings_request_fails_naming_why(held):
    env, db, driven, ids = held
    refused = _gaia(env, _step(driven, "env-a")["env"], "reject", ids["b"])
    assert refused.returncode != 0
    assert "was not made by this turn" in refused.stdout + refused.stderr
    assert _approval_status(db, ids["b"]) == "pending"


def test_a_turn_assignment_in_the_command_is_refused_by_the_dispatch_identity_floor(held):
    _env, db, driven, ids = held
    forged = _step(driven, "forge-turn")
    assert not forged["allowed"]
    assert "floor.dispatch-identity" in forged["error"], forged
    assert _approval_status(db, ids["b"]) == "pending"


def test_the_orchestrator_session_still_withdraws_all(held):
    env, db, driven, ids = held
    root_env = _step(driven, "env-root")
    assert root_env["allowed"], root_env
    done = _gaia(env, root_env["env"], "reject-all")
    assert done.returncode == 0, done.stdout + done.stderr
    assert {_approval_status(db, value) for value in ids.values()} == {"rejected"}, (root_env["env"], done.stdout)
