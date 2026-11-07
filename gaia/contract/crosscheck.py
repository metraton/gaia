"""
Cross-check layer (layer 2) -- gaia.db-backed approval_id / nonce resolution.

This module is the SECOND layer of the two-layer validator core. Layer 1
(``gaia.contract.validator``) validates an envelope by SHAPE ONLY and
deliberately EXCLUDES ``approval_request`` / nonce validation -- that
cross-check is this module's job.

What it does:
    When an envelope's ``approval_request.approval_id`` does NOT resolve to a
    row with ``status == 'pending'`` in the ``approvals`` table of gaia.db,
    this layer REJECTS it with the named code ``APPROVAL_ID_NOT_PENDING``.

Graceful degradation:
    When gaia.db is absent, this layer is a NO-OP: it never creates the
    database as a side effect of validating (a plain ``Path.exists()`` check
    gates every DB touch), and it reports ``skipped=True`` / ``ok=True`` so
    the FORM layer alone continues to gate the envelope. Cross-check absence
    is not itself a rejection reason.

Agnosticism:
    This module imports nothing under ``hooks/``. Besides the standard library
    and ``gaia.contract.validator`` it imports ``gaia.paths`` (DB path
    resolution), ``gaia.approvals.store`` (the canonical read API for the
    ``approvals`` table) and ``gaia.approvals.reading`` (the derived state of a
    non-pending row), each imported inside the function that uses it.

Read-only by construction:
    The DB is opened with a ``mode=ro`` URI connection -- this layer NEVER
    writes to gaia.db. Combined with the existence gate above, a missing
    gaia.db is never brought into being by running a cross-check.

Public surface:
    validate_crosscheck(envelope, *, db_path=None) -> CrossCheckResult
    validate(envelope, *, db_path=None, source="declaration") -> EnvelopeValidationResult
    CrossCheckErrorCode.APPROVAL_ID_NOT_PENDING
    CrossCheckError, CrossCheckResult, EnvelopeValidationResult
    CROSSCHECK_REPAIR_MESSAGE

``source`` is forwarded to layer 1 (``gaia.contract.validator.validate_form``)
unchanged and never read by this module -- it selects which of the two
canonical repair messages a shape failure gets, "declaration" (the agent's
own final fence) or "row" (the turn's persisted dispatch row); see the
row-first SubagentStop gate, ``hooks/adapters/subagent_stop_core.py::
resolve_subagent_stop_gate``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, List, Optional, Tuple

from gaia.contract.validator import FormValidationResult, validate_form

# The lowercase 'pending' status column value on the `approvals` table
# (schema.sql CHECK: 'pending' 'approved' 'rejected' 'revoked' 'expired').
# NOT to be confused with the UPPERCASE PENDING used by the unrelated
# `approval_grants` (T3 command_set) table; the two columns never collapse.
# The agent_contract_handoff `approval_request.approval_id` field
# is a `P-{uuid4_hex}` id that resolves against `approvals`, not
# `approval_grants`.
_PENDING_STATUS = "pending"


class CrossCheckErrorCode(str, Enum):
    """Named, stable error code emitted by the cross-check layer.

    ``str`` mixin: round-trips through JSON/CLI output like FormErrorCode.
    """

    APPROVAL_ID_NOT_PENDING = "APPROVAL_ID_NOT_PENDING"
    NEEDS_VERIFICATION_UNBOUND = "NEEDS_VERIFICATION_UNBOUND"


@dataclass(frozen=True)
class CrossCheckError:
    """A single cross-check violation. Duck-type compatible with FormError
    (same code/field/detail/__str__ shape) so callers can concatenate both
    error sequences without a type check."""

    code: CrossCheckErrorCode
    field: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover -- convenience only
        return f"{self.code.value} [{self.field}]: {self.detail}"


CROSSCHECK_REPAIR_MESSAGE = (
    "Repair: approval_request.approval_id must be the EXACT id relayed "
    "verbatim in the [T3_BLOCKED] denial (format P-{uuid4_hex} or the "
    "content-derived COMMAND_SET form) for a grant that is STILL PENDING in "
    "gaia.db. Do not invent an id, reuse one already approved/rejected/"
    "revoked/expired, or relay a stale id from a different turn."
)


UNBOUND_VERIFICATION_REPAIR_MESSAGE = (
    "Repair: NEEDS_VERIFICATION belongs to a plan-task-bound turn, whose task "
    "gate an independent verifier settles. This turn carries no plan_task_id "
    "and no parent_handoff_id, so nothing would ever verify or settle it. "
    "Close COMPLETE with a verification you can stand behind (type none when "
    "no oracle was required, self_review with reviewed, or a command this turn "
    "ran), or BLOCKED / NEEDS_INPUT naming why not. Executing a plan task? "
    "Finalize with --plan-task-id <id>."
)


@dataclass(frozen=True)
class CrossCheckResult:
    """Outcome of layer-2 (cross-check) validation.

    Attributes:
        ok: True when there is nothing to check (no approval_id present),
            the id resolves to a pending grant, or the check was skipped
            because gaia.db is absent (graceful degrade).
        errors: tuple of CrossCheckError; non-empty only when ``ok`` is False.
        checked: True iff an approval_id was present AND gaia.db was
            queried (skipped runs, and runs with no approval_id, are False).
        skipped: True iff an approval_id was present but gaia.db does not
            exist -- the graceful-fallback path.
        repair_message: CROSSCHECK_REPAIR_MESSAGE when ``ok`` is False, else
            empty string.
    """

    ok: bool
    errors: Tuple[CrossCheckError, ...] = ()
    checked: bool = False
    skipped: bool = False
    repair_message: str = ""


@dataclass(frozen=True)
class EnvelopeValidationResult:
    """Full-verdict result: layer 1 (form) composed with layer 2 (cross-check).

    Layer 2 only runs when layer 1 passes -- a shape-invalid envelope's
    ``approval_request`` cannot be trusted enough to cross-check, so
    ``crosscheck`` is a no-op CrossCheckResult(ok=True, checked=False) in
    that case (its own error, if any, is never masked -- there simply isn't
    one to report).
    """

    ok: bool
    form: FormValidationResult
    crosscheck: CrossCheckResult

    @property
    def errors(self) -> List[Any]:
        """Combined form + cross-check errors, form first."""
        return list(self.form.errors) + list(self.crosscheck.errors)

    def error_summary(self) -> str:
        return "; ".join(str(err) for err in self.errors)


# ---------------------------------------------------------------------------
# DB path resolution + read-only lookup
# ---------------------------------------------------------------------------

def _default_db_path() -> Path:
    """Resolve gaia.db's path via gaia.paths (the same SSOT gaia.store.writer
    uses), imported lazily so this module carries no import-time dependency
    on the paths package -- only the caller that omits ``db_path`` pays for
    it. Falls back to the documented default location if gaia.paths is
    unavailable for any reason (keeps this layer resilient, never crashing
    validation over a resolution failure)."""
    try:
        from gaia.paths import db_path as _gaia_db_path

        return _gaia_db_path()
    except Exception:
        return Path.home() / ".gaia" / "gaia.db"


def _extract_approval_id(envelope: Any) -> Optional[str]:
    """Pull ``approval_request.approval_id`` out of the envelope, or None.

    None is returned for: a non-dict envelope, a missing/non-dict
    approval_request, or an absent/blank approval_id -- all of which mean
    "nothing for layer 2 to check" rather than a violation (layer 1 owns
    envelope shape).
    """
    if not isinstance(envelope, dict):
        return None
    approval_request = envelope.get("approval_request")
    if not isinstance(approval_request, dict):
        return None
    raw = approval_request.get("approval_id")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _lookup_status(db_path: Path, approval_id: str) -> Optional[str]:
    """Read-only lookup of ``approval_id``: 'pending', or the derived state it left pending in.

    A decided or withdrawn row is named by ``gaia.approvals.reading``, so a
    replaced or expired request is not reported as revoked. Opens the
    connection in ``mode=ro`` URI form so this layer NEVER creates or mutates
    gaia.db -- pure observation. Returns None when the row, the ``approvals``
    table, or the database itself is not queryable (all collapse to "does not
    resolve to a pending grant").
    """
    uri = f"file:{db_path}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        return None
    try:
        from gaia.approvals.reading import decision_state
        from gaia.approvals.store import get_by_id, get_history

        row = get_by_id(approval_id, con=con)
        if row is None or row["status"] == _PENDING_STATUS:
            return row["status"] if row else None
        return decision_state(row, get_history(approval_id, con=con))
    except sqlite3.OperationalError:
        # e.g. a foreign/older gaia.db that predates the `approvals` table.
        return None
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def validate_crosscheck(
    envelope: Any, *, db_path: Optional[Path] = None
) -> CrossCheckResult:
    """Validate ``approval_request.approval_id`` against gaia.db (layer 2).

    Args:
        envelope: the already-parsed contract dict (same input shape as
            ``gaia.contract.validator.validate_form``).
        db_path: optional explicit path to gaia.db (used by tests). When
            None, resolves via ``gaia.paths.db_path()``.

    Returns:
        CrossCheckResult. ``ok`` is True when there is nothing to check, the
        approval_id resolves to a pending grant, or gaia.db is absent
        (graceful degrade). ``ok`` is False only when gaia.db exists
        AND the approval_id fails to resolve to a pending row.
    """
    approval_id = _extract_approval_id(envelope)
    if approval_id is None:
        return CrossCheckResult(ok=True, checked=False)

    resolved = Path(db_path) if db_path is not None else _default_db_path()
    if not resolved.exists():
        # Graceful fallback: no gaia.db -- the form layer stands alone,
        # and this layer never brings the DB into existence to check it.
        return CrossCheckResult(ok=True, checked=False, skipped=True)

    status = _lookup_status(resolved, approval_id)
    if status == _PENDING_STATUS:
        return CrossCheckResult(ok=True, checked=True)

    if status is None:
        detail = (
            f"approval_id {approval_id!r} does not resolve to any approval "
            "row in gaia.db"
        )
    else:
        detail = f"approval_id {approval_id!r} is {status}, not pending"

    error = CrossCheckError(
        code=CrossCheckErrorCode.APPROVAL_ID_NOT_PENDING,
        field="approval_request.approval_id",
        detail=detail,
    )
    return CrossCheckResult(
        ok=False,
        errors=(error,),
        checked=True,
        repair_message=CROSSCHECK_REPAIR_MESSAGE,
    )


def _chain_binding(db_path: Path, contract_id: str) -> Tuple[bool, bool]:
    """Whether *contract_id*'s row or any link it continues carries a plan_task_id, and a parent_handoff_id."""
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return False, False
    try:
        row = con.execute(
            "SELECT plan_task_id, parent_handoff_id, continues_handoff_id "
            "FROM agent_contract_handoffs WHERE contract_id = ?",
            (contract_id,),
        ).fetchone()
        has_task = has_parent = False
        seen = set()
        while row is not None:
            has_task = has_task or row[0] is not None
            has_parent = has_parent or row[1] is not None
            if row[2] is None or row[2] in seen:
                break
            seen.add(row[2])
            row = con.execute(
                "SELECT plan_task_id, parent_handoff_id, continues_handoff_id "
                "FROM agent_contract_handoffs WHERE id = ?",
                (row[2],),
            ).fetchone()
        return has_task, has_parent
    except sqlite3.OperationalError:
        return False, False
    finally:
        con.close()


def validate_verification_binding(
    envelope: Any,
    *,
    contract_id: str,
    plan_task_id: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> CrossCheckResult:
    """Refuse a NEEDS_VERIFICATION close with no plan_task_id and no parent_handoff_id; skipped without gaia.db."""
    status = envelope.get("agent_status") if isinstance(envelope, dict) else None
    if not isinstance(status, dict) or status.get("agent_state") != "NEEDS_VERIFICATION":
        return CrossCheckResult(ok=True, checked=False)
    if plan_task_id is not None:
        return CrossCheckResult(ok=True, checked=True)
    resolved = Path(db_path) if db_path is not None else _default_db_path()
    if not resolved.exists():
        return CrossCheckResult(ok=True, checked=False, skipped=True)
    has_task, has_parent = _chain_binding(resolved, contract_id)
    if has_task or has_parent:
        return CrossCheckResult(ok=True, checked=True)
    error = CrossCheckError(
        code=CrossCheckErrorCode.NEEDS_VERIFICATION_UNBOUND,
        field="agent_status.agent_state",
        detail=(
            f"contract {contract_id!r} closes NEEDS_VERIFICATION with no "
            "plan_task_id and no parent_handoff_id"
        ),
    )
    return CrossCheckResult(
        ok=False,
        errors=(error,),
        checked=True,
        repair_message=UNBOUND_VERIFICATION_REPAIR_MESSAGE,
    )


def validate(
    envelope: Any,
    *,
    db_path: Optional[Path] = None,
    source: str = "declaration",
    contract_id: Optional[str] = None,
    plan_task_id: Optional[int] = None,
) -> EnvelopeValidationResult:
    """Full-verdict validation: layer 1 (form) gates layer 2 (cross-check).

    This is the single call a caller (CLI validate-on-write, the finalize
    writer, the hook full-verdict gate) makes to get the complete verdict
    without composing the two layers itself.

    Args:
        envelope: the already-parsed contract dict.
        db_path: optional explicit path to gaia.db (used by tests).
        source: forwarded verbatim to ``validate_form`` -- ``"declaration"``
            (default) or ``"row"``. Purely a wording choice for layer 1's
            MISSING_FIELD detail and repair message; never a validation input,
            and irrelevant to layer 2, which does not read it.
        contract_id, plan_task_id: passed by finalize only, to add ``validate_verification_binding``.

    Returns:
        EnvelopeValidationResult. ``ok`` is True only when BOTH layers pass.
        When layer 1 fails, layer 2 is not evaluated (an untrustworthy shape
        makes its approval_request unreliable to cross-check) and
        ``crosscheck`` is reported as a no-op result.
    """
    form_result = validate_form(envelope, source=source)
    if not form_result.ok:
        return EnvelopeValidationResult(
            ok=False,
            form=form_result,
            crosscheck=CrossCheckResult(ok=True, checked=False),
        )

    crosscheck_result = validate_crosscheck(envelope, db_path=db_path)
    if crosscheck_result.ok and contract_id:
        crosscheck_result = validate_verification_binding(
            envelope, contract_id=contract_id, plan_task_id=plan_task_id, db_path=db_path,
        )
    return EnvelopeValidationResult(
        ok=crosscheck_result.ok,
        form=form_result,
        crosscheck=crosscheck_result,
    )


__all__ = [
    "CrossCheckErrorCode",
    "CrossCheckError",
    "CrossCheckResult",
    "EnvelopeValidationResult",
    "CROSSCHECK_REPAIR_MESSAGE",
    "UNBOUND_VERIFICATION_REPAIR_MESSAGE",
    "validate_crosscheck",
    "validate_verification_binding",
    "validate",
]
