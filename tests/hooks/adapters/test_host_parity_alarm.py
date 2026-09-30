"""Parity alarm: every Gaia capability a Claude Code hook carries is transported on OpenCode, excluded with a reason, or pending on a named task.

``GAIA_HOST_PARITY_STRICT=1`` ignores ``PENDING_GAPS``, so the alarm reports
every open gap instead of only the unassigned ones.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import AbstractSet, Iterable, List, Mapping

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "hooks"))

from adapters.opencode import _EVENT_TYPES
from adapters.opencode_parity import CAPABILITIES, PENDING_GAPS, Capability, Registration


def claude_code_registrations() -> frozenset:
    manifest = json.loads((ROOT / "build" / "gaia.manifest.json").read_text())
    return frozenset(
        (event, token)
        for event, entries in manifest["hooks"]["matchers"].items()
        for entry in entries
        for token in (entry.get("matcher") or "").split("|")
    )


def parity_failures(
    registrations: AbstractSet[Registration],
    capabilities: Iterable[Capability],
    bridge_events: AbstractSet[str],
    pending: Mapping[str, str],
) -> List[str]:
    """Return one message per parity break, each naming the capability or registration at fault."""
    capabilities = tuple(capabilities)
    claimed = frozenset().union(*(capability.claude_code for capability in capabilities))
    failures = [
        f"Claude Code registers {event}[{token}] and no capability claims it"
        for event, token in sorted(registrations - claimed)
    ]
    failures += [
        f"a capability claims {event}[{token}], which Claude Code does not register"
        for event, token in sorted(claimed - registrations)
    ]
    for capability in capabilities:
        if capability.opencode_event and capability.exclusion:
            failures.append(f"{capability.name}: declares both a transport and an exclusion")
        if capability.opencode_event and capability.opencode_event not in bridge_events:
            failures.append(
                f"{capability.name}: transport {capability.opencode_event!r} is not an event the OpenCode bridge maps"
            )
        if capability.is_gap and capability.name not in pending:
            carriers = ", ".join(f"{event}[{token}]" for event, token in sorted(capability.claude_code))
            failures.append(
                f"{capability.name}: OpenCode neither transports it nor declares a reasoned exclusion ({carriers})"
            )
        if not capability.is_gap and capability.name in pending:
            failures.append(f"{capability.name}: no longer a gap, remove it from the pending register")
    known = {capability.name for capability in capabilities}
    failures += [
        f"pending register names {name!r}, which is not a declared capability"
        for name in sorted(set(pending) - known)
    ]
    return failures


def test_host_parity_alarm_holds_every_open_gap_on_a_named_task():
    pending = {} if os.environ.get("GAIA_HOST_PARITY_STRICT") == "1" else PENDING_GAPS

    failures = parity_failures(claude_code_registrations(), CAPABILITIES, set(_EVENT_TYPES), pending)

    assert not failures, "\n".join(failures)


def test_host_parity_alarm_names_a_capability_with_no_transport_or_exclusion():
    registrations = claude_code_registrations() | {("FutureEvent", "")}
    capabilities = CAPABILITIES + (
        Capability(name="synthetic capability", claude_code=frozenset({("FutureEvent", "")})),
    )

    failures = parity_failures(registrations, capabilities, set(_EVENT_TYPES), PENDING_GAPS)

    assert failures == [
        "synthetic capability: OpenCode neither transports it nor declares a reasoned exclusion (FutureEvent[])"
    ]


def test_host_parity_alarm_names_a_claude_code_registration_no_capability_claims():
    registrations = claude_code_registrations() | {("PreToolUse", "FutureTool")}

    failures = parity_failures(registrations, CAPABILITIES, set(_EVENT_TYPES), PENDING_GAPS)

    assert failures == ["Claude Code registers PreToolUse[FutureTool] and no capability claims it"]


def test_host_parity_alarm_refuses_a_transport_the_bridge_does_not_map():
    capabilities = tuple(
        Capability(name=c.name, claude_code=c.claude_code, opencode_event="session.created")
        if c.name == "session birth block" else c
        for c in CAPABILITIES
    )
    pending = {name: task for name, task in PENDING_GAPS.items() if name != "session birth block"}

    failures = parity_failures(claude_code_registrations(), capabilities, set(_EVENT_TYPES), pending)

    assert failures == [
        "session birth block: transport 'session.created' is not an event the OpenCode bridge maps"
    ]


def test_host_parity_alarm_refuses_a_pending_entry_for_a_transported_capability():
    pending = {**PENDING_GAPS, "tool policy": "task 0"}

    failures = parity_failures(claude_code_registrations(), CAPABILITIES, set(_EVENT_TYPES), pending)

    assert failures == ["tool policy: no longer a gap, remove it from the pending register"]
