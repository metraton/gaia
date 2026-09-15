"""A question-lane approval must execute on OpenCode exactly as on Claude Code.

The set is sealed by the real ``gaia approvals request-set`` producer and
approved through the real ``activate_db_pending_by_id`` bridge -- the lane the
orchestrator's own approval skill drives. What the tests below add is the
execution side: the OpenCode boundary with no consent proof at all (the shape
the question lane can produce), a supplied proof still verified clause by
clause, and the durable record every refusal now leaves on its approval.

WHAT IS NOT PROVEN: no OpenCode host runs here, so the tool call is issued by
the driver rather than observed being issued by OpenCode. What is established is
which verdict Gaia returns for an event carrying those identities.
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

ORCHESTRATOR_SESSION = "ses-orchestrator"
SUBAGENT_SESSION = "ses-subagent"
SUBAGENT_CALL = "call-subagent-1"
AGENT_ID = "gaia-system"
FIRST_COMMAND = "git push origin main"
SECOND_COMMAND = "docker push registry/app:1"


@pytest.fixture()
def substrate(tmp_path, monkeypatch):
    """Point every lane at one private database carrying the real schema."""
    from gaia.store import writer

    db_path = tmp_path / "gaia.db"
    con = sqlite3.connect(db_path)
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_DB", str(db_path))
    monkeypatch.setenv("GAIA_OPENCODE_ATTESTATION_DIR", str(tmp_path / "ledger"))
    monkeypatch.setenv("CLAUDE_SESSION_ID", SUBAGENT_SESSION)
    monkeypatch.chdir(tmp_path)
    return db_path


def _bind_child_session(db_path, session_id=SUBAGENT_SESSION):
    """Satisfy the dispatch-binding backstop, which is not the gate under test.

    The backstop denies a dispatched child until its Task call reports the
    callID<->child-session pair; it has its own coverage, and without this row
    every case below would fail on it before reaching consent at all.
    """
    con = sqlite3.connect(db_path)
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name) VALUES ('default')")
        con.execute(
            "INSERT INTO agent_contract_handoffs "
            "(agent_id, workspace, agent_state, raw_handoff_json, harness_agent_id) "
            "VALUES (?, 'default', 'IN_PROGRESS', '{}', ?)",
            ("a" + "0" * 16, session_id),
        )
        con.commit()
    finally:
        con.close()


@pytest.fixture()
def attestation(substrate):
    """Mint the subagent's host attestation in this test's own ledger."""
    from modules.security.host_attestation import host_run_id, issue

    return issue(
        host_run=host_run_id(),
        session_id=SUBAGENT_SESSION,
        role=AGENT_ID,
        issuer="opencode-runtime",
    ).token


def _request_set(tmp_path, commands=(FIRST_COMMAND, SECOND_COMMAND)):
    """Seal the set with the real plan-first producer, never by hand."""
    argv = [sys.executable, str(GAIA_CLI), "approvals", "request-set"]
    for command in commands:
        argv += ["--command", command]
    argv += [
        "--rationale", "Publish the branch and the image under one consent",
        "--verification", "git -C . log --oneline -1",
        "--rollback", "revert the published revision",
        "--agent-id", AGENT_ID,
        "--session-id", SUBAGENT_SESSION,
        "--json",
    ]
    env = dict(os.environ)
    result = subprocess.run(
        argv, cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])["approval_id"]


def _approve_in_question_lane(approval_id, *, session_id=ORCHESTRATOR_SESSION):
    """Approve exactly as the orchestrator's AskUserQuestion lane does."""
    from modules.security.approval_grants import activate_db_pending_by_id

    result = activate_db_pending_by_id(approval_id, current_session_id=session_id)
    assert result.success, result.reason
    return result


def _bash_event(
    command, token, *, session_id=SUBAGENT_SESSION, call_id=SUBAGENT_CALL, proof=None,
):
    from adapters.opencode import OpenCodeAdapter

    payload = {
        "event": "tool.execute.before",
        "sessionID": session_id,
        "callID": call_id,
        "agent": AGENT_ID,
        "agentID": AGENT_ID,
        "roleContext": {
            "role": AGENT_ID,
            "issuer": "opencode-runtime",
            "attestation": token,
            "verified": True,
        },
        "tool": "bash",
        "args": {"command": command},
    }
    if proof is not None:
        payload["consentRetry"] = proof
    return OpenCodeAdapter().parse_event(json.dumps(payload))


def _grant_row(db_path, approval_id):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return dict(
            con.execute(
                "SELECT * FROM approval_grants WHERE approval_id=?", (approval_id,)
            ).fetchone()
        )
    finally:
        con.close()


def _approval_status(db_path, approval_id):
    con = sqlite3.connect(db_path)
    try:
        return con.execute(
            "SELECT status FROM approvals WHERE id=?", (approval_id,)
        ).fetchone()[0]
    finally:
        con.close()


def _denial_records(db_path, approval_id):
    """Return the execution-denial metadata recorded against an approval."""
    from gaia.approvals.store import EXECUTION_DENIAL_KIND

    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT metadata_json FROM approval_events "
            "WHERE approval_id=? AND event_type='NOOP' ORDER BY id",
            (approval_id,),
        ).fetchall()
    finally:
        con.close()
    records = []
    for (metadata_json,) in rows:
        metadata = json.loads(metadata_json or "{}")
        if metadata.get("kind") == EXECUTION_DENIAL_KIND:
            records.append(metadata)
    return records


def test_question_lane_grant_executes_under_opencode_without_a_proof(
    substrate, tmp_path, attestation,
):
    """The regression: an approval granted in the question lane must run.

    The grant is approved under the ORCHESTRATOR's session and the command is
    attempted from the SUBAGENT's, with no consentRetry -- the only shape the
    AskUserQuestion lane can produce. OpenCode used to deny it for lacking proof
    that lane cannot mint.
    """
    approval_id = _request_set(tmp_path)
    _approve_in_question_lane(approval_id)
    _bind_child_session(substrate)

    from adapters.opencode import OpenCodeAdapter

    response = OpenCodeAdapter().adapt_pre_tool_use(
        _bash_event(FIRST_COMMAND, attestation)
    )

    assert response.output.get("action") == "allow", response.output
    row = _grant_row(substrate, approval_id)
    assert row["session_id"] == ORCHESTRATOR_SESSION
    assert row["reservation_session_id"] == SUBAGENT_SESSION
    assert row["reservation_tool_use_id"] == SUBAGENT_CALL
    assert row["reservation_index"] == 0


def _valid_proof(event, approval_id, *, index=0, command=FIRST_COMMAND):
    import hashlib

    from adapters.consent_events import mint_correlation_id
    from adapters.opencode import ConsentBinding, OpenCodeAdapter

    original_call_id = "call-original"
    agent_id = OpenCodeAdapter._policy_agent_type(event)
    return {
        "approval_id": approval_id,
        "agent_id": agent_id,
        "session_id": event.session_id,
        "original_call_id": original_call_id,
        "retry_call_id": event.call_id,
        "command": command,
        "command_fingerprint": hashlib.sha256(command.encode("utf-8")).hexdigest(),
        "expected_index": index,
        "correlation_id": mint_correlation_id(
            approval_id,
            ConsentBinding(
                agent_id=agent_id,
                session_id=event.session_id,
                call_id=original_call_id,
            ),
        ),
    }


@pytest.mark.parametrize("drift", ["expected_index", "command_fingerprint", "correlation_id"])
def test_a_supplied_proof_is_still_verified_after_the_demand_is_dropped(
    substrate, tmp_path, attestation, drift,
):
    """Dropping the demand for a proof did not weaken a proof that IS supplied.

    The grant is activated under the retrying session here because the bound-
    grant clause requires that binding: with any other session the proof is
    refused for that reason alone, which would make the drift below untested.
    """
    approval_id = _request_set(tmp_path)
    _approve_in_question_lane(approval_id, session_id=SUBAGENT_SESSION)
    _bind_child_session(substrate)

    from adapters.opencode import OpenCodeAdapter

    event = _bash_event(FIRST_COMMAND, attestation)
    proof = _valid_proof(event, approval_id)
    proof[drift] = {"expected_index": 1, "command_fingerprint": "0" * 64,
                    "correlation_id": "c" * 64}[drift]

    response = OpenCodeAdapter().adapt_pre_tool_use(
        _bash_event(FIRST_COMMAND, attestation, proof=proof)
    )

    assert response.output.get("action") == "deny", response.output
    row = _grant_row(substrate, approval_id)
    assert row["next_index"] == 0
    assert row["reservation_session_id"] is None
    assert row["reservation_tool_use_id"] is None
    assert row["reservation_index"] is None


def test_an_undrifted_proof_still_reserves_its_index(substrate, tmp_path, attestation):
    """The control for the drift cases: a correct proof is still accepted."""
    approval_id = _request_set(tmp_path)
    _approve_in_question_lane(approval_id, session_id=SUBAGENT_SESSION)
    _bind_child_session(substrate)

    from adapters.opencode import OpenCodeAdapter

    event = _bash_event(FIRST_COMMAND, attestation)
    response = OpenCodeAdapter().adapt_pre_tool_use(
        _bash_event(FIRST_COMMAND, attestation, proof=_valid_proof(event, approval_id))
    )

    assert response.output.get("action") == "allow", response.output
    assert _grant_row(substrate, approval_id)["reservation_index"] == 0


def test_a_denied_execution_is_recorded_on_the_approval(substrate, tmp_path):
    """A refusal that knows the approval leaves a record, and changes nothing."""
    approval_id = _request_set(tmp_path)
    _approve_in_question_lane(approval_id)

    from modules.tools.bash_validator import BashValidator

    result = BashValidator()._validate_single_command(
        FIRST_COMMAND, is_subagent=True, session_id=SUBAGENT_SESSION,
        agent_type=AGENT_ID, tool_use_id="",
    )

    assert result.allowed is False
    assert "correlation" in result.reason
    records = _denial_records(substrate, approval_id)
    assert len(records) == 1, records
    assert records[0]["reason_code"] == "adapter_lacks_correlation"
    assert records[0]["index"] == 0
    assert _approval_status(substrate, approval_id) == "approved"
    row = _grant_row(substrate, approval_id)
    assert row["status"] == "PENDING"
    assert row["next_index"] == 0
    assert row["reservation_tool_use_id"] is None


def test_a_command_that_is_not_the_next_index_is_recorded_as_denied(substrate, tmp_path):
    """The silence the user described: a granted signature, never consumed."""
    approval_id = _request_set(tmp_path)
    _approve_in_question_lane(approval_id)

    from gaia.store import writer
    from modules.tools.bash_validator import BashValidator

    assert writer.reserve_plan_command(
        SECOND_COMMAND, session_id=SUBAGENT_SESSION, tool_use_id=SUBAGENT_CALL,
    ) is None

    result = BashValidator()._validate_single_command(
        SECOND_COMMAND, is_subagent=True, session_id=SUBAGENT_SESSION,
        agent_type=AGENT_ID, tool_use_id=SUBAGENT_CALL,
    )

    assert result.allowed is False
    records = _denial_records(substrate, approval_id)
    assert len(records) == 1, records
    assert records[0]["reason_code"] == "plan_command_not_reserved"
    assert records[0]["index"] == 1
    assert records[0]["call_id"] == SUBAGENT_CALL
    row = _grant_row(substrate, approval_id)
    assert row["next_index"] == 0
    assert row["reservation_tool_use_id"] is None


def _isolated_substrate(tmp_path, monkeypatch, name):
    """Give one host its own database, ledger and cwd, as ``substrate`` does.

    The parity test drives two adapters over the same fixture, and a grant holds
    one reservation: run in a shared database the second host would be refused
    for the first host's reservation rather than judged on its own event.
    """
    from gaia.store import writer

    root = tmp_path / name
    root.mkdir()
    db_path = root / "gaia.db"
    con = sqlite3.connect(db_path)
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()
    monkeypatch.setenv("GAIA_DATA_DIR", str(root))
    monkeypatch.setenv("GAIA_DB", str(db_path))
    monkeypatch.setenv("GAIA_OPENCODE_ATTESTATION_DIR", str(root / "ledger"))
    monkeypatch.setenv("CLAUDE_SESSION_ID", SUBAGENT_SESSION)
    monkeypatch.chdir(root)
    return root, db_path


def _claude_code_verdict(command, call_id=SUBAGENT_CALL):
    """Drive the Claude Code adapter over its native spelling of the event."""
    from adapters.claude_code import ClaudeCodeAdapter

    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": SUBAGENT_SESSION,
        "tool_use_id": call_id,
        "agent_id": AGENT_ID,
        "agent_type": AGENT_ID,
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    adapter = ClaudeCodeAdapter()
    response = adapter.adapt_pre_tool_use(
        adapter.parse_event(json.dumps(payload)), _dispatch_identity_in_env=True,
    )
    decision = (response.output or {}).get("hookSpecificOutput", {}) \
        if isinstance(response.output, dict) else {}
    return response.exit_code == 0 and decision.get("permissionDecision") != "deny"


def _opencode_verdict(command, token):
    """Drive the OpenCode adapter over its native spelling of the same event."""
    from adapters.opencode import OpenCodeAdapter

    response = OpenCodeAdapter().adapt_pre_tool_use(_bash_event(command, token))
    return response.output.get("action") == "allow"


def _reservation_outcome(db_path, approval_id):
    row = _grant_row(db_path, approval_id)
    return {
        key: row[key]
        for key in (
            "status", "next_index", "reservation_index",
            "reservation_session_id", "reservation_tool_use_id",
            "reserved_tool_use_ids_json",
        )
    }


def test_both_hosts_reach_the_same_verdict_on_the_same_granted_command(
    tmp_path, monkeypatch,
):
    """The requirement itself: one grant, one command, two hosts, one outcome.

    The events are equivalent in what Gaia's consent layer reads -- the same
    command, the same subagent session, the same call id, the same agent role --
    each written in its host's own spelling, which is the only difference a
    parity claim can tolerate. The verdict is normalized because each host
    encodes allow differently (Claude Code by exit code and permissionDecision,
    OpenCode by an action field); the reservation outcome is compared raw.
    """
    outcomes = {}
    for host in ("claude_code", "opencode"):
        root, db_path = _isolated_substrate(tmp_path, monkeypatch, host)
        approval_id = _request_set(root)
        _approve_in_question_lane(approval_id)
        _bind_child_session(db_path)

        if host == "claude_code":
            allowed = _claude_code_verdict(FIRST_COMMAND)
        else:
            from modules.security.host_attestation import host_run_id, issue

            token = issue(
                host_run=host_run_id(), session_id=SUBAGENT_SESSION,
                role=AGENT_ID, issuer="opencode-runtime",
            ).token
            allowed = _opencode_verdict(FIRST_COMMAND, token)

        outcomes[host] = {
            "allowed": allowed,
            "reservation": _reservation_outcome(db_path, approval_id),
        }

    assert outcomes["claude_code"]["allowed"] is True, outcomes
    assert outcomes["claude_code"] == outcomes["opencode"], outcomes
    assert outcomes["opencode"]["reservation"]["reservation_tool_use_id"] == SUBAGENT_CALL
    assert json.loads(
        outcomes["opencode"]["reservation"]["reserved_tool_use_ids_json"]
    ) == [SUBAGENT_CALL]


def test_neither_host_lets_one_call_reserve_twice(substrate, tmp_path, attestation):
    """The restored freshness property, met identically on both hosts.

    The call that reserved index 0 is replayed against index 1 through each
    adapter in turn: the neutral point refuses it whichever host asks.
    """
    approval_id = _request_set(tmp_path)
    _approve_in_question_lane(approval_id)
    _bind_child_session(substrate)

    from gaia.store import writer

    assert _opencode_verdict(FIRST_COMMAND, attestation) is True
    assert writer.settle_plan_command(
        approval_id, session_id=SUBAGENT_SESSION, tool_use_id=SUBAGENT_CALL,
        success=True,
    ) is True

    assert _opencode_verdict(SECOND_COMMAND, attestation) is False
    assert _claude_code_verdict(SECOND_COMMAND) is False
    row = _grant_row(substrate, approval_id)
    assert row["next_index"] == 1
    assert row["reservation_tool_use_id"] is None
    assert json.loads(row["reserved_tool_use_ids_json"]) == [SUBAGENT_CALL]

    # What was refused is the reused call, not the command or the host: a fresh
    # call id runs the same index the two refusals above could not reach.
    assert _claude_code_verdict(SECOND_COMMAND, call_id="call-subagent-2") is True
    assert json.loads(
        _grant_row(substrate, approval_id)["reserved_tool_use_ids_json"]
    ) == [SUBAGENT_CALL, "call-subagent-2"]


def test_a_retry_loop_appends_one_denial_per_call(substrate, tmp_path):
    """Dedupe per (approval_id, call_id): attempts collapse, calls do not."""
    approval_id = _request_set(tmp_path)
    _approve_in_question_lane(approval_id)

    from modules.tools.bash_validator import BashValidator

    validator = BashValidator()
    for _ in range(3):
        validator._validate_single_command(
            SECOND_COMMAND, is_subagent=True, session_id=SUBAGENT_SESSION,
            agent_type=AGENT_ID, tool_use_id=SUBAGENT_CALL,
        )
    validator._validate_single_command(
        SECOND_COMMAND, is_subagent=True, session_id=SUBAGENT_SESSION,
        agent_type=AGENT_ID, tool_use_id="call-subagent-2",
    )

    records = _denial_records(substrate, approval_id)
    assert [record["call_id"] for record in records] == [
        SUBAGENT_CALL, "call-subagent-2",
    ]
