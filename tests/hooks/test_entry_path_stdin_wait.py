#!/usr/bin/env python3
"""The gate that never runs is the one failure no verdict can describe.

The entry point decided whether it had work to do with a zero-wait ``select()``
on stdin: if the host's payload had not physically reached the pipe at that
exact instant, the hook printed a usage line and exited non-blocking -- no
verdict, no audit event, no user-visible marker, no record that the hook had
even been invoked. The operation then ran unvalidated, and nothing anywhere
said so.

The race is real and only accidentally biased in the safe direction: the
interpreter's own startup normally takes longer than the host's write, so the
payload is usually already buffered by the time the check runs. Nothing
guarantees that ordering.

These tests reproduce the timing rather than reason about it. They drive the
real entry point as a subprocess with stdin held as a pipe and write the
payload after a measured delay, which is exactly what the host's scheduling can
do on a loaded machine.

Two properties are pinned:

1. The verdict does not depend on WHEN the payload arrives -- a mutating
   command is stopped whether the write lands before the process starts or a
   second later.
2. No non-blocking exit of the entry point happens without a trace. This
   includes the genuine absence of input, which must still leave the same
   degradation record as any other gate failure instead of exiting mute.

The event tag and the marker are written here as literals, not imported from
the module under test: they are the external contract an operator greps for,
and importing them would make the assertion agree with the implementation by
construction.
"""

import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
ENTRY_POINT = REPO_ROOT / "hooks" / "pre_tool_use.py"

FAIL_OPEN_EVENT = "hook_fail_open"
FAIL_OPEN_MARKER = "[SECURITY GATE DEGRADED]"
TRACE_FILENAME = "hook-trace.jsonl"

# The delays the cold review measured. 0.0 is the case that already worked
# (the write beats the child's startup); the other two are the ones where the
# guard never ran at all.
DELAYS = (0.0, 0.3, 1.0)

# A push to a remote: a command the gate must never let through unremarked.
# The plain form is deliberate -- `git push --force` is permanently deny-listed
# and would be stopped by a different layer, proving nothing about this one.
MUTATING_PAYLOAD = {
    "hook_event_name": "PreToolUse",
    "session_id": "entry-path-stdin-wait",
    "tool_name": "Bash",
    "tool_input": {"command": "git push origin main"},
}

BENIGN_PAYLOAD = {
    "hook_event_name": "PreToolUse",
    "session_id": "entry-path-stdin-wait",
    "tool_name": "Bash",
    "tool_input": {"command": "echo hello"},
}

# Selects an adapter key that is not in the registry, so the gate fails inside
# its own try block. Used to hold the run on the fail-open lane at every delay,
# so the trace property is asserted on a genuinely non-blocking exit rather
# than on a vacuous one.
UNRESOLVABLE_ADAPTER_ENV = {"GAIA_HOST": "no-such-host-adapter"}


def _env(data_dir, extra_env=None):
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_DATA"] = str(data_dir)
    # Keep every write this run might attempt off the user's real substrate.
    env["GAIA_DATA_DIR"] = str(data_dir)
    env["GAIA_DB"] = str(Path(data_dir) / "gaia.db")
    env.pop("GAIA_DEBUG", None)
    if extra_env:
        env.update(extra_env)
    return env


def _run_with_delay(payload, data_dir, delay, extra_env=None):
    """Drive the entry point, writing the payload only after ``delay`` seconds.

    The child is started with stdin as an open pipe and nothing in it. Whether
    the payload is there when the child looks is decided by the sleep, which is
    the whole point: the host's write and the child's first read are not
    ordered by anything.
    """
    proc = subprocess.Popen(
        [sys.executable, str(ENTRY_POINT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_env(data_dir, extra_env),
    )
    if delay:
        time.sleep(delay)
    stdout, stderr = proc.communicate(json.dumps(payload), timeout=120)
    return proc.returncode, stdout, stderr


def _run_without_input(data_dir, extra_env=None):
    """Drive the entry point with a pipe that stays OPEN and never receives data.

    Deliberately not ``communicate()``: that closes the child's stdin, which is
    an EOF the child can see immediately. The case being probed is the other
    one -- a pipe with a live writer that never writes -- because that is what
    a bounded wait has to resolve, and what the zero-wait check resolved by
    declaring there was no input at all.
    """
    proc = subprocess.Popen(
        [sys.executable, str(ENTRY_POINT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_env(data_dir, extra_env),
    )
    try:
        proc.wait(timeout=120)
    except subprocess.TimeoutExpired:  # pragma: no cover - safety net
        proc.kill()
        proc.wait()
    stdout = proc.stdout.read()
    stderr = proc.stderr.read()
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        stream.close()
    return proc.returncode, stdout, stderr


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


def _trace_records(data_dir):
    path = Path(data_dir) / "logs" / TRACE_FILENAME
    if not path.exists():
        return []
    records = []
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


def _decision(stdout):
    """The permissionDecision the run delivered, if it delivered one."""
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        decision = payload.get("hookSpecificOutput", {}).get("permissionDecision")
        if decision:
            return decision
    return None


def _blocked(returncode, stdout):
    """True when the host would stop the operation: exit 2 or a deny/ask."""
    return returncode == 2 or _decision(stdout) in ("deny", "block", "ask")


@pytest.mark.parametrize("delay", DELAYS)
def test_mutating_command_is_stopped_whenever_the_payload_arrives(tmp_path, delay):
    """The verdict on a push does not depend on when the write lands.

    Measured before the fix: 0.0s stopped the command, 0.3s and 1.0s let it
    through with the gate never having run.
    """
    returncode, stdout, stderr = _run_with_delay(MUTATING_PAYLOAD, tmp_path, delay)

    assert _blocked(returncode, stdout), (
        f"a push reached the tool unstopped when the payload arrived {delay}s "
        f"late -- the gate never ran (rc={returncode}, stdout={stdout!r}, "
        f"stderr={stderr!r})"
    )


@pytest.mark.parametrize("delay", DELAYS)
def test_no_nonblocking_exit_without_a_trace(tmp_path, delay):
    """A gate that lets the operation through leaves a record, at every delay.

    Run on the injected-failure lane so the exit is non-blocking at every
    delay; otherwise the property would be satisfied vacuously by a deny.
    """
    returncode, stdout, stderr = _run_with_delay(
        BENIGN_PAYLOAD, tmp_path, delay, UNRESOLVABLE_ADAPTER_ENV
    )
    combined = stdout + stderr

    assert not _blocked(returncode, stdout), (
        "this lane is supposed to fail open; a blocking exit makes the trace "
        f"assertion vacuous (rc={returncode}, stdout={stdout!r})"
    )
    assert FAIL_OPEN_MARKER in combined, (
        f"the operation was allowed through with no marker at {delay}s delay; "
        f"stdout={stdout!r} stderr={stderr!r}"
    )
    events = [r for r in _audit_records(tmp_path) if r.get("event") == FAIL_OPEN_EVENT]
    assert events, (
        f"no {FAIL_OPEN_EVENT!r} record at {delay}s delay -- an allow with no "
        "event is indistinguishable from a gate that decided the command was "
        f"safe; records={_audit_records(tmp_path)!r}"
    )
    traces = [r for r in _trace_records(tmp_path) if r.get("hook") == "pre_tool_use"]
    assert traces, (
        f"the invocation itself was never recorded at {delay}s delay, so "
        "'did the hook run?' is unanswerable offline"
    )


def test_genuine_absence_of_input_is_not_silent(tmp_path):
    """No payload ever arriving is still a gate failure, and says so.

    Waiting for the payload narrows the race but cannot close it: the host may
    genuinely never write. That case must degrade like every other gate
    failure -- recorded, marked, and non-blocking -- instead of exiting mute.
    """
    returncode, stdout, stderr = _run_without_input(
        tmp_path, {"GAIA_HOOK_STDIN_TIMEOUT": "0.5"}
    )
    combined = stdout + stderr

    assert returncode != 2, (
        "a missing payload must not brick the session -- the product posture is "
        f"to keep failing open, instrumented (rc={returncode})"
    )
    assert FAIL_OPEN_MARKER in combined, (
        f"the gate exited without a payload and said nothing; stdout={stdout!r} "
        f"stderr={stderr!r}"
    )
    events = [r for r in _audit_records(tmp_path) if r.get("event") == FAIL_OPEN_EVENT]
    assert events, (
        f"no {FAIL_OPEN_EVENT!r} record for a gate that never saw its input; "
        f"records={_audit_records(tmp_path)!r}"
    )
    traces = [r for r in _trace_records(tmp_path) if r.get("hook") == "pre_tool_use"]
    assert traces, "the invocation that never got its input left no trace at all"


def test_waiting_for_input_is_bounded(tmp_path):
    """The wait has a deadline, so a host that never writes cannot hang the turn.

    A gate that blocks forever on an empty pipe is a worse failure than the one
    being fixed: it stops the session rather than the command.
    """
    started = time.monotonic()
    returncode, _, _ = _run_without_input(tmp_path, {"GAIA_HOOK_STDIN_TIMEOUT": "0.5"})
    elapsed = time.monotonic() - started

    assert returncode != 2
    assert elapsed < 30, (
        f"the entry point waited {elapsed:.1f}s for input that never came -- "
        "the wait is not bounded by the configured deadline"
    )
