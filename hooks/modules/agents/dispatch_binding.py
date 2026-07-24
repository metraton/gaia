"""
Born-at-dispatch binding + referential integrity (plan 34 task 6, Fase 2).

At dispatch time (the PreToolUse:Agent / SubagentStart path) the hook BIRTHS the
nascent ``agent_contract_handoffs`` row via
``gaia.store.writer.insert_dispatched_handoff``, stamping the four binding
coordinates (plan_task_id, plan_id, parent_handoff_id, kind) from the dispatch
metadata. Task 5 froze the writer primitive: it performs the INSERT but is
deliberately semantics-free -- it does NOT know which ``kind`` of dispatch
requires which coordinate. That knowledge lives HERE, as REFERENTIAL INTEGRITY
validation of the nascent row (validation of the row's coordinates, NOT semantic
gating by ``kind``):

  * ``kind == 'task_execution'`` REQUIRES a ``plan_task_id`` that RESOLVES to a
    DISPATCHABLE ``tasks.id`` row (the row exists AND its status is 'pending').
    A dispatch whose plan_task_id is missing, unknown, or already-terminal
    ('done' / 'skipped') is REJECTED -- the row is not born.
  * ``turn_role == 'verifier'`` REQUIRES a ``parent_handoff_id`` that RESOLVES to
    an existing ``agent_contract_handoffs.id`` row (a verifier turn binds to the
    producer turn it verifies). A verifier dispatch with a missing / unknown
    parent is REJECTED.
  * ``kind`` is a PURE LABEL (plan 34 S3): NO value of ``kind`` is ever rejected
    for its value. ``kind`` names what the turn is (task_execution / verifier /
    investigation / memory / ...); only the binding's referential integrity is
    validated, never the label itself.

Any ``plan_task_id`` / ``parent_handoff_id`` that IS present (even for a kind
that does not require it) is checked for mere EXISTENCE before birth, so a
dangling coordinate surfaces as a clean ``DispatchBindingError`` here rather than
as a raw SQLite foreign-key ``IntegrityError`` from the writer's INSERT. The
extra dispatchability constraint (status='pending') is scoped to task_execution,
per the rules above.
"""

from __future__ import annotations

import logging
import pathlib as _pl
import sys as _sys
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

# A dispatchable plan task is one still awaiting execution. 'done' / 'skipped'
# are terminal -- re-dispatching onto them would bind a fresh turn to a task the
# plan has already closed out. Mirrors tasks.status CHECK in schema.sql.
_DISPATCHABLE_TASK_STATUSES = frozenset({"pending"})

# The dispatch label that makes a resolvable plan_task_id MANDATORY. Every other
# label leaves plan_task_id optional -- kind is a pure tag, this is the ONE place
# a label drives a binding requirement, and it does so by referential integrity
# (the task must resolve), never by rejecting the label value.
_TASK_EXECUTION_KIND = "task_execution"

# The turn role that makes a resolvable parent_handoff_id MANDATORY.
_VERIFIER_ROLE = "verifier"


class DispatchBindingError(ValueError):
    """A nascent-row binding failed referential integrity -- birth is rejected.

    Carries a machine-readable ``reason`` code alongside the human message so a
    caller (and the gate tests) can assert on the cause without string-matching:

      * 'task_execution_requires_plan_task_id'
      * 'plan_task_id_unresolved'
      * 'plan_task_id_not_dispatchable'
      * 'verifier_requires_parent_handoff_id'
      * 'parent_handoff_id_unresolved'
    """

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def _import_writer():
    """Import ``gaia.store.writer`` from either an installed package or the repo.

    Mirrors the resolution ``handoff_persister`` uses so the hook layer stays
    agnostic to whether ``gaia`` is pip-installed or a sibling in the repo tree.
    """
    try:
        from gaia.store import writer as _writer  # noqa: WPS433 (local import)
    except ImportError:
        _repo_root = _pl.Path(__file__).resolve().parent.parent.parent.parent
        if str(_repo_root) not in _sys.path:
            _sys.path.insert(0, str(_repo_root))
        from gaia.store import writer as _writer  # noqa: WPS433
    return _writer


def _connect(db_path: "_pl.Path | None"):
    """Open a read connection through the store's own connect helper.

    Reusing ``gaia.store.reader._connect`` gives us schema materialization,
    ``foreign_keys=ON``, and a bounded busy_timeout identical to the writer, so
    the resolvability checks observe exactly the same rows the writer's INSERT
    will (no separate connection contract to keep in sync).
    """
    try:
        from gaia.store.reader import _connect as _reader_connect  # noqa: WPS433
    except ImportError:
        _repo_root = _pl.Path(__file__).resolve().parent.parent.parent.parent
        if str(_repo_root) not in _sys.path:
            _sys.path.insert(0, str(_repo_root))
        from gaia.store.reader import _connect as _reader_connect  # noqa: WPS433
    return _reader_connect(db_path)


def _task_dispatchability(
    con, plan_task_id: int
) -> "tuple[bool, Optional[str]]":
    """Return ``(exists, status)`` for a ``tasks.id`` (status None when absent)."""
    row = con.execute(
        "SELECT status FROM tasks WHERE id = ? LIMIT 1",
        (plan_task_id,),
    ).fetchone()
    if row is None:
        return (False, None)
    return (True, row["status"])


def _handoff_exists(con, parent_handoff_id: int) -> bool:
    """Return True iff an ``agent_contract_handoffs.id`` row exists."""
    row = con.execute(
        "SELECT 1 FROM agent_contract_handoffs WHERE id = ? LIMIT 1",
        (parent_handoff_id,),
    ).fetchone()
    return row is not None


def validate_dispatch_binding(
    *,
    kind: "Optional[str]",
    turn_role: "Optional[str]" = None,
    plan_task_id: "Optional[int]" = None,
    parent_handoff_id: "Optional[int]" = None,
    db_path: "_pl.Path | None" = None,
) -> None:
    """Validate the referential integrity of a nascent-row binding.

    Raises ``DispatchBindingError`` (with a ``reason`` code) when the binding is
    not resolvable per the rules in the module docstring; returns None when the
    binding is sound and the row may be born. ``kind`` is NEVER rejected for its
    value -- only the coordinates it (and ``turn_role``) require are validated.
    """
    con = _connect(db_path)
    try:
        # --- task_execution: a resolvable, dispatchable plan_task_id is required
        if kind == _TASK_EXECUTION_KIND:
            if plan_task_id is None:
                raise DispatchBindingError(
                    "a 'task_execution' dispatch requires a plan_task_id binding "
                    "it to the plan task it executes; none was supplied.",
                    reason="task_execution_requires_plan_task_id",
                )
            exists, status = _task_dispatchability(con, plan_task_id)
            if not exists:
                raise DispatchBindingError(
                    f"plan_task_id={plan_task_id} does not resolve to any "
                    f"tasks.id -- the task_execution dispatch is rejected.",
                    reason="plan_task_id_unresolved",
                )
            if status not in _DISPATCHABLE_TASK_STATUSES:
                raise DispatchBindingError(
                    f"plan_task_id={plan_task_id} resolves to a task with "
                    f"status={status!r}, which is not dispatchable "
                    f"(expected one of {sorted(_DISPATCHABLE_TASK_STATUSES)}).",
                    reason="plan_task_id_not_dispatchable",
                )
        elif plan_task_id is not None:
            # A non-task_execution kind MAY carry a plan_task_id (a verifier turn
            # bound to the same plan task, say). It is optional, but if present it
            # must at least EXIST, so a dangling FK surfaces here as a clean
            # rejection rather than a raw IntegrityError from the writer's INSERT.
            exists, _status = _task_dispatchability(con, plan_task_id)
            if not exists:
                raise DispatchBindingError(
                    f"plan_task_id={plan_task_id} does not resolve to any "
                    f"tasks.id.",
                    reason="plan_task_id_unresolved",
                )

        # --- verifier turn: a resolvable parent_handoff_id is required ---------
        if turn_role == _VERIFIER_ROLE:
            if parent_handoff_id is None:
                raise DispatchBindingError(
                    "a verifier turn requires a parent_handoff_id resolving to "
                    "the producer handoff it verifies; none was supplied.",
                    reason="verifier_requires_parent_handoff_id",
                )
            if not _handoff_exists(con, parent_handoff_id):
                raise DispatchBindingError(
                    f"parent_handoff_id={parent_handoff_id} does not resolve to "
                    f"any agent_contract_handoffs.id -- the verifier dispatch is "
                    f"rejected.",
                    reason="parent_handoff_id_unresolved",
                )
        elif parent_handoff_id is not None:
            # Present-but-optional parent for a non-verifier turn: existence only,
            # same clean-rejection rationale as the plan_task_id branch above.
            if not _handoff_exists(con, parent_handoff_id):
                raise DispatchBindingError(
                    f"parent_handoff_id={parent_handoff_id} does not resolve to "
                    f"any agent_contract_handoffs.id.",
                    reason="parent_handoff_id_unresolved",
                )
    finally:
        con.close()


def birth_dispatched_row(
    *,
    contract_id: str,
    agent_id: str,
    workspace: str,
    kind: "Optional[str]" = None,
    turn_role: "Optional[str]" = None,
    plan_task_id: "Optional[int]" = None,
    plan_id: "Optional[int]" = None,
    parent_handoff_id: "Optional[int]" = None,
    session_id: "Optional[str]" = None,
    brief_id: "Optional[int]" = None,
    db_path: "_pl.Path | None" = None,
) -> dict:
    """Validate the binding, then BIRTH the nascent DISPATCHED row.

    The dispatch-side counterpart to task 5's writer primitive: it runs
    :func:`validate_dispatch_binding` FIRST (raising ``DispatchBindingError`` and
    NOT touching the DB when the binding fails referential integrity), then
    stamps the binding into a nascent ``agent_state='DISPATCHED'`` row via
    ``gaia.store.writer.insert_dispatched_handoff``.

    Returns the writer's result dict (``status`` / ``created`` / ``handoff_id`` /
    ``contract_id``). Idempotency is inherited from the writer: a re-dispatch for
    the SAME contract_id is a no-op that never births a second row.
    """
    validate_dispatch_binding(
        kind=kind,
        turn_role=turn_role,
        plan_task_id=plan_task_id,
        parent_handoff_id=parent_handoff_id,
        db_path=db_path,
    )
    writer = _import_writer()
    return writer.insert_dispatched_handoff(
        contract_id=contract_id,
        agent_id=agent_id,
        workspace=workspace,
        plan_task_id=plan_task_id,
        plan_id=plan_id,
        parent_handoff_id=parent_handoff_id,
        kind=kind,
        session_id=session_id,
        brief_id=brief_id,
        db_path=db_path,
    )


def extract_dispatch_binding(metadata: "Mapping[str, Any]") -> dict:
    """Best-effort extraction of the binding from PreToolUse:Agent metadata.

    The orchestrator dispatches a subagent WITHOUT touching the CLI, so the
    binding coordinates must be read from the dispatch metadata (the Task tool
    parameters). This is a conservative parser -- it reads ``plan_id`` and
    ``task_id`` tokens from the dispatch prompt and infers ``turn_role`` from the
    target agent name -- and is intentionally forgiving: a coordinate it cannot
    find is left as None. The caller decides whether the resulting binding is
    complete enough to birth a row (the referential-integrity validation is the
    hard gate; this is only extraction).

    Returns a dict with keys ``plan_id``, ``plan_task_id``, ``kind``,
    ``turn_role`` -- any of which may be None.
    """
    import re as _re

    prompt = str(metadata.get("prompt", "") or "")
    agent = str(metadata.get("subagent_type") or metadata.get("agent_type") or "")

    def _int_token(pattern: str) -> "Optional[int]":
        m = _re.search(pattern, prompt)
        if not m:
            return None
        try:
            return int(m.group(1))
        except (TypeError, ValueError):
            return None

    plan_id = _int_token(r"plan_id\s*=\s*(\d+)")
    plan_task_id = _int_token(r"task_id\s*=\s*(\d+)")

    is_verifier = "verifier" in agent.lower()
    turn_role = _VERIFIER_ROLE if is_verifier else None
    kind = _VERIFIER_ROLE if is_verifier else _TASK_EXECUTION_KIND

    # A verifier turn is bound to the PRODUCER it verifies via parent_handoff_id,
    # NOT to a plan_task_id of its own (see module docstring + the finalize gate
    # in hooks/adapters/claude_code.py::_blind_verification_required). The verifier
    # prompt still MENTIONS the task_id it is verifying, so the `task_id=` token
    # extraction above would otherwise STAMP that plan_task_id onto the verifier's
    # nascent row -- which the plan_task_id-keyed blind-verification gate would then
    # read as "this turn is a plan-task-bound producer" and force its COMPLETE to
    # NEEDS_VERIFICATION. That is a DEADLOCK: the verifier could never promote the
    # increment because it would itself be sent back for verification forever.
    # Drop the plan_task_id for a verifier turn so it binds by parent_handoff_id
    # only and is treated as UNBOUND by the gate (free to self-COMPLETE / promote).
    if is_verifier:
        plan_task_id = None

    return {
        "plan_id": plan_id,
        "plan_task_id": plan_task_id,
        "kind": kind,
        "turn_role": turn_role,
    }
