#!/usr/bin/env python3
"""One host question event must activate EVERY grant the user signed.

Provenance of the multi-label fixture -- the recorded incident, not invention.
On 2026-08-19 an agent presented TWO protected-path approvals in ONE
``AskUserQuestion`` call, both labels correctly formatted per the mandatory
``^Approve\\b.*\\[P-{hex}]`` rule. The user approved BOTH. Only the first
activated: ``gaia approvals show P-19265f4d`` reported "No approval found"
(consumed, its edits landed) while ``gaia approvals show P-0d6aba7c`` was still
``pending`` in the SAME session and its Edit was correctly refused; re-presenting
the second alone worked. The host emits one ``answers`` entry per question in the
call, so a two-question call carries two answered labels -- that is the shape
asserted here, and it is the shape the incident produced.

Every affirmative assertion drives the REAL hook entry point
(``ClaudeCodeAdapter.adapt_post_tool_use`` on a ``PostToolUse`` /
``AskUserQuestion`` payload) and seals its payloads with the REAL producer
(``bash_validator._build_sealed_payload`` fed ``detect_mutative_command``'s own
verdict), so neither the handler nor the payload is re-implemented here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
HOOKS_DIR = _REPO_ROOT / "hooks"
for _p in (str(HOOKS_DIR), str(HOOKS_DIR / "adapters"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SESSION = "s-multi-decision"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sealed_payload(command: str, *, agent_type: str = "test-agent") -> dict:
    """Seal ``command`` with the REAL producer, fed the classifier's verdict.

    The verdict is asserted mutative before it is used: a payload built from a
    verb the classifier never derives asserts over a shape the hook cannot emit.
    """
    from modules.security.mutative_verbs import detect_mutative_command
    from modules.tools.bash_validator import _build_sealed_payload

    verdict = detect_mutative_command(command)
    assert verdict.is_mutative, f"{command!r} is not intercepted as a mutative verb"
    return _build_sealed_payload(
        command=command,
        verb=verdict.verb,
        category=verdict.category,
        agent_type=agent_type,
    )


def _question_event(answers: dict, *, session_id: str = SESSION):
    """Build the real PostToolUse event the host delivers for an answered call."""
    from adapters.types import HookEvent, HookEventType

    return HookEvent(
        event_type=HookEventType.POST_TOOL_USE,
        session_id=session_id,
        payload={
            "hook_event_name": "PostToolUse",
            "tool_name": "AskUserQuestion",
            "session_id": session_id,
            "tool_input": {},
            "tool_response": {"answers": answers},
        },
    )


def _deliver(answers: dict, *, session_id: str = SESSION):
    from adapters.claude_code import ClaudeCodeAdapter

    return ClaudeCodeAdapter().adapt_post_tool_use(
        _question_event(answers, session_id=session_id)
    )


def _approve_label(approval_id: str, command: str) -> str:
    """The mandatory approve-label spelling: anchored ^Approve + [P-<prefix>]."""
    return f"Approve -- {command} [P-{approval_id[len('P-'):len('P-') + 8]}]"


def _request(command: str, *, session_id: str = SESSION) -> str:
    import gaia.approvals.store as store

    return store.insert_requested(
        _sealed_payload(command), agent_id="test-agent", session_id=session_id
    )


def _status(approval_id: str) -> str:
    import gaia.approvals.store as store

    con = store._open_db()
    try:
        row = con.execute(
            "SELECT status FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
    finally:
        con.close()
    return row["status"]


def _grant(command: str, *, session_id: str = SESSION):
    from modules.security.approval_grants import check_approval_grant

    return check_approval_grant(command, session_id=session_id)


def _non_activation_records() -> list[dict]:
    """Read the durable non-activation records back OUT of the store.

    A store read, never a log string: the whole point of the record is that a
    later reader with no access to this process's logs can still tell a dropped
    signature from a signature never given.
    """
    from gaia.approvals.decision_audit import DECISION_NOT_ACTIVATED_EVENT
    from gaia.store.reader import cross_surface_query

    rows = cross_surface_query(
        surface="harness_events", type=DECISION_NOT_ACTIVATED_EVENT, last=50
    )
    return [json.loads(r["raw"]["payload"] or "{}") for r in rows]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_grants_dir(tmp_path, monkeypatch):
    """Filesystem grants land in tmp; the DB is already GAIA_DATA_DIR-isolated."""
    import modules.security.approval_grants as ag

    (tmp_path / ".claude" / "cache" / "approvals").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "modules.security.approval_grants.get_plugin_data_dir",
        lambda: tmp_path / ".claude",
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", SESSION)
    ag._last_cleanup_time = 0.0
    ag._grants_dir_created = False


# ---------------------------------------------------------------------------
# Outcome 1: activation completeness
# ---------------------------------------------------------------------------

class TestMultiGrantActivation:
    """N signed labels in one event are N signatures, so N grants activate."""

    def test_two_approved_labels_both_activate(self):
        first_cmd = "terraform apply"
        second_cmd = "kubectl delete pod web-1"
        first = _request(first_cmd)
        second = _request(second_cmd)

        response = _deliver({
            "Approve terraform apply?": _approve_label(first, first_cmd),
            "Approve kubectl delete?": _approve_label(second, second_cmd),
        })

        assert response.exit_code == 0
        assert _status(first) == "approved"
        assert _status(second) == "approved", (
            "the SECOND signed label was dropped -- the defect this test exists "
            "for: one event, two signatures, one activation"
        )
        assert _grant(first_cmd) is not None
        assert _grant(second_cmd) is not None, (
            "the second grant must be executable, not merely marked approved"
        )

    def test_mixed_event_activates_only_the_approved_label(self):
        approved_cmd = "terraform apply"
        rejected_cmd = "git push origin main"
        approved = _request(approved_cmd)
        rejected = _request(rejected_cmd)

        _deliver({
            "Approve terraform apply?": _approve_label(approved, approved_cmd),
            "Approve the push?": f"Do not approve -- {rejected_cmd} "
                                 f"[P-{rejected[len('P-'):len('P-') + 8]}]",
        })

        assert _status(approved) == "approved"
        assert _grant(approved_cmd) is not None
        assert _status(rejected) == "pending", (
            "a label the user did not approve must yield no nonce and no grant, "
            "even when it carries a [P-...] tag -- the ^Approve anchoring is what "
            "keeps this path unable to manufacture a signature"
        )
        assert _grant(rejected_cmd) is None

    def test_reject_only_event_activates_nothing(self):
        command = "git push origin main"
        approval_id = _request(command)

        _deliver({"Proceed?": "Reject"})

        assert _status(approval_id) == "pending"
        assert _grant(command) is None


# ---------------------------------------------------------------------------
# Outcome 2: the durable non-activation record
# ---------------------------------------------------------------------------

class TestDurableNonActivationRecord:
    """A decision that granted nothing is distinguishable, in the store, from
    a decision never given."""

    def test_no_decision_leaves_no_record(self):
        _deliver({})
        assert _non_activation_records() == [], (
            "no decision was ever given -- there is nothing to audit, and a row "
            "here would make the two cases indistinguishable in the other direction"
        )

    def test_no_nonce_in_labels_is_recorded_with_its_reason(self):
        from gaia.approvals.decision_audit import (
            LANE_CLAUDE_CODE_QUESTION,
            REASON_NO_NONCE_IN_LABELS,
        )

        _deliver({"Proceed?": "Reject"})

        records = _non_activation_records()
        assert len(records) == 1, records
        assert records[0]["reason"] == REASON_NO_NONCE_IN_LABELS
        assert records[0]["lane"] == LANE_CLAUDE_CODE_QUESTION
        assert records[0]["session_id"] == SESSION
        assert records[0]["decision_values"] == ["Reject"], (
            "the record must carry WHAT the user answered; nothing else in it "
            "distinguishes a rejection from a malformed approve label"
        )

    def test_missing_session_binding_is_recorded(self, monkeypatch):
        from gaia.approvals.decision_audit import REASON_NO_SESSION_BINDING

        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
        _deliver({"Proceed?": "Approve -- terraform apply [P-deadbeef]"}, session_id="")

        records = _non_activation_records()
        assert [r["reason"] for r in records] == [REASON_NO_SESSION_BINDING]
        assert records[0]["session_id"] == ""

    def test_failed_activation_is_recorded_with_its_correlation(self):
        from gaia.approvals.decision_audit import REASON_ACTIVATION_FAILED

        _deliver({"Proceed?": "Approve -- terraform apply [P-deadbeef]"})

        records = _non_activation_records()
        assert [r["reason"] for r in records] == [REASON_ACTIVATION_FAILED]
        assert records[0]["nonce_prefix"] == "deadbeef", (
            "a failed activation must record WHICH signature was lost"
        )

    def test_one_failure_does_not_withhold_the_other_signature(self):
        command = "terraform apply"
        approval_id = _request(command)

        _deliver({
            "Approve the missing one?": "Approve -- nothing here [P-deadbeef]",
            "Approve terraform apply?": _approve_label(approval_id, command),
        })

        assert _status(approval_id) == "approved", (
            "the loop must continue past a failed activation -- a signature the "
            "user gave cannot be withheld by an unrelated failure"
        )
        assert [r["reason"] for r in _non_activation_records()] == [
            "activation_failed"
        ]
