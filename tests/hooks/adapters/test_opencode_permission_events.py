"""One consent attempt, one neutral decision, whichever event delivered it.

The preferred lane is ``permission.replied``. These tests pin the precedence
between the lanes, the deduplication that keeps a single effect, and the
correlated neutral decision that reaches the Gaia CLI.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _path in (_REPO_ROOT / "hooks", _REPO_ROOT / "bin"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from adapters.consent_events import (
    COMPATIBILITY_DECISION_LANE,
    PREFERRED_DECISION_LANE,
    ConsentDecisionLedger,
    build_decision,
    lane_rank,
    normalize_decision,
)
from adapters.types import ConsentBinding, ConsentDecision
from cli.approvals import cmd_opencode_decide, cmd_opencode_present

_PLUGIN = _REPO_ROOT / "opencode" / "plugin.ts"
_BINDING = ConsentBinding(agent_id="agent-1", session_id="ses-1", call_id="call-1")


def test_the_preferred_lane_outranks_the_compatibility_lane():
    assert lane_rank(PREFERRED_DECISION_LANE) < lane_rank(COMPATIBILITY_DECISION_LANE)
    with pytest.raises(ValueError, match="unknown consent decision lane"):
        lane_rank("permission.v2.replied")


def test_one_consent_attempt_has_one_correlation_across_lanes_and_processes():
    preferred = build_decision("P-1", _BINDING, "once")
    compatibility = build_decision("P-1", _BINDING, "once")
    other_call = build_decision(
        "P-1", ConsentBinding(agent_id="agent-1", session_id="ses-1", call_id="call-2"), "once"
    )

    assert preferred.correlation_id == compatibility.correlation_id
    assert preferred.request_fingerprint == compatibility.request_fingerprint
    assert preferred.correlation_id != other_call.correlation_id
    assert preferred.protocol_version == "1"


def test_a_second_delivery_of_the_same_reply_never_acts_twice():
    ledger = ConsentDecisionLedger()
    decision = build_decision("P-1", _BINDING, "once")

    first = ledger.admit(PREFERRED_DECISION_LANE, decision)
    second = ledger.admit(COMPATIBILITY_DECISION_LANE, build_decision("P-1", _BINDING, "once"))

    assert (first.accepted, first.duplicate) == (True, False)
    assert (second.accepted, second.duplicate) == (False, True)
    assert ledger.effective(decision.correlation_id).lane == PREFERRED_DECISION_LANE


def test_the_preferred_lane_takes_precedence_when_it_arrives_second():
    ledger = ConsentDecisionLedger()
    compatibility = build_decision("P-1", _BINDING, "reject")

    first = ledger.admit(COMPATIBILITY_DECISION_LANE, compatibility)
    second = ledger.admit(PREFERRED_DECISION_LANE, build_decision("P-1", _BINDING, "once"))
    effective = ledger.effective(compatibility.correlation_id)

    assert first.accepted is True
    assert second.accepted is False
    assert second.superseded_lane == COMPATIBILITY_DECISION_LANE
    assert second.conflicting_lane == PREFERRED_DECISION_LANE
    assert second.decision.decision is ConsentDecision.REJECT
    assert effective.lane == PREFERRED_DECISION_LANE


def test_an_unrecognized_reply_is_never_read_as_consent():
    assert normalize_decision("always") is ConsentDecision.ALWAYS
    assert normalize_decision("reject") is ConsentDecision.REJECT
    assert normalize_decision("once") is ConsentDecision.ONCE
    with pytest.raises(ValueError, match="unrecognized consent reply"):
        normalize_decision("yes")


def _args(**overrides):
    values = {
        "approval_id": "P-open-1",
        "session_id": "ses-1",
        "call_id": "call-1",
        "token": "secret-token",
        "reply": "once",
        "decision_lane": PREFERRED_DECISION_LANE,
        "json": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _presented_store():
    store = MagicMock()
    store.get_by_id.return_value = {
        "id": "P-open-1",
        "status": "pending",
        "session_id": "ses-1",
        "agent_id": "agent-1",
    }
    events: list[dict[str, str]] = []
    store.get_history.side_effect = lambda _id: events
    store.record_event.side_effect = lambda *_a, **kw: events.append(
        {"event_type": "SHOWN", "metadata_json": kw["metadata_json"]}
    )
    return store


def _decide_through_cli(lane, capsys):
    store = _presented_store()
    with patch("cli.approvals._import_approval_store", return_value=store):
        assert cmd_opencode_present(_args()) == 0
        assert cmd_opencode_decide(_args(decision_lane=lane)) == 0
    return json.loads(capsys.readouterr().out.splitlines()[-1])


def test_both_lanes_reach_the_cli_as_one_correlated_neutral_decision(capsys):
    preferred = _decide_through_cli(PREFERRED_DECISION_LANE, capsys)
    compatibility = _decide_through_cli(COMPATIBILITY_DECISION_LANE, capsys)

    assert preferred["decision_lane"] == PREFERRED_DECISION_LANE
    assert compatibility["decision_lane"] == COMPATIBILITY_DECISION_LANE
    assert preferred["correlation_id"] == compatibility["correlation_id"]
    assert preferred["request_fingerprint"] == compatibility["request_fingerprint"]
    assert preferred["decision"] == "once"
    assert preferred["protocol_version"] == "1"


def test_the_cli_refuses_a_lane_it_does_not_know(capsys):
    store = _presented_store()
    with patch("cli.approvals._import_approval_store", return_value=store):
        assert cmd_opencode_present(_args()) == 0
        assert cmd_opencode_decide(_args(decision_lane="permission.v2.replied")) == 1

    assert "unknown consent decision lane" in capsys.readouterr().out
    store.approve.assert_not_called()


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the plugin edge")
def test_the_plugin_edge_routes_both_events_to_one_effect():
    script = (
        "import { PermissionDecisionRouter, permissionDecisionLane, normalizePermissionReply }"
        f" from {json.dumps(str(_PLUGIN))};"
        "const router = new PermissionDecisionRouter();"
        'const compat = router.admit("req-1", permissionDecisionLane("permission.v2.replied"));'
        'const preferred = router.admit("req-1", permissionDecisionLane("permission.replied"));'
        "console.log(JSON.stringify({compat, preferred,"
        ' effective: router.effectiveLane("req-1"),'
        ' unknownReply: normalizePermissionReply("something-new")}));'
    )
    result = subprocess.run(["bun", "-e", script], text=True, capture_output=True, check=True)
    observed = json.loads(result.stdout)

    assert observed["compat"] == {"lane": "compatibility", "accepted": True, "duplicate": False}
    assert observed["preferred"]["accepted"] is False
    assert observed["preferred"]["supersededLane"] == "compatibility"
    assert observed["effective"] == "preferred"
    assert observed["unknownReply"] == "reject"
