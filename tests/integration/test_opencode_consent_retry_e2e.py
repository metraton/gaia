"""The OpenCode consent retry, from a blocked tool call to a frozen grant.

Every step below is executed by a real component. The plugin closure in
``opencode/plugin.ts`` runs under bun; its policy bridge is the real
``opencode/bridge.py``; its consent surface and its permission reply go through
the real ``gaia approvals opencode-present`` / ``opencode-decide`` CLIs; the
reservation, settlement and freeze are the real ``gaia.store.writer`` lanes
reached through Gaia's own pre/post tool policy. Nothing here hand-writes a
payload under test.

WHAT IS NOT PROVEN, stated because the gate this file answers asks for it and a
test cannot supply it: no OpenCode host runs in this suite. The driver invokes
the host-owned permission hooks with their measured shapes, but it cannot prove
that a live host presents them or later invokes a command. Live presentation,
session/call ownership, and invocation semantics remain task 484's gate.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"
for _path in (str(REPO_ROOT), str(HOOKS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

GAIA_CLI = REPO_ROOT / "bin" / "gaia"
DRIVER = REPO_ROOT / "tests" / "opencode" / "consent_retry_driver.ts"
PLUGIN = REPO_ROOT / "opencode" / "plugin.ts"

ROOT_SESSION_ID = "ses-t5-root"
DISPATCH_CALL_ID = "call-t5-dispatch"
SESSION_ID = "ses-t5-retry"
CALL_ID = "call-t5-retry"
FRESH_CALL_ID = "call-t5-retry-fresh"
LATER_CALL_ID = "call-t5-later"
PERMISSION_ID = "perm-t5-retry"
AGENT_ID = "gaia-system"

# The bash calls under test must arrive as a DISPATCHED subagent, because that
# is the only role for which Gaia's delegate mode leaves Bash reachable at all
# (an orchestrator session is confined to `gaia *`). So every scenario opens
# with the real dispatch chain the plugin builds: the primary session takes a
# turn and is attested, it issues a task, the PARENT's own message.part.updated
# names the callID<->child-session binding (the real host emits this before
# tool.execute.after or session.idle report the same call complete -- measured
# in the T15 E2E run), and only then does the child session that comes back
# carry the dispatch handle the plugin derives from the task's call id. The
# backstop (hooks/adapters/opencode.py, gate 1013) denies a dispatched child's
# first tool call until this binding lands, so it must precede every child-side
# step below.
DISPATCH_STEPS = [
    {
        "kind": "message", "label": "root-turn",
        "sessionID": ROOT_SESSION_ID, "agent": "gaia-orchestrator",
    },
    {
        "kind": "before", "label": "dispatch", "sessionID": ROOT_SESSION_ID,
        "callID": DISPATCH_CALL_ID, "tool": "task",
        "args": {"subagent_type": AGENT_ID},
    },
    {
        "kind": "task-part", "label": "bound", "sessionID": ROOT_SESSION_ID,
        "callID": DISPATCH_CALL_ID, "childSessionID": SESSION_ID,
    },
    {
        "kind": "after-task", "label": "dispatched", "sessionID": ROOT_SESSION_ID,
        "callID": DISPATCH_CALL_ID, "childSessionID": SESSION_ID,
        "args": {"subagent_type": AGENT_ID},
    },
]

# Two T3 commands, carried only as data: no step in this file executes a
# command from the set. The set needs a second item because the freeze claim is
# about an index that must never run, which a one-item set cannot express.
FIRST_COMMAND = "git push origin main"
SECOND_COMMAND = "docker push registry/app:1"
# sha256 of FIRST_COMMAND, pinned so a silent change to the fingerprint
# function -- which is what binds a retry to its reserved index -- fails here.
FIRST_FINGERPRINT = (
    "16f880284c51ff513ff5465f0082c75d9c7ebb186e65e98b4fa362534044846a"
)


@pytest.fixture()
def db_env(tmp_path, monkeypatch, bootstrapped_db_template):
    """A real bootstrapped database this test alone owns, reachable by subprocess."""
    from tests.conftest import copy_bootstrapped_db

    db_path = tmp_path / "t5.db"
    copy_bootstrapped_db(bootstrapped_db_template, db_path)
    monkeypatch.setenv("GAIA_DB", str(db_path))
    env = os.environ.copy()
    env["GAIA_DB"] = str(db_path)
    return env, db_path


def _request_set(env, commands=(FIRST_COMMAND, SECOND_COMMAND)):
    """Seal the set with the real plan-first producer, never by hand."""
    argv = [sys.executable, str(GAIA_CLI), "approvals", "request-set"]
    for command in commands:
        argv += ["--command", command]
    argv += [
        "--rationale", "Publish the branch and the image under one consent",
        "--verification", "git -C . log --oneline -1",
        "--rollback", "revert the published revision",
        "--agent-id", AGENT_ID,
        "--session-id", SESSION_ID,
        "--json",
    ]
    result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])["approval_id"]


def _decide(env, approval_id, *, reply="once", call_id=CALL_ID, token="t5-token"):
    """Apply the reply through the same CLI the plugin's reply lane invokes."""
    result = subprocess.run(
        [
            sys.executable, str(GAIA_CLI), "approvals", "opencode-decide", approval_id,
            "--session-id", SESSION_ID,
            "--call-id", call_id,
            "--token", token,
            "--reply", reply,
            "--decision-lane", "preferred",
            "--json",
        ],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def _present(env, approval_id, *, call_id=CALL_ID, token="t5-token"):
    result = subprocess.run(
        [
            sys.executable, str(GAIA_CLI), "approvals", "opencode-present", approval_id,
            "--session-id", SESSION_ID,
            "--call-id", call_id,
            "--token", token,
            "--json",
        ],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def _approve_set(env, approval_id, *, call_id=CALL_ID, token="t5-token"):
    """Present then decide through the two real CLI entry points."""
    presented = _present(env, approval_id, call_id=call_id, token=token)
    assert presented.get("visible_lines"), presented
    return _decide(env, approval_id, call_id=call_id, token=token)


def _drive(env, steps, *, permission_id=PERMISSION_ID):
    """Run the real plugin under bun over the dispatch chain plus these steps."""
    scenario = {
        "permissionID": permission_id,
        "sessionID": SESSION_ID,
        "steps": DISPATCH_STEPS + list(steps),
    }
    result = subprocess.run(
        ["bun", str(DRIVER), json.dumps(scenario)],
        env=env, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def _before(label, command, *, call_id=CALL_ID):
    return {
        "kind": "before", "label": label, "sessionID": SESSION_ID,
        "callID": call_id, "tool": "bash", "command": command,
    }


def _grant(db_path, approval_id):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM approval_grants WHERE approval_id=?", (approval_id,)
    ).fetchone()
    con.close()
    return dict(row) if row is not None else None


def _tool_exchanges(driven, event="tool.execute.before", tool="bash"):
    """The bridge exchanges for one tool, in the order the plugin sent them.

    Filtered by tool because every scenario opens with the task dispatch that
    establishes the subagent session, and the identity claims under test are
    about the bash calls that follow it.
    """
    return [
        x for x in driven["exchanges"]
        if x["sent"].get("event") == event and x["sent"].get("tool") == tool
    ]


def _step(driven, label):
    matched = [s for s in driven["steps"] if s.get("label") == label]
    assert len(matched) == 1, f"{label}: {json.dumps(driven['steps'], indent=2)}"
    return matched[0]


def test_fresh_retry_reserves_exact_content_executes_settles_and_freezes(db_env):
    """A fresh call binds by exact content, then settlement freezes the set.

    The retry has a fresh call id and carries the same bytes; the reservation
    is the exact index; the failure freezes the set; and
    the freeze is asserted as the grant's terminal state, not merely as an
    index that happened not to run in this test.
    """
    env, db_path = db_env
    approval_id = _request_set(env)
    from gaia.approvals.command_set import command_fingerprint

    # Attempt BEFORE any reply exists: no executable grant, so the tool call is
    # refused. This is the invocation the retry must later match identically.
    retried = _drive(
        env,
        [
            _before("pre-approval", FIRST_COMMAND),
            {"kind": "question-reply", "label": "decision", "decision": "once"},
            _before("retry", FIRST_COMMAND, call_id=FRESH_CALL_ID),
            {
                "kind": "after", "label": "settle", "sessionID": SESSION_ID,
                "callID": FRESH_CALL_ID, "tool": "bash", "command": FIRST_COMMAND,
                "output": "fatal: remote rejected", "metadata": {"exitCode": 7},
            },
            _before("later-index", SECOND_COMMAND, call_id=LATER_CALL_ID),
        ],
    )
    first_attempt = _step(retried, "pre-approval")
    assert first_attempt["allowed"] is False, retried
    first_exchange = _tool_exchanges(retried)[0]
    assert len(retried["controlQuestions"]) >= 1
    question = retried["controlQuestions"][0]["questions"][0]
    assert approval_id in question["question"]
    assert [option["label"].split()[0] for option in question["options"]] == ["Approve", "Reject"]
    retry_step = _step(retried, "retry")
    assert retry_step["allowed"] is True, retried
    retry_exchange = _tool_exchanges(retried)[1]

    # Fresh identifier, byte-identical input, identical fingerprint.
    assert retry_exchange["sent"]["sessionID"] == first_exchange["sent"]["sessionID"] == SESSION_ID
    assert first_exchange["sent"]["callID"] == CALL_ID
    assert retry_exchange["sent"]["callID"] == FRESH_CALL_ID
    assert retry_exchange["sentArgsJSON"] == first_exchange["sentArgsJSON"]
    assert json.loads(retry_exchange["sentArgsJSON"])["command"] == FIRST_COMMAND
    def _observed(exchange):
        command = json.loads(exchange["sentArgsJSON"])["command"]
        return {
            "session_id": exchange["sent"]["sessionID"],
            "call_id": exchange["sent"]["callID"],
            "args": exchange["sentArgsJSON"],
            "fingerprint": command_fingerprint(command),
        }

    expected_blocked = {
        "session_id": SESSION_ID,
        "call_id": CALL_ID,
        "args": '{"command":"' + FIRST_COMMAND + '"}',
        "fingerprint": FIRST_FINGERPRINT,
    }
    expected_retry = {**expected_blocked, "call_id": FRESH_CALL_ID}
    assert _observed(first_exchange) == expected_blocked
    assert _observed(retry_exchange) == expected_retry

    grant = _grant(db_path, approval_id)
    items = json.loads(grant["command_set_json"])
    assert items[0]["fingerprint"] == command_fingerprint(FIRST_COMMAND)
    assert items[0]["command"] == FIRST_COMMAND

    # Settlement of the failed execution froze the set at the exact index.
    settled = _grant(db_path, approval_id)
    assert settled["status"] == "FAILED"
    assert settled["failed_index"] == 0
    assert settled["next_index"] == 0
    assert json.loads(settled["consumed_indexes_json"]) == []
    assert settled["reservation_tool_use_id"] is None

    # Zero later executions -- refused at the tool boundary AND unreachable in
    # the store, which is the terminal claim: no index after the failed one can
    # ever run under this grant, by any route.
    assert _step(retried, "later-index")["allowed"] is False, retried
    from gaia.store import writer

    assert writer.reserve_plan_command(
        SECOND_COMMAND, session_id=SESSION_ID, tool_use_id="any-later-call",
        db_path=db_path,
    ) is None
    assert writer.reserve_plan_command(
        FIRST_COMMAND, session_id=SESSION_ID, tool_use_id="any-retry-call",
        db_path=db_path,
    ) is None


def test_reservation_is_bound_to_the_retrying_call_not_merely_to_the_command(db_env):
    """A different call cannot settle the reservation the retry established."""
    env, db_path = db_env
    approval_id = _request_set(env)
    driven = _drive(env, [
        _before("blocked", FIRST_COMMAND, call_id="call-original"),
        {"kind": "question-reply", "label": "decision", "decision": "once"},
        _before("retry", FIRST_COMMAND),
    ])
    assert _step(driven, "retry")["allowed"] is True, driven
    reserved = _grant(db_path, approval_id)
    assert reserved["reservation_index"] == 0
    assert reserved["reservation_session_id"] == SESSION_ID
    assert reserved["reservation_tool_use_id"] == CALL_ID

    from gaia.store import writer

    assert writer.settle_plan_command(
        approval_id, session_id=SESSION_ID, tool_use_id=LATER_CALL_ID,
        success=True, db_path=db_path,
    ) is False
    # The refusal mutated nothing, observed rather than inferred from the
    # settlement that succeeds below: no freeze, no advanced index, and the
    # reservation still belongs to the call that took it.
    assert _grant(db_path, approval_id) == reserved
    assert writer.settle_plan_command(
        approval_id, session_id=SESSION_ID, tool_use_id=CALL_ID,
        success=True, db_path=db_path,
    ) is True


def test_uncorrelated_native_reply_grants_nothing_after_the_call_is_aborted(db_env):
    env, db_path = db_env
    approval_id = _request_set(env)

    driven = _drive(
        env,
        [
            _before("blocked", FIRST_COMMAND),
            {
                "kind": "replied", "label": "reply", "requestID": PERMISSION_ID,
                "reply": "once",
            },
        ],
    )
    assert _step(driven, "blocked")["allowed"] is False, driven
    assert _step(driven, "reply")["allowed"] is True, driven

    assert driven["permissionAsks"] == [], driven
    assert _grant(db_path, approval_id) is None


def test_reject_without_a_correlated_host_request_grants_nothing(db_env):
    env, db_path = db_env
    approval_id = _request_set(env)
    driven = _drive(
        env,
        [
            _before("blocked", FIRST_COMMAND),
            {
                "kind": "replied", "label": "rejected", "requestID": PERMISSION_ID,
                "reply": "reject",
            },
        ],
    )

    assert _step(driven, "blocked")["allowed"] is False, driven
    assert driven["permissionAsks"] == [], driven
    assert _step(driven, "rejected")["allowed"] is True, driven
    assert _grant(db_path, approval_id) is None


def test_structured_reject_and_free_text_create_no_grant(db_env):
    env, db_path = db_env
    rejected_id = _request_set(env)
    rejected = _drive(env, [
        _before("blocked", FIRST_COMMAND),
        {"kind": "question-reply", "label": "decision", "decision": "reject"},
    ])
    assert _step(rejected, "blocked")["allowed"] is False
    assert _grant(db_path, rejected_id) is None

    free_text_id = _request_set(env, commands=("npm publish", "docker push registry/other:1"))
    free_text = _drive(env, [
        _before("blocked-free", "npm publish"),
        {"kind": "question-reply", "label": "free", "decision": "yes please"},
    ], permission_id="perm-free")
    assert _step(free_text, "blocked-free")["allowed"] is False
    assert _grant(db_path, free_text_id) is None


def test_a_blocked_attempt_names_the_pending_plan_first_approval(db_env):
    """The fail-closed error names the existing set rather than minting one."""
    env, db_path = db_env
    approval_id = _request_set(env)

    for label, command, call_id in (
        ("blocked-first", FIRST_COMMAND, CALL_ID),
        ("blocked-second", SECOND_COMMAND, LATER_CALL_ID),
    ):
        driven = _drive(env, [_before(label, command, call_id=call_id)])
        step = _step(driven, label)
        assert step["allowed"] is False, driven
        assert approval_id in step["error"]
        assert driven["permissionAsks"] == []

    # The named id remains the one plan-first set, with no singular request
    # minted as a side effect of either blocked attempt.
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, status, payload_json FROM approvals WHERE status='pending'"
    ).fetchall()
    con.close()
    assert [row["id"] for row in rows] == [approval_id], (
        "a blocked attempt left an extra pending approval behind"
    )
    assert json.loads(rows[0]["payload_json"])["request_type"] == "COMMAND_SET"

def test_plugin_fails_closed_after_presenting_an_approval():
    """A pending approval aborts this invocation instead of returning into it."""
    source = PLUGIN.read_text()
    assert '"permission.ask"' in source
    assert "session.permission.create" not in source
    blocked = source.index("await requestApproval(response, call.sessionID, call.callID)")
    denied = source.index("Gaia blocked this invocation", blocked)
    branch_end = source.index("\n      }", blocked)
    assert blocked < denied < branch_end
