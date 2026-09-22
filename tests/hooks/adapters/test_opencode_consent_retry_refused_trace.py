"""Every refused consent retry leaves a ``consent.retry.refused`` row naming its cause.

Two gates can refuse a claimed retry: the plugin, before the call reaches
policy (``opencode/plugin.ts::evaluateConsentRetry``, relayed through the
bridge's ``retry.refused`` event), and the policy adapter, when the proof the
plugin forwarded does not verify against the grant it names
(``hooks/adapters/opencode.py::_consent_retry_rejection``). Until this trace
existed, the first left nothing in the store and the second wrote only an
``approval_events`` denial that needed a matching grant to exist.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HOOKS_DIR = REPO_ROOT / "hooks"
for _path in (str(REPO_ROOT), str(HOOKS_DIR), str(REPO_ROOT / "opencode")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from adapters.opencode import OpenCodeAdapter  # noqa: E402

APPROVAL_ID = "P-a4e54958238e4428b49e5623ab4f527a"
SESSION_ID = "ses_f53ee3ac2ffeLzO3PSaVXUxFYy"
RETRY_CALL_ID = "call_RPws80NkLnr3VjRiriMk6QSx"
ROLE = "gaia-operator"
COMMAND = "cp /dev/null /home/jorge/.gaia/scratch/oc-lote-probe1.txt"
FINGERPRINT = hashlib.sha256(COMMAND.encode("utf-8")).hexdigest()


@pytest.fixture()
def db_env(tmp_path, monkeypatch, bootstrapped_db_template):
    """A real bootstrapped database this test alone owns."""
    from tests.conftest import copy_bootstrapped_db

    db_path = tmp_path / "retry-trace.db"
    copy_bootstrapped_db(bootstrapped_db_template, db_path)
    monkeypatch.setenv("GAIA_DB", str(db_path))
    env = os.environ.copy()
    env["GAIA_DB"] = str(db_path)
    return env


def _refusals(db_env):
    from gaia.approvals.decision_audit import CONSENT_RETRY_REFUSED_EVENT
    from gaia.store.reader import cross_surface_query

    rows = cross_surface_query(
        surface="harness_events", type=CONSENT_RETRY_REFUSED_EVENT,
        db_path=Path(db_env["GAIA_DB"]),
    )
    return [(row["raw"]["severity"], json.loads(row["raw"]["payload"])) for row in rows]


def test_the_bridge_records_a_refused_retry_with_the_comparison_that_refused_it(db_env):
    import bridge as opencode_bridge

    refusals = (
        ("role_mismatch", ROLE, "developer"),
        ("fingerprint_mismatch", FINGERPRINT, hashlib.sha256(f"{COMMAND} ".encode()).hexdigest()),
    )
    for reason, expected, received in refusals:
        response = opencode_bridge.handle({
            "event": "retry.refused",
            "sessionID": SESSION_ID,
            "callID": RETRY_CALL_ID,
            "approvalID": APPROVAL_ID,
            "reason": reason,
            "expected": expected,
            "received": received,
        })
        assert response["action"] == "allow", response

    recorded = _refusals(db_env)
    assert [severity for severity, _ in recorded] == ["warning", "warning"]
    assert [
        (p["reason"], p["expected"], p["received"]) for _, p in recorded
    ] == list(refusals)
    for _, payload in recorded:
        assert payload["approval_id"] == APPROVAL_ID
        assert payload["session_id"] == SESSION_ID
        assert payload["call_id"] == RETRY_CALL_ID
        assert payload["lane"] == "opencode.plugin_gate"


@pytest.mark.parametrize("host_before", [None, "claude_code"])
def test_the_bridge_leaves_the_host_selection_it_found(db_env, monkeypatch, host_before):
    """A leaked GAIA_HOST=opencode makes every later in-process hook parse Claude Code payloads as OpenCode."""
    import bridge as opencode_bridge

    if host_before is None:
        monkeypatch.delenv("GAIA_HOST", raising=False)
    else:
        monkeypatch.setenv("GAIA_HOST", host_before)

    response = opencode_bridge.handle({
        "event": "retry.refused",
        "sessionID": SESSION_ID,
        "callID": RETRY_CALL_ID,
        "approvalID": APPROVAL_ID,
        "reason": "role_mismatch",
        "expected": ROLE,
        "received": "developer",
    })

    assert response["action"] == "allow", response
    assert os.environ.get("GAIA_HOST") == host_before


def _retry_event(proof: dict):
    raw = {
        "event": "tool.execute.before",
        "sessionID": SESSION_ID,
        "callID": RETRY_CALL_ID,
        "agent": ROLE,
        "roleContext": {
            "role": ROLE, "capabilities": [], "issuer": "opencode-runtime",
            "attestation": f"{SESSION_ID}:{ROLE}", "verified": True,
        },
        "tool": "bash",
        "args": {"command": COMMAND},
        "consentRetry": proof,
    }
    return OpenCodeAdapter().parse_event(json.dumps(raw))


def test_the_policy_gate_records_a_rejected_proof_against_the_grant_it_should_have_matched(db_env, monkeypatch):
    import gaia.approvals.store as store
    import gaia.store.writer as writer

    denials = []
    monkeypatch.setattr(
        writer, "find_pending_plan_command",
        lambda command: {"approval_id": APPROVAL_ID, "index": 0, "fingerprint": FINGERPRINT},
    )
    monkeypatch.setattr(
        store, "record_execution_denial",
        lambda approval_id, reason, **kwargs: denials.append((approval_id, reason, kwargs)),
    )
    rejection = "OpenCode consent retry proof drifted from its bound grant"

    OpenCodeAdapter._record_retry_denial(
        _retry_event({"approval_id": APPROVAL_ID}), "bash", rejection,
    )

    recorded = _refusals(db_env)
    assert len(recorded) == 1, recorded
    severity, payload = recorded[0]
    assert severity == "warning"
    assert payload["reason"] == "proof_rejected"
    assert payload["detail"] == rejection
    assert payload["lane"] == "opencode.policy_gate"
    assert payload["approval_id"] == APPROVAL_ID
    assert payload["session_id"] == SESSION_ID
    assert payload["call_id"] == RETRY_CALL_ID
    assert payload["expected"] == f"grant {APPROVAL_ID}[0] fingerprint {FINGERPRINT}"
    assert payload["received"] == f"{SESSION_ID}/{RETRY_CALL_ID} as {ROLE} fingerprint {FINGERPRINT}"
    assert [(d[0], d[1], d[2]["detail"]) for d in denials] == [
        (APPROVAL_ID, "consent_retry_proof_rejected", rejection),
    ]


def test_the_policy_gate_still_traces_a_proof_that_names_no_live_grant(db_env, monkeypatch):
    """No approval row to hang a denial off is not a reason to lose the refusal."""
    import gaia.approvals.store as store
    import gaia.store.writer as writer

    monkeypatch.setattr(writer, "find_pending_plan_command", lambda command: None)
    monkeypatch.setattr(
        store, "record_execution_denial",
        lambda *args, **kwargs: pytest.fail("no approval row exists for this denial"),
    )
    rejection = "OpenCode consent retry proof names no executable command"

    OpenCodeAdapter._record_retry_denial(
        _retry_event({"approval_id": APPROVAL_ID}), "bash", rejection,
    )

    recorded = _refusals(db_env)
    assert len(recorded) == 1, recorded
    _, payload = recorded[0]
    assert payload["reason"] == "proof_rejected"
    assert payload["detail"] == rejection
    assert payload["approval_id"] == APPROVAL_ID
    assert payload["expected"] == "the active typed grant named by this proof"
