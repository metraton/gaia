"""
gaia.state.task_closure_event -- Shape of the auditable record a manual
task-close override leaves behind.

``gaia.state.task_closure`` answers whether a task's gates amount to an
approving verdict. It does not decide what happens when they do not, and closing
a task anyway -- by hand, with a stated reason -- is a sanctioned escape hatch.
What this module owns is the other half of that escape hatch: the escape must
not be silent. An override that leaves no trace is indistinguishable, a week
later, from a task that was genuinely verified, so it carries a record of WHO
closed the task, WHEN, and WHY.

This module holds the SHAPE of that record and nothing else. It is pure in the
same sense as ``gaia.state.task_closure``: no DB, no subprocess, no filesystem,
no environment read, no LLM. It builds a value. The single impure step -- the
append -- lives in ``gaia.store.writer.write_task_close_override_event``, which
is also where the channel's one environment read (the dispatch identity)
happens.

Four decisions are encoded here, each load-bearing:

  * THE SUBSTRATE IS ``harness_events``, NOT A NEW TABLE. That append-only
    mirror already carries every field the record needs (``ts``, ``type``,
    ``source``, ``agent``, ``result``, ``severity``, ``payload``); it is already
    exposed by two readers (``gaia.store.reader.cross_surface_query`` on the
    ``harness_events`` surface, and ``read_defects``); and appending to it is not
    a second writer of ``tasks.status``, because it is a different table
    entirely. ``agent.cut`` and ``agent.contract_rejected`` are the living
    precedent for a harness-observed abnormality riding this channel. Accepted
    consequence: the record inherits that table's 90-day retention window
    (``gaia.store.writer._maybe_prune_harness_events``).

  * SEVERITY IS ABOVE ``info``, SO THE RECORD SURFACES AS A DEFECT.
    ``read_defects`` admits an orchestrator-origin row by SEVERITY, not by an
    enumerated list of types: whatever the harness grades outside
    ``gaia.store.reader.NON_DEFECT_EVENT_SEVERITIES`` is a defect for triage.
    Grading this event ``info`` would still persist it and still let a
    type-filtered query find it, while making it invisible in ``gaia defects`` --
    the surface an operator actually reads. Closing a task without verification
    is discipline worth seeing, so the grade is chosen, not inherited.

  * THE WHO LANDS IN THE ``agent`` COLUMN, NOT ONLY IN THE PAYLOAD. Both readers
    filter and render that column (``gaia defects --agent=NAME``), whereas the
    payload is opaque to SQL. An actor recorded only inside the JSON is readable
    one row at a time and filterable not at all.

  * NOTHING THAT HAS A COLUMN IS REPEATED IN THE PAYLOAD. The WHEN is
    ``harness_events.ts``, stamped by the append itself and surfaced at the top
    level by both readers; the workspace and the actor likewise have columns.
    Copying any of them into the payload would create two values for one fact
    with nothing to keep them equal. The payload carries only what has no
    column: which task, and why.

The record's granularity of identity is the AGENT NAME, because that is the only
coordinate that reaches a CLI invocation (``GAIA_DISPATCH_AGENT``, the same value
``gaia.state.permissions`` reads). Its absence means a human CLI caller, not an
unknown one, and is recorded as :data:`HUMAN_ACTOR` rather than left blank -- an
empty ``agent`` column would read as "not recorded" when in fact it is known.

Deciding whether an override is *permitted*, and enforcing that a reason was
given before a task is closed, belong to the closure path, not here. This module
does enforce one thing about its own output: it will not build a record with no
reason. A record that cannot say why is not an audit record, so the channel
cannot emit one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# The dotted event type. Consumers filter on this exact string -- `gaia query
# --surface=harness_events --type=task.close_override` and `gaia defects
# --type=task.close_override` -- so it is a name in the substrate's vocabulary,
# not a label: renaming it orphans every record already written under the old
# one. Dotted noun.verb, matching agent.cut / agent.contract_rejected /
# command.executed.
TASK_CLOSE_OVERRIDE_EVENT = "task.close_override"

# Above `info` on purpose, which is what puts the record in `gaia defects` (see
# the module docstring). `warning` rather than `error` because the override is a
# sanctioned act with a stated reason, not a failure: it belongs next to
# agent.cut, which is also graded `warning`, rather than next to
# agent.contract_rejected, which is graded `error`.
TASK_CLOSE_OVERRIDE_SEVERITY = "warning"

# The `source` column: who emitted the record. Every other harness event is
# written from a hook and carries "hook"; this one is emitted from the closure
# path a CLI invocation drives, and saying so is how a reader tells the two
# origins apart in a single listing.
TASK_CLOSE_OVERRIDE_SOURCE = "cli"

# The actor recorded when no dispatch identity is present. Mirrors the contract
# of the guards in gaia.state.permissions: an unset or blank GAIA_DISPATCH_AGENT
# is a human CLI caller, a known identity rather than a missing one.
HUMAN_ACTOR = "human"

# Rejection message for a missing reason. A module constant, like
# task_closure.EMPTY_GATE_SET_REASON, so a caller rendering it to an operator --
# or a test asserting on it -- never has to string-match a sentence written
# inline at the raise site.
MISSING_REASON_MESSAGE = (
    "a manual close override requires a non-empty reason: the record exists to "
    "state WHY a task was closed without an approving gate verdict, and a record "
    "that cannot say why is not an audit record"
)

# Caller-supplied context is nested under this ONE payload key instead of being
# merged flat. Flattening would let a caller's key named "actor" or "reason"
# overwrite the two fields the record exists to carry; nesting makes that
# collision structurally impossible rather than merely discouraged.
DETAILS_PAYLOAD_KEY = "details"


@dataclass(frozen=True)
class OverrideEvent:
    """One task-close-override record, ready to append.

    The five scalar fields map one-to-one onto ``harness_events`` columns;
    ``meta`` is what lands in ``payload`` as JSON. Frozen because a record is a
    statement about something that happened: rewriting it after construction
    would be editing the audit trail rather than writing it.
    """

    event_type: str
    source: str
    agent: str
    result: str
    severity: str
    meta: dict[str, Any]

    def as_write_kwargs(self) -> dict[str, Any]:
        """Return this record as keyword arguments for ``write_harness_event``.

        Every key is a parameter of ``gaia.store.writer.write_harness_event``,
        so the record is appended without a translation step in between -- the
        same relationship ``task_closure.derive_gate_verdict`` has with
        ``list_task_gates``, in the other direction. ``workspace`` and
        ``db_path`` are deliberately absent: they locate the substrate being
        written to, not the event being recorded.

        ``meta`` is copied, so a caller cannot reach back through the returned
        dict and mutate the frozen record's payload.
        """
        return {
            "event_type": self.event_type,
            "source": self.source,
            "agent": self.agent,
            "result": self.result,
            "severity": self.severity,
            "meta": dict(self.meta),
        }


def normalize_reason(reason: object) -> str:
    """Return ``reason`` stripped, or raise when it states nothing.

    Whitespace-only, empty, ``None`` and any non-string are one case, not four:
    none of them tells a later reader why a task was closed, so each raises
    ``ValueError(MISSING_REASON_MESSAGE)``. The check lives here, in the shape,
    rather than only at the CLI flag that collects the reason, so no future
    caller of the channel can bypass it.
    """
    if not isinstance(reason, str):
        raise ValueError(MISSING_REASON_MESSAGE)
    stripped = reason.strip()
    if not stripped:
        raise ValueError(MISSING_REASON_MESSAGE)
    return stripped


def resolve_actor(dispatch_agent: object) -> str:
    """Resolve the recorded actor from a raw dispatch-identity value.

    Pure by design: it takes the value rather than reading the environment, so
    the only environment read in the channel stays in the impure writer. Unset,
    blank and non-string all resolve to :data:`HUMAN_ACTOR` -- the same reading
    ``gaia.state.permissions`` gives an absent ``GAIA_DISPATCH_AGENT`` (a human
    CLI caller), never a blank ``agent`` column that a reader would have to
    interpret.
    """
    if not isinstance(dispatch_agent, str):
        return HUMAN_ACTOR
    return dispatch_agent.strip() or HUMAN_ACTOR


def build_override_event(
    *,
    brief_name: str,
    task_order_num: int,
    reason: object,
    actor: object = None,
    task_id: int | None = None,
    details: Mapping[str, Any] | None = None,
) -> OverrideEvent:
    """Build the record for a manual close override of one task.

    Args:
        brief_name:     Brief the task's plan belongs to -- an operator
                        coordinate with no ``harness_events`` column, so it goes
                        in the payload.
        task_order_num: The task's ``order_num`` within that plan, likewise.
        reason:         WHY the task is being closed without an approving
                        verdict. Passed through :func:`normalize_reason`, which
                        rejects anything that states nothing.
        actor:          Raw dispatch-identity value, resolved by
                        :func:`resolve_actor`. ``None`` (the default) resolves to
                        :data:`HUMAN_ACTOR`; the writer passes the environment's
                        value here.
        task_id:        Persisted ``tasks.id`` when the caller already resolved
                        it. Optional because ``harness_events`` carries no
                        foreign key to ``tasks`` and the record must not depend
                        on a lookup succeeding.
        details:        Optional structured context (for instance which gates
                        were outstanding), nested under
                        :data:`DETAILS_PAYLOAD_KEY` so it can never shadow the
                        actor or the reason.

    Returns:
        An :class:`OverrideEvent`.

    Raises:
        ValueError: when ``reason`` states nothing.
    """
    reason_text = normalize_reason(reason)
    actor_name = resolve_actor(actor)

    meta: dict[str, Any] = {
        "actor": actor_name,
        "reason": reason_text,
        "brief_name": brief_name,
        "task_order_num": task_order_num,
    }
    if task_id is not None:
        meta["task_id"] = task_id
    if details:
        meta[DETAILS_PAYLOAD_KEY] = dict(details)

    # The actor and the reason lead the line because both readers truncate it
    # (`_query_harness_events` at 80 characters, `_render_table` to whatever the
    # viewport leaves), and a triage listing that cuts off before the reason
    # forces a second query to learn anything.
    result = (
        f"manual close override by {actor_name} on task {task_order_num} "
        f"of brief '{brief_name}': {reason_text}"
    )

    return OverrideEvent(
        event_type=TASK_CLOSE_OVERRIDE_EVENT,
        source=TASK_CLOSE_OVERRIDE_SOURCE,
        agent=actor_name,
        result=result,
        severity=TASK_CLOSE_OVERRIDE_SEVERITY,
        meta=meta,
    )


__all__ = [
    "DETAILS_PAYLOAD_KEY",
    "HUMAN_ACTOR",
    "MISSING_REASON_MESSAGE",
    "OverrideEvent",
    "TASK_CLOSE_OVERRIDE_EVENT",
    "TASK_CLOSE_OVERRIDE_SEVERITY",
    "TASK_CLOSE_OVERRIDE_SOURCE",
    "build_override_event",
    "normalize_reason",
    "resolve_actor",
]
