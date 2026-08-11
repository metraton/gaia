"""Instrumentation for a security gate that fails open.

Only exit 2 or an explicit deny decision stops an operation; every other exit
lets it run. So an exception raised while the gate is still deciding does not
merely lose the verdict -- it grants the operation. Failing CLOSED instead was
weighed and rejected: a gate that denies everything the moment it crashes can
brick a session, and the crash is usually in the gate, not in the command.

What is not acceptable is failing open SILENTLY. An allow with no record is
indistinguishable, after the fact, from a gate that read the command and
decided it was safe -- which is precisely the reading an operator must never be
led into. So every fail-open leaves two traces:

- a record in the always-on audit sink (``audit-*.jsonl``, written regardless
  of GAIA_DEBUG, unlike the ``logging`` module which hook code routes through a
  NullHandler by default), tagged with :data:`FAIL_OPEN_EVENT` so it is
  queryable by event and grouped by its reason;
- :data:`FAIL_OPEN_MARKER` in the text returned to the user, so the degradation
  is visible in the turn it happened rather than only in a log nobody opens.

Both emissions are best-effort by construction: a failure in the diagnostic
path must never be able to change the outcome of the operation it is
describing.

There is ONE bounded exception to passing. If the command had already been
classified as mutating BEFORE the failure, letting it through is not "losing a
verdict" -- the verdict existed, and it was going to demand consent. Degrading
that into a permission hands over exactly what the gate was holding back, which
is worse than any outcome the fail-open policy was protecting against. So a
fail-open that finds such a classification blocks instead, and says why. The
exception cannot brick a session, because it reaches only commands that were
already going to stop and ask.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

# Audit vocabulary. FAIL_OPEN_EVENT is the event tag an operator greps for and
# the metrics layer groups on; the reason discriminates WHICH fail-open lane
# fired, the same shape the approval-persistence sensor already uses
# (event tag + reason tag).
FAIL_OPEN_EVENT = "hook_fail_open"
FAIL_OPEN_COMPONENT = "gaia.pre_tool_use"

# The user-facing marker. A single fixed token so the notice is greppable in a
# transcript and cannot be confused with an ordinary tool error.
FAIL_OPEN_MARKER = "[SECURITY GATE DEGRADED]"


def record_fail_open(
    *,
    reason: str,
    detail: str,
    component: str = FAIL_OPEN_COMPONENT,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a fail-open to the always-on audit sink. Never raises.

    Writes both an ``error`` record (carrying the underlying exception text for
    triage) and a :data:`FAIL_OPEN_EVENT` event record (carrying the reason tag
    for counting), mirroring the two-sensor shape the approval-persistence
    degradation already emits.

    Args:
        reason: Short machine tag for the lane that failed open (e.g.
            "unhandled_exception").
        detail: Underlying exception text or human-readable detail.
        component: Subsystem that failed open.
        context: Optional extra fields, redacted by the audit sanitizer.
    """
    try:
        from ..audit.logger import log_error, log_event

        log_error(
            component=component,
            error_type=reason,
            detail=detail,
            context=context,
        )
        log_event(
            event=FAIL_OPEN_EVENT,
            component=component,
            reason=reason,
            context=context,
        )
    except Exception:
        pass


def build_fail_open_message(reason: str, detail: str) -> str:
    """Return the user-visible notice for a gate that failed open.

    States the three things a reader needs and cannot infer: that the gate did
    not reach a verdict, that the operation proceeded anyway, and that this is
    not an approval.
    """
    return (
        f"{FAIL_OPEN_MARKER} The security gate raised an error before reaching a "
        f"verdict and the operation was allowed to proceed unvalidated.\n"
        f"Reason: {reason}\n"
        f"Detail: {detail}\n"
        f"This is NOT an approval: the command was never classified. A "
        f"'{FAIL_OPEN_EVENT}' audit event was recorded."
    )


def build_fail_open_block_message(reason: str, detail: str, verb: str) -> str:
    """Return the user-visible denial for a fail-open on an already-T3 command."""
    return (
        f"{FAIL_OPEN_MARKER} The security gate raised an error AFTER classifying "
        f"this command as state-mutating, and the command was blocked rather than "
        f"allowed through the failure.\n"
        f"Verb: {verb}\n"
        f"Reason: {reason}\n"
        f"Detail: {detail}\n"
        f"The command was already going to require approval, so passing it here "
        f"would have granted exactly what the gate was withholding. Retry once "
        f"the underlying error is resolved."
    )


# ---------------------------------------------------------------------------
# Classification breadcrumb
# ---------------------------------------------------------------------------
# Process-local on purpose: the host runs one hook process per tool call, so
# this never outlives the single command it describes. It is written the moment
# a command is known to be T3 and read only by the fail-open path, which needs
# to know what the gate knew BEFORE it crashed -- including when the crash is in
# the classifier itself, where re-classifying to find out would fail the same
# way.


@dataclass(frozen=True)
class Classification:
    """What the gate had already decided about the command when it failed."""

    command: str
    verb: str
    category: str


@dataclass(frozen=True)
class FailOpenOutcome:
    """The fail-open decision: what to tell the user, and whether to block."""

    message: str
    exit_code: int
    blocked: bool


_classification: Optional[Classification] = None


def note_mutative_classification(command: str, verb: str, category: str) -> None:
    """Record that this command was classified as state-mutating."""
    global _classification
    _classification = Classification(command=command, verb=verb, category=category)


def known_mutative_classification() -> Optional[Classification]:
    """Return the classification the gate reached before failing, if any."""
    return _classification


def clear_classification() -> None:
    """Drop the breadcrumb. Exists for tests; a real hook process is short-lived."""
    global _classification
    _classification = None


def decide_fail_open(reason: str, detail: str) -> FailOpenOutcome:
    """Record a gate failure and decide whether the operation still proceeds.

    Blocks only when a mutating classification was already reached; otherwise
    the operation passes, instrumented. Recording happens on both branches, so
    the audit sink carries the failure regardless of which way it resolved.
    """
    known = known_mutative_classification()
    if known is None:
        record_fail_open(
            reason=reason, detail=detail, context={"outcome": "allowed"}
        )
        return FailOpenOutcome(
            message=build_fail_open_message(reason, detail),
            exit_code=1,
            blocked=False,
        )

    record_fail_open(
        reason=reason,
        detail=detail,
        context={
            "outcome": "blocked",
            "verb": known.verb,
            "category": known.category,
        },
    )
    return FailOpenOutcome(
        message=build_fail_open_block_message(reason, detail, known.verb),
        # Exit 2 is the only exit code the host treats as blocking; exit 1 is a
        # non-blocking "hook error" and the tool call would proceed.
        exit_code=2,
        blocked=True,
    )
