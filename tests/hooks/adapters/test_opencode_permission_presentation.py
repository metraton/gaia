"""What OpenCode's native permission mechanism is actually handed for a T3 ask.

Every assertion here is made against a payload some real component PRODUCED:
the Gaia CLI's own `approvals opencode-present --json` output, and the exact
object the real GaiaOpenCodePlugin passes to session.permission.create while
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


def _drive_plugin(env, approval_id, call_id=CALL_ID):
    """Run the real plugin under bun and return what it delivered natively."""
    scenario = {
        "sessionID": SESSION_ID,
        "callID": call_id,
        "approvalID": approval_id,
        "tool": "bash",
        "args": {"command": COMMANDS[0]},
    }
    result = subprocess.run(
        ["bun", str(DRIVER), json.dumps(scenario)],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_cli_presentation_seals_every_required_field_visibly(db_env, approval_id):
    emitted = _present(db_env, approval_id)
    envelope = _expected_envelope(approval_id)
    visible = "\n".join(emitted["visible_lines"])

    assert emitted["visible_text"] == visible
    assert not consent_presentation.missing_visible_fields(visible, envelope)
    for name in REQUIRED_VISIBLE:
        assert getattr(envelope, name) in visible, name
    assert emitted["metadata"] == consent_presentation.native_metadata(envelope)
    assert json.loads(emitted["metadata"]["canonical_payload"]) == json.loads(
        envelope.canonical_payload()
    )


def test_delivered_permission_payload_carries_the_sealed_envelope(db_env, approval_id):
    delivered = _drive_plugin(db_env, approval_id)
    assert len(delivered["created"]) == 1, delivered
    payload = delivered["created"][0]
    envelope = _expected_envelope(approval_id)
    expected = consent_presentation.native_presentation(envelope)

    assert payload["sessionID"] == SESSION_ID
    assert payload["action"] == "gaia-approval"
    assert payload["resources"] == expected["visible_lines"]
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
    payload = delivered["created"][0]
    visible = "\n".join(payload["resources"])
    envelope = _expected_envelope(approval_id)

    assert not consent_presentation.missing_visible_fields(visible, envelope)
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

    assert "\n".join(delivered["created"][0]["resources"]) == emitted["visible_text"]
    assert delivered["created"][0]["metadata"]["gaiaConsent"] == emitted["metadata"]


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
    assert delivered["created"] == []
    assert "could not seal a complete consent surface" in delivered["error"]


def test_a_surface_that_hides_a_sealed_field_is_named_not_shown(monkeypatch):
    envelope = _expected_envelope("P-tripwire")
    complete = consent_presentation.render_native_text(envelope)

    assert consent_presentation.missing_visible_fields(
        complete.replace(SEALED_PAYLOAD["verification"], ""), envelope
    ) == ("verification",)
    reordered = "\n".join(reversed(complete.split("\n")))
    assert any(
        item.startswith("commands[") for item in
        consent_presentation.missing_visible_fields(reordered, envelope)
    )

    # A renderer regression must fail closed rather than deliver a surface the
    # user cannot read the whole request from.
    monkeypatch.setattr(
        consent_presentation,
        "render_native_text",
        lambda _envelope: complete.replace(SEALED_PAYLOAD["rollback_hint"], ""),
    )
    with pytest.raises(ValueError, match="would hide sealed fields"):
        consent_presentation.native_presentation(envelope)
