"""What OpenCode is handed when a T3 call needs a signature.

Every assertion here is made against a payload some real component PRODUCED:
the Gaia CLI's own `approvals opencode-present --json` output, and what the
real GaiaOpenCodePlugin does under bun for one blocked call. Nothing in this
file hand-writes the shape under test -- three earlier rounds of this plan
passed while asserting over a payload no adapter emits.

No OpenCode UI is observed: no OpenCode host runs in this suite. The question
Gaia writes into the host's question tool is asserted by test_own_adapter.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HOOKS_DIR = REPO_ROOT / "hooks"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from adapters import consent_events, consent_presentation  # noqa: E402

GAIA_CLI = REPO_ROOT / "bin" / "gaia"
DRIVER = REPO_ROOT / "tests" / "opencode" / "presentation_driver.ts"

SESSION_ID = "ses-t4-presentation"
CALL_ID = "call-t4-presentation"
# The role presentation_driver.ts dispatches; only the requesting agent presents.
AGENT_ID = "gaia-system"
TOKEN = "t4-presentation-token"

COMMANDS = (
    "git -C /home/jorge/ws/me/gaia push origin fix/consent-protocol",
    "flux reconcile kustomization apps --with-source",
)

SEALED_PAYLOAD = {
    "operation": "PUSH command intercepted: push",
    "exact_content": "\n".join(COMMANDS),
    "commands": list(COMMANDS),
    "scope": "COMMAND_SET",
    "risk_level": "high",
    "rollback_hint": "git -C /home/jorge/ws/me/gaia push --force-with-lease origin <prior-sha>",
    "rationale": "Publishes the branch and reconciles the cluster from it",
    "impact": "Remote branch advances and the cluster reconciles to the pushed revision",
    "verification": "git -C /home/jorge/ws/me/gaia log --oneline -1 origin/fix/consent-protocol",
}

PHRASES = {
    "what": "Publicar la rama y reconciliar el cluster.",
    "question": "¿Publico la rama?",
    "items": [
        {"command": command, "does": "Publica una parte.", "impact": "Queda visible."}
        for command in COMMANDS
    ],
}
PRESENTABLE_PAYLOAD = {**SEALED_PAYLOAD, **PHRASES}

PRODUCED_COMMANDS = COMMANDS
PRODUCED_RATIONALE = "Publishes the branch and reconciles the cluster from it"
PRODUCED_VERIFICATION = (
    "git -C /home/jorge/ws/me/gaia log --oneline -1 origin/fix/consent-protocol"
)
PRODUCED_ROLLBACK = (
    "git -C /home/jorge/ws/me/gaia push --force-with-lease "
    "origin fix/consent-protocol@{1}:fix/consent-protocol"
)


@pytest.fixture()
def db_env(tmp_path, monkeypatch, bootstrapped_db_template):
    """A real bootstrapped database this test alone owns, reachable by subprocess."""
    from tests.conftest import copy_bootstrapped_db

    db_path = tmp_path / "t4.db"
    copy_bootstrapped_db(bootstrapped_db_template, db_path)
    monkeypatch.setenv("GAIA_DB", str(db_path))
    env = os.environ.copy()
    env["GAIA_DB"] = str(db_path)
    return env


@pytest.fixture()
def approval_id(db_env):
    from gaia.approvals.store import insert_requested

    return insert_requested(PRESENTABLE_PAYLOAD, agent_id=AGENT_ID, session_id=SESSION_ID)


def _run_present(env, approval_id, token=TOKEN, call_id=CALL_ID):
    return subprocess.run(
        [
            sys.executable, str(GAIA_CLI), "approvals", "opencode-present", approval_id,
            "--session-id", SESSION_ID,
            "--agent-id", AGENT_ID,
            "--call-id", call_id,
            "--token", token,
            "--json",
        ],
        env=env, capture_output=True, text=True, timeout=120,
    )


def _present(env, approval_id, token=TOKEN, call_id=CALL_ID):
    result = _run_present(env, approval_id, token=token, call_id=call_id)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def _renderer_signature(approval_id):
    """The signature the shared renderer produces for the persisted payload."""
    from gaia.approvals import surface

    payload = _stored_payload(approval_id)
    rendered = surface.render(payload, approval_id)
    return {
        "block": f"```\n{rendered.text}\n```",
        "details_block": f"```\n{rendered.details}\n```",
        "question": payload["question"],
        "header": rendered.question["header"],
        "options": rendered.question["options"],
    }


def _expected_envelope(approval_id, call_id=CALL_ID):
    binding = consent_events.binding_from_mapping(
        {"agent_id": AGENT_ID, "session_id": SESSION_ID, "call_id": call_id}
    )
    return consent_presentation.envelope_from_sealed_payload(
        SEALED_PAYLOAD, approval_id=approval_id, binding=binding
    )


def _request_set(env, *, verification, rollback, commands=PRODUCED_COMMANDS):
    """Seal a payload with the real plan-first producer: `gaia approvals request-set`."""
    argv = [sys.executable, str(GAIA_CLI), "approvals", "request-set"]
    for command in commands:
        argv += ["--command", command, "--does", "Publica una parte.", "--impact", "Queda visible."]
    argv += [
        "--what", PRODUCED_RATIONALE,
        "--question", "¿Publico la rama?",
        "--rationale", PRODUCED_RATIONALE,
        "--verification", verification,
        "--rollback", rollback,
        "--agent-id", AGENT_ID,
        "--session-id", SESSION_ID,
        "--json",
    ]
    result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])["approval_id"]


def _stored_payload(approval_id):
    """Read back the payload the producer actually persisted, not one composed here."""
    from gaia.approvals.store import get_by_id

    row = get_by_id(approval_id)
    assert row is not None, approval_id
    return json.loads(row["payload_json"])


def _drive_plugin(
    env, approval_id, call_id=CALL_ID, command=COMMANDS[0], control_prompt=None, directory=None,
):
    """Run the real plugin under bun and return what it delivered natively.

    ``control_prompt="rejected"`` makes the driver's host stub answer
    ``session.promptAsync`` the way the SDK client reports a schema rejection:
    a resolved ``{ error, response: { ok: false } }``, never a throw.
    ``directory`` is the host's project directory handed to the plugin.
    """
    scenario = {
        "sessionID": SESSION_ID,
        "callID": call_id,
        "approvalID": approval_id,
        "tool": "bash",
        "args": {"command": command},
    }
    if control_prompt is not None:
        scenario["controlPrompt"] = control_prompt
    if directory is not None:
        scenario["directory"] = directory
    result = subprocess.run(
        ["bun", str(DRIVER), json.dumps(scenario)],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def _drive_abort_outcome(env, approval_id, outcome):
    scenario = {
        "sessionID": SESSION_ID,
        "callID": f"call-abort-{outcome}",
        "approvalID": approval_id,
        "outcome": outcome,
        "tool": "bash",
        "args": {"command": COMMANDS[0]},
    }
    result = subprocess.run(
        ["bun", str(DRIVER), json.dumps(scenario)],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("outcome", ["pending", "no-decision", "rejected", "malformed", "timeout"])
def test_exported_before_hook_aborts_every_non_allow_outcome(db_env, approval_id, outcome):
    """Drive the exported hook OpenCode awaits and model execution only on return."""
    delivered = _drive_abort_outcome(db_env, approval_id, outcome)

    assert delivered["error"], delivered
    assert delivered["originalInvocationExecuted"] is False, delivered


def test_cli_presentation_emits_the_renderer_signature_and_the_sealed_metadata(db_env, approval_id):
    """One renderer for every host: the CLI hands OpenCode exactly its signature."""
    emitted = _present(db_env, approval_id)
    envelope = _expected_envelope(approval_id)

    assert emitted["signature"] == _renderer_signature(approval_id)
    assert PHRASES["what"] in emitted["signature"]["block"]
    assert emitted["signature"]["question"] == PHRASES["question"]
    assert emitted["metadata"] == consent_presentation.native_metadata(envelope)
    assert json.loads(emitted["metadata"]["canonical_payload"]) == json.loads(
        envelope.canonical_payload()
    )


def test_a_phraseless_request_is_never_presented(db_env):
    """PD10: a request lacking its requester's phrases records no SHOWN and opens no question."""
    from gaia.approvals.store import get_history, insert_requested

    phraseless_id = insert_requested(SEALED_PAYLOAD, agent_id=AGENT_ID, session_id=SESSION_ID)
    refused = _run_present(db_env, phraseless_id, token="phraseless-token", call_id="call-phraseless")
    assert refused.returncode == 1, refused.stdout
    assert "--question" in json.loads(refused.stdout.strip().splitlines()[-1])["error"]

    delivered = _drive_plugin(db_env, phraseless_id, call_id="call-phraseless-2")
    assert delivered["originalInvocationExecuted"] is False
    assert f"Gaia could not present approval {phraseless_id}" in delivered["error"]
    assert delivered["controlPrompts"] == []
    assert [e["event_type"] for e in get_history(phraseless_id)] == ["REQUESTED"]


def test_approval_live_fixes_bridge_marks_presentable_only_a_phrased_approval(db_env, approval_id):
    """The plugin presents on `presentable`; a phraseless placeholder never earns it."""
    from adapters.opencode import OpenCodeAdapter
    from adapters.tool_policy import PolicyVerdict
    from gaia.approvals.store import insert_requested

    phraseless_id = insert_requested(SEALED_PAYLOAD, agent_id=AGENT_ID, session_id=SESSION_ID)

    def deny(named):
        verdict = PolicyVerdict(decision="deny", reason="[T3_BLOCKED]", approval_id=named)
        return OpenCodeAdapter._format_policy_verdict(verdict).output

    assert deny(approval_id)["presentable"] is True
    phraseless = deny(phraseless_id)
    assert phraseless["approval_id"] == phraseless_id
    assert "presentable" not in phraseless
    assert "presentable" not in deny("P-" + "0" * 32)


def test_a_refused_presentation_keeps_the_approval_id_and_gaia_cause(db_env):
    """The agent must see WHICH approval failed and WHY Gaia refused, not a generic line."""
    from gaia.approvals.store import get_history, insert_requested

    cause = "OpenCode presentation must come from the requesting session"
    foreign_id = insert_requested(PRESENTABLE_PAYLOAD, agent_id=AGENT_ID, session_id="ses-other-owner")
    delivered = _drive_plugin(db_env, foreign_id, call_id="call-refused")

    assert delivered["controlPrompts"] == [], delivered
    assert delivered["originalInvocationExecuted"] is False
    assert foreign_id in delivered["error"], delivered["error"]
    assert cause in delivered["error"]
    assert [e["event_type"] for e in get_history(foreign_id)] == ["REQUESTED"]

    traces = [
        e for e in delivered["bridgeEvents"]
        if e.get("event") == "permission.uncorrelated" and "approvalID" in e
    ]
    assert len(traces) == 1, delivered["bridgeEvents"]
    assert traces[0]["approvalID"] == foreign_id
    assert traces[0]["cause"] == cause
    assert traces[0]["sessionID"] == SESSION_ID
    assert traces[0]["callID"] == "call-refused"


def test_gaia_runs_from_the_session_directory_not_the_serve_cwd(db_env, approval_id, tmp_path):
    """The presentation is attributed to the workspace of the session's directory.

    `opencode serve` may run from a directory that is not the project; Gaia
    resolves the workspace from the cwd of the process that writes, so the
    plugin must start `bin/gaia` from the directory the host handed it.
    """
    session_directory = tmp_path / "session-project"
    session_directory.mkdir()
    assert str(session_directory) != os.getcwd()

    delivered = _drive_plugin(
        db_env, approval_id, call_id="call-cwd", directory=str(session_directory),
    )

    assert len(delivered["controlPrompts"]) == 1, delivered
    assert delivered["gaiaSpawnCwds"] == [str(session_directory)], delivered["gaiaSpawnCwds"]


def test_gaia_inherits_the_process_cwd_when_the_host_names_no_directory(db_env, approval_id):
    delivered = _drive_plugin(db_env, approval_id, call_id="call-no-directory")

    assert delivered["gaiaSpawnCwds"] == [None], delivered["gaiaSpawnCwds"]


def test_control_prompt_body_matches_the_installed_sdk_types(db_env, approval_id):
    """SessionPromptAsyncData.body (@opencode-ai/sdk 1.18.18): system is a string, not an array.

    The array form was accepted by every stub and rejected by the real host,
    which left the control session empty and the user never asked.
    """
    delivered = _drive_plugin(db_env, approval_id, call_id="call-sdk-shape")

    assert len(delivered["controlPrompts"]) == 1, delivered
    prompt = delivered["controlPrompts"][0]
    assert prompt["path"] == {"id": SESSION_ID}
    body = prompt["body"]
    assert isinstance(body["system"], str) and body["system"]
    assert "agent" not in body
    assert "tools" not in body
    assert body["parts"][0]["type"] == "text"
    assert isinstance(body["parts"][0]["text"], str)


def test_control_question_uses_the_cached_specialist_child_instead_of_creating_a_hidden_child(
    db_env, approval_id,
):
    """The active root can render questions only for child sessions already in its cache."""
    delivered = _drive_plugin(db_env, approval_id, call_id="call-cached-child")

    assert [prompt["path"]["id"] for prompt in delivered["controlPrompts"]] == [SESSION_ID]
    assert delivered["deletedSessions"] == []


def test_an_opened_control_is_traced_after_the_host_accepted_the_prompt(db_env, approval_id):
    """SHOWN records the presentation; the question reaching the host is a separate trace."""
    delivered = _drive_plugin(db_env, approval_id, call_id="call-opened")

    opened = [e for e in delivered["bridgeEvents"] if e.get("event") == "control.opened"]
    assert len(opened) == 1, delivered["bridgeEvents"]
    assert opened[0]["approvalID"] == approval_id
    assert opened[0]["sessionID"] == SESSION_ID
    assert opened[0]["callID"] == "call-opened"
    assert opened[0]["controlSessionID"] == SESSION_ID


def test_a_rejected_control_prompt_fails_closed_with_the_host_cause(db_env, approval_id):
    """A prompt the host refused leaves no control waiting and names the approval and cause."""
    from gaia.approvals.store import get_history

    delivered = _drive_plugin(db_env, approval_id, call_id="call-rejected-prompt", control_prompt="rejected")

    assert delivered["originalInvocationExecuted"] is False
    error = delivered["error"]
    assert error.startswith(
        f"Gaia could not open the consent control plane for {approval_id}: control-plane prompt rejected: HTTP 400"
    ), error
    assert "BadRequestError" in error
    assert "[T3_BLOCKED]" not in error
    assert delivered["deletedSessions"] == []
    assert delivered["controlSessionLingered"] is False
    # SHOWN waits for the signature's block, which only the question call posts;
    # a refused prompt never got that far, so nothing claims the user saw it.
    assert [e["event_type"] for e in get_history(approval_id)] == ["REQUESTED"]
    assert [e for e in delivered["bridgeEvents"] if e.get("event") == "control.opened"] == []


def test_a_rejected_control_prompt_is_traced_with_its_phase(db_env, approval_id):
    delivered = _drive_plugin(db_env, approval_id, call_id="call-rejected-trace", control_prompt="rejected")

    traces = [
        e for e in delivered["bridgeEvents"]
        if e.get("event") == "permission.uncorrelated" and "approvalID" in e
    ]
    assert len(traces) == 1, delivered["bridgeEvents"]
    assert traces[0]["approvalID"] == approval_id
    assert traces[0]["sessionID"] == SESSION_ID
    assert traces[0]["callID"] == "call-rejected-trace"
    assert traces[0]["stage"] == "control-plane"
    assert traces[0]["cause"].startswith("control-plane prompt rejected: HTTP 400"), traces[0]


def test_the_bridge_records_a_control_plane_failure_under_its_own_reason(db_env, approval_id):
    sys.path.insert(0, str(REPO_ROOT / "opencode"))
    import bridge as opencode_bridge

    from gaia.approvals.decision_audit import (
        DECISION_NOT_ACTIVATED_EVENT,
        DETAILS_PAYLOAD_KEY,
        REASON_CONTROL_PLANE_FAILED,
    )
    from gaia.store.reader import cross_surface_query

    response = opencode_bridge.handle({
        "event": "permission.uncorrelated",
        "sessionID": SESSION_ID,
        "callID": "call-rejected-trace",
        "approvalID": approval_id,
        "stage": "control-plane",
        "cause": "control-plane prompt rejected: HTTP 400",
    })
    assert response["action"] == "allow", response

    rows = cross_surface_query(
        surface="harness_events", type=DECISION_NOT_ACTIVATED_EVENT,
        db_path=Path(db_env["GAIA_DB"]),
    )
    assert len(rows) == 1, rows
    assert rows[0]["raw"]["severity"] == "warning"
    payload = json.loads(rows[0]["raw"]["payload"])
    assert payload["reason"] == REASON_CONTROL_PLANE_FAILED
    assert payload["approval_id"] == approval_id
    assert payload["detail"] == "control-plane prompt rejected: HTTP 400"
    assert payload[DETAILS_PAYLOAD_KEY]["call_id"] == "call-rejected-trace"


def test_the_bridge_records_a_refused_decision_under_its_own_reason(db_env, approval_id):
    sys.path.insert(0, str(REPO_ROOT / "opencode"))
    import bridge as opencode_bridge

    from gaia.approvals.decision_audit import (
        DECISION_NOT_ACTIVATED_EVENT,
        DETAILS_PAYLOAD_KEY,
        REASON_DECIDE_FAILED,
    )
    from gaia.store.reader import cross_surface_query

    response = opencode_bridge.handle({
        "event": "permission.uncorrelated",
        "sessionID": SESSION_ID,
        "callID": "call-decide",
        "approvalID": approval_id,
        "stage": "decide",
        "cause": "approval is not pending",
    })
    assert response["action"] == "allow", response

    rows = cross_surface_query(
        surface="harness_events", type=DECISION_NOT_ACTIVATED_EVENT,
        db_path=Path(db_env["GAIA_DB"]),
    )
    assert len(rows) == 1, rows
    assert rows[0]["raw"]["severity"] == "warning"
    payload = json.loads(rows[0]["raw"]["payload"])
    assert payload["reason"] == REASON_DECIDE_FAILED
    assert payload["approval_id"] == approval_id
    assert payload["detail"] == "approval is not pending"
    assert payload[DETAILS_PAYLOAD_KEY]["call_id"] == "call-decide"


def test_the_bridge_records_a_closed_control_with_its_reason(db_env, approval_id):
    sys.path.insert(0, str(REPO_ROOT / "opencode"))
    import bridge as opencode_bridge

    from gaia.approvals.decision_audit import CONTROL_CLOSED_EVENT
    from gaia.store.reader import cross_surface_query

    for reason, detail in (("decided", "once"), ("question_mismatch", "host asked [...]")):
        response = opencode_bridge.handle({
            "event": "control.closed",
            "sessionID": SESSION_ID,
            "callID": "call-closed",
            "approvalID": approval_id,
            "controlSessionID": "control-call-closed",
            "reason": reason,
            "detail": detail,
        })
        assert response["action"] == "allow", response

    rows = cross_surface_query(
        surface="harness_events", type=CONTROL_CLOSED_EVENT,
        db_path=Path(db_env["GAIA_DB"]),
    )
    by_reason = {json.loads(row["raw"]["payload"])["reason"]: row["raw"] for row in rows}
    assert set(by_reason) == {"decided", "question_mismatch"}, rows
    assert by_reason["decided"]["severity"] == "info"
    assert by_reason["question_mismatch"]["severity"] == "warning"
    payload = json.loads(by_reason["question_mismatch"]["payload"])
    assert payload["approval_id"] == approval_id
    assert payload["control_session_id"] == "control-call-closed"
    assert payload["detail"] == "host asked [...]"


def test_the_bridge_records_an_applied_decision_and_who_was_told(db_env, approval_id):
    sys.path.insert(0, str(REPO_ROOT / "opencode"))
    import bridge as opencode_bridge

    from gaia.approvals.decision_audit import DECISION_APPLIED_EVENT
    from gaia.store.reader import cross_surface_query

    common = {
        "event": "decision.applied", "sessionID": SESSION_ID, "callID": "call-applied",
        "approvalID": approval_id, "controlSessionID": "control-call-applied",
        "reply": "once", "lane": "control", "nextIndex": 0,
    }
    assert opencode_bridge.handle({**common, "notifiedSessionID": "ses-root"})["action"] == "allow"
    assert opencode_bridge.handle({**common, "notifyFailure": "no root session"})["action"] == "allow"

    rows = cross_surface_query(
        surface="harness_events", type=DECISION_APPLIED_EVENT,
        db_path=Path(db_env["GAIA_DB"]),
    )
    assert len(rows) == 2, rows
    payloads = {row["raw"]["severity"]: json.loads(row["raw"]["payload"]) for row in rows}
    assert payloads["info"]["notified_session_id"] == "ses-root"
    assert payloads["info"]["approval_id"] == approval_id
    assert payloads["info"]["next_index"] == 0
    assert payloads["warning"]["notified_session_id"] == ""
    assert payloads["warning"]["notify_failure"] == "no root session"


def test_the_bridge_records_an_opened_control_by_approval(db_env, approval_id):
    sys.path.insert(0, str(REPO_ROOT / "opencode"))
    import bridge as opencode_bridge

    from gaia.approvals.decision_audit import CONTROL_OPENED_EVENT
    from gaia.store.reader import cross_surface_query

    response = opencode_bridge.handle({
        "event": "control.opened",
        "sessionID": SESSION_ID,
        "callID": "call-opened",
        "approvalID": approval_id,
        "controlSessionID": "control-call-opened",
    })
    assert response["action"] == "allow", response

    rows = cross_surface_query(
        surface="harness_events", type=CONTROL_OPENED_EVENT,
        db_path=Path(db_env["GAIA_DB"]),
    )
    assert len(rows) == 1, rows
    assert rows[0]["raw"]["severity"] == "info"
    payload = json.loads(rows[0]["raw"]["payload"])
    assert payload["approval_id"] == approval_id
    assert payload["session_id"] == SESSION_ID
    assert payload["call_id"] == "call-opened"
    assert payload["control_session_id"] == "control-call-opened"


def test_the_bridge_records_a_refused_presentation_under_its_own_reason(db_env, approval_id):
    sys.path.insert(0, str(REPO_ROOT / "opencode"))
    import bridge as opencode_bridge

    from gaia.approvals.decision_audit import (
        DECISION_NOT_ACTIVATED_EVENT,
        DETAILS_PAYLOAD_KEY,
        REASON_PRESENTATION_FAILED,
    )
    from gaia.store.reader import cross_surface_query

    response = opencode_bridge.handle({
        "event": "permission.uncorrelated",
        "sessionID": SESSION_ID,
        "callID": "call-refused",
        "approvalID": approval_id,
        "cause": "OpenCode session does not own this approval",
    })
    assert response["action"] == "allow", response

    rows = cross_surface_query(
        surface="harness_events", type=DECISION_NOT_ACTIVATED_EVENT,
        db_path=Path(db_env["GAIA_DB"]),
    )
    assert len(rows) == 1, rows
    assert rows[0]["raw"]["severity"] == "warning"
    payload = json.loads(rows[0]["raw"]["payload"])
    assert payload["reason"] == REASON_PRESENTATION_FAILED
    assert payload["approval_id"] == approval_id
    assert payload["detail"] == "OpenCode session does not own this approval"
    assert payload[DETAILS_PAYLOAD_KEY]["call_id"] == "call-refused"


def test_the_bridge_records_a_report_with_no_cause_on_the_same_channel(db_env):
    """harness_events and decision_audit carry it: no new table, column or vocabulary."""
    sys.path.insert(0, str(REPO_ROOT / "opencode"))
    import bridge as opencode_bridge

    from gaia.approvals.decision_audit import (
        DECISION_NOT_ACTIVATED_EVENT,
        DETAILS_PAYLOAD_KEY,
    )
    from gaia.store.reader import cross_surface_query

    response = opencode_bridge.handle({
        "event": "permission.uncorrelated",
        "sessionID": SESSION_ID,
        "callID": "call-no-cause",
    })
    assert response["action"] == "allow", response

    rows = cross_surface_query(
        surface="harness_events", type=DECISION_NOT_ACTIVATED_EVENT,
        db_path=Path(db_env["GAIA_DB"]),
    )
    assert len(rows) == 1, rows
    assert rows[0]["raw"]["severity"] == "warning"
    payload = json.loads(rows[0]["raw"]["payload"])
    assert payload["lane"] == opencode_bridge.PERMISSION_ASK_LANE
    assert payload["session_id"] == SESSION_ID
    assert payload[DETAILS_PAYLOAD_KEY]["call_id"] == "call-no-cause"


def test_a_surface_that_hides_a_sealed_field_is_named_not_shown(monkeypatch):
    envelope = _expected_envelope("P-tripwire")
    complete = consent_presentation.render_native_text(envelope)

    assert consent_presentation.missing_visible_fields(
        complete.replace(SEALED_PAYLOAD["verification"], ""), SEALED_PAYLOAD
    ) == ("verification",)
    reordered = "\n".join(reversed(complete.split("\n")))
    assert any(
        item.startswith("commands[") for item in
        consent_presentation.missing_visible_fields(reordered, SEALED_PAYLOAD)
    )

    # A renderer regression must fail closed rather than deliver a surface the
    # user cannot read the whole request from.
    monkeypatch.setattr(
        consent_presentation,
        "render_native_text",
        lambda _envelope: complete.replace(SEALED_PAYLOAD["rollback_hint"], ""),
    )
    with pytest.raises(ValueError, match="would hide sealed fields"):
        consent_presentation.native_presentation(envelope, SEALED_PAYLOAD)


def test_a_surface_agreeing_with_its_envelope_but_not_the_seal_is_refused():
    """The check reads the seal, so envelope-versus-payload divergence is visible.

    A render is internally consistent with whatever envelope produced it by
    construction; the failure mode that actually occurs is a derivation that
    substitutes a fallback for a field a producer sealed. Comparing the render
    against the envelope cannot see that, so the reference is the payload.
    """
    dropped = {key: value for key, value in SEALED_PAYLOAD.items() if key != "verification"}
    envelope = consent_presentation.envelope_from_sealed_payload(
        dropped,
        approval_id="P-divergence",
        binding=consent_events.binding_from_mapping(
            {"agent_id": AGENT_ID, "session_id": SESSION_ID, "call_id": CALL_ID}
        ),
    )
    surface = consent_presentation.render_native_text(envelope)

    assert not consent_presentation.missing_visible_fields(surface, dropped)
    assert consent_presentation.missing_visible_fields(surface, SEALED_PAYLOAD) == (
        "verification",
    )
    with pytest.raises(ValueError, match="verification"):
        consent_presentation.native_presentation(envelope, SEALED_PAYLOAD)


def test_a_real_producer_seals_the_fields_the_presentation_carries(db_env):
    """The payload under test is the one `gaia approvals request-set` wrote.

    Gate 895 asks that the metadata equal the SEALED operation, command bytes,
    scope, impact, risk, rollback and verification. A hand-authored payload can
    satisfy that clause while no producer emits the shape, so here the
    left-hand side is produced by the real plan-first CLI and read back out of
    the database -- never written by this file.
    """
    approval_id = _request_set(
        db_env, verification=PRODUCED_VERIFICATION, rollback=PRODUCED_ROLLBACK
    )
    stored = _stored_payload(approval_id)
    assert stored["verification"] == PRODUCED_VERIFICATION
    assert stored["rollback_hint"] == PRODUCED_ROLLBACK

    emitted = _present(
        db_env, approval_id, token="produced-token", call_id="call-produced"
    )
    assert emitted["signature"] == _renderer_signature(approval_id)
    metadata = emitted["metadata"]

    assert metadata["operation"] == stored["operation"]
    assert metadata["commands"] == list(PRODUCED_COMMANDS)
    assert metadata["scope"] == stored["scope"]
    assert metadata["risk"] == stored["risk_level"] + " -- " + stored["rationale"]
    assert metadata["rollback"] == stored["rollback_hint"]
    assert metadata["verification"] == stored["verification"]

    # request-set authors no top-level `impact` (the core seals it as None), so
    # the metadata states the absence instead of composing a consequence nobody assessed.
    assert stored.get("impact") is None
    assert metadata["impact"] == consent_presentation._IMPACT_ABSENT
