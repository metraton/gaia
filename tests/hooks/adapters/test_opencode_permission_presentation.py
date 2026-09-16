"""What OpenCode's native permission mechanism is actually handed for a T3 ask.

Every assertion here is made against a payload some real component PRODUCED:
the Gaia CLI's own `approvals opencode-present --json` output, and the exact
object the real GaiaOpenCodePlugin enriches in the host's permission.ask hook while
driven by bun. Nothing in this file hand-writes the shape under test -- three
earlier rounds of this plan passed while asserting over a payload no adapter
emits, and the claim being made here ("the delivered payload carries the sealed
envelope, visibly") is precisely the direction where that is fatal.

No OpenCode UI is observed: no OpenCode host runs in this suite. What is
verified is the payload delivered TO the native mechanism, which is what the
plugin controls and all it can be held to.
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
PLUGIN = REPO_ROOT / "opencode" / "plugin.ts"

SESSION_ID = "ses-t4-presentation"
CALL_ID = "call-t4-presentation"
AGENT_ID = "gitops-operator"
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

REQUIRED_VISIBLE = ("operation", "scope", "impact", "risk", "rollback", "verification")

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

    return insert_requested(SEALED_PAYLOAD, agent_id=AGENT_ID, session_id=SESSION_ID)


def _present(env, approval_id, token=TOKEN, call_id=CALL_ID):
    result = subprocess.run(
        [
            sys.executable, str(GAIA_CLI), "approvals", "opencode-present", approval_id,
            "--session-id", SESSION_ID,
            "--call-id", call_id,
            "--token", token,
            "--json",
        ],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


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
        argv += ["--command", command]
    argv += [
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


def _drive_plugin(env, approval_id, call_id=CALL_ID, command=COMMANDS[0]):
    """Run the real plugin under bun and return what it delivered natively."""
    scenario = {
        "sessionID": SESSION_ID,
        "callID": call_id,
        "approvalID": approval_id,
        "tool": "bash",
        "args": {"command": command},
    }
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


def test_adapter_uses_the_real_permission_ask_boundary_not_the_nonexistent_creator():
    source = PLUGIN.read_text()

    assert "session.permission.create" not in source
    assert '"permission.ask"' in source
    assert "permissionID" in source
    assert "event.properties.response" in source


def test_host_permission_request_is_held_for_user_reply_when_correlation_is_exact(db_env, approval_id):
    delivered = _drive_plugin(db_env, approval_id, call_id="call-presented")
    # The driver models a host-created request with the exact session/call pair.
    assert delivered["asked"][0]["status"] == "ask"


@pytest.mark.parametrize("outcome", ["pending", "no-decision", "rejected", "malformed", "timeout"])
def test_exported_before_hook_aborts_every_non_allow_outcome(db_env, approval_id, outcome):
    """Drive the exported hook OpenCode awaits and model execution only on return."""
    delivered = _drive_abort_outcome(db_env, approval_id, outcome)

    assert delivered["error"], delivered
    assert delivered["originalInvocationExecuted"] is False, delivered


def test_cli_presentation_seals_every_required_field_visibly(db_env, approval_id):
    emitted = _present(db_env, approval_id)
    envelope = _expected_envelope(approval_id)
    visible = "\n".join(emitted["visible_lines"])

    assert emitted["visible_text"] == visible
    assert not consent_presentation.missing_visible_fields(visible, SEALED_PAYLOAD)
    for name in REQUIRED_VISIBLE:
        assert getattr(envelope, name) in visible, name
    assert emitted["metadata"] == consent_presentation.native_metadata(envelope)
    assert json.loads(emitted["metadata"]["canonical_payload"]) == json.loads(
        envelope.canonical_payload()
    )


def test_delivered_permission_payload_carries_the_sealed_envelope(db_env, approval_id):
    delivered = _drive_plugin(db_env, approval_id)
    assert len(delivered["asked"]) == 1, delivered
    payload = delivered["asked"][0]["permission"]
    envelope = _expected_envelope(approval_id)
    expected = consent_presentation.native_presentation(envelope, SEALED_PAYLOAD)

    assert payload["sessionID"] == SESSION_ID
    assert payload["title"] == "Gaia approval required"
    assert payload["pattern"] == expected["visible_lines"]
    assert payload["metadata"]["gaiaApprovalID"] == approval_id
    assert payload["metadata"]["gaiaCallID"] == CALL_ID
    assert payload["metadata"]["gaiaConsent"] == expected["metadata"]

    metadata = payload["metadata"]["gaiaConsent"]
    assert metadata["operation"] == SEALED_PAYLOAD["operation"]
    assert metadata["commands"] == list(COMMANDS)
    assert metadata["scope"] == SEALED_PAYLOAD["scope"]
    assert metadata["impact"] == SEALED_PAYLOAD["impact"]
    assert metadata["risk"] == "high -- " + SEALED_PAYLOAD["rationale"]
    assert metadata["rollback"] == SEALED_PAYLOAD["rollback_hint"]
    assert metadata["verification"] == SEALED_PAYLOAD["verification"]
    assert metadata["protocol_version"] == "1"


def test_delivered_visible_slot_alone_carries_every_field_in_order(db_env, approval_id):
    """The user-visible slot is judged with the metadata discarded entirely."""
    delivered = _drive_plugin(db_env, approval_id)
    payload = delivered["asked"][0]["permission"]
    visible = "\n".join(payload["pattern"])
    envelope = _expected_envelope(approval_id)

    assert not consent_presentation.missing_visible_fields(visible, SEALED_PAYLOAD)
    positions = [visible.index(command) for command in COMMANDS]
    assert positions == sorted(positions)
    for name in REQUIRED_VISIBLE:
        assert getattr(envelope, name) in visible, name
    for fingerprint in envelope.fingerprints:
        assert fingerprint in visible


def test_delivered_visible_text_agrees_with_the_cli_sealed_surface(db_env, approval_id):
    """One producer, two consumers: the CLI's surface is the delivered surface."""
    emitted = _present(db_env, approval_id)
    delivered = _drive_plugin(db_env, approval_id)

    assert "\n".join(delivered["asked"][0]["permission"]["pattern"]) == emitted["visible_text"]
    assert delivered["asked"][0]["permission"]["metadata"]["gaiaConsent"] == emitted["metadata"]


def test_an_unsealable_payload_is_never_presented_as_a_permission(db_env):
    """No command bytes to show means no native permission is raised at all."""
    from gaia.approvals.store import insert_requested

    empty_id = insert_requested(
        {"operation": "FILE_WRITE command intercepted: write", "scope": "FILE_PATH"},
        agent_id=AGENT_ID, session_id=SESSION_ID,
    )
    emitted = _present(db_env, empty_id, token="unsealable-token", call_id="call-unsealable")
    assert "presentation_error" in emitted
    assert "visible_lines" not in emitted

    delivered = _drive_plugin(db_env, empty_id, call_id="call-unsealable-2")
    assert delivered["asked"] == []
    assert "could not seal a complete consent surface" in delivered["error"]


def test_an_approval_minted_without_a_session_is_adopted_by_the_presenting_session(db_env):
    """request-set without --session-id persists NULL; presentation binds it, once."""
    from gaia.approvals.store import get_by_id, get_history, insert_requested

    unowned_id = insert_requested(SEALED_PAYLOAD, agent_id=AGENT_ID, session_id=None)
    assert get_by_id(unowned_id)["session_id"] is None

    emitted = _present(db_env, unowned_id, token="adopt-token", call_id="call-adopt")
    assert emitted["status"] == "presented"
    assert get_by_id(unowned_id)["session_id"] == SESSION_ID
    assert [e["event_type"] for e in get_history(unowned_id)] == ["REQUESTED", "SHOWN"]

    foreign = subprocess.run(
        [
            sys.executable, str(GAIA_CLI), "approvals", "opencode-present", unowned_id,
            "--session-id", "ses-someone-else",
            "--call-id", "call-foreign",
            "--token", "foreign-token",
            "--json",
        ],
        env=db_env, capture_output=True, text=True, timeout=120,
    )
    assert foreign.returncode == 1, foreign.stdout
    assert json.loads(foreign.stdout.strip().splitlines()[-1]) == {
        "error": "OpenCode session does not own this approval"
    }
    assert get_by_id(unowned_id)["session_id"] == SESSION_ID
    assert len(get_history(unowned_id)) == 2


def test_plugin_presents_an_approval_minted_without_a_session(db_env):
    from gaia.approvals.store import get_by_id, insert_requested

    unowned_id = insert_requested(SEALED_PAYLOAD, agent_id=AGENT_ID, session_id=None)
    delivered = _drive_plugin(db_env, unowned_id, call_id="call-adopt-plugin")

    assert delivered["asked"][0]["status"] == "ask", delivered
    assert get_by_id(unowned_id)["session_id"] == SESSION_ID


def test_a_refused_presentation_keeps_the_approval_id_and_gaia_cause(db_env):
    """The agent must see WHICH approval failed and WHY Gaia refused, not a generic line."""
    from gaia.approvals.store import get_history, insert_requested

    foreign_id = insert_requested(SEALED_PAYLOAD, agent_id=AGENT_ID, session_id="ses-other-owner")
    delivered = _drive_plugin(db_env, foreign_id, call_id="call-refused")

    assert delivered["asked"] == [], delivered
    assert delivered["originalInvocationExecuted"] is False
    assert foreign_id in delivered["error"], delivered["error"]
    assert "OpenCode session does not own this approval" in delivered["error"]
    assert [e["event_type"] for e in get_history(foreign_id)] == ["REQUESTED"]

    # The driver also models the host raising its own permission request after
    # the abort, which is reported uncorrelated (no approvalID); the plugin's
    # presentation-failure trace is the one naming the approval.
    traces = [
        e for e in delivered["bridgeEvents"]
        if e.get("event") == "permission.uncorrelated" and "approvalID" in e
    ]
    assert len(traces) == 1, delivered["bridgeEvents"]
    assert traces[0]["approvalID"] == foreign_id
    assert traces[0]["cause"] == "OpenCode session does not own this approval"
    assert traces[0]["sessionID"] == SESSION_ID
    assert traces[0]["callID"] == "call-refused"


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


def test_a_real_producer_seals_the_fields_the_delivered_surface_shows(db_env):
    """The payload under test is the one `gaia approvals request-set` wrote.

    Gate 895 asks that the delivered metadata equal the SEALED operation, command
    bytes, scope, impact, risk, rollback and verification. A hand-authored
    payload can satisfy that clause while no producer emits the shape, so here
    the left-hand side is produced by the real plan-first CLI and read back out
    of the database -- never written by this file.
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
    visible = "\n".join(emitted["visible_lines"])
    metadata = emitted["metadata"]

    assert metadata["operation"] == stored["operation"]
    assert metadata["commands"] == list(PRODUCED_COMMANDS)
    assert metadata["scope"] == stored["scope"]
    assert metadata["risk"] == stored["risk_level"] + " -- " + stored["rationale"]
    assert metadata["rollback"] == stored["rollback_hint"]
    assert metadata["verification"] == stored["verification"]
    assert not consent_presentation.missing_visible_fields(visible, stored)

    # No Gaia producer authors `impact`, so the surface states the absence
    # instead of composing a consequence nobody assessed.
    assert "impact" not in stored
    assert metadata["impact"] == consent_presentation._IMPACT_ABSENT

    delivered = _drive_plugin(
        db_env, approval_id, call_id="call-produced", command=PRODUCED_COMMANDS[0]
    )
    payload = delivered["asked"][0]["permission"]
    assert "\n".join(payload["pattern"]) == emitted["visible_text"]
    assert payload["metadata"]["gaiaConsent"] == metadata
