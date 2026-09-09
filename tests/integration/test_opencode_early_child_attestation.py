"""Real plugin/bridge/ledger/DB coverage with child Bash BEFORE Task completion.

Host events are simulated; only the shell.env identity observation is executed.
No publication command, host restart, or live substrate mutation is performed.
"""

import copy
import json
import hashlib
import re
import subprocess
import sqlite3
from pathlib import Path

import pytest

from tests.integration.test_opencode_consent_retry_e2e import (
    CALL_ID, DISPATCH_STEPS, DRIVER, ROOT_SESSION_ID, SESSION_ID,
)
from tests.conftest import IsolatedRuntimeEnv, copy_bootstrapped_db


@pytest.fixture
def isolated_env(tmp_path, monkeypatch, bootstrapped_db_template):
    """Keep both issuance and policy subprocesses on this test's substrate."""
    env = IsolatedRuntimeEnv(tmp_path / "state")
    env.prepare_hook_workspace()
    database = copy_bootstrapped_db(bootstrapped_db_template, Path(env["GAIA_DB"]))
    monkeypatch.setenv("GAIA_DB", env["GAIA_DB"])
    monkeypatch.setenv("GAIA_DATA_DIR", env["GAIA_DATA_DIR"])
    from gaia.paths import db_path
    from gaia.store import writer

    assert db_path().resolve() == database.resolve()
    with writer._connect() as con:
        assert Path(con.execute("PRAGMA database_list").fetchone()[2]).resolve() == database.resolve()
        assert con.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    return env


def _run(env, steps, **options):
    """Run a fresh plugin process and return only redacted boundary evidence."""
    scenario = {"steps": steps, "redactIdentityRecords": True, **options}
    result = subprocess.run(
        ["bun", str(DRIVER), json.dumps(scenario)], env=env,
        cwd=env["WORKSPACE"],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, "early-child driver failed"
    return json.loads(result.stdout.strip().splitlines()[-1])


def _dispatch():
    """Copy the shared fixture so negative scenarios cannot contaminate it."""
    return copy.deepcopy(DISPATCH_STEPS)


def _message(agent="gaia-system", session=SESSION_ID, label="child-message"):
    """Describe a host assistant identity event."""
    return {"kind": "message", "label": label, "sessionID": session, "agent": agent}


def _bash(label="child-bash", session=SESSION_ID):
    """Carry a read-only command as policy data, not an executed command."""
    return {"kind": "before", "label": label, "sessionID": session,
            "callID": CALL_ID, "tool": "bash", "command": "git status"}


def _shell():
    """Observe only the single delivered dispatch identity environment field."""
    return {"kind": "shell-env", "label": "identity", "sessionID": SESSION_ID, "callID": CALL_ID}


def _step(driven, label):
    """Read a uniquely named observation without dumping bridge credentials."""
    return next(step for step in driven["steps"] if step["label"] == label)


def _bash_diagnostic(driven, label="child-bash"):
    """Return only allowlisted denial categories and correlation-presence flags."""
    step = _step(driven, label)
    reasons = {
        "Shell environment transport requires attested call correlation": "attested_correlation_missing",
        "Gaia bridge did not confirm authenticated shell environment delivery": "shell_delivery_unconfirmed",
        "Gaia refused the authorized child start binding": "start_binding_refused",
        "Gaia could not attest the authorized child binding": "child_issuance_refused",
        "driver injected bridge failure": "injected_bridge_failure",
    }
    error = step.get("error", "")
    category = reasons.get(error, "unclassified" if error else "none")
    if error.startswith("opencode-adapter:child-session-binding-backstop:"):
        category = "child_session_unbound"
    exchanges = [item for item in driven["exchanges"]
                 if item["sent"].get("event") == "tool.execute.before"
                 and item["sent"].get("tool") == "bash"
                 and item["sent"].get("sessionID") == SESSION_ID]
    latest = exchanges[-1] if exchanges else {}
    sent = latest.get("sent", {})
    context = sent.get("roleContext") or {}
    response = latest.get("received") or {}
    return {
        "denial_category": category,
        "error_present": bool(error),
        "bash_request_present": bool(exchanges),
        "session_present": bool(sent.get("sessionID")),
        "call_present": bool(sent.get("callID")),
        "context_present": bool(context),
        "attestation_present": bool(context.get("attestationPresent")),
        "bridge_denied": response.get("action") == "deny",
        "shell_env_present": bool(response.get("shell_env")),
    }


def _binding_counts(env, call_id):
    """Read only this scratch database's dispatch and child binding cardinalities."""
    uri = Path(env["GAIA_DB"]).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        return {
            "dispatch_rows": con.execute(
                "SELECT COUNT(*) FROM agent_contract_handoffs WHERE dispatch_tool_use_id = ?",
                (call_id,),
            ).fetchone()[0],
            "bound_rows": con.execute(
                "SELECT COUNT(*) FROM agent_contract_handoffs WHERE harness_agent_id = ?",
                (SESSION_ID,),
            ).fetchone()[0],
        }


def _child_claims(driven, session=SESSION_ID):
    """Select actual issuance responses for this child."""
    return [exchange for exchange in driven["exchanges"]
            if exchange["sent"].get("event") == "identity.attest"
            and exchange["sent"].get("sessionID") == session]


def _assert_early_delivery(driven):
    """Require authenticated delivery and a single parent-backed issuance before completion."""
    assert _step(driven, "child-bash")["allowed"], _bash_diagnostic(driven)
    assert _step(driven, "child-bash")["commandAfter"] == "git status"
    assert _step(driven, "identity")["childIdentity"] == "gaia-system"
    claims = _child_claims(driven)
    assert len(claims) == 1
    assert claims[0]["sent"]["parentAttestationPresent"]
    assert claims[0]["received"]["attestationPresent"]
    assert claims[0]["received"]["granted_by"] == ROOT_SESSION_ID
    assert claims[0]["received"]["delegation_depth"] == 1
    events = driven["exchanges"]
    issuance = events.index(claims[0])
    bash = next(i for i, e in enumerate(events)
                if e["sent"].get("event") == "tool.execute.before" and e["sent"].get("tool") == "bash")
    completion = next(i for i, e in enumerate(events)
                      if e["sent"].get("event") == "tool.execute.after" and e["sent"].get("tool") == "task")
    assert issuance < bash < completion
    assert _step(driven, "dispatched")["allowed"]


def test_child_executes_before_parent_task_completes(isolated_env):
    root, before, binding, after = _dispatch()
    driven = _run(isolated_env, [root, before, binding, _message(), _bash(), _shell(), after])
    _assert_early_delivery(driven)


def test_old_root_session_is_reidentified_in_a_fresh_process(isolated_env):
    root, before, binding, after = _dispatch()
    previous = _run(isolated_env, [root])
    assert previous["exchanges"][0]["received"]["attestationPresent"]
    messages = {ROOT_SESSION_ID: [{"info": {
        "role": "assistant", "sessionID": ROOT_SESSION_ID, "agent": "gaia-orchestrator",
    }}]}
    driven = _run(isolated_env, [before, binding, _message(), _bash(), _shell(), after], messages=messages)
    _assert_early_delivery(driven)
    root_claim = driven["exchanges"][0]
    assert root_claim["sent"]["event"] == "identity.attest"
    assert root_claim["sent"]["sessionID"] == ROOT_SESSION_ID
    assert not root_claim["sent"].get("parentAttestationPresent")


def test_prebinding_child_denies_then_recovers_on_the_real_binding(isolated_env):
    root, before, binding, after = _dispatch()
    driven = _run(isolated_env, [root, before, _message(), _bash("too-early"),
                                 binding, _bash(), _shell(), after])
    assert not _step(driven, "too-early")["allowed"]
    assert _step(driven, "child-bash")["allowed"]
    assert _step(driven, "identity")["childIdentity"] == "gaia-system"
    assert len(_child_claims(driven)) == 1


@pytest.mark.parametrize("message_first", [True, False])
def test_concurrent_message_and_binding_do_not_absorb_issuance(isolated_env, message_first):
    root, before, binding, after = _dispatch()
    simultaneous = [_message(), binding] if message_first else [binding, _message()]
    driven = _run(isolated_env, [root, before, {"kind": "concurrent", "steps": simultaneous},
                                 _bash(), _shell(), after])
    _assert_early_delivery(driven)


def test_first_bash_waits_for_inflight_binding_and_issuance(isolated_env):
    root, before, binding, after = _dispatch()
    driven = _run(isolated_env, [root, before, _message(),
                                 {"kind": "concurrent", "steps": [binding, _bash()]},
                                 _shell(), after])
    _assert_early_delivery(driven)


@pytest.mark.parametrize("mutation", [
    "unsolicited", "wrong-parent", "unknown-call", "root-as-child", "role-mismatch", "role-alias-conflict",
])
def test_untrusted_binding_never_reaches_issuer_or_database_binding(isolated_env, mutation):
    root, before, binding, _after = _dispatch()
    steps = [root, before]
    if mutation == "unsolicited":
        steps = [root]
    elif mutation == "wrong-parent":
        binding["sessionID"] = "ses-wrong-parent"
    elif mutation == "unknown-call":
        binding["callID"] = "call-unknown"
    elif mutation == "root-as-child":
        binding["childSessionID"] = ROOT_SESSION_ID
    elif mutation == "role-mismatch":
        steps.append(_message("gaia-orchestrator"))
    elif mutation == "role-alias-conflict":
        before["args"]["agent"] = "gaia-orchestrator"
    driven = _run(isolated_env, steps + [binding, _bash(), _shell()])
    assert not _step(driven, "bound")["allowed"]
    assert not _step(driven, "child-bash")["allowed"]
    assert not _step(driven, "identity")["allowed"]
    assert not _child_claims(driven)
    assert not any(e["sent"].get("event") == "message.part.updated" for e in driven["exchanges"])


@pytest.mark.parametrize("conflict", ["different-child", "different-call", "relabel", "reused-before", "after-task-rebind"])
def test_bound_dispatch_cannot_be_reassigned_or_escalated(isolated_env, conflict):
    root, before, binding, after = _dispatch()
    steps = [root, before, binding]
    if conflict == "relabel":
        forged = _message("gaia-orchestrator", label="forged")
    elif conflict == "reused-before":
        forged = copy.deepcopy(before)
        forged["label"] = "forged"
        forged["args"]["subagent_type"] = "gaia-orchestrator"
    elif conflict == "after-task-rebind":
        forged = {**after, "label": "forged", "childSessionID": "ses-impostor"}
    else:
        forged = copy.deepcopy(binding)
        forged["label"] = "forged"
        if conflict == "different-child":
            forged["childSessionID"] = "ses-impostor"
        else:
            other = copy.deepcopy(before)
            other.update(callID="call-other", label="other-dispatch")
            steps.append(other)
            forged["callID"] = "call-other"
    driven = _run(isolated_env, steps + [forged, _bash(), _shell(), after])
    assert not _step(driven, "forged")["allowed"]
    assert not _child_claims(driven, "ses-impostor")
    _assert_early_delivery(driven)


def test_denied_task_cannot_authorize_a_later_child_binding(isolated_env):
    root, before, binding, _after = _dispatch()
    root["agent"] = "gaia-system"
    driven = _run(isolated_env, [root, before, binding, _bash(), _shell()])
    assert not _step(driven, "dispatch")["allowed"]
    assert not _step(driven, "bound")["allowed"]
    assert not _step(driven, "child-bash")["allowed"]
    assert not _step(driven, "identity")["allowed"]
    assert not _child_claims(driven)


def test_after_task_fallback_is_idempotent(isolated_env):
    root, before, _binding, after = _dispatch()
    repeated = {**after, "label": "duplicate-after"}
    driven = _run(isolated_env, [root, before, after, repeated])
    assert _step(driven, "dispatched")["allowed"]
    assert _step(driven, "duplicate-after")["allowed"]
    assert len(_child_claims(driven)) == 1


def _observation(driven, label):
    """Read a barrier snapshot containing only identifiers and counters."""
    return next(item for item in driven["observations"] if item["label"] == label)


@pytest.mark.parametrize("outcome", ["allow", "deny", "throw"])
def test_start_readiness_blocks_messages_and_bash_and_rolls_back_failure(isolated_env, outcome):
    root, before, binding, after = _dispatch()
    other_before = {**copy.deepcopy(before), "callID": "call-other", "label": "other-dispatch"}
    wrong_child = {**binding, "childSessionID": "ses-conflict", "label": "wrong-child"}
    wrong_call = {**binding, "callID": "call-other", "label": "wrong-call"}
    held = {
        "kind": "held-start", "label": "start-held", "outcome": outcome, "first": binding,
        "during": [_message(label="waiting-message"), _bash("waiting-bash"), wrong_child, wrong_call],
    }
    steps = [root, before, other_before, held]
    if outcome != "allow":
        steps += [
            _message(label="message-after-failure"), _bash("bash-after-failure"),
            {"kind": "observe", "label": "after-failure"},
            # Retry the failed start, not Task-before: that already birthed its contract.
            {**binding, "label": "binding-retry"},
        ]
    driven = _run(isolated_env, steps + [_message(), _bash(), _shell(), after])
    snapshot = _observation(driven, "start-held")
    assert SESSION_ID not in snapshot["identityAttempts"]
    assert set(snapshot["settledWhileHeld"]) == {"wrong-child", "wrong-call"}
    assert not _step(driven, "wrong-child")["allowed"]
    assert not _step(driven, "wrong-call")["allowed"]
    for label in ("bound", "waiting-message", "waiting-bash"):
        assert _step(driven, label)["allowed"] is (outcome == "allow")
    if outcome != "allow":
        assert _step(driven, "message-after-failure")["allowed"]
        assert not _step(driven, "bash-after-failure")["allowed"]
        assert SESSION_ID not in _observation(driven, "after-failure")["identityAttempts"]
        assert _step(driven, "binding-retry")["allowed"]
    counts = _binding_counts(isolated_env, binding["callID"])
    assert counts == {"dispatch_rows": 1, "bound_rows": 1}, {
        **counts, **_bash_diagnostic(driven),
    }
    assert _step(driven, "child-bash")["allowed"], _bash_diagnostic(driven)
    assert _step(driven, "identity")["childIdentity"] == "gaia-system"
    assert len(_child_claims(driven)) == 1
    assert not _child_claims(driven, "ses-conflict")


@pytest.mark.parametrize("outcome", ["allow", "deny", "throw"])
def test_distinct_children_serialize_issuance_and_failed_issuer_does_not_poison_queue(isolated_env, outcome):
    root, before, binding, after = _dispatch()
    second_session = "ses-second-child"
    second_before = {**copy.deepcopy(before), "callID": "call-second-dispatch", "label": "second-dispatch"}
    second_binding = {**binding, "callID": second_before["callID"],
                      "childSessionID": second_session, "label": "second-bound"}
    held = {
        "kind": "held-issuance", "label": "issuer-held", "outcome": outcome,
        "first": binding, "second": second_binding,
    }
    steps = [root, before, second_before, held]
    if outcome != "allow":
        steps.append({**binding, "label": "issuance-retry"})
    second_bash = {**_bash("second-bash", second_session), "callID": "call-second-bash"}
    second_shell = {**_shell(), "sessionID": second_session, "callID": "call-second-bash", "label": "second-identity"}
    second_after = {**after, "callID": second_before["callID"],
                    "childSessionID": second_session, "label": "second-after"}
    driven = _run(isolated_env, steps + [_bash(), _shell(), second_bash, second_shell, after, second_after])
    snapshot = _observation(driven, "issuer-held")
    assert snapshot["identityAttempts"] == [ROOT_SESSION_ID, SESSION_ID]
    assert snapshot["maxActiveIssuers"] == driven["maxActiveIssuers"] == 1
    assert _step(driven, "bound")["allowed"] is (outcome == "allow")
    assert _step(driven, "second-bound")["allowed"]
    if outcome != "allow":
        assert _step(driven, "issuance-retry")["allowed"]
    # Both Bash checks run AFTER both issuances; each must resolve its stored nonce
    # through the real adapter in the same host-run namespace, not a test resolver.
    for label in ("child-bash", "second-bash"):
        assert _step(driven, label)["allowed"]
    for label in ("identity", "second-identity"):
        assert _step(driven, label)["childIdentity"] == "gaia-system"
    for session in (SESSION_ID, second_session):
        claims = _child_claims(driven, session)
        assert len(claims) == 1
        assert claims[0]["received"]["attestationPresent"]
        assert claims[0]["received"]["granted_by"] == ROOT_SESSION_ID
        assert claims[0]["received"]["delegation_depth"] == 1


def test_runtime_fixture_excludes_unrelated_environment_and_redacts_repr(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_TEST_UNRELATED_CREDENTIAL", "synthetic-test-value")
    env = IsolatedRuntimeEnv(tmp_path / "privacy")
    assert set(env) == {
        "PATH", "HOME", "TMPDIR", "GAIA_DATA_DIR", "GAIA_DB",
        "GAIA_OPENCODE_ATTESTATION_DIR", "WORKSPACE", "PYTHONDONTWRITEBYTECODE",
    }
    assert repr(env) == "<IsolatedRuntimeEnv: values redacted>"
    assert Path(env["GAIA_DB"]) == Path(env["GAIA_DATA_DIR"]) / "gaia.db"
    copied = env.copy()
    copied["GAIA_TEST_EXPLICIT"] = "synthetic-explicit-value"
    assert "GAIA_TEST_EXPLICIT" not in env
    assert repr({"env": [env, copied]}) == (
        "{'env': [<IsolatedRuntimeEnv: values redacted>, "
        "<IsolatedRuntimeEnv: values redacted>]}"
    )
    child = subprocess.run(
        ["bun", "-e", "console.log(JSON.stringify({"
         "absent: !('GAIA_TEST_UNRELATED_CREDENTIAL' in process.env),"
         "explicit: process.env.GAIA_TEST_EXPLICIT === 'synthetic-explicit-value'"
         "}))"],
        env=copied, capture_output=True, text=True, check=True, timeout=30,
    )
    assert json.loads(child.stdout) == {"absent": True, "explicit": True}


def test_reexecuting_an_allowed_task_is_not_a_start_event_retry(isolated_env):
    """Pin the duplicate-birth failure separately from legitimate start recovery."""
    root, before, binding, _after = _dispatch()
    repeated_task = {**copy.deepcopy(before), "label": "repeated-task"}
    driven = _run(isolated_env, [root, before, repeated_task, binding, _message(), _bash()])
    assert _step(driven, "repeated-task")["allowed"]
    counts = _binding_counts(isolated_env, binding["callID"])
    assert counts == {"dispatch_rows": 2, "bound_rows": 0}, counts
    assert not _step(driven, "child-bash")["allowed"], _bash_diagnostic(driven)
    assert _bash_diagnostic(driven)["denial_category"] == "child_session_unbound"


@pytest.mark.parametrize("mutation", [
    None, "active", "unsolicited", "wrong-parent", "relabel", "wrong-child", "stale-call",
])
def test_sequential_resume_requires_completed_exact_authorization(isolated_env, mutation):
    """Bind a new Task call to the explicitly requested existing host session only."""
    root, before, binding, after = _dispatch()
    resumed = copy.deepcopy(before)
    resumed.update(callID="call-resume", label="resume")
    resumed["args"]["task_id"] = SESSION_ID
    rebound = {**binding, "callID": "call-resume", "label": "rebound"}
    steps = [root, before, binding, _bash("old-shell-record")]
    if mutation != "active":
        steps.append(after)
    if mutation == "unsolicited":
        del resumed["args"]["task_id"]
    elif mutation == "wrong-parent":
        resumed["sessionID"] = rebound["sessionID"] = "ses-wrong-parent"
    elif mutation == "relabel":
        resumed["args"]["subagent_type"] = "gaia-orchestrator"
    elif mutation == "wrong-child":
        resumed["args"]["task_id"] = "ses-other"
    elif mutation == "stale-call":
        resumed["callID"] = rebound["callID"] = before["callID"]
    driven = _run(isolated_env, steps + [resumed, rebound,
        {**_shell(), "label": "old-delivery"}, _bash(), _shell()])
    if mutation is not None:
        # Replaying the original binding may be idempotent, but not its Task-before.
        label = "resume" if mutation == "stale-call" else "rebound"
        assert not _step(driven, label)["allowed"]
        return
    assert _step(driven, "resume")["allowed"]
    assert _step(driven, "rebound")["allowed"]
    assert not _step(driven, "old-delivery")["allowed"]
    assert _step(driven, "child-bash")["allowed"], _bash_diagnostic(driven)
    assert _step(driven, "child-bash")["commandAfter"] == "git status"
    assert _step(driven, "identity")["childIdentity"] == "gaia-system"
    assert len(_child_claims(driven)) == 1
    assert _binding_counts(isolated_env, "call-resume") == {"dispatch_rows": 1, "bound_rows": 2}


def _resume(before, binding, after, call):
    """Build a distinct explicitly authorized continuation of the same child."""
    request = copy.deepcopy(before)
    request.update(callID=call, label=call)
    request["args"]["task_id"] = SESSION_ID
    return request, {**binding, "callID": call, "label": call + "-bound"}, {
        **after, "callID": call, "label": call + "-after",
    }


@pytest.mark.parametrize("variant", ["baseline", "candidate"])
def test_three_call_compaction_plugin_comparison(isolated_env, tmp_path, variant):
    """Observe identical positive host events against exact baseline and current plugins."""
    options = {}
    repo = DRIVER.parents[2]
    if variant == "baseline":
        blob = subprocess.run(
            ["git", "-C", str(repo), "show",
             "c407aadd643051d320a3fc90ffbcbe3fdf75cb5a:opencode/plugin.ts"],
            capture_output=True, check=True,
        ).stdout
        imports = re.findall(r'from\s+["\']([^"\']+)["\']', blob.decode())
        assert imports and all(name.startswith("node:") for name in imports)
        module = tmp_path / "baseline-plugin.ts"
        module.write_bytes(blob)
        assert module.read_bytes() == blob
        options = {"pluginModulePath": str(module), "legacyBridge": True}
    else:
        blob = (repo / "opencode" / "plugin.ts").read_bytes()
    root, before, binding, after = _dispatch()
    second, bound2, after2 = _resume(before, binding, after, "call-second")
    third, bound3, _ = _resume(before, binding, after, "call-third")
    driven = _run(isolated_env, [root, before, binding, _message(),
        _bash("first-tool"), after, second, bound2, _bash("second-tool"),
        after2, third, bound3, _bash("third-tool"),
        {"kind": "compact", "sessionID": SESSION_ID, "label": "context"}], **options)
    context = "\n".join(_step(driven, "context")["context"])
    uri = Path(isolated_env["GAIA_DB"]).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        rows = con.execute(
            "SELECT dispatch_tool_use_id, contract_id FROM agent_contract_handoffs "
            "WHERE harness_agent_id = ?", (SESSION_ID,),
        ).fetchall()
    boundaries = []
    for exchange in driven["exchanges"]:
        sent, received = exchange["sent"], exchange["received"]
        if sent.get("event") in ("tool.execute.before", "session.compacting"):
            boundaries.append({
                "event": sent["event"], "tool": sent.get("tool"),
                "call": sent.get("callID"), "handle": sent.get("agentID"),
                "role_context_present": bool(sent.get("roleContext")),
                "action": received.get("action"),
                "context_items": len((received.get("updated_input") or {}).get("context", [])),
            })
    summary = {
        "variant": variant, "plugin_sha256": hashlib.sha256(blob).hexdigest(),
        "steps": {s["label"]: s.get("allowed") for s in driven["steps"]},
        "bound_calls": sorted(call for call, _ in rows),
        "context_contains_calls": sorted(call for call, contract in rows if contract in context),
        "context_items": len(_step(driven, "context")["context"]),
        "child_claims": len(_child_claims(driven)), "boundaries": boundaries,
    }
    print("COMPACTION_COMPARISON " + json.dumps(summary, sort_keys=True))
    assert all(_step(driven, label)["allowed"] for label in
               ("root-turn", "dispatch", "bound", "dispatched", "call-second",
                "call-second-bound", "call-second-after", "call-third", "call-third-bound"))
    assert len(rows) == 3
    assert len(boundaries) == 7


@pytest.fixture
def three_call_transport(isolated_env):
    """Enforce transport independently of the known compaction-context debt."""
    root, before, binding, after = _dispatch()
    second, bound2, after2 = _resume(before, binding, after, "call-second")
    third, bound3, _after3 = _resume(before, binding, after, "call-third")
    driven = _run(isolated_env, [root, before, binding, _bash("first-tool"), after,
        second, bound2, _bash("second-tool"), after2, third, bound3,
        {**binding, "label": "stale-first"}, {**bound2, "label": "stale-second"},
        _bash(), _shell(), {"kind": "compact", "sessionID": SESSION_ID, "label": "context"}])
    for label in ("dispatch", "bound", "dispatched", "call-second", "call-second-after",
                  "call-third", "call-second-bound", "call-third-bound",
                  "first-tool", "second-tool", "child-bash"):
        assert _step(driven, label)["allowed"]
    for label in ("stale-first", "stale-second"):
        assert not _step(driven, label)["allowed"]
    tools = [e["sent"] for e in driven["exchanges"]
             if e["sent"].get("event") == "tool.execute.before" and e["sent"].get("tool") == "bash"]
    assert [e["agentID"] for e in tools] == [before["callID"], "call-second", "call-third"]
    assert all(e["roleContext"] == tools[0]["roleContext"] for e in tools)
    assert len(_child_claims(driven)) == 1
    assert _step(driven, "identity")["childIdentity"] == "gaia-system"
    uri = Path(isolated_env["GAIA_DB"]).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        rows = con.execute("SELECT contract_id FROM agent_contract_handoffs WHERE harness_agent_id = ? AND dispatch_tool_use_id = ?",
                           (SESSION_ID, "call-third")).fetchall()
    assert len(rows) == 1
    assert _step(driven, "context")["allowed"]
    context = _step(driven, "context")["context"]
    compact = next(e["sent"] for e in driven["exchanges"] if e["sent"].get("event") == "session.compacting")
    assert compact["agentID"] == "call-third"
    assert compact["roleContext"] == tools[0]["roleContext"]
    return context, rows[0][0]


def test_three_call_resume_preserves_identity_and_latest_handle(three_call_transport):
    """Transport setup must pass even while compaction remains unresolved."""
    assert three_call_transport is not None


class EmptyCompactionContext(AssertionError):
    """Identify only the established empty-context regression."""


@pytest.mark.xfail(
    strict=True,
    raises=EmptyCompactionContext,
    reason="feedback_opencode_compaction_contexto_ambiguo_tras_resume",
)
def test_three_call_compaction_contains_current_contract(three_call_transport):
    """Graduate the known debt when compaction restores the current contract."""
    context, contract_id = three_call_transport
    if context == []:
        raise EmptyCompactionContext("compaction returned no context items")
    assert contract_id in "\n".join(context)


def test_resume_concurrent_reservations_allow_only_one_child_owner(isolated_env):
    """A held real start makes competing resume reservations deterministic."""
    root, before, binding, after = _dispatch()
    second, bound2, _ = _resume(before, binding, after, "call-second")
    third, bound3, _ = _resume(before, binding, after, "call-third")
    driven = _run(isolated_env, [root, before, binding, after, second, third,
        {"kind": "held-start", "label": "resume-held", "outcome": "allow",
         "first": bound2, "during": [bound3]}, _bash(), _shell()])
    assert _observation(driven, "resume-held")["settledWhileHeld"] == ["call-third-bound"]
    assert _step(driven, "call-second-bound")["allowed"]
    assert not _step(driven, "call-third-bound")["allowed"]
    assert _binding_counts(isolated_env, "call-third") == {"dispatch_rows": 1, "bound_rows": 2}
    assert _step(driven, "child-bash")["allowed"]
    assert len(_child_claims(driven)) == 1


@pytest.mark.parametrize("task_id", [None, "ses-unrelated"])
def test_resume_new_task_cannot_appropriate_previous_child(isolated_env, task_id):
    """Fresh or mismatched Task authorization never confers an old child binding."""
    root, before, binding, after = _dispatch()
    request, rebound, _ = _resume(before, binding, after, "call-new")
    if task_id is None:
        del request["args"]["task_id"]
    else:
        request["args"]["task_id"] = task_id
    driven = _run(isolated_env, [root, before, binding, after, request, rebound])
    assert _step(driven, "call-new")["allowed"]
    assert not _step(driven, "call-new-bound")["allowed"]
    assert _binding_counts(isolated_env, "call-new") == {"dispatch_rows": 1, "bound_rows": 1}
    assert len(_child_claims(driven)) == 1


def test_resume_background_return_does_not_complete_previous_dispatch(isolated_env):
    """A running background return cannot authorize sequential resume."""
    root, before, binding, after = _dispatch()
    request, rebound, _ = _resume(before, binding, after, "call-resume")
    background = {"kind": "after", "label": "background-return", "tool": "task",
                  "sessionID": ROOT_SESSION_ID, "callID": before["callID"],
                  "metadata": {"sessionId": SESSION_ID, "background": True, "jobId": SESSION_ID},
                  "output": f'<task id="{SESSION_ID}" state="running"></task>'}
    driven = _run(isolated_env, [root, before, binding, background, request, rebound,
        after, {**rebound, "label": "after-foreground"}, _bash()])
    assert _step(driven, "background-return")["allowed"]
    assert not _step(driven, "call-resume-bound")["allowed"]
    assert _step(driven, "after-foreground")["allowed"]
    assert _step(driven, "child-bash")["allowed"]
