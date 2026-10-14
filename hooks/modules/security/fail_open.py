"""Handling for a security gate that fails before reaching a verdict.

Only exit 2 or an explicit deny stops an operation; every other exit lets it
run, so a gate that crashes mid-decision would grant what it never classified.
The gate therefore fails CLOSED: every failure blocks with exit 2. Failing open
was the earlier policy, retired because an unclassified tool call is the one
outcome the gate exists to prevent; a gate broken for every call is visible
and fixed, while one silently passing calls is not.

Every failure leaves two traces:

- a record in the always-on audit sink (``audit-*.jsonl``, written regardless
  of GAIA_DEBUG), tagged :data:`FAIL_OPEN_EVENT` with its reason and the
  outcome. The tag predates the fail-closed policy and is kept so existing
  metrics queries still group on it; the ``outcome`` field is what says the
  call was blocked;
- :data:`FAIL_OPEN_MARKER` in the text returned to the user.

Both emissions are best-effort: a failure in the diagnostic path must never
change the outcome of the operation it describes.

When the command had already been classified as mutating before the failure,
the denial names that verb, since the call was going to stop and ask anyway.
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


# How the gate came to have no verdict. The default is the historical case, an
# exception mid-decision. The second is the gate never running at all: the tool
# call payload did not reach the hook, so there was nothing to decide about.
# They degrade identically and are told apart only so the notice does not
# report an error that never happened.
CAUSE_ERROR = "raised an error before reaching a verdict"
CAUSE_NO_INPUT = "never received the tool call payload on stdin, so it never ran"


def build_fail_open_message(reason: str, detail: str, cause: str = CAUSE_ERROR) -> str:
    """Return the user-visible denial for a gate that reached no verdict."""
    return (
        f"{FAIL_OPEN_MARKER} The security gate {cause} "
        f"and the operation was blocked unvalidated.\n"
        f"Reason: {reason}\n"
        f"Detail: {detail}\n"
        f"The command was never classified. Run `gaia doctor` to find the "
        f"failing component, then retry. A '{FAIL_OPEN_EVENT}' audit event "
        f"was recorded."
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
# Written the moment a command is known to be T3, read only by the fail-open
# path -- which needs to know what the gate knew BEFORE it crashed, including
# when the crash is in the classifier itself, where re-classifying to find out
# would fail the same way.
#
# The state is module-level, so its LIFETIME is the thing that has to be
# right. It used to rest on "the host runs one hook process per tool call",
# which is true today and enforced by nothing: under a persistent or batched
# hook the first mutation would mark the process for good, and every later
# read would inherit that mark and be denied on the strength of a push it had
# nothing to do with -- the bounded exception escaping its bound in the
# direction that stops harmless work.
#
# So the lifetime is now bound to the validation instead of to the process:
# BashValidator.validate() clears the breadcrumb for every command it is about
# to gate, which means the mark can only ever describe the command currently
# being decided. Process isolation is no longer load-bearing.
#
# Deliberately NOT done: having known_mutative_classification() verify that
# the mark's command matches the one being gated. The string reaching the T3
# decision is the NORMALIZED one, not the one validate() was entered with:
# `nohup terraform apply &` and `terraform apply > out.txt` both mark plain
# `terraform apply`, because the sanitizer strips the backgrounding and the
# redirect before classification. A mismatch is therefore ordinary, and a
# guard that silently returned None on it would disarm the block in the
# direction that lets a mutation through -- the expensive direction to be
# wrong in.


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
    """Drop the breadcrumb, so it cannot outlive the command it describes.

    Called by BashValidator.validate() before it gates a command: whatever is
    recorded at that point belongs to a previous command, and carrying it
    forward would deny the current one on someone else's classification.
    """
    global _classification
    _classification = None


def decide_fail_open(
    reason: str, detail: str, cause: str = CAUSE_ERROR
) -> FailOpenOutcome:
    """Record a gate failure and block the operation.

    Args:
        reason: Short machine tag for the lane that failed.
        detail: Underlying exception text or human-readable detail.
        cause: How the gate came to have no verdict -- see CAUSE_ERROR /
            CAUSE_NO_INPUT. Affects only the wording of the notice.
    """
    known = known_mutative_classification()
    if known is None:
        record_fail_open(
            reason=reason, detail=detail, context={"outcome": "blocked"}
        )
        return FailOpenOutcome(
            message=build_fail_open_message(reason, detail, cause),
            exit_code=2,
            blocked=True,
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
