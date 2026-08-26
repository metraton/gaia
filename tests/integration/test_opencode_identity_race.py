"""The identity race at ``tool.execute.before``, driven through the real plugin.

OpenCode 1.18.23 triggers ``tool.execute.before`` with exactly
``{tool, sessionID, callID}``: the name that identifies a session travels the
event bus instead, so a dispatch can reach this edge while ``message.updated``
is still undelivered and present no identity at all. These cases run the real
plugin closure under bun with no ``message.updated`` step, and answer
``identity.attest`` from the real Gaia-side bridge.

The ledger is scratch and namespaced by the bun process's own ``host_run_id``,
which the bridge derives from its parent rather than from anything a caller
sends -- so the token minted here reaches no other run.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import sys

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "hooks") not in sys.path:
    sys.path.insert(0, str(_ROOT / "hooks"))

from adapters.opencode import OpenCodeAdapter  # noqa: E402
from modules.orchestrator.delegate_mode import (  # noqa: E402
    SessionRole,
    check_delegate_mode,
    classify_session_role,
)

DRIVER = _ROOT / "tests" / "opencode" / "race_identity_driver.ts"
SESSION = "ses-race"
CALL = "call-race"


@pytest.fixture
def env(tmp_path, bootstrapped_db_template):
    db_path = tmp_path / "gaia.db"
    shutil.copy(bootstrapped_db_template, db_path)
    environment = os.environ.copy()
    environment["GAIA_DB"] = str(db_path)
    environment["GAIA_OPENCODE_ATTESTATION_DIR"] = str(tmp_path / "ledger")
    return environment


def _drive(env, scenario):
    result = subprocess.run(
        ["bun", str(DRIVER), json.dumps(scenario)],
        env=env, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _assistant(agent):
    return {"info": {"role": "assistant", "sessionID": SESSION, "agent": agent}}


def _task_step(subagent="developer"):
    return {
        "kind": "before",
        "sessionID": SESSION,
        "callID": CALL,
        "tool": "task",
        "args": {"subagent_type": subagent, "prompt": "do the thing"},
    }


def _before_requests(driven):
    return [r for r in driven["requests"] if r["event"] == "tool.execute.before"]


def test_a_child_session_carries_a_dispatch_handle_while_it_is_still_running(env):
    """The dispatch window: the child's own tool calls precede the task's after.

    ``dispatchBySession`` is written only when the parent's
    ``tool.execute.after`` reports the child it produced, which is after that
    child has finished. Reading it directly left ``agentID`` absent for every
    tool call the subagent made, Gaia's ``classify_session_role`` read the
    absent ``agent_id`` as a main thread, and delegate mode denied the
    specialist its Bash with "NOT RUNNABLE STANDALONE".
    """
    child = "ses-race-child"
    driven = _drive(env, {
        "messages": {SESSION: [], child: []},
        "steps": [
            {"kind": "message", "sessionID": SESSION, "agent": "gaia-orchestrator"},
            _task_step("gaia-system"),
            {"kind": "message", "sessionID": child, "agent": "gaia-system"},
            {
                "kind": "before",
                "sessionID": child,
                "callID": "call-child-bash",
                "tool": "bash",
                "args": {"command": "gaia paths"},
            },
        ],
    })

    primary, dispatched = _before_requests(driven)
    # The primary must present no handle, or a truthy agent_id would classify
    # the control plane as a subagent before its attested context is consulted.
    assert "agentID" not in primary
    assert primary["roleContext"]["role"] == "gaia-orchestrator"
    assert dispatched["agent"] == "gaia-system"
    assert dispatched["agentID"] == child

    payload = OpenCodeAdapter().build_policy_payload(
        OpenCodeAdapter().parse_event(json.dumps(dict(dispatched, event="tool.execute.before")))
    )
    assert payload["agent_id"] == child
    assert classify_session_role(payload) is SessionRole.SUBAGENT
    assert check_delegate_mode("Bash", payload).blocked is False


def test_task_call_before_any_message_event_still_resolves_an_identity(env):
    """The first window: no message.updated has run, and the dispatch is named."""
    driven = _drive(env, {
        "messages": {SESSION: [_assistant("gaia-orchestrator")]},
        "steps": [_task_step()],
    })

    assert driven["messageReads"] == [SESSION]
    sent = _before_requests(driven)
    assert len(sent) == 1
    assert sent[0]["agent"] == "gaia-orchestrator"


def test_that_recovered_identity_carries_an_attested_context(env):
    """The second window: naming the session is not enough, the claim must exist.

    Resolving the name and leaving the claim for a later turn would reproduce the
    other denial verbatim, so the same edge must have issued before it composes.
    """
    driven = _drive(env, {
        "messages": {SESSION: [_assistant("gaia-orchestrator")]},
        "steps": [_task_step()],
    })

    attested = [r for r in driven["requests"] if r["event"] == "identity.attest"]
    assert len(attested) == 1
    assert attested[0]["sessionID"] == SESSION
    assert attested[0]["role"] == "gaia-orchestrator"
    # Parentless, so depth 0: JSON.stringify drops the undefined grantor.
    assert "parentAttestation" not in attested[0]

    context = _before_requests(driven)[0]["roleContext"]
    assert context["role"] == "gaia-orchestrator"
    assert context["issuer"] == "opencode-runtime"
    assert context["verified"] is True
    assert isinstance(context["attestation"], str) and context["attestation"]


def test_identity_is_issued_once_when_both_edges_reach_the_session(env):
    """A message event and a dispatch on one session mint one claim, not two."""
    driven = _drive(env, {
        "messages": {SESSION: [_assistant("gaia-orchestrator")]},
        "steps": [
            {"kind": "message", "sessionID": SESSION, "agent": "gaia-orchestrator"},
            _task_step(),
        ],
    })

    attested = [r for r in driven["requests"] if r["event"] == "identity.attest"]
    assert len(attested) == 1
    # The cached name makes the host read unnecessary, so it is not performed.
    assert driven["messageReads"] == []


def test_a_host_that_names_nobody_leaves_the_dispatch_unidentified(env):
    """The fallback reads the host; it never composes a name of its own.

    With no assistant message to read, the edge must present nothing rather than
    invent an identity -- the denial is the correct outcome, not a regression.
    """
    driven = _drive(env, {
        "messages": {SESSION: []},
        "steps": [_task_step()],
    })

    sent = _before_requests(driven)
    assert len(sent) == 1
    # The V1 shape verbatim: JSON.stringify drops both undefined values, so the
    # bridge receives a dispatch with no identity keys at all and refuses it.
    assert "agent" not in sent[0]
    assert "roleContext" not in sent[0]
    assert not [r for r in driven["requests"] if r["event"] == "identity.attest"]


def test_a_client_without_the_session_api_does_not_break_the_edge(env):
    """An absent host read degrades to the previous behaviour, never to a throw."""
    driven = _drive(env, {
        "clientHasSessionApi": False,
        "steps": [_task_step()],
    })

    assert "denial" not in driven
    assert "agent" not in _before_requests(driven)[0]


def _attest_requests(driven):
    return [r for r in driven["requests"] if r["event"] == "identity.attest"]


def test_the_dispatch_placeholder_is_not_read_back_as_the_dispatching_agent(env):
    """handleSubtask writes the CALLEE's placeholder into the CALLER's transcript.

    OpenCode 1.18.23 awaits that updateMessage before it triggers this edge, so
    the newest assistant name the host can return is the agent being dispatched.
    Reading it would attest the callee's name against the caller's session, and
    the ledger makes the first claim durable for the whole host run.
    """
    driven = _drive(env, {
        "messages": {
            SESSION: [_assistant("gaia-orchestrator"), _assistant("gaia-planner")],
        },
        "steps": [_task_step("gaia-planner")],
    })

    attested = _attest_requests(driven)
    assert [r["role"] for r in attested] == ["gaia-orchestrator"]
    assert [r["sessionID"] for r in attested] == [SESSION]

    sent = _before_requests(driven)
    assert len(sent) == 1
    assert sent[0]["agent"] == "gaia-orchestrator"
    assert sent[0]["roleContext"]["role"] == "gaia-orchestrator"


def test_a_self_dispatch_leaves_the_caller_unidentified_rather_than_mislabelled(env):
    """Skipping the placeholder cannot tell a real self-name from a placeholder.

    Every readable name equals the dispatched one, so the scan runs out and the
    edge presents nothing -- a denial the next message.updated repairs, where
    reading one of them would mint a claim only a host restart clears.
    """
    driven = _drive(env, {
        "messages": {
            SESSION: [_assistant("gaia-orchestrator"), _assistant("gaia-orchestrator")],
        },
        "steps": [_task_step("gaia-orchestrator")],
    })

    assert _attest_requests(driven) == []
    sent = _before_requests(driven)
    assert len(sent) == 1
    # JSON.stringify drops both undefined values, so the keys are absent rather
    # than null: the bridge receives a dispatch with no identity and refuses it.
    assert "agent" not in sent[0]
    assert "roleContext" not in sent[0]


def test_a_relabelled_session_presents_the_new_name_under_the_first_claim(env):
    """Current behaviour, pinned as observed -- the guard for it is not this change.

    Two message.updated events rename one session. attest is issued once, for
    the first name; the later name replaces the cached one and is what the edge
    presents, carrying the token minted for its predecessor.
    """
    driven = _drive(env, {
        "messages": {SESSION: []},
        "steps": [
            {"kind": "message", "sessionID": SESSION, "agent": "gaia-orchestrator"},
            {"kind": "message", "sessionID": SESSION, "agent": "gaia-planner"},
            _task_step(),
        ],
    })

    attested = _attest_requests(driven)
    assert [r["role"] for r in attested] == ["gaia-orchestrator"]

    sent = _before_requests(driven)
    assert len(sent) == 1
    # The cached name answers identify(), so the placeholder skip never runs.
    assert driven["messageReads"] == []
    assert sent[0]["agent"] == "gaia-planner"
    assert sent[0]["roleContext"]["role"] == "gaia-planner"
    assert isinstance(sent[0]["roleContext"]["attestation"], str)
