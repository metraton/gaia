"""The OpenCode consent retry, from a blocked tool call to a frozen grant.

Every step below is executed by a real component. The plugin closure in
``opencode/plugin.ts`` runs under bun; its policy bridge is the real
``opencode/bridge.py``; its consent surface and its permission reply go through
the real ``gaia approvals opencode-present`` / ``opencode-decide`` CLIs; the
reservation, settlement and freeze are the real ``gaia.store.writer`` lanes
reached through Gaia's own pre/post tool policy. Nothing here hand-writes a
payload under test.

WHAT IS NOT PROVEN, stated because the gate this file answers asks for it and a
test cannot supply it: no OpenCode host runs in this suite, so the original and
fresh retry invocations are issued by the driver, not observed being issued by
OpenCode. What is established is that the plugin plus Gaia require a new call
identity with identical command bytes and fingerprint, then reserve and settle
exactly one bound grant index.
"""

from __future__ import annotations

import hashlib
import json
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
RETRY_CALL_ID = "call-t5-retry-fresh"
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


def _isolated_env(root, bootstrapped_db_template):
    """Create a workspace whose hook files and substrate share one isolated root."""
    from tests.conftest import IsolatedRuntimeEnv, copy_bootstrapped_db

    env = IsolatedRuntimeEnv(root)
    env.prepare_hook_workspace()
    db_path = Path(env["GAIA_DB"])
    copy_bootstrapped_db(bootstrapped_db_template, db_path)
    return env, db_path


@pytest.fixture()
def db_env(tmp_path, monkeypatch, bootstrapped_db_template):
    """Use actual hook path resolution inside this test's private workspace."""
    env, db_path = _isolated_env(tmp_path / "state", bootstrapped_db_template)
    monkeypatch.chdir(env["WORKSPACE"])
    monkeypatch.setenv("GAIA_DB", str(db_path))
    monkeypatch.setenv("GAIA_DATA_DIR", env["GAIA_DATA_DIR"])
    monkeypatch.setenv("GAIA_OPENCODE_ATTESTATION_DIR", env["GAIA_OPENCODE_ATTESTATION_DIR"])
    from gaia.paths import db_path as resolved_db_path
    from gaia.store import writer
    from modules.core.paths import clear_path_cache, find_claude_dir, get_plugin_data_dir
    from modules.core.state import _get_state_dir, _get_state_file_path

    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    clear_path_cache()
    claude_dir = Path(env["WORKSPACE"]) / ".claude"
    assert find_claude_dir() == claude_dir
    assert get_plugin_data_dir() == claude_dir
    assert _get_state_dir().parent == claude_dir
    assert _get_state_file_path().parent == claude_dir

    assert resolved_db_path().resolve() == db_path.resolve()
    with writer._connect() as con:
        actual_path = Path(con.execute("PRAGMA database_list").fetchone()[2])
        assert actual_path.resolve() == db_path.resolve()
        assert con.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    return env, db_path


def _request_set(env, commands=(FIRST_COMMAND, SECOND_COMMAND)):
    """Seal the set with the real plan-first producer, never by hand."""
    argv = [sys.executable, str(GAIA_CLI), "approvals", "request-set"]
    for command in commands:
        argv += ["--command", command, "--does", "Publica una parte.", "--impact", "Queda visible."]
    argv += [
        "--what", "Publicar la rama y la imagen.",
        "--question", "¿Publico la rama y la imagen?",
        "--rationale", "Publish the branch and the image under one consent",
        "--verification", "git -C . log --oneline -1",
        "--rollback", "revert the published revision",
        "--agent-id", AGENT_ID,
        "--session-id", SESSION_ID,
        "--json",
    ]
    result = subprocess.run(argv, cwd=env["WORKSPACE"], env=env, capture_output=True, text=True, timeout=180)
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
        cwd=env["WORKSPACE"], env=env, capture_output=True, text=True, timeout=180,
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
        cwd=env["WORKSPACE"], env=env, capture_output=True, text=True, timeout=180,
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


def _drive(
    env, steps, *, permission_id=PERMISSION_ID, initial_permissions=None,
    question_during_prompt=False, duplicate_question_when_prompt_races=False,
    racing_duplicate_delay_ms=20_000, auto_safe_idle=True,
):
    """Run the real plugin under bun over the dispatch chain plus these steps."""
    scenario = {
        "permissionID": permission_id,
        "sessionID": SESSION_ID,
        "steps": DISPATCH_STEPS + list(steps),
        "autoSafeIdle": auto_safe_idle,
    }
    if initial_permissions is not None:
        scenario["initialPermissions"] = {SESSION_ID: initial_permissions}
    if question_during_prompt:
        scenario["questionDuringPrompt"] = True
    if duplicate_question_when_prompt_races:
        scenario["duplicateQuestionWhenPromptRaces"] = True
        scenario["racingDuplicateDelayMs"] = racing_duplicate_delay_ms
    result = subprocess.run(
        ["bun", str(DRIVER), json.dumps(scenario)],
        cwd=env["WORKSPACE"], env=env, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def _before(label, command, *, call_id=CALL_ID):
    return {
        "kind": "before", "label": label, "sessionID": SESSION_ID,
        "callID": call_id, "tool": "bash", "command": command,
    }


def _file_before(label, tool, file_path, *, call_id, content="updated\n"):
    return {
        "kind": "before", "label": label, "sessionID": SESSION_ID,
        "callID": call_id, "tool": tool,
        "args": {"file_path": str(file_path), "content": content},
    }


def test_reactive_singular_bash_approval_arms_one_typed_identical_retry(db_env):
    env, _db_path = db_env

    driven = _drive(env, [
        _before("blocked-singular", FIRST_COMMAND),
        {"kind": "control-decision", "label": "approve-singular", "answer": "approve"},
        _before("retry-singular", FIRST_COMMAND, call_id=RETRY_CALL_ID),
        {
            "kind": "after", "label": "settle-singular", "sessionID": SESSION_ID,
            "callID": RETRY_CALL_ID, "tool": "bash", "command": FIRST_COMMAND,
            "metadata": {"exitCode": 0},
        },
        _before("post-settlement", FIRST_COMMAND, call_id=LATER_CALL_ID),
    ])

    assert _step(driven, "blocked-singular")["allowed"] is False
    assert _step(driven, "approve-singular")["allowed"] is True
    assert _step(driven, "retry-singular")["allowed"] is True
    retry_exchange = next(
        item for item in _tool_exchanges(driven)
        if item["sent"].get("callID") == RETRY_CALL_ID
    )
    assert retry_exchange["sent"]["consentRetry"] | {
        "version": 1,
        "kind": "SCOPE_SEMANTIC_SIGNATURE",
        "command": FIRST_COMMAND,
        "command_fingerprint": FIRST_FINGERPRINT,
    } == retry_exchange["sent"]["consentRetry"]
    assert _step(driven, "post-settlement")["allowed"] is False
    post_exchange = next(
        item for item in _tool_exchanges(driven)
        if item["sent"].get("callID") == LATER_CALL_ID
    )
    assert "consentRetry" not in post_exchange["sent"]


def test_protected_file_approval_arms_an_edit_retry_for_only_the_canonical_target(db_env):
    env, db_path = db_env
    protected = Path(env["WORKSPACE"]) / ".claude" / "hooks" / "pre_tool_use.py"

    driven = _drive(env, [
        _file_before("blocked-file", "write", protected, call_id=CALL_ID),
        {"kind": "control-decision", "label": "approve-file", "answer": "approve"},
        _file_before("retry-file", "edit", protected, call_id=RETRY_CALL_ID),
        {
            "kind": "after", "label": "settle-file", "sessionID": SESSION_ID,
            "callID": RETRY_CALL_ID, "tool": "edit",
            "args": {"file_path": str(protected), "content": "updated\n"},
        },
    ])

    assert _step(driven, "blocked-file")["allowed"] is False
    assert _step(driven, "approve-file")["allowed"] is True
    approval_id = driven["permissionAsks"][0]["permission"]["metadata"]["gaiaApprovalID"]
    assert _approval_status(db_path, approval_id) == "approved"
    assert _grant(db_path, approval_id)["scope"] == "SCOPE_FILE_PATH"
    assert len(driven["controlPrompts"]) == 2, driven
    assert _step(driven, "retry-file")["allowed"] is True
    retry_exchange = next(
        item for item in _tool_exchanges(driven, tool="Edit")
        if item["sent"].get("callID") == RETRY_CALL_ID
    )
    assert retry_exchange["sent"]["consentRetry"] | {
        "version": 1,
        "kind": "SCOPE_FILE_PATH",
        "canonical_path": str(protected.resolve()),
        "path_fingerprint": hashlib.sha256(str(protected.resolve()).encode()).hexdigest(),
        "tool_family": "Edit",
    } == retry_exchange["sent"]["consentRetry"]


def test_file_retry_path_drift_fails_closed_and_clears_the_typed_claim(db_env):
    env, db_path = db_env
    protected = Path(env["WORKSPACE"]) / ".claude" / "hooks" / "pre_tool_use.py"
    wrong = Path(env["WORKSPACE"]) / "src" / "other.py"

    driven = _drive(env, [
        _file_before("blocked-file", "write", protected, call_id=CALL_ID),
        {"kind": "control-decision", "label": "approve-file", "answer": "approve"},
        {
            "kind": "before", "label": "unrelated-read", "sessionID": SESSION_ID,
            "callID": "call-unrelated", "tool": "skill", "args": {"name": "agent-protocol"},
        },
        _file_before("wrong-file", "edit", wrong, call_id="call-wrong-file"),
        _file_before("ordinary-after-clear", "edit", protected, call_id=RETRY_CALL_ID),
    ])

    assert _step(driven, "unrelated-read")["allowed"] is True
    assert _step(driven, "wrong-file")["allowed"] is False
    assert "path_mismatch" in _step(driven, "wrong-file")["error"]
    assert any(item["reason"] == "path_mismatch" for item in _retry_refusals(db_path))
    assert _step(driven, "ordinary-after-clear")["allowed"] is True
    exchange = next(
        item for item in _tool_exchanges(driven, tool="Edit")
        if item["sent"].get("callID") == RETRY_CALL_ID
    )
    assert "consentRetry" not in exchange["sent"]


def _grant(db_path, approval_id):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM approval_grants WHERE approval_id=?", (approval_id,)
    ).fetchone()
    con.close()
    return dict(row) if row is not None else None


def _approval_status(db_path, approval_id):
    with sqlite3.connect(db_path) as con:
        return con.execute(
            "SELECT status FROM approvals WHERE id=?", (approval_id,)
        ).fetchone()[0]


def _row_count(db_path, table):
    if table not in {"approvals", "approval_grants"}:
        raise ValueError(f"unsupported table: {table}")
    with sqlite3.connect(db_path) as con:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _harness_payloads(db_path, event_type):
    """The payloads the bridge appended to harness_events under one type, in order."""
    with sqlite3.connect(db_path) as con:
        rows = con.execute(
            "SELECT payload FROM harness_events WHERE type=? ORDER BY id", (event_type,)
        ).fetchall()
    return [json.loads(row[0]) for row in rows]


def _control_closures(db_path):
    """(reason, approval_id) for every consent.control.closed trace, in order."""
    return [
        (payload["reason"], payload["approval_id"])
        for payload in _harness_payloads(db_path, "consent.control.closed")
    ]


def _retry_refusals(db_path):
    """Every consent.retry.refused trace, reduced to the fields a reader acts on."""
    return [
        {key: payload[key] for key in ("reason", "expected", "received", "approval_id", "call_id", "lane")}
        for payload in _harness_payloads(db_path, "consent.retry.refused")
    ]


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


def test_control_question_is_sealed_and_one_yes_activates_one_bound_grant(db_env):
    env, db_path = db_env
    approval_id = _request_set(env)

    driven = _drive(env, [
        _before("blocked", FIRST_COMMAND),
        {"kind": "control-decision", "label": "approve", "answer": "approve"},
    ])

    assert _step(driven, "blocked")["allowed"] is False, driven
    decision = _step(driven, "approve")
    assert decision["allowed"] is True, driven
    assert decision["controlSessionID"] == SESSION_ID
    question = decision["question"]
    visible = question["question"]
    assert approval_id in visible
    assert "DECISION:" in visible and "correlation C-" in visible
    for command in (FIRST_COMMAND, SECOND_COMMAND):
        assert command in visible
    assert [option["label"] for option in question["options"]] == [
        f"Approve once [{approval_id}]",
        f"Reject [{approval_id}]",
    ]
    prompt = driven["controlPrompts"][0]["body"]
    assert "tools" not in prompt
    assert isinstance(prompt["system"], str), prompt["system"]
    assert "agent" not in prompt
    assert _approval_status(db_path, approval_id) == "approved"
    grant = _grant(db_path, approval_id)
    assert grant is not None
    assert grant["agent_id"] == AGENT_ID
    assert grant["session_id"] == SESSION_ID
    assert grant["status"] == "PENDING"
    assert grant["next_index"] == 0
    assert grant["reservation_tool_use_id"] is None
    assert _row_count(db_path, "approval_grants") == 1
    assert _control_closures(db_path) == [("decided", approval_id)]


def test_pre_hook_accepts_the_measured_host_shape_with_custom_omitted(db_env):
    env, db_path = db_env
    approval_id = _request_set(env)

    driven = _drive(env, [
        _before("blocked", FIRST_COMMAND),
        {
            "kind": "control-decision",
            "label": "approve",
            "answer": "approve",
            "questionCallEncoding": "event-60148",
            "questionEncoding": "event-60148",
        },
    ])

    assert _step(driven, "approve")["allowed"] is True, driven
    assert _approval_status(db_path, approval_id) == "approved"
    assert _grant(db_path, approval_id) is not None
    assert _control_closures(db_path) == [("decided", approval_id)]


def test_a_second_exact_question_call_is_refused_before_decision(db_env):
    env, db_path = db_env
    approval_id = _request_set(env)

    driven = _drive(env, [
        _before("blocked", FIRST_COMMAND),
        {
            "kind": "control-decision",
            "label": "duplicate-call",
            "answer": "approve",
            "questionCallEncoding": "event-60148",
            "secondQuestionCall": True,
        },
    ])

    duplicate = _step(driven, "duplicate-call")
    assert duplicate["allowed"] is False, driven
    assert duplicate["error"] == "Gaia consent control plane permits one exact binary question"
    assert _approval_status(db_path, approval_id) == "pending"
    assert _grant(db_path, approval_id) is None
    assert _control_closures(db_path) == [("drifted_tool_call", approval_id)]


def test_control_prompt_preserves_specialist_permissions_until_typed_retry(db_env):
    env, _db_path = db_env
    original = [{"permission": "bash", "pattern": "*", "action": "ask"}]

    driven = _drive(env, [
        _before("blocked", FIRST_COMMAND),
        {"kind": "control-decision", "label": "approve", "answer": "approve"},
        _before("retry", FIRST_COMMAND, call_id=RETRY_CALL_ID),
    ], initial_permissions=original)

    assert driven["sessionPermissions"][SESSION_ID] == original
    assert driven["permissionTransitions"][0] == {
        "sessionID": SESSION_ID, "before": original, "after": original,
    }
    assert _step(driven, "retry")["allowed"] is True


def test_concurrent_approvals_are_serialized_and_each_native_answer_closes_only_its_control(db_env):
    env, db_path = db_env
    first_call = "call-concurrent-first"
    second_call = "call-concurrent-second"

    driven = _drive(env, [
        {"kind": "concurrent", "steps": [
            _before("blocked-first", FIRST_COMMAND, call_id=first_call),
            _before("blocked-second", SECOND_COMMAND, call_id=second_call),
        ]},
        {"kind": "control-decision", "label": "reject-first", "answer": "reject"},
        {"kind": "control-decision", "label": "reject-second", "answer": "reject"},
    ], permission_id=None)

    assert _step(driven, "blocked-first")["allowed"] is False
    assert _step(driven, "blocked-second")["allowed"] is False
    assert len(driven["permissionAsks"]) == 2
    assert len(driven["controlPrompts"]) == 2
    approval_ids = [
        item["permission"]["metadata"]["gaiaApprovalID"]
        for item in driven["permissionAsks"]
    ]
    assert len(set(approval_ids)) == 2
    assert all(_approval_status(db_path, approval_id) == "rejected" for approval_id in approval_ids)
    assert sorted(_control_closures(db_path)) == sorted([
        ("decided", approval_ids[0]), ("decided", approval_ids[1]),
    ])


def test_late_approval_waits_through_decision_idle_and_first_retry_lifecycle(db_env):
    env, db_path = db_env
    second_call = "call-late-second"
    retry_call = "call-late-first-retry"

    driven = _drive(env, [
        _before("blocked-first", FIRST_COMMAND),
        {"kind": "control-decision", "label": "approve-first", "answer": "approve", "deferIdle": True},
        _before("blocked-second-before-idle", SECOND_COMMAND, call_id=second_call),
        {"kind": "observe-controls", "label": "before-idle"},
        {"kind": "lifecycle", "label": "first-idle", "sessionID": SESSION_ID, "eventType": "session.idle"},
        {"kind": "observe-controls", "label": "after-idle"},
        _before("retry-first", FIRST_COMMAND, call_id=retry_call),
        {
            "kind": "after", "label": "settle-first", "sessionID": SESSION_ID,
            "callID": retry_call, "tool": "bash", "command": FIRST_COMMAND,
            "metadata": {"exitCode": 0},
        },
        {"kind": "observe-controls", "label": "after-first-settlement"},
        {"kind": "control-decision", "label": "reject-second", "answer": "reject"},
    ], permission_id=None)

    assert _step(driven, "blocked-first")["allowed"] is False
    assert _step(driven, "blocked-second-before-idle")["allowed"] is False
    assert _step(driven, "before-idle")["specialistControlPromptCount"] == 1
    assert _step(driven, "after-idle")["specialistControlPromptCount"] == 1
    assert _step(driven, "retry-first")["allowed"] is True
    assert _step(driven, "after-first-settlement")["specialistControlPromptCount"] == 2

    approval_ids = [
        item["permission"]["metadata"]["gaiaApprovalID"]
        for item in driven["permissionAsks"]
    ]
    assert len(approval_ids) == 2
    assert _approval_status(db_path, approval_ids[0]) == "approved"
    assert _approval_status(db_path, approval_ids[1]) == "rejected"
    assert sorted(_control_closures(db_path)) == sorted([
        ("decided", approval_ids[0]), ("decided", approval_ids[1]),
    ])


def test_an_activated_approval_is_announced_to_the_orchestrator_and_traced(db_env):
    """The user's yes has an actor: the root session is prompted to resume the specialist.

    The specialist's turn ended with the blocked attempt, so the notice goes to
    the orchestrator's ROOT session, naming the specialist session to resume
    with task_id and the index it must retry.
    """
    env, db_path = db_env
    approval_id = _request_set(env)

    driven = _drive(env, [
        _before("blocked", FIRST_COMMAND),
        {"kind": "control-decision", "label": "approve", "answer": "approve"},
    ])

    prompts = driven["controlPrompts"]
    assert len(prompts) == 2, prompts
    notice = prompts[1]
    assert notice["path"] == {"id": ROOT_SESSION_ID}
    assert notice["body"]["parts"] == [{
            "type": "text",
            "text": (
                f"Gaia: approval {approval_id} has an executable COMMAND_SET grant "
                "and typed retry descriptor. "
                f"Resume the specialist session {SESSION_ID} (task_id) so it retries command [0] now."
            ),
    }]
    assert "agent" not in notice["body"]
    applied = _harness_payloads(db_path, "consent.decision.applied")
    assert len(applied) == 1, applied
    assert applied[0]["approval_id"] == approval_id
    assert applied[0]["session_id"] == SESSION_ID
    assert applied[0]["call_id"] == CALL_ID
    assert applied[0]["control_session_id"] == _step(driven, "approve")["controlSessionID"]
    assert applied[0]["reply"] == "once"
    assert applied[0]["lane"] == "control"
    assert applied[0]["next_index"] == 0
    assert applied[0]["notified_session_id"] == ROOT_SESSION_ID
    assert "notify_failure" not in applied[0]


def test_a_rejection_is_not_announced(db_env):
    env, db_path = db_env
    _request_set(env)

    driven = _drive(env, [
        _before("blocked", FIRST_COMMAND),
        {"kind": "control-decision", "label": "reject", "answer": "reject"},
    ])

    assert len(driven["controlPrompts"]) == 1, driven["controlPrompts"]
    assert _harness_payloads(db_path, "consent.decision.applied") == []


@pytest.mark.parametrize("encoding", ["reordered", "extra-keys", "event-60148"])
def test_the_hosts_own_encoding_of_the_question_still_correlates(db_env, encoding):
    """question.asked carries the host's re-serialization of the question, not the plugin's bytes.

    OpenCode's QuestionInfo orders keys question, header, options, multiple and
    may add or omit optional `custom`; the control must correlate on the
    consent-bearing fields after validating the exact tool call, or every live
    answer closes the control before it can be read.
    """
    env, db_path = db_env
    approval_id = _request_set(env)

    driven = _drive(env, [
        _before("blocked", FIRST_COMMAND),
        {"kind": "control-decision", "label": "approve", "answer": "approve", "questionEncoding": encoding},
    ])

    assert _step(driven, "approve")["allowed"] is True, driven
    assert _approval_status(db_path, approval_id) == "approved"
    assert _grant(db_path, approval_id) is not None
    assert _control_closures(db_path) == [("decided", approval_id)]


def test_event_60148_correlates_while_prompt_async_is_still_resolving(db_env):
    env, db_path = db_env
    approval_id = _request_set(env)

    driven = _drive(env, [
        _before("blocked", FIRST_COMMAND),
        {"kind": "control-reply", "label": "approve", "answer": "approve"},
    ], question_during_prompt=True)

    assert _step(driven, "blocked")["allowed"] is False, driven
    assert _step(driven, "approve")["allowed"] is True, driven
    assert _approval_status(db_path, approval_id) == "approved"
    assert _grant(db_path, approval_id) is not None
    assert _control_closures(db_path) == [("decided", approval_id)]


def test_control_waits_for_original_turn_idle_before_the_measured_delayed_duplicate_shape(db_env):
    env, db_path = db_env
    approval_id = _request_set(env)

    driven = _drive(env, [
        _before("blocked", FIRST_COMMAND),
        {"kind": "observe-controls", "label": "before-safe-idle"},
        {"kind": "lifecycle", "label": "safe-idle", "sessionID": SESSION_ID, "eventType": "session.idle"},
        {"kind": "control-reply", "label": "reject", "answer": "reject"},
    ], question_during_prompt=True, duplicate_question_when_prompt_races=True, auto_safe_idle=False)

    assert _step(driven, "blocked")["allowed"] is False, driven
    assert _step(driven, "before-safe-idle")["specialistControlPromptCount"] == 0
    assert len(driven["controlPrompts"]) == 1
    assert driven["controlQuestionBeforeCalls"] == 1
    assert driven["racingDuplicateAttempted"] is False
    assert _step(driven, "reject")["allowed"] is True
    assert _approval_status(db_path, approval_id) == "rejected"
    assert _grant(db_path, approval_id) is None
    assert _control_closures(db_path) == [("decided", approval_id)]


def test_an_exact_pre_call_question_event_is_not_accepted_as_gaia_provenance(db_env):
    env, db_path = db_env
    approval_id = _request_set(env)

    driven = _drive(env, [
        _before("blocked", FIRST_COMMAND),
        {
            "kind": "question-event",
            "label": "unrelated-exact-event",
            "questionEncoding": "event-60148",
        },
    ])

    assert _step(driven, "unrelated-exact-event")["allowed"] is True, driven
    assert _approval_status(db_path, approval_id) == "pending"
    assert _grant(db_path, approval_id) is None
    assert _control_closures(db_path) == [("question_mismatch", approval_id)]
    closed = _harness_payloads(db_path, "consent.control.closed")[0]
    assert "preceded the validated Gaia call" in closed["detail"]


def test_a_later_question_event_closes_the_correlated_control_without_a_decision(db_env):
    env, db_path = db_env
    approval_id = _request_set(env)

    driven = _drive(env, [
        _before("blocked", FIRST_COMMAND),
        {
            "kind": "control-decision",
            "label": "duplicate",
            "answer": "approve",
            "questionEncoding": "event-60148",
            "duplicateQuestionEvent": True,
        },
    ])

    assert _step(driven, "duplicate")["allowed"] is True, driven
    assert _approval_status(db_path, approval_id) == "pending"
    assert _grant(db_path, approval_id) is None
    assert _control_closures(db_path) == [("question_mismatch", approval_id)]
    closed = _harness_payloads(db_path, "consent.control.closed")[0]
    assert "second question" in closed["detail"]


def test_unrelated_tool_call_is_denied_before_policy_and_releases_only_the_active_queue_entry(db_env):
    env, db_path = db_env
    first_call = "call-unrelated-first"
    second_call = "call-unrelated-second"
    unrelated_call = "call-unrelated-during-control"

    driven = _drive(env, [
        {"kind": "concurrent", "steps": [
            _before("blocked-first", FIRST_COMMAND, call_id=first_call),
            _before("blocked-second", SECOND_COMMAND, call_id=second_call),
        ]},
        _before("unrelated", "pwd", call_id=unrelated_call),
        {"kind": "observe-controls", "label": "before-idle"},
        {"kind": "lifecycle", "label": "idle", "sessionID": SESSION_ID, "eventType": "session.idle"},
        {"kind": "observe-controls", "label": "after-idle"},
        {"kind": "control-decision", "label": "reject-second", "answer": "reject"},
    ], permission_id=None)

    unrelated = _step(driven, "unrelated")
    assert unrelated["allowed"] is False, driven
    assert unrelated["error"] == "Gaia consent control plane permits one exact binary question"
    assert all(
        exchange["sent"].get("callID") != unrelated_call
        for exchange in driven["exchanges"]
    ), driven["exchanges"]
    assert _step(driven, "before-idle")["specialistControlPromptCount"] == 1
    assert _step(driven, "after-idle")["specialistControlPromptCount"] == 2

    approval_ids = [
        item["permission"]["metadata"]["gaiaApprovalID"]
        for item in driven["permissionAsks"]
    ]
    assert len(approval_ids) == 2
    closures = _control_closures(db_path)
    assert [reason for reason, _approval_id in closures] == ["drifted_tool_call", "decided"]
    drifted_id = closures[0][1]
    decided_id = closures[1][1]
    assert {drifted_id, decided_id} == set(approval_ids)
    assert _approval_status(db_path, drifted_id) == "pending"
    assert _approval_status(db_path, decided_id) == "rejected"


def test_a_question_that_is_not_gaias_closes_the_control_with_a_trace(db_env):
    env, db_path = db_env
    approval_id = _request_set(env)

    driven = _drive(env, [
        _before("blocked", FIRST_COMMAND),
        {"kind": "control-decision", "label": "mismatch", "answer": "approve", "questionEncoding": "mismatch"},
    ])

    # The driver still delivers an approving reply for the host's question; the
    # control closed on question.asked, so that reply reaches no decision.
    assert _approval_status(db_path, approval_id) == "pending"
    assert _grant(db_path, approval_id) is None
    assert _control_closures(db_path) == [("question_mismatch", approval_id)]
    closed = _harness_payloads(db_path, "consent.control.closed")[0]
    assert closed["detail"].startswith("host asked "), closed
    assert closed["control_session_id"] == _step(driven, "mismatch")["controlSessionID"]
    assert closed["session_id"] == SESSION_ID
    assert closed["call_id"] == CALL_ID


@pytest.mark.parametrize(
    ("steps", "expected_status", "expected_closures"),
    [
        ([{"kind": "control-decision", "label": "decision", "answer": "reject"}], "rejected", ["decided"]),
        ([{"kind": "control-decision", "label": "decision", "answer": "Approve"}], "pending", ["reply_unreadable"]),
        ([{"kind": "control-decision", "label": "decision", "answers": []}], "pending", ["reply_unreadable"]),
        ([{
            "kind": "replied",
            "label": "decision",
            "requestID": PERMISSION_ID,
            "reply": "always",
        }], "pending", ["decide_failed"]),
        ([], "pending", []),
    ],
    ids=["reject", "free-text", "malformed", "autoapproval", "no-decision"],
)
def test_non_yes_decisions_create_no_executable_effect(db_env, steps, expected_status, expected_closures):
    env, db_path = db_env
    approval_id = _request_set(env)

    driven = _drive(env, [_before("blocked", FIRST_COMMAND), *steps])

    assert _step(driven, "blocked")["allowed"] is False, driven
    assert _approval_status(db_path, approval_id) == expected_status
    assert _grant(db_path, approval_id) is None
    assert _row_count(db_path, "approval_grants") == 0
    assert _row_count(db_path, "approvals") == 1
    assert _control_closures(db_path) == [(reason, approval_id) for reason in expected_closures]


def test_a_reply_gaia_refuses_is_traced_with_its_cause_and_releases_the_control(db_env):
    """The user answered, opencode-decide refused: the refusal must be queryable by approval.

    The approval is rejected out of band between the presentation and the
    answer, so the real CLI refuses the 'once' reply. The plugin traces the
    cause (decide_failed) and clears the pending control instead of keeping a
    control whose question is already consumed.
    """
    env, db_path = db_env
    approval_id = _request_set(env)

    driven = _drive(env, [
        _before("blocked", FIRST_COMMAND),
        {"kind": "gaia", "label": "rejected-out-of-band", "args": ["approvals", "reject", approval_id]},
        {"kind": "control-decision", "label": "approve", "answer": "approve"},
        {"kind": "replied", "label": "late-host-reply", "requestID": PERMISSION_ID, "reply": "once"},
    ])

    assert _step(driven, "rejected-out-of-band")["allowed"] is True, driven
    # `gaia approvals reject` marks a presented approval revoked; what matters
    # here is that the user's later 'once' could not turn it into a grant.
    assert _approval_status(db_path, approval_id) not in ("pending", "approved")
    assert _grant(db_path, approval_id) is None
    refusals = _harness_payloads(db_path, "consent.decision.not_activated")
    decide_refusals = [p for p in refusals if p["reason"] == "decide_failed"]
    assert len(decide_refusals) == 1, refusals
    assert decide_refusals[0]["approval_id"] == approval_id
    assert decide_refusals[0]["session_id"] == SESSION_ID
    assert decide_refusals[0]["details"]["call_id"] == CALL_ID
    assert decide_refusals[0]["detail"], decide_refusals[0]
    # One closure, with Gaia's cause; the late host reply finds no pending
    # approval and records nothing more.
    assert _control_closures(db_path) == [("decide_failed", approval_id)]
    assert _harness_payloads(db_path, "consent.control.closed")[0]["detail"] == decide_refusals[0]["detail"]


def test_drift_after_yes_is_refused_before_policy_and_changes_no_state(db_env):
    env, db_path = db_env
    approval_id = _request_set(env)
    drifted = FIRST_COMMAND + " "

    driven = _drive(env, [
        _before("blocked", FIRST_COMMAND),
        {"kind": "control-decision", "label": "approve", "answer": "approve"},
        _before("drifted", drifted, call_id=RETRY_CALL_ID),
    ])

    assert _step(driven, "drifted")["allowed"] is False, driven
    drifted_fingerprint = hashlib.sha256(drifted.encode("utf-8")).hexdigest()
    # The specialist is told which comparison refused it, not a generic verdict.
    assert _step(driven, "drifted")["error"] == (
        f"Gaia refused consent retry for {approval_id}: fingerprint_mismatch "
        f"(expected {FIRST_FINGERPRINT}, received {drifted_fingerprint})"
    )
    assert len(_tool_exchanges(driven)) == 1
    grant = _grant(db_path, approval_id)
    assert grant["status"] == "PENDING"
    assert grant["next_index"] == 0
    assert grant["reservation_tool_use_id"] is None
    assert _row_count(db_path, "approval_grants") == 1
    assert _row_count(db_path, "approvals") == 1
    assert _retry_refusals(db_path) == [{
        "reason": "fingerprint_mismatch",
        "expected": FIRST_FINGERPRINT,
        "received": drifted_fingerprint,
        "approval_id": approval_id,
        "call_id": RETRY_CALL_ID,
        "lane": "opencode.plugin_gate",
    }]
    with sqlite3.connect(db_path) as con:
        severities = con.execute(
            "SELECT severity FROM harness_events WHERE type='consent.retry.refused'"
        ).fetchall()
    assert severities == [("warning",)]


def test_replayed_retry_is_refused_before_a_second_policy_effect(db_env):
    env, db_path = db_env
    approval_id = _request_set(env)

    driven = _drive(env, [
        _before("blocked", FIRST_COMMAND),
        {"kind": "control-decision", "label": "approve", "answer": "approve"},
        _before("retry", FIRST_COMMAND, call_id=RETRY_CALL_ID),
        _before("replay", FIRST_COMMAND, call_id=RETRY_CALL_ID),
    ])

    assert _step(driven, "retry")["allowed"] is True, driven
    assert _step(driven, "replay")["allowed"] is False, driven
    assert _step(driven, "replay")["error"] == (
        f"Gaia refused consent retry for {approval_id}: replayed_call_id "
        f"(expected an unused call id, received {RETRY_CALL_ID})"
    )
    assert len(_tool_exchanges(driven)) == 2
    # The accepted retry left no refusal behind; only the replay did.
    assert [(r["reason"], r["call_id"]) for r in _retry_refusals(db_path)] == [
        ("replayed_call_id", RETRY_CALL_ID),
    ]
    grant = _grant(db_path, approval_id)
    assert grant["status"] == "PENDING"
    assert grant["next_index"] == 0
    assert grant["reservation_tool_use_id"] == RETRY_CALL_ID
    assert _row_count(db_path, "approval_grants") == 1
    assert _row_count(db_path, "approvals") == 1


def test_fresh_bound_retry_reserves_exact_index_executes_settles_and_freezes(db_env):
    """The whole chain, from the original call to one fresh bound retry.

    The retry keeps the agent/session/approval identity and exact command bytes
    while using a fresh call id; the reservation is the exact index; the failure
    freezes the set; and the freeze is asserted as the grant's terminal state.
    """
    env, db_path = db_env
    approval_id = _request_set(env)
    from gaia.approvals.command_set import command_fingerprint, request_fingerprint

    # Attempt BEFORE any reply exists: no executable grant, so the tool call is
    # refused. This is the invocation the retry must later match identically.
    driven = _drive(
        env,
        [
            _before("pre-approval", FIRST_COMMAND),
            {"kind": "control-decision", "label": "approve", "answer": "approve"},
            _before("retry", FIRST_COMMAND, call_id=RETRY_CALL_ID),
            {
                "kind": "after", "label": "settle", "sessionID": SESSION_ID,
                "callID": RETRY_CALL_ID, "tool": "bash", "command": FIRST_COMMAND,
                "output": "fatal: remote rejected", "metadata": {"exitCode": 7},
            },
            _before("later-index", SECOND_COMMAND, call_id=LATER_CALL_ID),
        ],
    )
    first_attempt = _step(driven, "pre-approval")
    assert first_attempt["allowed"] is False, driven
    assert _step(driven, "approve")["allowed"] is True, driven
    retry_step = _step(driven, "retry")
    assert retry_step["allowed"] is True, driven
    first_exchange, retry_exchange = _tool_exchanges(driven)[:2]

    # Fresh call, same bound identity, byte-identical input and fingerprint.
    assert retry_exchange["sent"]["sessionID"] == first_exchange["sent"]["sessionID"] == SESSION_ID
    assert first_exchange["sent"]["callID"] == CALL_ID
    assert retry_exchange["sent"]["callID"] == RETRY_CALL_ID
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

    assert _observed(first_exchange) == {
        "session_id": SESSION_ID,
        "call_id": CALL_ID,
        "args": '{"command":"' + FIRST_COMMAND + '"}',
        "fingerprint": FIRST_FINGERPRINT,
    }
    assert _observed(retry_exchange) == {
        "session_id": SESSION_ID,
        "call_id": RETRY_CALL_ID,
        "args": '{"command":"' + FIRST_COMMAND + '"}',
        "fingerprint": FIRST_FINGERPRINT,
    }
    proof = retry_exchange["sent"]["consentRetry"]
    assert proof == {
        "approval_id": approval_id,
        "version": 1,
        "kind": "COMMAND_SET",
        "correlation_id": (
            driven["permissionAsks"][0]["permission"]["metadata"]
            ["gaiaConsent"]["correlation_id"]
        ),
        "agent_id": AGENT_ID,
        "role": AGENT_ID,
        "session_id": SESSION_ID,
        "original_call_id": CALL_ID,
        "retry_call_id": RETRY_CALL_ID,
        "command": FIRST_COMMAND,
        "command_fingerprint": FIRST_FINGERPRINT,
        "expected_index": 0,
        "request_fingerprint": request_fingerprint([FIRST_COMMAND, SECOND_COMMAND]),
    }

    grant = _grant(db_path, approval_id)
    assert grant is not None and grant["scope"] == "COMMAND_SET"
    assert grant["source"] == "plan-first"
    # A retry that matched its bound grant is not a refusal: no trace at all.
    assert _harness_payloads(db_path, "consent.retry.refused") == []

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
    assert _step(driven, "later-index")["allowed"] is False, driven
    from gaia.store import writer

    assert writer.reserve_plan_command(
        SECOND_COMMAND, session_id=SESSION_ID, tool_use_id="any-later-call",
        db_path=db_path,
    ) is None
    assert writer.reserve_plan_command(
        FIRST_COMMAND, session_id=SESSION_ID, tool_use_id="any-retry-call",
        db_path=db_path,
    ) is None


def test_a_retry_the_host_gate_refuses_after_gaia_allowed_it_is_traced_by_approval(db_env):
    """Bloqueo -> present -> decide -> retry with accepted proof -> host gate refuses.

    OpenCode evaluates its ruleset inside tool.execute and throws before
    tool.execute.after (measured 2026-09-17: cp /dev/null under
    external_directory "*": "deny"). The only host signal is the errored tool
    part; without this handling the allowed retry vanished with no trace.
    """
    env, db_path = db_env
    approval_id = _request_set(env)
    host_error = (
        "The user has specified a rule which prevents you from using this specific "
        'tool call. Here are some of the relevant rules [{"permission":"external_directory",'
        '"pattern":"*","action":"deny"}]'
    )
    driven = _drive(
        env,
        [
            _before("pre-approval", FIRST_COMMAND),
            {"kind": "control-decision", "label": "approve", "answer": "approve"},
            _before("retry", FIRST_COMMAND, call_id=RETRY_CALL_ID),
            {
                "kind": "tool-error-part", "label": "host-refused", "sessionID": SESSION_ID,
                "callID": RETRY_CALL_ID, "tool": "bash", "error": host_error,
            },
        ],
    )
    assert _step(driven, "retry")["allowed"] is True, driven
    assert _step(driven, "host-refused")["allowed"] is True, driven

    refusals = _harness_payloads(db_path, "consent.retry.refused")
    assert len(refusals) == 1, refusals
    refused = refusals[0]
    assert refused["approval_id"] == approval_id
    assert refused["reason"] == "host_gate_refused"
    assert refused["lane"] == "opencode.plugin_gate"
    assert refused["session_id"] == SESSION_ID
    assert refused["call_id"] == RETRY_CALL_ID
    assert refused["expected"] == "host execution of allowed command [0]"
    assert refused["received"] == host_error

    # The host refusal is neither a failed run nor withdrawn consent: the grant
    # is not frozen, and the slot is left to the reservation TTL.
    grant = _grant(db_path, approval_id)
    assert grant["status"] == "PENDING"
    assert grant["failed_index"] is None
    assert grant["reservation_tool_use_id"] == RETRY_CALL_ID


def test_reservation_is_bound_to_the_retrying_call_not_merely_to_the_command(db_env):
    """A different call cannot settle the reservation the retry established."""
    env, db_path = db_env
    approval_id = _request_set(env)
    driven = _drive(env, [
        _before("blocked", FIRST_COMMAND),
        {"kind": "control-decision", "label": "approve", "answer": "approve"},
        _before("retry", FIRST_COMMAND, call_id=RETRY_CALL_ID),
    ])
    assert _step(driven, "retry")["allowed"] is True, driven
    reserved = _grant(db_path, approval_id)
    assert reserved["reservation_index"] == 0
    assert reserved["reservation_session_id"] == SESSION_ID
    assert reserved["reservation_tool_use_id"] == RETRY_CALL_ID

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
        approval_id, session_id=SESSION_ID, tool_use_id=RETRY_CALL_ID,
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

    # The plugin enriched exactly one host-created permission, carrying the approval
    # the bridge named and a visible surface Gaia sealed.
    assert len(driven["permissionAsks"]) == 1, driven
    presented = driven["permissionAsks"][0]["permission"]
    presented_id = presented["metadata"]["gaiaApprovalID"]
    assert presented["sessionID"] == SESSION_ID
    assert presented["metadata"]["gaiaCallID"] == CALL_ID
    assert presented["pattern"], presented

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


def test_plugin_reply_lane_rejects_the_exact_host_permission_request(db_env):
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
    assert driven["permissionAsks"][0]["status"] == "ask", driven
    assert _step(driven, "rejected")["allowed"] is True, driven
    with sqlite3.connect(db_path) as con:
        status = con.execute("SELECT status FROM approvals WHERE id=?", (approval_id,)).fetchone()[0]
    assert status in {"rejected", "REJECTED"}, status


def test_a_blocked_attempt_surfaces_the_pending_plan_first_approval(db_env):
    """The block path names the pending set, not a freshly minted singular id.

    This assertion is the inverse of the one it replaces. The former detector
    asserted the two ids DIVERGE and said in its own docstring that it would
    fail the day they converge; this is that day, so the detector is inverted
    rather than deleted -- the same observation, read for the outcome that is
    now correct. The id is read out of the plugin's own
        ``permissionAsks[0].permission.metadata.gaiaApprovalID``, so what is asserted is
    what the plugin presented, never a value this test supplied.

    Both items are attempted, each on its own plugin run. At pending time the
    set has consumed nothing, so every item belongs to the consent being
    sought and each must name the set. Naming it is not permission to run it
    out of order: ``reserve_plan_command`` still matches only at
    ``next_index``, and that ordering is asserted by the reservation test
    above.

    The id the block path surfaced is then carried into the decide entry
    point, and the grant it activates is asserted to be the plan-first set --
    scope, source, index and item count -- so the reply lane's COMMAND_SET
    branch is shown to be the one that ran, not a singular approval.
    """
    env, db_path = db_env
    approval_id = _request_set(env)

    presented_ids = []
    for label, command, call_id in (
        ("blocked-first", FIRST_COMMAND, CALL_ID),
        ("blocked-second", SECOND_COMMAND, LATER_CALL_ID),
    ):
        driven = _drive(env, [_before(label, command, call_id=call_id)])
        assert _step(driven, label)["allowed"] is False, driven
        assert len(driven["permissionAsks"]) == 1, driven
        presented_ids.append(
            driven["permissionAsks"][0]["permission"]["metadata"]["gaiaApprovalID"]
        )

        assert presented_ids[-1] == approval_id, (
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

    # The id fed to the decide entry point is the one the BLOCK PATH surfaced,
    # read out of the plugin's presentation metadata -- never the one
    # _request_set returned -- so this leg cannot pass on a value the test
    # supplied. SUBSTITUTED LINK: the host's permission.replied event. What
    # runs instead is the CLI pair the plugin's own reply lane invokes, with
    # the presentation that binds the token the reply must carry; that OpenCode
    # delivers the event at all is not established here.
    decision = _approve_set(env, presented_ids[0])
    assert decision["decision"] == "once"
    assert decision["status"] == "approved"

    grant = _grant(db_path, presented_ids[0])
    assert grant is not None, "the reply lane activated no grant at all"
    assert grant["scope"] == "COMMAND_SET", grant
    assert grant["source"] == "plan-first", grant
    assert int(grant["next_index"]) == 0, grant
    assert len(json.loads(grant["command_set_json"])) == 2, grant


def test_plugin_delegates_the_permission_request_to_the_host_hook():
    """The adapter does not fabricate a native permission creator.

    Gaia registers the presentation before aborting the original invocation.
    The permission hook enriches a correlated host request and waits for its
    reply event; only a fresh invocation may execute after approval.
    """
    source = PLUGIN.read_text()
    assert '"permission.ask"' in source
    assert "session.permission.create" not in source
    assert 'await requestApproval(response, call.sessionID, call.callID, agent ?? "")\n        throw new Error' in source


def test_overlapping_bound_workspaces_settle_independently(tmp_path, bootstrapped_db_template):
    """Keep two identical call reservations outstanding, then settle opposite outcomes."""
    contexts = [
        _isolated_env(tmp_path / name, bootstrapped_db_template)
        for name in ("success", "failure")
    ]
    approvals = []
    reserved = []
    for env, db in contexts:
        approval = _request_set(env, (FIRST_COMMAND,))
        approvals.append(approval)
        reserved.append(_drive(env, [
            _before("blocked", FIRST_COMMAND),
            {"kind": "control-decision", "label": "approve", "answer": "approve"},
            _before("reserve", FIRST_COMMAND, call_id=RETRY_CALL_ID),
        ]))
    for (env, db), approval, driven in zip(contexts, approvals, reserved):
        assert _step(driven, "reserve")["allowed"] is True
        grant = _grant(db, approval)
        assert grant["reservation_session_id"] == SESSION_ID
        assert grant["reservation_tool_use_id"] == RETRY_CALL_ID
        assert _grant(db, approvals[1 - approvals.index(approval)]) is None

    from modules.core.state import STATE_DIR_NAME

    state_paths = [Path(env["WORKSPACE"]) / ".claude" / STATE_DIR_NAME
                    / f"{SESSION_ID}__{RETRY_CALL_ID}.json" for env, _ in contexts]
    snapshots = [path.read_bytes() for path in state_paths]
    assert state_paths[0] != state_paths[1]
    for index, ((env, db), approval) in enumerate(zip(contexts, approvals)):
        driven = _drive(env, [{
            "kind": "after", "label": "settle", "sessionID": SESSION_ID,
            "callID": RETRY_CALL_ID, "tool": "bash", "command": FIRST_COMMAND,
            "output": "success" if index == 0 else "fatal: rejected",
            "metadata": {"exitCode": 0 if index == 0 else 7},
        }])
        assert _step(driven, "settle")["allowed"] is True
        grant = _grant(db, approval)
        assert grant["status"] == ("CONSUMED" if index == 0 else "FAILED")
        assert json.loads(grant["consumed_indexes_json"]) == ([0] if index == 0 else [])
        assert grant["reservation_tool_use_id"] is None
        if index == 0:
            assert state_paths[1].read_bytes() == snapshots[1]
            assert _grant(contexts[1][1], approvals[1])["reservation_tool_use_id"] == RETRY_CALL_ID
    assert _grant(contexts[0][1], approvals[0])["status"] == "CONSUMED"
