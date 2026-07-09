"""
Cross-check layer (layer 2) -- gaia.db-backed approval_id / nonce resolution.

This module is the SECOND layer of the two-layer validator core (brief:
contract-as-managed-data-agent-contract-handoff-agnostico-por-cli, decision
#2). Layer 1 (``gaia.contract.validator``) validates an envelope by SHAPE
ONLY and deliberately EXCLUDES ``approval_request`` / nonce validation --
that cross-check is this module's job.

What it does (AC-3):
    When an envelope's ``approval_request.approval_id`` does NOT resolve to a
    row with ``status == 'pending'`` in the ``approvals`` table of gaia.db,
    this layer REJECTS it with the named code ``APPROVAL_ID_NOT_PENDING``.
    This revives the DEAD ``nonce_issue`` variable in
    ``hooks/modules/agents/contract_validator.py::validate_approval_request``
    (hardcoded to ``None`` -- never actually checked against a real grant).

Graceful degradation (AC-3):
    When gaia.db is absent, this layer is a NO-OP: it never creates the
    database as a side effect of validating (a plain ``Path.exists()`` check
    gates every DB touch), and it reports ``skipped=True`` / ``ok=True`` so
    the FORM layer alone continues to gate the envelope. Cross-check absence
    is not itself a rejection reason.

Agnosticism (decision #1):
    gaia.db is Gaia's OWN substrate, not the harness's -- consulting it does
    not violate agnosticism. What IS forbidden, and enforced by never
    importing anything under ``hooks/`` from this module, is coupling to the
    Claude Code harness. This module imports only the standard library plus
    two other gaia-substrate modules that are themselves harness-free:
    ``gaia.paths`` (DB path resolution) and ``gaia.approvals.store``
    (the canonical read API for the ``approvals`` table).

Read-only by construction:
    The DB is opened with a ``mode=ro`` URI connection -- this layer NEVER
    writes to gaia.db. Combined with the existence gate above, a missing
    gaia.db is never brought into being by running a cross-check.

Public surface (stable for T4/T9/T16 -- the CLI validate-on-write path, the
finalize store writer, and the hook full-verdict gate all consume this):
    validate_crosscheck(envelope, *, db_path=None) -> CrossCheckResult
    validate(envelope, *, db_path=None)            -> EnvelopeValidationResult
    CrossCheckErrorCode.APPROVAL_ID_NOT_PENDING
    CrossCheckError, CrossCheckResult, EnvelopeValidationResult
    CROSSCHECK_REPAIR_MESSAGE
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
# `approval_grants` (T3 command_set) table -- see agent-approval-protocol
# SKILL.md "Status vocabularies -- distinct columns, opposite casing, never
# collapse". The agent_contract_handoff `approval_request.approval_id` field
# is a `P-{uuid4_hex}` id that resolves against `approvals`, not
# `approval_grants`.
_PENDING_STATUS = "pending"


class CrossCheckErrorCode(str, Enum):
    """Named, stable error code emitted by the cross-check layer (AC-3).

    ``str`` mixin: round-trips through JSON/CLI output like FormErrorCode.
    """

    APPROVAL_ID_NOT_PENDING = "APPROVAL_ID_NOT_PENDING"


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


@dataclass(frozen=True)
class CrossCheckResult:
    """Outcome of layer-2 (cross-check) validation.

    Attributes:
        ok: True when there is nothing to check (no approval_id present),
            the id resolves to a pending grant, or the check was skipped
            because gaia.db is absent (graceful degrade -- AC-3).
        errors: tuple of CrossCheckError; non-empty only when ``ok`` is False.
        checked: True iff an approval_id was present AND gaia.db was
            queried (skipped runs, and runs with no approval_id, are False).
        skipped: True iff an approval_id was present but gaia.db does not
            exist -- the graceful-fallback path required by AC-3.
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
    """Read-only lookup of ``approvals.status`` for ``approval_id``.

    Opens the connection in ``mode=ro`` URI form so this layer NEVER creates
    or mutates gaia.db -- pure observation. Returns None when the row, the
    ``approvals`` table, or the database itself is not queryable (all
    collapse to "does not resolve to a pending grant").
    """
    uri = f"file:{db_path}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        return None
    try:
        from gaia.approvals.store import get_by_id

        row = get_by_id(approval_id, con=con)
        return row["status"] if row else None
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
        (graceful degrade, AC-3). ``ok`` is False only when gaia.db exists
        AND the approval_id fails to resolve to a pending row.
    """
    approval_id = _extract_approval_id(envelope)
    if approval_id is None:
        return CrossCheckResult(ok=True, checked=False)

    resolved = Path(db_path) if db_path is not None else _default_db_path()
    if not resolved.exists():
        # AC-3 graceful fallback: no gaia.db -- the form layer stands alone,
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
        detail = (
            f"approval_id {approval_id!r} resolves to status {status!r}, "
            "not 'pending'"
        )

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


def validate(
    envelope: Any, *, db_path: Optional[Path] = None
) -> EnvelopeValidationResult:
    """Full-verdict validation: layer 1 (form) gates layer 2 (cross-check).

    This is the single call a caller (CLI validate-on-write, the finalize
    writer, the hook full-verdict gate) makes to get the complete verdict
    without composing the two layers itself.

    Args:
        envelope: the already-parsed contract dict.
        db_path: optional explicit path to gaia.db (used by tests).

    Returns:
        EnvelopeValidationResult. ``ok`` is True only when BOTH layers pass.
        When layer 1 fails, layer 2 is not evaluated (an untrustworthy shape
        makes its approval_request unreliable to cross-check) and
        ``crosscheck`` is reported as a no-op result.
    """
    form_result = validate_form(envelope)
    if not form_result.ok:
        return EnvelopeValidationResult(
            ok=False,
            form=form_result,
            crosscheck=CrossCheckResult(ok=True, checked=False),
        )

    crosscheck_result = validate_crosscheck(envelope, db_path=db_path)
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
    "validate_crosscheck",
    "validate",
]
