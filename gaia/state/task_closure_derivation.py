"""
gaia.state.task_closure_derivation -- What a freshly recorded gate verdict
implies, unattended, for the status of the task the gates belong to.

The three sibling modules answer questions a HUMAN caller's close raises.
``gaia.state.task_closure`` derives whether a task's gates amount to an
approving verdict. ``gaia.state.task_closure_condition`` turns that verdict plus
an optional stated reason into a permission. ``gaia.state.task_closure_identity``
puts the caller's standing over that permission. All three answer "may this
actor close this task?".

This module answers a different question, and the difference is the whole reason
it exists: given that a verdict was just recorded on a task's gates, WHAT SHOULD
HAPPEN to the task with no actor asking for anything. Nobody is requesting a
close here; the evidence changed, and the task's status either still follows from
it or no longer does. So the output is not a permission but an ACT --
:class:`DerivedClosureAction` -- and the caller that performs it goes through the
same single writer of ``tasks.status`` every manual close goes through, with the
same guard in front of it. There is no privileged path: an act this module names
is still subject to the closure condition downstream, and it is produced only in
the cells where that condition is already satisfied by the evidence alone.

EXACTLY TWO ACTS EXIST, AND THEY ARE NOT SYMMETRIC.

  * CLOSE -- a pending task whose every gate passed. The approving verdict IS
    the proof of verification, so the closure derives from evidence and needs no
    stated reason: an unattended close has no human to write one and nothing to
    justify. That is why no override travels with a derived close, and why the
    absence of any record naming who produced the task cannot block one -- an
    absent binding withholds evidence about the CALLER, and the caller is not
    what this close rests on.
  * REOPEN -- a closed task whose gates no longer all pass. Withdrawing a
    closure asserts nothing; it removes an assertion. It carries no gate
    condition for the same reason the manual reopen does not
    (``task_closure_condition.UNCONDITIONED_STATUSES``).

THE PRODUCER IS REFUSED A DERIVED CLOSE, AND IS NOT REFUSED A DERIVED REOPEN.
When the caller recording the verdict is the agent the task was dispatched to
(``task_closure_identity.ProducerStanding.BOUND_PRODUCER``), no CLOSE is
produced. The point is not that the downstream guard would refuse it -- it would,
absolutely and with no override able to lift it -- but that the automatism must
not become the route by which a producer's verdict on its own work closes its own
task. Deciding it HERE rather than discovering it downstream has a second,
concrete consequence: the gate verdict is already committed by the time this
question is asked, so producing an act that is certain to be refused would turn a
successfully recorded verdict into a raised exception. The reopen is deliberately
left available to that same producer: a producer that records a FAILING verdict
on its own task un-closing it moves in the fail-closed direction, and refusing it
would preserve a closure the evidence no longer supports.

``skipped`` IS NEVER TOUCHED, IN EITHER DIRECTION. A skipped task was set aside
by a human deciding the work should not happen, which is a statement about scope
rather than about verification; evidence arriving afterwards does not resurrect
it (and the lifecycle has no ``skipped`` -> ``done`` edge to travel anyway), and
it is not "closed", so there is nothing to withdraw.

IDEMPOTENCE IS DECIDED HERE, NOT LEFT TO THE WRITE. A task that already holds the
status the verdict implies yields NONE rather than an act that would happen to
change nothing. The single writer does treat a no-op transition as a no-op, so
relying on it would also be safe -- but then "re-recording a verdict does
nothing" would be a property of the write rather than of the derivation, and a
reader auditing this table could not see it. Re-recording a verdict is a decision
to do nothing, and it is spelled that way.

Pure in the same sense as its three siblings: no DB, no subprocess, no
filesystem, no environment read, no LLM. It classifies values handed to it and
returns one. The impure halves -- reading the gate rows, the binding rows, the
caller's dispatch identity and the task's current status, then performing the
transition -- live in ``gaia.store.writer``, at the seam where a verdict is
persisted. Purity is what makes the three-input decision exhaustively testable as
a truth table, which is the only way to show that no cell of it closes a task
merely because nothing named that cell.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from gaia.state.task_closure import GateVerdict
from gaia.state.task_closure_condition import CLOSING_STATUS
from gaia.state.task_closure_identity import ProducerStanding

# The two non-closing task statuses this module reasons about, named rather than
# inlined so the table below reads as three exhaustive branches over
# ``gaia.state.VALID_TASK_STATUSES`` and a fourth value shows up as unclassified
# instead of falling into whichever comparison happens to be last.
OPEN_STATUS = "pending"
SET_ASIDE_STATUS = "skipped"


class DerivedClosureAction(Enum):
    """What follows, unattended, from a verdict that was just recorded.

    Three members and the set is closed. ``NONE`` is a first-class answer, not
    the absence of one: most cells of the table resolve to it, and each does so
    for a stated reason the caller can surface.
    """

    # A pending task whose every gate passed moves to CLOSING_STATUS.
    CLOSE = "close"

    # A closed task whose gates no longer all pass moves back to OPEN_STATUS.
    REOPEN = "reopen"

    # Nothing follows. Carried with the reason why, never as a bare fallthrough.
    NONE = "none"


@dataclass(frozen=True)
class DerivedClosureDecision:
    """One derived act, with the status to write and why it was reached.

    ``target_status`` is the value to hand the single writer, and is None
    exactly when ``action`` is ``NONE`` -- so a caller never has to re-derive
    the target from the action. ``why`` is populated on every branch, including
    the acting ones, because the caller reports the derivation to an operator
    who did not ask for it and would otherwise see a status change with no
    stated cause.
    """

    action: DerivedClosureAction
    target_status: str | None
    why: str

    @property
    def acts(self) -> bool:
        """True iff a transition follows from this decision."""
        return self.action is not DerivedClosureAction.NONE


def _inaction(why: str) -> DerivedClosureDecision:
    """Build the NONE decision, so no branch can produce one without a reason."""
    return DerivedClosureDecision(
        action=DerivedClosureAction.NONE,
        target_status=None,
        why=why,
    )


def decide_derived_closure(
    *,
    verdict: GateVerdict,
    task_status: object,
    standing: ProducerStanding,
) -> DerivedClosureDecision:
    """Decide what follows for a task from the verdict its gates now derive.

    Args:
        verdict:     The task's derived gate verdict
                     (``task_closure.derive_gate_verdict``). Fail-closed
                     already: zero gates is not approving, so a task nobody
                     wrote a gate for can never be reached by a derived close.
        task_status: The task's CURRENT ``tasks.status``. Required, with no
                     default -- the same act is right or wrong depending on it,
                     and defaulting it would decide a third of the table by the
                     language rather than by this module. An unrecognized value
                     yields NONE.
        standing:    The caller's standing relative to the task's known
                     producers (``task_closure_identity.classify_producer_
                     standing``). Required for the same reason.

    Returns:
        A :class:`DerivedClosureDecision`. Never raises: this runs after the
        gate verdict is already persisted, so every unexpected input resolves to
        an answer rather than to an exception that would leave a recorded
        verdict looking like a failure.
    """
    status = task_status if isinstance(task_status, str) else ""

    if verdict.approving:
        if status == CLOSING_STATUS:
            return _inaction(
                "the task is already closed and every gate still passes, so the "
                "re-recorded verdict implies no transition"
            )
        if status == SET_ASIDE_STATUS:
            return _inaction(
                f"the task is {SET_ASIDE_STATUS!r}: it was set aside as work "
                "that should not happen, which evidence arriving afterwards "
                "does not reverse"
            )
        if status != OPEN_STATUS:
            return _inaction(_unrecognized_status_reason(task_status))
        if standing is ProducerStanding.BOUND_PRODUCER:
            return _inaction(
                "every gate passed, but the verdict was recorded by the agent "
                "this task was dispatched to: a derived close here would be the "
                "producer certifying its own work through the automatism. The "
                "verdict stands on the gates; an independent actor's close, or "
                "a verdict recorded from an independent turn, still derives it"
            )
        return DerivedClosureDecision(
            action=DerivedClosureAction.CLOSE,
            target_status=CLOSING_STATUS,
            why=(
                f"every one of the task's {verdict.gate_count} gate(s) passed, "
                "which is the proof of verification the closure rests on"
            ),
        )

    if status == CLOSING_STATUS:
        return DerivedClosureDecision(
            action=DerivedClosureAction.REOPEN,
            target_status=OPEN_STATUS,
            why=(
                "the task is closed but its gates no longer amount to an "
                "approving verdict ("
                + ("; ".join(verdict.reasons) or "verdict not approving")
                + "), so the closure it asserted no longer follows"
            ),
        )
    if status == OPEN_STATUS:
        return _inaction(
            "the gates do not amount to an approving verdict and the task is "
            "already open, so nothing follows"
        )
    if status == SET_ASIDE_STATUS:
        return _inaction(
            f"the task is {SET_ASIDE_STATUS!r}: it asserts no closure, so there "
            "is none to withdraw"
        )
    return _inaction(_unrecognized_status_reason(task_status))


def _unrecognized_status_reason(task_status: object) -> str:
    """Explain a refusal to act on a status outside the task vocabulary.

    Such a value cannot reach the column through the writers (the DB CHECK and
    ``gaia.store.writer.set_task_status`` both reject it), so seeing one means
    the row did not come from that path. Acting on it would guess; the
    fail-closed answer is to leave a row nobody can classify exactly as found.
    """
    return (
        f"the task carries status {task_status!r}, which is outside "
        f"{[OPEN_STATUS, CLOSING_STATUS, SET_ASIDE_STATUS]}: a row that cannot "
        "be classified is left as found rather than transitioned on a guess"
    )


__all__ = [
    "OPEN_STATUS",
    "SET_ASIDE_STATUS",
    "DerivedClosureAction",
    "DerivedClosureDecision",
    "decide_derived_closure",
]
