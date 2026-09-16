"""Gaia's verdict is the last word on OpenCode, as it already is on Claude Code.

OpenCode submits a tool to its own permission gate AFTER tool.execute.before has
returned Gaia's verdict, so that gate can revoke consent Gaia already granted --
measured on 2026-09-15 against approval P-7d9c1365aca54d719f857a3103d53356,
where the user signed, Gaia reserved index 0, and the command never ran. Claude
Code has no equivalent second gate: PreToolUse is final there.

Both halves of the property are asserted, because the repair must not turn the
absence of correlation into consent: a call Gaia allowed resolves allowed, and a
request correlating to no Gaia verdict stays denied. Every status below is the
one the real plugin wrote onto the host's mutable output object under bun.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DRIVER = REPO_ROOT / "tests" / "opencode" / "permission_allow_driver.ts"

# The live coordinates of the measured incident, so the regression under test is
# the one that actually happened rather than a shape invented for the test.
SESSION_ID = "ses_f58618508ffedZT30SR1HooKFc"
CALL_ID = "call_szcMxac7pdlwc7svHGBMqVUH"
COMMAND = "cp /dev/null /tmp/gaia-command-set-e2e-20260915.txt"

UNCORRELATED_AUDIT_EVENT = "permission.uncorrelated"


def _drive(**scenario):
    scenario.setdefault("sessionID", SESSION_ID)
    scenario.setdefault("callID", CALL_ID)
    scenario.setdefault("command", COMMAND)
    scenario.setdefault("directory", str(REPO_ROOT))
    result = subprocess.run(
        ["bun", str(DRIVER), json.dumps(scenario)],
        capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_a_call_gaia_allowed_is_allowed_by_the_host_permission_gate():
    delivered = _drive()

    assert delivered["beforeError"] is None, delivered
    assert delivered["status"] == "allow", delivered


def test_a_request_correlating_to_no_gaia_verdict_stays_denied():
    """The half that must NOT change: no correlation is never consent."""
    delivered = _drive(runBefore=False)

    assert delivered["status"] == "deny", delivered


def test_a_foreign_call_id_under_an_allowed_session_stays_denied():
    """Correlation is the pair, not the session: one allow frees one call."""
    delivered = _drive(askCallID="call-never-ruled-on")

    assert delivered["beforeError"] is None, delivered
    assert delivered["status"] == "deny", delivered


def test_one_allow_is_consumed_by_one_permission_request():
    """A replay of the same request finds nothing left to correlate against."""
    delivered = _drive(askTwice=True)

    assert delivered["status"] == "allow", delivered
    assert delivered["secondStatus"] == "deny", delivered


def test_an_uncorrelated_denial_is_reported_to_gaia_for_the_audit_trail():
    """The denial the user could not distinguish from "never attempted"."""
    delivered = _drive(runBefore=False)

    audit = [
        event for event in delivered["bridgeEvents"]
        if event.get("event") == UNCORRELATED_AUDIT_EVENT
    ]
    assert len(audit) == 1, delivered
    assert audit[0]["sessionID"] == SESSION_ID
    assert audit[0]["callID"] == CALL_ID


def test_an_allowed_call_reports_no_denial(pytestconfig):
    delivered = _drive()

    assert not [
        event for event in delivered["bridgeEvents"]
        if event.get("event") == UNCORRELATED_AUDIT_EVENT
    ], delivered


@pytest.fixture()
def db_env(tmp_path, monkeypatch, bootstrapped_db_template):
    from tests.conftest import copy_bootstrapped_db

    db_path = tmp_path / "permission-audit.db"
    copy_bootstrapped_db(bootstrapped_db_template, db_path)
    monkeypatch.setenv("GAIA_DB", str(db_path))
    return db_path


def test_the_bridge_turns_that_report_into_a_queryable_harness_event(db_env):
    """The adapter's Python half writes the record, on Gaia's existing channel.

    harness_events is the append-only mirror `gaia query --surface harness_events`
    and `gaia defects` already read, and decision_audit is already the one shape
    for "a consent path produced no grant" -- so no new table, column or event
    vocabulary is introduced to make this denial observable.
    """
    sys.path.insert(0, str(REPO_ROOT / "opencode"))
    import bridge as opencode_bridge

    from gaia.approvals.decision_audit import (
        DECISION_NOT_ACTIVATED_EVENT,
        DETAILS_PAYLOAD_KEY,
    )
    from gaia.store.reader import cross_surface_query

    response = opencode_bridge.handle({
        "event": UNCORRELATED_AUDIT_EVENT,
        "sessionID": SESSION_ID,
        "callID": CALL_ID,
    })
    assert response["action"] == "allow", response

    rows = cross_surface_query(
        surface="harness_events", type=DECISION_NOT_ACTIVATED_EVENT, db_path=db_env
    )
    assert len(rows) == 1, rows
    assert rows[0]["raw"]["severity"] == "warning"
    payload = json.loads(rows[0]["raw"]["payload"])
    assert payload["lane"] == opencode_bridge.PERMISSION_ASK_LANE
    assert payload["session_id"] == SESSION_ID
    assert payload[DETAILS_PAYLOAD_KEY]["call_id"] == CALL_ID
