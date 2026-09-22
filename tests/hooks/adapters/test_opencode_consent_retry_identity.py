"""The consent retry proof names the approval's Gaia identity, not the host role.

Measured 2026-09-16 (P-a4e54958238e4428b49e5623ab4f527a): the approval and its
grant carried ``agent_id=a69d869dc02031f54`` -- the requesting contract's Gaia
identity -- while the executing session's host role was ``gaia-operator``.
A verifier that compared the two as one namespace refused a byte-identical
retry of command [0].
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = Path(__file__).resolve().parents[3] / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from adapters.consent_events import mint_correlation_id
from adapters.opencode import OpenCodeAdapter
from adapters.types import ConsentBinding

APPROVAL_ID = "P-a4e54958238e4428b49e5623ab4f527a"
GAIA_AGENT_ID = "a69d869dc02031f54"
ROLE = "gaia-operator"
SESSION_ID = "ses_f53ee3ac2ffeLzO3PSaVXUxFYy"
ORIGINAL_CALL_ID = "call_fwv2msrC7uZKlZDftj64alRO"
RETRY_CALL_ID = "call_RPws80NkLnr3VjRiriMk6QSx"
COMMAND = "cp /dev/null /home/jorge/.gaia/scratch/oc-lote-probe1.txt"
FINGERPRINT = hashlib.sha256(COMMAND.encode("utf-8")).hexdigest()
REQUEST_FINGERPRINT = "request-fingerprint"


def _grant(agent_id: str = GAIA_AGENT_ID) -> dict:
    return {
        "approval_id": APPROVAL_ID,
        "agent_id": agent_id,
        "session_id": SESSION_ID,
        "scope": "COMMAND_SET",
        "source": "plan-first",
        "status": "PENDING",
        "next_index": 0,
        "reservation_tool_use_id": None,
        "request_fingerprint": REQUEST_FINGERPRINT,
        "command_set_json": json.dumps(
            [{"command": COMMAND, "fingerprint": FINGERPRINT, "rationale": ""}]
        ),
    }


def _proof(agent_id: str = GAIA_AGENT_ID) -> dict:
    binding = ConsentBinding(
        agent_id=agent_id, session_id=SESSION_ID, call_id=ORIGINAL_CALL_ID
    )
    return {
        "version": 1,
        "kind": "COMMAND_SET",
        "approval_id": APPROVAL_ID,
        "correlation_id": mint_correlation_id(APPROVAL_ID, binding),
        "agent_id": agent_id,
        "role": ROLE,
        "session_id": SESSION_ID,
        "original_call_id": ORIGINAL_CALL_ID,
        "retry_call_id": RETRY_CALL_ID,
        "command": COMMAND,
        "command_fingerprint": FINGERPRINT,
        "expected_index": 0,
        "request_fingerprint": REQUEST_FINGERPRINT,
    }


def _event(proof: dict):
    raw = {
        "event": "tool.execute.before",
        "sessionID": SESSION_ID,
        "callID": RETRY_CALL_ID,
        "agent": ROLE,
        "roleContext": {
            "role": ROLE,
            "capabilities": [],
            "issuer": "opencode-runtime",
            "attestation": f"{SESSION_ID}:{ROLE}",
            "verified": True,
        },
        "tool": "bash",
        "args": {"command": COMMAND},
        "consentRetry": proof,
    }
    return OpenCodeAdapter().parse_event(json.dumps(raw))


@pytest.fixture
def bound_grant(monkeypatch):
    import gaia.store.writer as writer

    grant = _grant()
    monkeypatch.setattr(
        writer, "find_pending_plan_command",
        lambda command: {"approval_id": APPROVAL_ID, "index": 0, "fingerprint": FINGERPRINT}
        if command == COMMAND else None,
    )
    monkeypatch.setattr(writer, "list_approval_grants", lambda **_: [grant])
    monkeypatch.setattr(
        OpenCodeAdapter, "_resolved_attestation", staticmethod(lambda event: object())
    )
    return grant


def test_gaia_agent_id_in_proof_is_verified_against_the_grant_not_the_host_role(bound_grant):
    assert OpenCodeAdapter._policy_agent_type(_event(_proof())) == ROLE

    rejection = OpenCodeAdapter._consent_retry_rejection(_event(_proof()), "bash")

    assert rejection is None


def test_proof_naming_a_different_agent_than_the_grant_still_drifts(bound_grant):
    rejection = OpenCodeAdapter._consent_retry_rejection(
        _event(_proof(agent_id="a0000000000000000")), "bash"
    )

    assert rejection == "OpenCode consent retry proof drifted from its bound grant"


def test_proof_without_an_agent_id_does_not_match(bound_grant):
    proof = _proof()
    proof["agent_id"] = ""

    rejection = OpenCodeAdapter._consent_retry_rejection(_event(proof), "bash")

    assert rejection == "OpenCode consent retry proof does not match this fresh tool call"


def _typed_event(tool: str, args: dict, proof: dict):
    raw = {
        "event": "tool.execute.before",
        "sessionID": SESSION_ID,
        "callID": RETRY_CALL_ID,
        "agent": ROLE,
        "roleContext": {
            "role": ROLE,
            "capabilities": [],
            "issuer": "opencode-runtime",
            "attestation": f"{SESSION_ID}:{ROLE}",
            "verified": True,
        },
        "tool": tool,
        "args": args,
        "consentRetry": proof,
    }
    return OpenCodeAdapter().parse_event(json.dumps(raw))


def _common_proof(kind: str) -> dict:
    binding = ConsentBinding(
        agent_id=GAIA_AGENT_ID, session_id=SESSION_ID, call_id=ORIGINAL_CALL_ID
    )
    return {
        "version": 1,
        "kind": kind,
        "approval_id": APPROVAL_ID,
        "correlation_id": mint_correlation_id(APPROVAL_ID, binding),
        "agent_id": GAIA_AGENT_ID,
        "role": ROLE,
        "session_id": SESSION_ID,
        "original_call_id": ORIGINAL_CALL_ID,
        "retry_call_id": RETRY_CALL_ID,
    }


@pytest.mark.parametrize("kind", ["SCOPE_SEMANTIC_SIGNATURE", "SCOPE_FILE_PATH"])
def test_typed_singular_and_file_proofs_are_verified_against_the_named_active_grant(
    monkeypatch, kind
):
    import gaia.store.writer as writer

    proof = _common_proof(kind)
    if kind == "SCOPE_SEMANTIC_SIGNATURE":
        proof.update({"command": COMMAND, "command_fingerprint": FINGERPRINT})
        grant_payload = {"command": COMMAND, "scope_signature": {}}
        event = _typed_event("bash", {"command": COMMAND}, proof)
    else:
        path = "/home/jorge/ws/me/gaia/hooks/example.py"
        proof.update({
            "canonical_path": path,
            "path_fingerprint": hashlib.sha256(path.encode()).hexdigest(),
            "tool_family": "Edit",
        })
        grant_payload = {"file_path": path, "scope_signature": {}}
        event = _typed_event("edit", {"file_path": path}, proof)
    grant = {
        "approval_id": APPROVAL_ID,
        "agent_id": GAIA_AGENT_ID,
        "session_id": SESSION_ID,
        "scope": kind,
        "source": "legacy",
        "status": "PENDING",
        "command_set_json": json.dumps(grant_payload),
    }
    monkeypatch.setattr(writer, "list_approval_grants", lambda **_: [grant])
    monkeypatch.setattr(
        OpenCodeAdapter, "_resolved_attestation", staticmethod(lambda event: object())
    )

    assert OpenCodeAdapter._consent_retry_rejection(event, event.payload["tool_name"]) is None


def test_typed_file_proof_with_a_different_target_fails_closed(monkeypatch):
    import gaia.store.writer as writer

    path = "/home/jorge/ws/me/gaia/hooks/example.py"
    proof = _common_proof("SCOPE_FILE_PATH")
    proof.update({
        "canonical_path": path,
        "path_fingerprint": hashlib.sha256(path.encode()).hexdigest(),
        "tool_family": "Write",
    })
    grant = {
        "approval_id": APPROVAL_ID,
        "agent_id": GAIA_AGENT_ID,
        "session_id": SESSION_ID,
        "scope": "SCOPE_FILE_PATH",
        "status": "PENDING",
        "command_set_json": json.dumps({"file_path": path}),
    }
    monkeypatch.setattr(writer, "list_approval_grants", lambda **_: [grant])
    monkeypatch.setattr(
        OpenCodeAdapter, "_resolved_attestation", staticmethod(lambda event: object())
    )
    event = _typed_event("write", {"file_path": f"{path}.other"}, proof)

    assert OpenCodeAdapter._consent_retry_rejection(event, "write") == (
        "OpenCode file retry proof path or tool family drifted from this call"
    )
