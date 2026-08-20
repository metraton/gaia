"""Provenance of the OpenCode control-plane claim, issuance through verdict.

The affirmative case is asserted over the payload the real plugin emits, driven
through bun with the real Gaia-side bridge answering issuance: an attested lane
proven on a hand-written dict proves nothing, which this plan established three
times. The negatives are synthetic on purpose -- an arbitrary writer on the
bridge's stdin really can emit them, and that is exactly what must be refused.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_HOOKS_DIR = _REPO / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from adapters.opencode import OpenCodeAdapter
from modules.orchestrator.delegate_mode import SessionRole, classify_session_role
from modules.security.host_attestation import (
    ATTESTATION_SCHEME,
    MAX_DELEGATION_DEPTH,
    AttestationDenied,
    host_run_id,
    issue,
    ledger_path,
    resolve,
)

_DRIVER = _REPO / "tests" / "opencode" / "attestation_driver.ts"
_BRIDGE = _REPO / "opencode" / "bridge.py"
_ISSUER = "opencode-runtime"


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    """Point issuance and resolution at a ledger this test owns."""
    monkeypatch.setenv("GAIA_OPENCODE_ATTESTATION_DIR", str(tmp_path / "ledger"))
    return tmp_path


@pytest.fixture
def drive(ledger, monkeypatch):
    """Run the real plugin, then join the host run its bridge minted in.

    Issuance happens in a bridge process the bun driver spawned; these
    assertions resolve here, in the pytest process. Production runs both in
    bridge children of one OpenCode host, so the namespace each derives from
    its parent is the same one -- across this test's two unrelated parents it
    would not be, for a reason production does not have. The namespace is read
    back from the ledger the bridge chose to write, never named by this test,
    so the negatives below still fail on what they tamper with rather than on a
    namespace that never matched.
    """

    def run(scenario):
        requests = _drive(scenario)
        written = sorted((ledger / "ledger").glob("*.json"))
        assert len(written) == 1, f"the bridge wrote no single ledger: {written}"
        monkeypatch.setattr(
            "modules.security.host_attestation.host_run_id", lambda: written[0].stem
        )
        return requests

    return run


def _drive(scenario):
    """Return every event the real plugin sent Gaia for this scenario."""
    result = subprocess.run(
        ["bun", str(_DRIVER), json.dumps(scenario)],
        text=True,
        capture_output=True,
        env=os.environ.copy(),
        cwd=str(_REPO),
    )
    assert result.returncode == 0, f"driver failed: {result.stderr}"
    return json.loads(result.stdout)


def _emitted(requests, event, session_id):
    for request in requests:
        if request.get("event") == event and request.get("sessionID") == session_id:
            return request
    raise AssertionError(f"plugin emitted no {event} for {session_id}: {requests}")


def _policy_payload(emitted):
    adapter = OpenCodeAdapter()
    return adapter.build_policy_payload(adapter.parse_event(json.dumps(emitted)))


def _control_plane_turn(drive):
    return drive(
        {
            "steps": [
                {"kind": "message", "sessionID": "ses-root", "agent": "gaia-orchestrator"},
                {
                    "kind": "before",
                    "sessionID": "ses-root",
                    "callID": "call-1",
                    "tool": "bash",
                    "args": {"command": "gaia plan show brief"},
                },
            ],
        }
    )


def test_a_host_issued_claim_confers_the_control_plane_lane(drive):
    """The affirmative claim, over the plugin's own emission end to end."""
    requests = _control_plane_turn(drive)
    emitted = _emitted(requests, "tool.execute.before", "ses-root")

    attestation = emitted["roleContext"]["attestation"]
    assert attestation.startswith(ATTESTATION_SCHEME)
    # No part of the token is derivable from the session or the role: the
    # spelling the defect used was exactly `${sessionID}:${role}`.
    assert "ses-root" not in attestation and "gaia-orchestrator" not in attestation

    payload = _policy_payload(emitted)

    assert payload["role_context"]["provenance"] == "host-issued"
    assert payload["role_context"]["granted_by"] is None
    assert payload["role_context"]["delegation_depth"] == 0
    assert payload["agent_type"] == "gaia-orchestrator"
    assert classify_session_role(payload) is SessionRole.ORCHESTRATOR
    assert OpenCodeAdapter().adapt_pre_tool_use(
        OpenCodeAdapter().parse_event(json.dumps(emitted))
    ).output.get("action") != "deny"


def test_the_issued_token_is_recorded_by_the_issuing_process(drive, ledger):
    """Provenance means host state: the claim exists in the issuer's ledger."""
    emitted = _emitted(_control_plane_turn(drive), "tool.execute.before", "ses-root")
    # Read from the file the issuing process chose to write, which is the whole
    # point: the ledger is named by that process, so nothing here can name it.
    written = sorted((ledger / "ledger").glob("*.json"))
    recorded = json.loads(written[0].read_text())["records"]

    token = emitted["roleContext"]["attestation"]
    assert recorded[token]["session_id"] == "ses-root"
    assert recorded[token]["role"] == "gaia-orchestrator"
    assert recorded[token]["depth"] == 0
    assert recorded[token]["granted_by"] is None


def test_a_caller_minted_attestation_is_refused_the_lane(drive):
    """The exact string the defect minted, on the shape the plugin emits."""
    emitted = _emitted(_control_plane_turn(drive), "tool.execute.before", "ses-root")
    forged = dict(
        emitted,
        roleContext=dict(emitted["roleContext"], attestation="ses-root:gaia-orchestrator"),
    )

    payload = _policy_payload(forged)

    assert payload["role_context"]["verified"] is False
    assert payload["role_context"]["provenance"] == "unresolved"
    assert payload["agent_type"] == "opencode-unattested"
    assert classify_session_role(payload) is not SessionRole.ORCHESTRATOR

    response = OpenCodeAdapter().adapt_pre_tool_use(
        OpenCodeAdapter().parse_event(json.dumps(forged))
    )
    assert response.output["action"] == "deny"
    assert "not attested" in response.output["reason"]


def test_the_forwarding_this_task_replaced_would_have_conferred_the_lane(drive):
    """The defect, held as a regression: the old body was ``asdict(context)``.

    ``git show HEAD:hooks/adapters/opencode.py`` carries that one-line
    forwarding, and the classifier it feeds is frozen, so the same
    caller-minted claim is run through both bodies here. The old one still
    reaches the control-plane role; the shipped one cannot.
    """
    from dataclasses import asdict

    emitted = _emitted(_control_plane_turn(drive), "tool.execute.before", "ses-root")
    forged = dict(
        emitted,
        roleContext=dict(emitted["roleContext"], attestation="ses-root:gaia-orchestrator"),
    )
    event = OpenCodeAdapter().parse_event(json.dumps(forged))
    payload = OpenCodeAdapter().build_policy_payload(event)

    pre_change = dict(payload, role_context=asdict(event.role_context))
    assert classify_session_role(pre_change) is SessionRole.ORCHESTRATOR
    assert event.role_context.claims_control_plane_shape is True

    assert classify_session_role(payload) is not SessionRole.ORCHESTRATOR


@pytest.mark.parametrize(
    "mutation",
    [
        {"attestation": ATTESTATION_SCHEME + "0" * 32},
        {"issuer": "opencode-plugin"},
    ],
    ids=["unknown-nonce", "issuer-not-the-recorded-one"],
)
def test_a_claim_that_does_not_resolve_against_host_state_is_refused(mutation, drive):
    emitted = _emitted(_control_plane_turn(drive), "tool.execute.before", "ses-root")
    tampered = dict(emitted, roleContext=dict(emitted["roleContext"], **mutation))

    response = OpenCodeAdapter().adapt_pre_tool_use(
        OpenCodeAdapter().parse_event(json.dumps(tampered))
    )

    assert response.output["action"] == "deny"
    assert classify_session_role(_policy_payload(tampered)) is not SessionRole.ORCHESTRATOR


def test_a_claim_replayed_on_another_session_does_not_resolve(drive):
    emitted = _emitted(_control_plane_turn(drive), "tool.execute.before", "ses-root")
    replayed = dict(emitted, sessionID="ses-other")

    assert classify_session_role(_policy_payload(replayed)) is not SessionRole.ORCHESTRATOR


def test_an_attested_parent_cannot_mint_an_attested_control_plane_child():
    """The laundering route: presence-only retention let a name be re-let."""
    root = issue(
        host_run="run-chain",
        session_id="ses-root",
        role="gaia-orchestrator",
        issuer=_ISSUER,
    )

    with pytest.raises(AttestationDenied, match="control-plane child"):
        issue(
            host_run="run-chain",
            session_id="ses-child",
            role="gaia-orchestrator",
            issuer=_ISSUER,
            parent_attestation=root.token,
        )


def test_a_child_named_gaia_orchestrator_reaches_no_attested_lane(drive):
    """The same refusal through the plugin's own dispatch route."""
    requests = drive(
        {
            "steps": [
                {"kind": "message", "sessionID": "ses-root", "agent": "gaia-orchestrator"},
                {
                    "kind": "before",
                    "sessionID": "ses-root",
                    "callID": "call-1",
                    "tool": "task",
                    "args": {"subagent_type": "gaia-orchestrator", "prompt": "go"},
                },
                {
                    "kind": "after-task",
                    "sessionID": "ses-root",
                    "callID": "call-1",
                    "args": {"subagent_type": "gaia-orchestrator", "prompt": "go"},
                    "childSessionID": "ses-child",
                },
                {
                    "kind": "before",
                    "sessionID": "ses-child",
                    "callID": "call-2",
                    "tool": "bash",
                    "args": {"command": "rm -rf /tmp/probe"},
                },
            ],
        }
    )
    child = _emitted(requests, "tool.execute.before", "ses-child")

    assert "roleContext" not in child
    assert classify_session_role(_policy_payload(child)) is not SessionRole.ORCHESTRATOR


def test_a_second_session_named_by_the_host_takes_no_control_plane_claim(drive):
    """The message.updated feed route, which needs no dispatch at all."""
    requests = drive(
        {
            "steps": [
                {"kind": "message", "sessionID": "ses-root", "agent": "gaia-orchestrator"},
                {"kind": "message", "sessionID": "ses-late", "agent": "gaia-orchestrator"},
                {
                    "kind": "before",
                    "sessionID": "ses-late",
                    "callID": "call-9",
                    "tool": "bash",
                    "args": {"command": "gaia plan show brief"},
                },
            ],
        }
    )
    late = _emitted(requests, "tool.execute.before", "ses-late")

    assert "roleContext" not in late
    assert classify_session_role(_policy_payload(late)) is not SessionRole.ORCHESTRATOR


def test_the_ledger_binds_the_control_plane_to_one_session_per_run():
    issue(
        host_run="run-unique",
        session_id="ses-root",
        role="gaia-orchestrator",
        issuer=_ISSUER,
    )

    with pytest.raises(AttestationDenied, match="already bound"):
        issue(
            host_run="run-unique",
            session_id="ses-other",
            role="gaia-orchestrator",
            issuer=_ISSUER,
        )


def test_a_delegation_chain_stops_at_the_declared_ceiling():
    parent = issue(
        host_run="run-depth", session_id="ses-0", role="gaia-orchestrator", issuer=_ISSUER
    )
    for depth in range(1, MAX_DELEGATION_DEPTH + 1):
        parent = issue(
            host_run="run-depth",
            session_id=f"ses-{depth}",
            role="developer",
            issuer=_ISSUER,
            parent_attestation=parent.token,
        )
        assert parent.depth == depth
        assert parent.granted_by == f"ses-{depth - 1}"

    with pytest.raises(AttestationDenied, match="exceeds the ceiling"):
        issue(
            host_run="run-depth",
            session_id="ses-over",
            role="developer",
            issuer=_ISSUER,
            parent_attestation=parent.token,
        )


def test_a_grant_from_an_unresolvable_parent_is_refused():
    with pytest.raises(AttestationDenied, match="does not resolve against host state"):
        issue(
            host_run="run-orphan",
            session_id="ses-child",
            role="developer",
            issuer=_ISSUER,
            parent_attestation=ATTESTATION_SCHEME + "f" * 32,
        )


def test_resolution_rejects_a_record_beyond_the_ceiling(monkeypatch):
    """The ceiling is enforced again at the boundary, not only at issuance."""
    issued = issue(
        host_run="run-lowered", session_id="ses-0", role="developer", issuer=_ISSUER
    )
    parent = issue(
        host_run="run-lowered",
        session_id="ses-1",
        role="developer",
        issuer=_ISSUER,
        parent_attestation=issued.token,
    )
    monkeypatch.setattr(
        "modules.security.host_attestation.MAX_DELEGATION_DEPTH", parent.depth - 1
    )

    assert (
        resolve(
            host_run="run-lowered",
            token=parent.token,
            session_id="ses-1",
            role="developer",
            issuer=_ISSUER,
        )
        is None
    )


def test_a_dispatched_child_carries_the_grant_that_created_it(drive):
    """Chain accountability: the forwarded claim names who granted it."""
    requests = drive(
        {
            "steps": [
                {"kind": "message", "sessionID": "ses-root", "agent": "gaia-orchestrator"},
                {
                    "kind": "before",
                    "sessionID": "ses-root",
                    "callID": "call-1",
                    "tool": "task",
                    "args": {"subagent_type": "developer", "prompt": "go"},
                },
                {
                    "kind": "after-task",
                    "sessionID": "ses-root",
                    "callID": "call-1",
                    "args": {"subagent_type": "developer", "prompt": "go"},
                    "childSessionID": "ses-child",
                },
                {
                    "kind": "before",
                    "sessionID": "ses-child",
                    "callID": "call-2",
                    "tool": "bash",
                    "args": {"command": "gaia contract list"},
                },
            ],
        }
    )
    payload = _policy_payload(_emitted(requests, "tool.execute.before", "ses-child"))

    assert payload["role_context"]["provenance"] == "host-issued"
    assert payload["role_context"]["granted_by"] == "ses-root"
    assert payload["role_context"]["delegation_depth"] == 1
    assert classify_session_role(payload) is not SessionRole.ORCHESTRATOR


def test_the_bridge_mints_in_the_namespace_of_the_process_that_started_it(ledger):
    """The issuing namespace traces to host state, not to the request.

    The bridge is spawned here, so the process that started it is this test
    process and the namespace it must derive is this process's own identity.
    The request nominates a different one, and that name must reach nothing.
    """
    request = {
        "event": "identity.attest",
        "hostRun": "run-planted",
        "sessionID": "ses-root",
        "role": "gaia-orchestrator",
        "issuer": _ISSUER,
    }
    result = subprocess.run(
        [sys.executable, str(_BRIDGE)],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        env=os.environ.copy(),
        cwd=str(_REPO),
    )
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["action"] == "allow", response

    stems = sorted(path.stem for path in (ledger / "ledger").glob("*.json"))

    assert len(stems) == 1, stems
    assert stems[0].startswith(f"host-{os.getpid()}-")
    assert "run-planted" not in stems
    recorded = json.loads(ledger_path(stems[0]).read_text())["records"]
    assert recorded[response["attestation"]]["session_id"] == "ses-root"


def test_a_claim_minted_in_another_host_run_does_not_resolve_in_this_one():
    """A genuine claim from another run is still refused this run's lane.

    The payload nominates exactly the namespace the token is bound in, which is
    what the shipped resolution read: verifying a token against a ledger the
    claimant names establishes that the pair agrees with itself, never where the
    token came from. Resolution answers from this process's own host run, so the
    nomination reaches nothing.
    """
    issued = issue(
        host_run="run-elsewhere",
        session_id="ses-root",
        role="gaia-orchestrator",
        issuer=_ISSUER,
    )
    presented = {
        "event": "tool.execute.before",
        "hostRun": "run-elsewhere",
        "sessionID": "ses-root",
        "callID": "call-1",
        "tool": "bash",
        "args": {"command": "gaia plan show brief"},
        "agent": "gaia-orchestrator",
        "roleContext": {
            "role": "gaia-orchestrator",
            "capabilities": [],
            "issuer": _ISSUER,
            "attestation": issued.token,
            "verified": True,
        },
    }
    claimed = dict(
        token=issued.token,
        session_id="ses-root",
        role="gaia-orchestrator",
        issuer=_ISSUER,
    )

    assert resolve(host_run="run-elsewhere", **claimed) is not None
    assert resolve(host_run=host_run_id(), **claimed) is None

    response = OpenCodeAdapter().adapt_pre_tool_use(
        OpenCodeAdapter().parse_event(json.dumps(presented))
    )

    assert response.output["action"] == "deny"
    assert classify_session_role(_policy_payload(presented)) is not SessionRole.ORCHESTRATOR
