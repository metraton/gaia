"""
Per-turn circuit breaker for the contract-rejection loop.

The defect this exists for: the SubagentStop gate rejects a turn with
exit_code=2, the harness hands that rejection back to the SUBAGENT, and the
subagent tries again -- with no layer anywhere counting how many times that
already happened. Measured: one agent retried ELEVEN times on the same
rejection code and spent 361k tokens; another, the day before, ten. The
rejection message was byte-identical on attempt one and attempt eleven, so the
agent had no way to know it was in a loop, and the relay file it re-read grew
to 37 KB.

Three things were missing and are supplied here:

  * A COUNT that outlives the attempt but dies with the turn. It lives in the
    relay's own per-turn key space (``<data_dir>/rejected_turns/<key>.attempts``,
    keyed on session + harness agent id, exactly like the preserved text), so
    the gate can read on attempt N what it wrote on attempt N-1.
  * A NUMBER IN THE MESSAGE -- see :func:`retry_notice`. An agent told "attempt
    2 of 3, 1 remaining" can change strategy; an agent handed the same bytes
    twice cannot tell the two attempts apart.
  * A CEILING. At :data:`DEFAULT_MAX_REJECTIONS` the breaker trips and the turn
    ENDS instead of being invited to repair again. This module only SIGNALS
    that: ``CircuitState.tripped`` is a fact about the count, not an action on
    the turn. What actually ends the turn is the CALLER's choice of output
    channel on the trip branch (``adapt_subagent_stop`` in
    ``hooks/adapters/claude_code.py``) -- specifically, NOT re-emitting
    ``hookSpecificOutput.additionalContext``, the one stdout key the harness
    hands back to the subagent as a system reminder and that therefore RESUMES
    the very turn being cut. Measured by suppression against the live harness:
    with that key emitted, a tripped turn crossed the gate 7 more times; with
    it suppressed, the turn ended by itself on the next pass. A caller that
    reads ``tripped=True`` and still emits that key has counted correctly and
    not cut anything.

The trip is DEGRADED AND LOUD, and the hard constraint is that it is degraded:
ending a turn is not certifying it. Nothing here finalizes a contract row,
promotes a state, or writes a terminal verdict -- an unfinalized row stays
unfinalized and stays readable as such. What the trip does produce is a
critical anomaly (``episode_anomalies``) plus an ``agent.contract_circuit_open``
event (``harness_events``), both reachable with ``gaia defects``.

A trip is STICKY: once a key has tripped, further rejections of that key report
the trip again rather than starting a fresh count, so a re-entered turn can
never restart the loop it was cut out of.

The count also carries the loop's remaining insumo gap: an agent that repeats
a rejection knew WHICH FIELD was wrong and nothing else -- not what it was
dispatched to do, not what it was told last time. :func:`retry_notice` now
reinjects the dispatch's own objective (bounded) and the PREVIOUS attempt's
typed rejection codes (one attempt's worth, carried in this same counter) --
both degrading to nothing, silently, when their input is absent.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

_KEY_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")

# Values that LOOK like an identity and identify nothing. The empty string is
# the obvious one; the literal "unknown" is the one that actually bit, because
# ``task_info_builder`` substitutes it when the SubagentStop payload carries no
# agent_id -- so every unidentified turn in a session arrived here wearing the
# same name.
_NON_IDENTIFYING = frozenset({"", "unknown", "none", "null"})

# How many rejections of ONE turn are tolerated before the breaker trips. Three
# is the user-set policy: two real chances to repair, and the third rejection
# ends the turn instead of extending the loop.
DEFAULT_MAX_REJECTIONS = 3
_MAX_REJECTIONS_ENV_VAR = "GAIA_CONTRACT_MAX_REJECTIONS"

CIRCUIT_OPEN_EVENT = "agent.contract_circuit_open"
CIRCUIT_ANOMALY_TYPE = "contract_rejection_circuit_open"

_SUFFIX = ".attempts"


def max_rejections() -> int:
    """The configured ceiling; the default whenever the override is unusable.

    A malformed or non-positive override is logged and ignored rather than
    honored -- a ceiling of 0 or -1 would trip every first rejection, which is
    a far worse failure than falling back to the policy default.
    """
    raw = os.environ.get(_MAX_REJECTIONS_ENV_VAR, "").strip()
    if not raw:
        return DEFAULT_MAX_REJECTIONS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Rejection circuit: %s=%r is not an integer; using the default of %d.",
            _MAX_REJECTIONS_ENV_VAR, raw, DEFAULT_MAX_REJECTIONS,
        )
        return DEFAULT_MAX_REJECTIONS
    if value < 1:
        logger.warning(
            "Rejection circuit: %s=%d is not a usable ceiling; using the "
            "default of %d.",
            _MAX_REJECTIONS_ENV_VAR, value, DEFAULT_MAX_REJECTIONS,
        )
        return DEFAULT_MAX_REJECTIONS
    return value


@dataclass(frozen=True)
class CircuitState:
    """This turn's standing with the breaker, after one rejection is counted.

    Attributes:
        attempt: which rejection of THIS turn just happened (1-based).
        limit: the ceiling in force for this turn.
        tripped: True -> the turn must END rather than be invited to repair.
        error: why the count could not be persisted, when it could not. The
            breaker then degrades to the pre-existing behavior (keep
            rejecting), but never silently -- the caller surfaces this.
        previous_codes: the typed rejection codes of the PRECEDING attempt for
            this same turn (empty on the first attempt, in 3-case mode, or
            whenever the prior pass carried no typed codes). Exactly one
            attempt's worth, never an accumulating history -- see
            :func:`record_rejection`.
    """

    attempt: int
    limit: int
    tripped: bool
    error: Optional[str] = None
    previous_codes: Tuple[str, ...] = ()

    @property
    def remaining(self) -> int:
        """Repairs left before the breaker trips; 0 once it has."""
        return max(0, self.limit - self.attempt)


def counter_key(session_id: Optional[str], task_info: Dict[str, Any]) -> Optional[str]:
    """A key that identifies ONE turn, or None when it cannot be built.

    Deliberately NOT ``rejected_turn_relay.preservation_key``. That key exists
    to locate PRESERVED TEXT, and its fallback chain is right for that job and
    catastrophic for this one: it degrades to the agent TYPE and then to the
    literal ``unknown``, both of which every dispatch of that agent in the
    session shares. Sharing a key costs the relay a merged text file; it costs
    the breaker a turn cut for rejections it never made -- MEASURED: a turn on
    its FIRST EVER rejection came back ``attempt=3, tripped=True`` because an
    unrelated turn had already spent the ceiling under the same key.

    So this key requires the harness's per-dispatch ``agent_id`` and returns
    None without it. None means NO CEILING for that turn -- the same fail-open
    direction the rest of this module takes, and the only safe one: a breaker
    that cannot tell two turns apart must decline to cut either, not guess.
    """
    harness_agent_id = str(task_info.get("agent_id") or "").strip()
    if harness_agent_id.lower() in _NON_IDENTIFYING:
        return None
    raw = f"{session_id or 'nosession'}.{harness_agent_id}"
    return _KEY_SAFE_RE.sub("-", raw)[:120]


def _counter_dir() -> Path:
    """The relay's own directory -- one per-turn artifact space, not two.

    Only the DIRECTORY is shared with the relay. The KEY is this module's own
    (see :func:`counter_key`), because the two have incompatible requirements
    for what happens when a turn cannot be identified.
    """
    from modules.agents.rejected_turn_relay import _relay_dir

    return _relay_dir()


def _counter_path(key: str) -> Path:
    return _counter_dir() / f"{key}{_SUFFIX}"


def _read(key: str) -> Dict[str, Any]:
    try:
        path = _counter_path(key)
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        # A corrupt counter reads as "no attempts yet". That is the direction
        # that keeps the pre-existing behavior rather than tripping a turn on
        # its first rejection because of an unreadable file.
        logger.warning(
            "Rejection circuit: counter for %s is unreadable (%s); restarting "
            "the count for this turn.", key, exc,
        )
        return {}


def _write(key: str, payload: Dict[str, Any]) -> None:
    directory = _counter_dir()
    target = directory / f"{key}{_SUFFIX}"
    tmp = directory / f".{key}.{os.getpid()}.{secrets.token_hex(4)}.count.tmp"
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, target)


def count(key: str) -> int:
    """Rejections recorded for ``key`` so far; 0 when the turn is clean."""
    try:
        return int(_read(key).get("attempts", 0))
    except Exception:
        return 0


def reset(key: str) -> None:
    """Drop the count once its turn is over (the gate finally accepted it)."""
    try:
        _counter_path(key).unlink(missing_ok=True)
    except Exception as exc:
        logger.debug("Rejection circuit: reset failed for %s: %s", key, exc)


def record_rejection(key: str, codes: Iterable[str] = ()) -> CircuitState:
    """Count one rejection of ``key`` and say whether the breaker trips.

    ``codes`` are THIS rejection's typed codes (the gate's anomaly codes;
    empty in 3-case mode or whenever the gate produced none). They are
    persisted into the SAME counter the attempt count already lives in -- the
    counter already survives between passes, so carrying one more field costs
    nothing new -- so the NEXT call can read them back as
    :attr:`CircuitState.previous_codes`. Only the immediately PRECEDING
    attempt's codes ever travel forward: each write replaces the prior codes
    rather than appending to them, which is what keeps a later retry message
    from growing with the number of attempts.

    Never raises: a persistence failure comes back as a state carrying
    ``error`` with ``tripped=False``, so the caller keeps the pre-existing
    rejecting behavior and reports the failure instead of acting on a count it
    does not have.
    """
    limit = max_rejections()
    codes_tuple = tuple(dict.fromkeys(str(c) for c in codes if c))
    try:
        state = _read(key)
        if state.get("tripped"):
            # Sticky: a turn already cut out of the loop must not be able to
            # re-enter it with a fresh count.
            return CircuitState(int(state.get("attempts", limit)), limit, True)

        previous_codes = tuple(str(c) for c in (state.get("codes") or ()) if c)
        attempt = int(state.get("attempts", 0)) + 1
        tripped = attempt >= limit
        _write(key, {"attempts": attempt, "tripped": tripped, "codes": list(codes_tuple)})
        if tripped:
            logger.error(
                "Rejection circuit OPEN for %s: rejection %d of %d -- the turn "
                "is being closed degraded instead of invited to repair again.",
                key, attempt, limit,
            )
        else:
            logger.warning(
                "Rejection circuit: rejection %d of %d for %s.", attempt, limit, key,
            )
        return CircuitState(attempt, limit, tripped, previous_codes=previous_codes)
    except Exception as exc:
        logger.warning(
            "Rejection circuit: could not record the rejection for %s: %s -- "
            "the loop ceiling is NOT in force for this turn.", key, exc,
        )
        return CircuitState(0, limit, False, error=str(exc))


def inline_budget(attempt: int, base: int) -> int:
    """Characters of preserved text to reinject on this attempt.

    The measured cost of the loop was never the retry alone -- it was retrying
    while re-reading everything that came before. The agent has already been
    handed the full text once, and the rejection also names the file it is
    preserved in, so a later attempt gets a smaller inline excerpt rather than
    the whole ceiling again.
    """
    if attempt <= 1:
        return base
    return max(base // (2 ** (attempt - 1)), 2000)


# Characters of the dispatch objective reinjected into a retry notice. Fixed
# and bounded so the section never dominates the message and, together with
# previous_codes carrying only ONE attempt's worth (see record_rejection),
# keeps the notice from growing with the number of attempts.
_OBJECTIVE_CHAR_BUDGET = 400


def _bounded_objective(dispatch_prompt: Optional[str]) -> str:
    """The dispatch's own goal, trimmed to a fixed budget -- or "" when unusable.

    "" is the only signal the caller reads: a missing or blank
    ``dispatch_prompt`` (no born row, or a row that never recorded one)
    degrades to "" and the objective section is omitted entirely, without
    exception. Never raises -- an untrusted value in, a string out, always.
    """
    try:
        text = (dispatch_prompt or "").strip()
    except Exception:
        return ""
    if not text:
        return ""
    if len(text) <= _OBJECTIVE_CHAR_BUDGET:
        return text
    return text[:_OBJECTIVE_CHAR_BUDGET].rstrip() + "…"


def retry_notice(state: CircuitState, *, dispatch_prompt: Optional[str] = None) -> str:
    """The attempt counter appended to a rejection that still invites repair.

    The message on attempt 2 differs from the message on attempt 1 because it
    carries a different number -- which is the only thing that lets an agent
    notice it is repeating itself. Two more insumos ride along, both aimed at
    the SAME repeated-mistake defect: an agent that only knows WHICH FIELD is
    wrong -- not what it was dispatched to do, nor what it was told last time
    -- reproduces the identical wrong fix on every pass.

      * the dispatch's own OBJECTIVE (``dispatch_prompt`` off the born row),
        bounded by :data:`_OBJECTIVE_CHAR_BUDGET`;
      * the TYPED CODES of the PREVIOUS rejection
        (``state.previous_codes``, one attempt's worth, not a history).

    Both degrade to nothing, silently and without exception, when their input
    is absent: no ``dispatch_prompt`` -> no objective section; no
    ``previous_codes`` (first rejection, or 3-case mode, which carries no
    typed codes at all) -> no codes section. A retry notice that fails to
    build is worse than one missing a nicety, so neither addition can raise.

    Both sections are appended AFTER the actionable "what to fix" paragraph,
    and the error of form itself lives entirely BEFORE this function's output
    (the caller prepends the gate's own rejection reason ahead of it) -- a
    reader hunting only for what to fix never needs to read past that
    paragraph into either section below it.
    """
    notice = (
        f"\n\n=== INTENTO {state.attempt} DE {state.limit} ===\n"
        f"Este es el rechazo n.{state.attempt} de ESTE turno. Te "
        f"{'queda' if state.remaining == 1 else 'quedan'} {state.remaining} "
        f"{'intento' if state.remaining == 1 else 'intentos'} antes de que el "
        "turno se cierre DEGRADADO y termine sin contrato valido.\n"
        "Reemitir lo mismo va a fallar igual: cambia lo que el mensaje de "
        "arriba senala como invalido o faltante. Si no podes producir un "
        "contrato valido, cerra en BLOCKED con la razon -- un cierre honesto "
        "vale mas que otro reintento identico.\n"
    )
    objective = _bounded_objective(dispatch_prompt)
    if objective:
        notice += f"\n--- PARA QUE FUISTE DESPACHADO ---\n{objective}\n"
    if state.previous_codes:
        notice += (
            "\n--- CODIGOS DEL RECHAZO ANTERIOR ---\n"
            f"{', '.join(state.previous_codes)}\n"
        )
    return notice


def degraded_close_reason(state: CircuitState, rejection_reason: str) -> str:
    """The record of WHY the turn ended, kept with the verdict that ended it.

    The gate's own last verdict is carried inside it: a degraded close that
    dropped the reason would be indistinguishable from a clean one.
    """
    return (
        f"[CONTRACT CIRCUIT OPEN] El contrato de este turno fue rechazado "
        f"{state.attempt} veces (limite {state.limit}). El turno se cierra "
        "DEGRADADO: termina, NO se certifica. El contrato NO quedo completo y "
        "la fila persistida sigue sin finalizar.\n"
        "El trabajo y la evidencia del turno se conservan (ver el archivo "
        "preservado del relay); el bucle de reintentos se corta aca.\n"
        f"Ultimo veredicto de la compuerta:\n{rejection_reason}"
    )


def circuit_anomaly(agent_type: str, state: CircuitState) -> Dict[str, Any]:
    """The critical anomaly recorded on the episode for a tripped turn."""
    return {
        "type": CIRCUIT_ANOMALY_TYPE,
        "severity": "critical",
        "code": "CONTRACT_REJECTION_LIMIT",
        "message": (
            f"Contract rejection circuit opened for {agent_type}: "
            f"{state.attempt} rejections of the same turn (limit {state.limit}). "
            "The turn was closed DEGRADED -- ended, not certified. The contract "
            "is NOT complete and its row was left unfinalized."
        ),
    }


def no_key_anomaly(agent_type: str) -> Dict[str, Any]:
    """Anomaly for a turn the breaker could not identify, and so did not guard.

    Recorded rather than logged because the alternative -- guessing a key -- is
    what cuts innocent turns, and the alternative to recording is a turn silently
    running with no ceiling at all.
    """
    return {
        "type": "contract_rejection_circuit_unavailable",
        "severity": "warning",
        "message": (
            f"Contract rejection circuit could not identify this turn for "
            f"{agent_type} (the SubagentStop payload carried no harness "
            "agent_id), so no per-turn counter could be kept. The rejection "
            "ceiling is NOT in force and the retry loop is unbounded for this "
            "turn."
        ),
    }


def counter_error_anomaly(agent_type: str, state: CircuitState) -> Dict[str, Any]:
    """Anomaly for a breaker that could not count -- the ceiling is off.

    Recorded so that a turn running WITHOUT the loop ceiling is visible, rather
    than looking exactly like a turn that simply never reached it.
    """
    return {
        "type": "contract_rejection_circuit_unavailable",
        "severity": "warning",
        "message": (
            f"Contract rejection circuit could not count this turn's rejections "
            f"for {agent_type} ({state.error}); the {state.limit}-rejection "
            "ceiling is NOT in force and the retry loop is unbounded."
        ),
    }


def record_circuit_event(agent_type: str, state: CircuitState, **meta: Any) -> None:
    """Land the tripped turn in ``harness_events``. Never raises.

    Same append-only channel as ``agent.contract_rejected``, so an operator
    reaches it with the verb that already exists:
    ``gaia defects --type=agent.contract_circuit_open``.
    """
    try:
        from modules.events.event_writer import EventWriter

        payload: Dict[str, Any] = {
            "attempts": state.attempt,
            "limit": state.limit,
            "closed": "degraded",
            "contract_complete": False,
        }
        payload.update({k: v for k, v in meta.items() if v is not None})
        EventWriter().write_event(
            CIRCUIT_OPEN_EVENT,
            "hook",
            agent_type,
            (
                f"contract rejection circuit opened for {agent_type} after "
                f"{state.attempt} rejections (limit {state.limit}); turn closed "
                "degraded, contract NOT complete"
            ),
            severity="error",
            meta=payload,
        )
    except Exception as exc:  # pragma: no cover - telemetry must never block
        logger.warning(
            "Rejection circuit: could not record the circuit-open event: %s", exc,
        )
