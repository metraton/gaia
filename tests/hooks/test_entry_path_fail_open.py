#!/usr/bin/env python3
"""The pre-tool entry path may fail open, but never silently.

Only exit 2 or an explicit deny decision stops an operation. Every other exit
lets the tool call proceed. So an unhandled exception raised while the security
gate is still deciding ends the turn with exit 1: the operation runs, and
nothing records that the gate never reached a verdict.

The product decision is to keep passing -- a gate that bricks the session on its
own crash is worse than one that lets a command through -- so what these tests
pin is the other half: the pass leaves a trace. The failure is INJECTED at a
real seam (an unresolvable host adapter, which raises inside the entry point's
try block) and driven through the actual entry-point process, not through a
stand-in for it.

The event tag and the user-facing marker are written here as literals rather
than imported from the module under test. They are the external contract --
what an operator greps for in the audit sink, and what the user reads in the
returned text -- and importing them would make the assertion agree with the
implementation by construction instead of checking it.
"""

import glob
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
ENTRY_POINT = REPO_ROOT / "hooks" / "pre_tool_use.py"

FAIL_OPEN_EVENT = "hook_fail_open"
FAIL_OPEN_MARKER = "[SECURITY GATE DEGRADED]"

BASH_PAYLOAD = {
    "hook_event_name": "PreToolUse",
    "session_id": "entry-path-fail-open",
    "tool_name": "Bash",
    "tool_input": {"command": "echo hello"},
}

# Selects an adapter key that is not in the registry, so get_adapter() raises
# KeyError inside the entry point's try -- a real misconfiguration, not a
# test-only hook into the security path.
UNRESOLVABLE_ADAPTER_ENV = {"GAIA_HOST": "no-such-host-adapter"}


def _run_entry_point(payload, data_dir, extra_env=None):
    """Drive the real entry point as a subprocess with an isolated data dir."""
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_DATA"] = str(data_dir)
    env.pop("GAIA_DEBUG", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(ENTRY_POINT)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
    )


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


def _blocked(proc):
    """True when the host would stop the operation: exit 2 or a deny decision."""
    if proc.returncode == 2:
        return True
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("hookSpecificOutput", {}).get("permissionDecision") in (
            "deny",
            "block",
        ):
            return True
    return False


@pytest.fixture
def degraded_run(tmp_path):
    """One injected-failure invocation of the entry point, with its data dir."""
    proc = _run_entry_point(BASH_PAYLOAD, tmp_path, UNRESOLVABLE_ADAPTER_ENV)
    return proc, tmp_path


def test_entry_path_fail_open_does_not_block(degraded_run):
    """An unhandled gate exception still lets the operation through."""
    proc, _ = degraded_run
    assert not _blocked(proc), (
        "the entry path must keep failing OPEN -- a crashing gate must not be "
        f"able to brick the session (rc={proc.returncode}, stdout={proc.stdout!r})"
    )


def test_entry_path_fail_open_emits_queryable_event(degraded_run):
    """The same invocation records an always-on audit event carrying its reason."""
    _, data_dir = degraded_run
    records = _audit_records(data_dir)
    events = [r for r in records if r.get("event") == FAIL_OPEN_EVENT]
    assert events, (
        f"no {FAIL_OPEN_EVENT!r} record in the always-on audit sink -- a gate "
        "that fails open without an event is indistinguishable from a gate "
        f"that allowed on purpose; records found: {records!r}"
    )
    assert any(e.get("reason") for e in events), (
        f"the {FAIL_OPEN_EVENT!r} event carries no reason tag: {events!r}"
    )


def test_entry_path_fail_open_warns_the_user(degraded_run):
    """The same invocation returns text carrying the degradation notice."""
    proc, _ = degraded_run
    combined = proc.stdout + proc.stderr
    assert FAIL_OPEN_MARKER in combined, (
        "the degradation notice is missing from the text returned to the user; "
        f"got stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )


def test_entry_path_fail_open_holds_on_one_invocation(degraded_run):
    """Not-blocked, event emitted and user warned all hold for the SAME run."""
    proc, data_dir = degraded_run
    events = [r for r in _audit_records(data_dir) if r.get("event") == FAIL_OPEN_EVENT]
    assert not _blocked(proc)
    assert events
    assert FAIL_OPEN_MARKER in proc.stdout + proc.stderr
