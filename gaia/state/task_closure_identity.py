"""
gaia.state.task_closure_identity -- Where the caller of a closure stands with
respect to the agent that produced the task.

``gaia.state.task_closure`` derives whether the gates amount to an approving
verdict. ``gaia.state.task_closure_condition`` turns that verdict, plus an
optional stated reason, into a decision. This module adds the THIRD input --
whether the actor asking for the closure is the same agent the work was
dispatched to -- and composes it OVER that decision in
:func:`decide_closure_under_identity`.

THE COMPOSITION IS A WRAPPER, NOT A SECOND PATH, and the direction of the
dependency is what makes that true: this module imports the closure condition
and delegates to it verbatim, never the reverse. There is exactly one place a
closure is permitted and exactly one validator of an override reason, both in
``task_closure_condition``; what is added here is a refusal in front of them.
Re-implementing either -- even "just" the reason check -- would create the
parallel path this arrangement exists to avoid, and would let the two copies
drift apart on the next edit.

FAIL CLOSED, AND THE TWO BRANCHES ARE NOT THE SAME BRANCH:

  * A BOUND PRODUCER IS REFUSED ABSOLUTELY. When a binding row ties an agent to
    the task by ``plan_task_id`` and the caller carries that agent's name, the
    closure is refused whatever else is true -- gates passing, override stated,
    both. This is the one refusal in the whole closure path that no argument can
    lift, which is why it is resolved BEFORE the disjunction rather than as
    another disjunct: a self-certified closure is exactly what an independent
    verifier exists to prevent, and an override that could rescue it would make
    the producer its own verifier by typing a sentence.

  * NO BINDING IS NOT AN APPROVAL. When nothing names who produced the task, the
    absence proves nothing -- neither that the caller is the producer nor that
    they are not. So this module NEVER returns a verified-identity verdict; the
    strongest thing an absent binding yields is :data:`ProducerStanding.UNLINKED`,
    which grants nothing and simply hands the decision back to the disjunction
    the closure condition already owns. That is the whole content of the
    unlinked branch: the guard adds no permission, and it must not be read as
    adding one. A close with no approving verdict still needs the override the
    closure condition demands -- the SAME override, not a second one built here.

AN APPROVING VERDICT SUFFICES ON ITS OWN, AND THAT EXEMPTION IS DECLARED RATHER
THAN LEFT IMPLICIT. An approving gate verdict closes a task with no override,
including -- especially -- when no binding exists at all. The verdict
IS the proof of verification, and the derived close that carries it runs without
a human and has no reason to state. Demanding an override there would make an
automatic close impossible in practice (a binding is rare), so the exemption is
declared here in the standing that would otherwise be read as demanding one.

THE GRANULARITY IS THE AGENT NAME, NOT THE INSTANCE. ``GAIA_DISPATCH_AGENT`` --
the same value ``gaia.state.permissions`` reads, and the only identity coordinate
that reaches a CLI invocation -- carries a NAME ('developer'), never the minted
``agent_id`` and never a usable session id. A binding row born at dispatch stamps
that same name into ``agent_contract_handoffs.agent_id``, so the comparison is
name against name and is therefore exact at the granularity of the name and blind
below it: two dispatches of the same agent on the same task are one identity
here. That is a property of what the CLI is given, not a shortcut, and it is
named rather than left for a reader to infer from an ``==``.

Pure in the same sense as its three siblings: no DB, no subprocess, no
filesystem, no environment read, no LLM. It classifies values handed to it. The
impure halves -- reading ``GAIA_DISPATCH_AGENT`` and SELECTing the binding rows
-- live in ``gaia.store.writer``, which is where the closure is actually
enforced. Purity is what makes the three-input predicate exhaustively testable as
a truth table, which is the only way to show that no cell of it permits a closure
merely because nothing named that cell.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import replace
from enum import Enum

from gaia.state.task_closure import GateVerdict
from gaia.state.task_closure_condition import ClosureDecision, decide_task_closure

# The minted-agent-id shape, imported from the validator that owns it so this
# module never re-spells the literal. The fallback is byte-identical and exists
# for the same reason as the one in ``gaia.state.task_closure``: a pure module
# must not hard-fail on an import edge.
try:
    from gaia.contract.validator import AGENT_ID_PATTERN_TEXT as _AGENT_ID_TEXT
except Exception:  # pragma: no cover - defensive fallback only
    _AGENT_ID_TEXT = r"^a[0-9a-f]{16,}$"

_MINTED_AGENT_ID = re.compile(_AGENT_ID_TEXT)

# The column a binding row carries its actor in. Named because the same column
# holds two different identity spaces depending on which writer wrote the row
# (see :func:`producer_agent_names`), and a reader has to know which one is
# being read.
BINDING_ACTOR_KEY = "agent_id"


class ProducerStanding(Enum):
    """Where the caller stands relative to the task's known producers.

    Three values, and the set is closed: a caller either matches a known
    producer, differs from every known producer, or there are no known producers
    to compare against. Enumerating the third case as its own member -- rather
    than folding it into "differs" -- is what keeps the absence of evidence from
    being spelled the same way as evidence of absence, even though both grant
    exactly the same thing (nothing).
    """

    # A binding names this caller as the agent the task was dispatched to.
    # Refused absolutely; no override lifts it.
    BOUND_PRODUCER = "bound_producer"

    # A binding exists and names someone else. Carries no permission of its own:
    # the closure condition's disjunction decides, unchanged.
    DISTINCT_FROM_PRODUCER = "distinct_from_producer"

    # Nothing names who produced the task. Also carries no permission: identical
    # in what it grants to DISTINCT_FROM_PRODUCER, distinct in what it lets the
    # refusal SAY (see :func:`unlinked_denial_clause`).
    UNLINKED = "unlinked"


def producer_agent_names(binding_rows: object) -> tuple[str, ...]:
    """Extract the comparable producer NAMES from a task's binding rows.

    ``binding_rows`` is whatever the caller read for one ``plan_task_id`` -- the
    ``agent_contract_handoffs`` rows that reference it. Only the actor column is
    consulted, and only the values that are NAMES survive:

      * A born-at-dispatch row stamps the agent's name ('developer'), because
        that is all the dispatch metadata carries. Comparable -- kept.
      * A finalized row stamps the minted ``agent_id`` ('a' + 16+ hex), an
        identifier space no CLI invocation can produce. Not comparable to
        anything the guard will ever be handed, so it is dropped rather than
        kept as a name that can never match -- keeping it would inflate the
        binding set with rows that prove nothing about the caller.

    Being liberal about WHICH rows count is the fail-closed direction here: any
    row that carries a name is treated as naming a producer, so a new writer of
    the binding widens the refusal rather than escaping it.

    Returns the distinct names in first-seen order (order is not semantic; it is
    stable so a rendered message reads the same twice). Never raises: a row that
    is not a mapping, or carries no usable actor, contributes nothing.
    """
    if binding_rows is None or isinstance(binding_rows, (str, bytes, Mapping)):
        return ()
    if not isinstance(binding_rows, Iterable):
        return ()

    names: list[str] = []
    for row in binding_rows:
        if not isinstance(row, Mapping):
            continue
        actor = row.get(BINDING_ACTOR_KEY)
        if not isinstance(actor, str):
            continue
        actor = actor.strip()
        if not actor or _MINTED_AGENT_ID.match(actor):
            continue
        if actor not in names:
            names.append(actor)
    return tuple(names)


def classify_producer_standing(
    *,
    caller_agent: object,
    producer_agents: object,
) -> ProducerStanding:
    """Classify the caller against the task's known producers.

    Args:
        caller_agent:    The caller's resolved identity -- the agent NAME from
                         ``GAIA_DISPATCH_AGENT``, or
                         ``task_closure_event.HUMAN_ACTOR`` for a human CLI
                         caller (``task_closure_event.resolve_actor`` is the one
                         resolver, reused rather than restated).
        producer_agents: The names from :func:`producer_agent_names`.

    Returns:
        A :class:`ProducerStanding`. Never raises: an unusable caller value or
        an unusable collection resolves to a standing that grants nothing, never
        to one that grants something.
    """
    names = tuple(producer_agents) if isinstance(producer_agents, (list, tuple)) else ()
    if not names:
        return ProducerStanding.UNLINKED
    if isinstance(caller_agent, str) and caller_agent.strip() in names:
        return ProducerStanding.BOUND_PRODUCER
    return ProducerStanding.DISTINCT_FROM_PRODUCER


def build_producer_self_close_message(
    *,
    brief_name: str,
    task_order_num: object,
    caller_agent: object,
) -> str:
    """Render the absolute refusal an operator can act on.

    Three things it has to say, and the third is the one usually left out: WHO
    was refused (the name that matched, so an operator can see which identity the
    command ran under), WHY it is refused at all, and -- because every other
    refusal in this path names the override as a way out -- that this one has no
    way out. A message that omitted the last point would send the reader
    straight to a flag that cannot help them.
    """
    return (
        f"refusing to close task {task_order_num} of brief '{brief_name}': the "
        f"caller ({caller_agent!r}) is the agent this task was dispatched to, so "
        "closing it would be the producer certifying its own work. This refusal "
        "is ABSOLUTE: --override does not lift it, because an override that "
        "could would make the producer its own verifier. Record the verdict on "
        f"the gates (`gaia task gate set-status {brief_name} {task_order_num} "
        "<gate_id> pass`) from an independent verification turn, and the closure "
        "then derives from that evidence -- or have a different actor close it."
    )


def unlinked_denial_clause() -> str:
    """The sentence appended to a gate refusal when nothing names a producer.

    A refusal has to distinguish WHY the override is being
    demanded. Unapproved gates and a missing binding are different facts with
    different corrections, and a message that states only the first leaves an
    operator trying to fix gates when what is also missing is any record of who
    did the work. The clause states the fact and, explicitly, that the fact is
    not a permission -- because the tempting reading of "nobody is named" is
    "nobody is blocked".
    """
    return (
        " Note also that NO dispatch binding names who produced this task, so "
        "the caller's identity contributes no evidence either way -- and an "
        "absent binding is not an approval. That does not add a requirement on "
        "top of the two exits above; it is why neither of them can be skipped."
    )


def decide_closure_under_identity(
    *,
    verdict: GateVerdict,
    brief_name: str,
    task_order_num: object,
    standing: ProducerStanding,
    caller_agent: object = None,
    override_reason: object | None = None,
) -> ClosureDecision:
    """Decide a closure with the caller's standing taken into account.

    The whole closure predicate in one pure function, so its three inputs --
    gate verdict, caller standing, override -- can be enumerated as an
    exhaustive truth table with no cell left to a language default. It composes
    rather than reimplements: for every standing but one it returns
    ``task_closure_condition.decide_task_closure``'s answer unchanged, so the
    disjunction and the override's reason validation have exactly one
    implementation.

    Two things happen here that the wrapped decision cannot do, both because
    only this layer knows the standing:

      * A BOUND PRODUCER IS REFUSED BEFORE ANYTHING ELSE IS CONSIDERED, above
        the disjunction and above the argument check. That ordering is a
        decision, not an accident: no argument can lift this refusal, so
        validating the override first would only change WHICH refusal the caller
        reads. The wrapped decision is never reached, which is also why the
        refusal cannot be confused with a failed disjunct.
      * AN UNLINKED REFUSAL SAYS SO. A refusal that would already have happened
        gains the sentence explaining that nothing names a producer either
        (:func:`unlinked_denial_clause`); nothing else about the decision
        changes. The clause is appended only to a refusal, never to a
        permission, so a task that closed cleanly is not handed a warning about
        evidence it did not need.

    Args:
        verdict:         The task's derived gate verdict.
        brief_name:      Brief owning the task, for the rendered refusal.
        task_order_num:  The task's ``order_num``, likewise.
        standing:        The caller's standing from
                         :func:`classify_producer_standing`. Required, with no
                         default -- identity is a third input to the predicate,
                         and defaulting it here would decide a cell of the truth
                         table by the language rather than by this module.
        caller_agent:    The caller's resolved name, used only to name them in
                         the producer refusal. Nothing is decided from it: the
                         comparison's outcome is already in ``standing``.
        override_reason: WHY the task is being closed without an approving
                         verdict, passed through untouched.

    Returns:
        A :class:`ClosureDecision`.

    Raises:
        ValueError: when an override was requested with a reason that states
            nothing -- raised by the wrapped decision's validator, not by a
            second copy of it. A bound producer is refused before that
            validation runs, so an unusable reason never turns their absolute
            refusal into an argument error.
    """
    if standing is ProducerStanding.BOUND_PRODUCER:
        return ClosureDecision(
            permitted=False,
            override_used=False,
            reason=None,
            verdict=verdict,
            denial_message=build_producer_self_close_message(
                brief_name=brief_name,
                task_order_num=task_order_num,
                caller_agent=caller_agent,
            ),
        )

    decision = decide_task_closure(
        verdict=verdict,
        brief_name=brief_name,
        task_order_num=task_order_num,
        override_reason=override_reason,
    )

    if standing is ProducerStanding.UNLINKED and decision.denial_message:
        return replace(
            decision,
            denial_message=decision.denial_message + unlinked_denial_clause(),
        )
    return decision


__all__ = [
    "BINDING_ACTOR_KEY",
    "ProducerStanding",
    "build_producer_self_close_message",
    "classify_producer_standing",
    "decide_closure_under_identity",
    "producer_agent_names",
    "unlinked_denial_clause",
]
