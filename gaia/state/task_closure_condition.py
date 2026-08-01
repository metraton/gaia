"""
gaia.state.task_closure_condition -- Whether a task may be closed, given the
verdict its gates derive and whether an override was stated.

``gaia.state.task_closure`` derives whether a task's gates amount to an
approving verdict and deliberately stops there: it never decides what to do with
the answer. ``gaia.state.task_closure_event`` owns the record a manual override
leaves behind. This module holds the piece between them -- the DECISION -- and
nothing else.

THE PREDICATE IS A DISJUNCTION. A task may be closed when EITHER its gates
amount to an approving verdict OR the caller states a reason for closing it
anyway::

    close permitted  <=>  verdict.approving  OR  override reason stated

Both disjuncts are evidence of something. The first is evidence that the work was
verified; the second is not evidence of verification but of ACCOUNTABILITY -- a
named actor saying, on the record, why they are closing a task the gates do not
support. What is refused is the third case: a close backed by neither. That is
the whole content of the decision, and the reason a bare False is never enough
for a caller to act on -- see ``denial_message``.

THE DISJUNCTION IS NOT THE WHOLE CONDITION, AND THIS MODULE IS NOT WHERE THE
REST OF IT LIVES. ``gaia.state.task_closure_identity`` wraps this decision with
a refusal that sits ABOVE the disjunction -- a caller that is the agent the task
was dispatched to is refused whatever else is true. That wrapper delegates here
verbatim for every other caller, so this module stays the ONE place a closure is
permitted and the ONE place an override is validated; nothing about identity is
decided, duplicated, or defaulted below this line.

THE DISJUNCTS ARE EVALUATED IN THAT ORDER, and the order is observable: an
approving verdict closes the task WITHOUT consuming an override, so a stated
reason is not recorded when the gates already justify the close. An override is
recorded only when it was actually needed, which is what keeps the audit trail's
meaning exact -- every ``task.close_override`` record marks a task that was
closed without an approving verdict, never one that merely passed a redundant
flag.

A MALFORMED OVERRIDE IS AN ARGUMENT ERROR, NOT A VERDICT. A reason that states
nothing (empty, whitespace-only, or not a string) raises through
``task_closure_event.normalize_reason`` -- the one validator, reused rather than
restated -- and it raises whether or not the override turned out to be needed.
Accepting a blank reason on a task whose gates happened to pass would teach that
the flag does not need one.

ONLY CLOSING IS CONDITIONED, AND THE EXEMPTION IS DECLARED. ``tasks.status``
admits three values; this module classifies all three
(:data:`CLOSING_STATUS` against :data:`UNCONDITIONED_STATUSES`) so no status can
end up exempt by omission. Marking a task ``skipped`` is a human act about
whether the work should happen at all, not a claim that it was verified, and
reopening to ``pending`` withdraws a closure rather than asserting one -- neither
carries a gate condition. That exemption is a decision, so it is named here
rather than left implicit in an ``if`` at the call site.

Pure in the same sense as its two siblings: no DB, no subprocess, no filesystem,
no environment read, no LLM. It takes a verdict and an argument and returns a
value; the caller performs the write, emits the record, and raises. Being pure
is what makes the predicate exhaustively testable as a truth table, which is the
only way to show that no cell of it falls through to permit-by-default.
"""

from __future__ import annotations

from dataclasses import dataclass

from gaia.state.task_closure import APPROVING_GATE_STATUS, GateVerdict
from gaia.state.task_closure_event import (
    TASK_CLOSE_OVERRIDE_EVENT,
    normalize_reason,
)

# The one ``tasks.status`` value that asserts the work is finished, and therefore
# the only transition the gate condition governs.
CLOSING_STATUS = "done"

# The statuses the condition deliberately does NOT govern. Enumerated rather
# than left as "everything else" so that adding a fourth task status cannot
# silently inherit an exemption: with both sides named, the pair can be checked
# against ``gaia.state.VALID_TASK_STATUSES`` and a new value shows up as an
# unclassified one demanding a decision.
UNCONDITIONED_STATUSES: tuple[str, ...] = ("pending", "skipped")


class TaskClosureBlocked(ValueError):
    """Raised when a task's closure is backed by neither a verdict nor a reason.

    ``ValueError`` as the base class on purpose: every caller of
    ``gaia.store.writer.set_task_status`` already handles ``ValueError`` for an
    illegal transition or a missing entity, so a refused closure surfaces
    through the paths that exist rather than needing a new one -- while a caller
    that wants to tell the two apart still can, by type.
    """


@dataclass(frozen=True)
class ClosureDecision:
    """The decision about one closure, with everything the caller needs next.

    ``permitted`` is the disjunction. ``override_used`` says which disjunct
    carried it, and is True ONLY when the override was needed -- it is the
    caller's signal to emit the audit record, so a False here means no record is
    owed. ``reason`` is the normalized override text when one was consumed, and
    None otherwise. ``verdict`` is carried through so the caller can report what
    was outstanding without deriving it a second time. ``denial_message`` is the
    operator-facing refusal, set when and only when ``permitted`` is False.
    """

    permitted: bool
    override_used: bool
    reason: str | None
    verdict: GateVerdict
    denial_message: str | None


def closure_is_conditioned(new_status: object) -> bool:
    """True iff transitioning a task to ``new_status`` is a closure.

    The single place the exemption is expressed. A caller asks this instead of
    comparing against a literal, so ``skipped`` and the reopen to ``pending``
    stay exempt by construction (see :data:`UNCONDITIONED_STATUSES`).
    """
    return new_status == CLOSING_STATUS


def override_not_applicable_message(new_status: object) -> str:
    """Explain that an override was stated for a transition that is not a closure.

    Refusing is deliberate where ignoring would be easier: an operator who
    states a reason expects it to be recorded, and silently dropping it leaves
    them believing there is an audit record where there is none.
    """
    return (
        f"an override applies only to closing a task ({CLOSING_STATUS!r}), and "
        f"the requested status is {new_status!r}. Transitions to "
        f"{list(UNCONDITIONED_STATUSES)} carry no gate condition, so there is "
        "nothing to override and the stated reason would not be recorded: "
        "re-issue the command without the override."
    )


def build_closure_denial_message(
    *,
    brief_name: str,
    task_order_num: object,
    verdict: GateVerdict,
) -> str:
    """Render the refusal an operator can act on.

    Two things make it actionable, and both are required rather than
    stylistic: it says WHAT is missing (the verdict's own reasons, which
    distinguish "gates outstanding" from "no gates at all" -- different
    corrections), and it names BOTH ways out, with the command for each. A
    refusal that states only the first leaves the operator without the escape
    hatch; one that states only the second invites the override for a task that
    merely needed its verdict recorded.

    A third fact can also be behind the demand -- that nothing on record names
    who produced the task -- and it is appended by
    ``task_closure_identity.decide_closure_under_identity``, which is the layer
    that knows it. Kept out of here so this renderer needs no identity input.
    """
    why = "; ".join(verdict.reasons) or "the gate verdict is not approving"
    message = (
        f"refusing to close task {task_order_num} of brief '{brief_name}': "
        f"{why}. There are exactly two ways to close it. (1) EVIDENCE -- bring "
        f"every gate to '{APPROVING_GATE_STATUS}' ("
        f"`gaia task gate list {brief_name} {task_order_num}` shows what is "
        f"outstanding; `gaia task gate set-status {brief_name} "
        f"{task_order_num} <gate_id> {APPROVING_GATE_STATUS}` records a "
        "verdict) and the closure then derives from that evidence. (2) OVERRIDE "
        f"-- close it against the gates with `gaia task set-status "
        f"{brief_name} {task_order_num} {CLOSING_STATUS} --override "
        "--reason='why you are closing it anyway'`, which is recorded as an "
        f"auditable '{TASK_CLOSE_OVERRIDE_EVENT}' event and surfaces in "
        "`gaia defects`."
    )
    return message


def decide_task_closure(
    *,
    verdict: GateVerdict,
    brief_name: str,
    task_order_num: object,
    override_reason: object | None = None,
) -> ClosureDecision:
    """Decide whether a task may be closed, and on which grounds.

    Args:
        verdict:         The task's derived gate verdict
                         (``task_closure.derive_gate_verdict``).
        brief_name:      Brief owning the task -- used only to render the
                         refusal in terms the operator can retype.
        task_order_num:  The task's ``order_num``, likewise.
        override_reason: WHY the task is being closed without an approving
                         verdict. ``None`` means no override was requested;
                         anything else is a request, validated by
                         ``task_closure_event.normalize_reason``.

    Returns:
        A :class:`ClosureDecision`. Never raises for a non-approving verdict --
        a refusal is a value, not an exception, so the whole predicate is
        exercisable as a truth table and the raise stays at the seam that
        actually refuses to write.

    Raises:
        ValueError: when an override was requested with a reason that states
            nothing (``task_closure_event.MISSING_REASON_MESSAGE``).
    """
    # Before the disjunction, not inside it: a malformed override is rejected
    # even when the gates would have closed the task anyway.
    reason = normalize_reason(override_reason) if override_reason is not None else None

    if verdict.approving:
        return ClosureDecision(
            permitted=True,
            override_used=False,
            reason=None,
            verdict=verdict,
            denial_message=None,
        )

    if reason is not None:
        return ClosureDecision(
            permitted=True,
            override_used=True,
            reason=reason,
            verdict=verdict,
            denial_message=None,
        )

    return ClosureDecision(
        permitted=False,
        override_used=False,
        reason=None,
        verdict=verdict,
        denial_message=build_closure_denial_message(
            brief_name=brief_name,
            task_order_num=task_order_num,
            verdict=verdict,
        ),
    )


__all__ = [
    "CLOSING_STATUS",
    "ClosureDecision",
    "TaskClosureBlocked",
    "UNCONDITIONED_STATUSES",
    "build_closure_denial_message",
    "closure_is_conditioned",
    "decide_task_closure",
    "override_not_applicable_message",
]
