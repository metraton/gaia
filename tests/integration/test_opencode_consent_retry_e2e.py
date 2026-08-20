"""The OpenCode consent retry, from a blocked tool call to a frozen grant.

Every step below is executed by a real component. The plugin closure in
``opencode/plugin.ts`` runs under bun; its policy bridge is the real
``opencode/bridge.py``; its consent surface and its permission reply go through
the real ``gaia approvals opencode-present`` / ``opencode-decide`` CLIs; the
reservation, settlement and freeze are the real ``gaia.store.writer`` lanes
reached through Gaia's own pre/post tool policy. Nothing here hand-writes a
payload under test.

WHAT IS NOT PROVEN, stated because the gate this file answers asks for it and a
test cannot supply it: no OpenCode host runs in this suite, so the second
invocation carrying the first invocation's ``session_id``/``call_id`` is issued
by the driver, not observed being issued by OpenCode. What is established is
that the plugin plus Gaia treat such an invocation as one continuous consent --
identical bytes, identical fingerprint, one reservation index. That OpenCode
DELIVERS it is a fact about OpenCode's runtime; see
``test_plugin_aborts_instead_of_awaiting_the_host_deferred``, which records the
plugin-side reason it currently would not.
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
LATER_CALL_ID = "call-t5-later"
PERMISSION_ID = "perm-t5-retry"
AGENT_ID = "gaia-system"

# The bash calls under test must arrive as a DISPATCHED subagent, because that
# is the only role for which Gaia's delegate mode leaves Bash reachable at all
# (an orchestrator session is confined to `gaia *`). So every scenario opens
# with the real dispatch chain the plugin builds: the primary session takes a
# turn and is attested, it issues a task, and the child session that comes back
# carries the dispatch handle the plugin derives from the task's call id.
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
    """Present then reply, which is exactly what the plugin's own lanes do.

    The pair is used instead of ``permission.replied`` because the plugin's
    reply lane can only reach the approval its policy bridge named when it
    refused the call, and that is never the pending plan-first set -- the gap
    pinned by ``test_a_blocked_attempt_does_not_surface_the_pending_plan_first_approval``.
    Both halves are the real CLIs the plugin invokes, in the real order: the
    presentation is what binds the token the reply must carry.
    """
    presented = _present(env, approval_id, call_id=call_id, token=token)
    assert presented.get("visible_lines"), presented
    return _decide(env, approval_id, call_id=call_id, token=token)


def _drive(env, steps, *, permission_id=PERMISSION_ID):
    """Run the real plugin under bun over the dispatch chain plus these steps."""
    scenario = {"permissionID": permission_id, "steps": DISPATCH_STEPS + list(steps)}
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


def test_same_binding_retry_reserves_exact_index_executes_settles_and_freezes(db_env):
    """The whole chain, on one session/call binding, through the real plugin.

    The retry is the same identity as the blocked attempt and carries the same
    bytes; the reservation is the exact index; the failure freezes the set; and
    the freeze is asserted as the grant's terminal state, not merely as an
    index that happened not to run in this test.
    """
    env, db_path = db_env
    approval_id = _request_set(env)
    from gaia.approvals.command_set import command_fingerprint

    # Attempt BEFORE any reply exists: no executable grant, so the tool call is
    # refused. This is the invocation the retry must later match identically.
    blocked = _drive(env, [_before("pre-approval", FIRST_COMMAND)])
    first_attempt = _step(blocked, "pre-approval")
    assert first_attempt["allowed"] is False, blocked
    first_exchange = _tool_exchanges(blocked)[0]
    assert _grant(db_path, approval_id) is None

    # The user's reply, applied through the same CLI the plugin's
    # permission.replied lane invokes (see the reply-lane test below).
    decision = _approve_set(env, approval_id)
    assert decision["decision"] == "once"
    assert decision["status"] == "approved"
    assert decision["protocol_version"]
    grant = _grant(db_path, approval_id)
    assert grant is not None and grant["status"] == "PENDING"
    assert grant["scope"] == "COMMAND_SET" and grant["source"] == "plan-first"

    # The retry: same session, same call, same command bytes.
    retried = _drive(
        env,
        [
            _before("retry", FIRST_COMMAND),
            {
                "kind": "after", "label": "settle", "sessionID": SESSION_ID,
                "callID": CALL_ID, "tool": "bash", "command": FIRST_COMMAND,
                "output": "fatal: remote rejected", "metadata": {"exitCode": 7},
            },
            _before("later-index", SECOND_COMMAND, call_id=LATER_CALL_ID),
        ],
    )
    retry_step = _step(retried, "retry")
    assert retry_step["allowed"] is True, retried
    retry_exchange = _tool_exchanges(retried)[0]

    # Same binding, byte-identical input, identical fingerprint.
    assert retry_exchange["sent"]["sessionID"] == first_exchange["sent"]["sessionID"] == SESSION_ID
    assert retry_exchange["sent"]["callID"] == first_exchange["sent"]["callID"] == CALL_ID
    assert retry_exchange["sentArgsJSON"] == first_exchange["sentArgsJSON"]
    assert json.loads(retry_exchange["sentArgsJSON"])["command"] == FIRST_COMMAND
    # The binding, pinned literally, so a reader sees the two invocations are
    # one identity rather than taking the equality assertions above on trust.
    def _observed(exchange):
        command = json.loads(exchange["sentArgsJSON"])["command"]
        return {
            "session_id": exchange["sent"]["sessionID"],
            "call_id": exchange["sent"]["callID"],
            "args": exchange["sentArgsJSON"],
            "fingerprint": command_fingerprint(command),
        }

    expected_binding = {
        "session_id": SESSION_ID,
        "call_id": CALL_ID,
        "args": '{"command":"' + FIRST_COMMAND + '"}',
        "fingerprint": FIRST_FINGERPRINT,
    }
    assert _observed(first_exchange) == expected_binding
    assert _observed(retry_exchange) == expected_binding

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
    _approve_set(env, approval_id)

    driven = _drive(env, [_before("retry", FIRST_COMMAND)])
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
    assert writer.settle_plan_command(
        approval_id, session_id=SESSION_ID, tool_use_id=CALL_ID,
        success=True, db_path=db_path,
    ) is True


def test_plugin_reply_lane_applies_a_native_reply_through_the_real_cli(db_env):
    """permission.replied=once reaches Gaia's decide CLI from the plugin itself.

    The approval this lane can reach is whichever one the policy bridge named
    when it refused the call -- the plugin never chooses an approval id. That is
    what the next test pins down.
    """
    env, db_path = db_env
    _request_set(env)

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

    # The plugin presented exactly one native permission, carrying the approval
    # the bridge named and a visible surface Gaia sealed.
    assert len(driven["permissionCreates"]) == 1, driven
    presented = driven["permissionCreates"][0]
    presented_id = presented["metadata"]["gaiaApprovalID"]
    assert presented["sessionID"] == SESSION_ID
    assert presented["metadata"]["gaiaCallID"] == CALL_ID
    assert presented["resources"], presented

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT status FROM approvals WHERE id=?", (presented_id,)
    ).fetchone()
    con.close()
    assert row is not None, presented_id
    assert row["status"] != "REQUESTED", (
        "permission.replied=once did not move the approval the plugin presented"
    )


def test_a_blocked_attempt_surfaces_the_pending_plan_first_approval(db_env):
    """The block path names the pending set, not a freshly minted singular id.

    This assertion is the inverse of the one it replaces. The former detector
    asserted the two ids DIVERGE and said in its own docstring that it would
    fail the day they converge; this is that day, so the detector is inverted
    rather than deleted -- the same observation, read for the outcome that is
    now correct. The id is read out of the plugin's own
    ``permissionCreates[0].metadata.gaiaApprovalID``, so what is asserted is
    what the plugin presented, never a value this test supplied.

    Both items are attempted, each on its own plugin run. At pending time the
    set has consumed nothing, so every item belongs to the consent being
    sought and each must name the set. Naming it is not permission to run it
    out of order: ``reserve_plan_command`` still matches only at
    ``next_index``, and that ordering is asserted by the reservation test
    above.
    """
    env, db_path = db_env
    approval_id = _request_set(env)

    for label, command, call_id in (
        ("blocked-first", FIRST_COMMAND, CALL_ID),
        ("blocked-second", SECOND_COMMAND, LATER_CALL_ID),
    ):
        driven = _drive(env, [_before(label, command, call_id=call_id)])
        assert _step(driven, label)["allowed"] is False, driven
        assert len(driven["permissionCreates"]) == 1, driven
        presented_id = driven["permissionCreates"][0]["metadata"]["gaiaApprovalID"]

        assert presented_id == approval_id, (
            f"{label}: the blocked attempt minted a fresh singular approval "
            "instead of naming the pending plan-first set, so the plugin's "
            "reply lane cannot reach activate_command_set_atomically"
        )

    # The presented id is the set's, so the reply lane's own branch condition
    # (payload request_type == COMMAND_SET in cmd_opencode_decide) holds on it.
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


def test_plugin_aborts_instead_of_awaiting_the_host_deferred():
    """Why the host's own same-call resume is not the mechanism in play.

    OpenCode pauses an ``ask`` decision on a deferred and resumes the ORIGINAL
    tool execution when a reply resolves it. The plugin does not consume that:
    ``tool.execute.before`` registers the pending approval and then throws,
    which aborts the call. Any second invocation is therefore a fresh one from
    the host's point of view, and a same-``call_id`` retry cannot be asserted
    from this repository. Recorded as a source-level fact so the claim is
    falsifiable rather than an opinion in a report.
    """
    source = PLUGIN.read_text()
    assert "pending.set(decided.id, approval)" in source
    assert "throw new Error(response.reason" in source
    assert "await" not in source.split("pending.set(decided.id, approval)")[1].split("\n")[0]
