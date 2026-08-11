#!/usr/bin/env python3
"""A degraded gate never hands over what it had already decided to withhold.

The gate is allowed to fail open so its own crash cannot brick a session. That
policy has one boundary: it applies to commands whose verdict was never
reached. When the gate had ALREADY classified a command as state-mutating and
then failed, letting it through is not losing a verdict -- the verdict existed,
and it was going to demand consent. Degrading it into a permission grants
exactly what the gate was holding back.

Two contrasting cases over the same degradation mechanism:

1. An already-classified mutating command hitting the failure is BLOCKED with a
   reason (the historical shape here is a push to a remote, which is the one
   that actually reached remote state in the recorded degradations).
2. A command with no such classification hitting the same failure still PASSES,
   which is what keeps the bounded exception from bricking a session.

Both assert the failure was recorded, because a decision the audit sink cannot
show is not reviewable either way.
"""

import glob
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
HOOKS_DIR = REPO_ROOT / "hooks"
ENTRY_POINT = HOOKS_DIR / "pre_tool_use.py"

if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from modules.audit.logger import AuditLogger
from modules.tools.bash_validator import validate_bash_command

FAIL_OPEN_EVENT = "hook_fail_open"
FAIL_OPEN_MARKER = "[SECURITY GATE DEGRADED]"

# A push to a remote: the shape of the recorded degradations that actually
# reached remote state. The plain form is deliberate -- `git push --force` is
# permanently deny-listed and is stopped before this branch, so it could not
# demonstrate anything about the degraded path.
MUTATING_COMMAND = "git push origin main"
DENY_LISTED_COMMAND = "git push --force origin main"
NON_MUTATING_COMMAND = "echo hello"


def _audit_records(data_dir):
    records = []
    for path in glob.glob(str(Path(data_dir) / "logs" / "audit-*.jsonl")):
        with open(path) as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


@pytest.fixture
def audit_sink(tmp_path, monkeypatch):
    """Redirect the always-on audit sink to a temp dir for the whole process."""
    import modules.audit.logger as audit_logger

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(audit_logger, "_audit_logger", AuditLogger(log_dir=logs_dir))
    return tmp_path


@pytest.fixture
def persistence_failure(monkeypatch):
    """Make approval persistence fail the way the recorded degradations did."""
    import gaia.approvals.store as store
    import modules.tools.bash_validator as validator

    def _raise(*args, **kwargs):
        raise RuntimeError("injected approval-store failure")

    # Keep the probe for an existing pending off the real database, so this
    # exercises the persist-failure branch and nothing else.
    monkeypatch.setattr(validator, "_find_pending_in_db", lambda *a, **k: None)
    monkeypatch.setattr(store, "insert_requested", _raise)


@pytest.fixture(autouse=True)
def clean_breadcrumb():
    """The classification breadcrumb is process-local; do not leak it between tests."""
    from modules.security import fail_open

    fail_open.clear_classification()
    yield
    fail_open.clear_classification()


def _decision(result):
    response = result.block_response or {}
    return response.get("hookSpecificOutput", {}).get("permissionDecision")


def test_degraded_allow_does_not_permit_an_already_mutating_command(
    audit_sink, persistence_failure
):
    """A push that was already classified T3 is denied, not allowed, on failure."""
    result = validate_bash_command(
        MUTATING_COMMAND,
        is_subagent=True,
        session_id="degraded-allow-bounded",
    )

    decision = _decision(result)
    assert decision != "allow", (
        "the degraded path turned an already-classified mutating command into a "
        f"permission -- that is the grant the gate existed to withhold "
        f"(decision={decision!r}, reason={result.reason!r})"
    )
    assert decision == "deny", f"expected an explicit deny, got {decision!r}"
    assert result.allowed is False


def test_degraded_allow_denial_carries_a_reason(audit_sink, persistence_failure):
    """The denial explains itself instead of failing opaquely."""
    result = validate_bash_command(
        MUTATING_COMMAND,
        is_subagent=True,
        session_id="degraded-allow-bounded",
    )
    assert _decision(result) == "deny"
    reason = (result.block_response or {}).get("hookSpecificOutput", {}).get(
        "permissionDecisionReason", ""
    )
    assert reason.strip(), "the denial carries no reason text"
    assert "persist" in reason.lower() or "degrad" in reason.lower(), (
        f"the denial does not name the degradation that caused it: {reason!r}"
    )


def test_degraded_allow_records_the_event(audit_sink, persistence_failure):
    """The degradation is recorded whichever way it resolves."""
    validate_bash_command(
        MUTATING_COMMAND,
        is_subagent=True,
        session_id="degraded-allow-bounded",
    )
    records = _audit_records(audit_sink)
    events = [r for r in records if r.get("event", "").startswith("t3_degraded_")]
    assert events, f"no t3_degraded_* event was recorded; records={records!r}"
    assert any(e.get("reason") for e in events), f"event carries no reason: {events!r}"


def test_degraded_allow_breadcrumb_survives_the_classification(audit_sink):
    """Classifying a command as T3 leaves the breadcrumb the fail-open path reads.

    Without this the bounded exception is unreachable: the fail-open path would
    have nothing to distinguish an already-gated command from an unclassified
    one, and would pass both.
    """
    from modules.security import fail_open

    assert fail_open.known_mutative_classification() is None

    validate_bash_command(
        MUTATING_COMMAND,
        is_subagent=False,
        session_id="degraded-allow-bounded",
    )

    known = fail_open.known_mutative_classification()
    assert known is not None, (
        "a T3 classification left no breadcrumb, so a later gate failure cannot "
        "tell that this command was already going to require consent"
    )
    assert known.verb == "push"
    assert known.command == MUTATING_COMMAND


def test_degraded_allow_leaves_the_deny_list_permanent(audit_sink, persistence_failure):
    """A deny-listed command is stopped before the degraded path, as before."""
    result = validate_bash_command(
        DENY_LISTED_COMMAND,
        is_subagent=True,
        session_id="degraded-allow-bounded",
    )
    assert result.allowed is False
    assert _decision(result) != "allow"


def test_degraded_allow_still_passes_a_non_mutating_command(tmp_path):
    """The same failure on an unclassified command still lets it through.

    This is what bounds the exception: it reaches only commands that were
    already going to stop and ask, so a crashing gate cannot brick a session.
    """
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_DATA"] = str(tmp_path)
    env["GAIA_HOST"] = "no-such-host-adapter"
    env.pop("GAIA_DEBUG", None)

    proc = subprocess.run(
        [sys.executable, str(ENTRY_POINT)],
        input=json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "degraded-allow-bounded",
                "tool_name": "Bash",
                "tool_input": {"command": NON_MUTATING_COMMAND},
            }
        ),
        text=True,
        capture_output=True,
        env=env,
    )

    assert proc.returncode != 2, (
        "a non-mutating command must still pass a degraded gate -- blocking it "
        f"would brick the session (stdout={proc.stdout!r}, stderr={proc.stderr!r})"
    )
    assert FAIL_OPEN_MARKER in proc.stdout + proc.stderr
    events = [
        r for r in _audit_records(tmp_path) if r.get("event") == FAIL_OPEN_EVENT
    ]
    assert events, "the pass-through was not recorded"


def test_degraded_allow_blocks_when_the_breadcrumb_says_mutating(audit_sink):
    """A gate failure AFTER classification blocks instead of passing.

    The entry path can crash between classifying a command and delivering the
    denial. Re-classifying at that point is not an option when the classifier is
    what failed, so the decision reads what the gate had already established.
    """
    from modules.security import fail_open

    fail_open.note_mutative_classification(MUTATING_COMMAND, "push", "MUTATIVE")
    outcome = fail_open.decide_fail_open(
        "unhandled_exception", "RuntimeError: injected"
    )

    assert outcome.blocked is True
    assert outcome.exit_code == 2, (
        "only exit 2 blocks; any other code lets the operation run"
    )
    assert FAIL_OPEN_MARKER in outcome.message

    events = [
        r for r in _audit_records(audit_sink) if r.get("event") == FAIL_OPEN_EVENT
    ]
    assert events, "the blocked degradation was not recorded"
    assert any(
        e.get("context", {}).get("outcome") == "blocked" for e in events
    ), f"the event does not record that it blocked: {events!r}"
