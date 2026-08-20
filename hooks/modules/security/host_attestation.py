"""Gaia-side issuance and provenance resolution of OpenCode identity claims.

AC-11 requires OpenCode to propagate a *verifiable* control-plane identity. The
claim it propagated was a string the dispatching agent supplied: the plugin
interpolated a tool argument into an attestation and marked it verified, so
every consumer checked presence and none checked provenance. Issuance therefore
happens here, inside a Gaia-side process, from a nonce this module mints and
records; a value a caller can put in a tool argument resolves against no record.

The ledger is scoped to one host run so the control-plane binding is unique
within that run: the primary session takes it, and a child session whose
host-declared name is a control-plane spelling finds it already bound.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

ATTESTATION_SCHEME = "gaia-att:1:"

CONTROL_PLANE_ROLE = "gaia-orchestrator"

# Root plus two dispatched generations. The ceiling is declared here and
# enforced at issuance and again at resolution, so a record that predates a
# lowered ceiling stops resolving instead of being grandfathered in.
MAX_DELEGATION_DEPTH = 2

_LEDGER_DIR_ENV = "GAIA_OPENCODE_ATTESTATION_DIR"

_HOST_RUN_RE = re.compile(r"\A[A-Za-z0-9._-]{1,64}\Z")


class AttestationDenied(RuntimeError):
    """Issuance refused: the request cannot be granted from host state."""


@dataclass(frozen=True)
class Attestation:
    """One issued claim, as recorded by the issuing host process."""

    token: str
    session_id: str
    role: str
    issuer: str
    depth: int
    granted_by: str | None
    issued_at: str


def ledger_dir() -> Path:
    """Return the directory holding per-host-run attestation ledgers."""
    override = os.environ.get(_LEDGER_DIR_ENV)
    if override:
        return Path(override)
    try:
        from gaia.paths import state_dir
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
        from gaia.paths import state_dir
    return state_dir() / "opencode-attestations"


def ledger_path(host_run: str) -> Path:
    """Return the ledger file for one host run."""
    return ledger_dir() / f"{_host_run(host_run)}.json"


def issue(
    *,
    host_run: str,
    session_id: str,
    role: str,
    issuer: str,
    parent_attestation: str | None = None,
) -> Attestation:
    """Mint and record one attestation, or refuse the request.

    ``parent_attestation`` is the grantor's own token and must resolve in this
    run's ledger; the depth and grantor of the new record are read from that
    resolved parent rather than from the request, which is why a caller cannot
    ask for a shallower chain or a different grantor than the one it holds.
    """
    session_id = _required(session_id, "session_id")
    role = _required(role, "role")
    issuer = _required(issuer, "issuer")
    path = ledger_path(host_run)
    records = _load(path)

    existing = _record_for_session(records, session_id)
    if existing is not None:
        if existing.role != role or existing.issuer != issuer:
            raise AttestationDenied(
                f"session {session_id} is already attested as {existing.role}"
            )
        return existing

    if parent_attestation:
        parent = _lookup(records, parent_attestation)
        if parent is None:
            raise AttestationDenied(
                "the granting attestation does not resolve against host state"
            )
        if _is_control_plane(role):
            raise AttestationDenied(
                "an attested parent cannot mint an attested control-plane child"
            )
        depth = parent.depth + 1
        granted_by = parent.session_id
        if depth > MAX_DELEGATION_DEPTH:
            raise AttestationDenied(
                f"delegation depth {depth} exceeds the ceiling {MAX_DELEGATION_DEPTH}"
            )
    else:
        depth = 0
        granted_by = None
        if _is_control_plane(role):
            bound = _control_plane_holder(records)
            if bound is not None:
                raise AttestationDenied(
                    "a control-plane attestation is already bound to session "
                    f"{bound} in this host run"
                )

    issued = Attestation(
        token=ATTESTATION_SCHEME + secrets.token_hex(16),
        session_id=session_id,
        role=role,
        issuer=issuer,
        depth=depth,
        granted_by=granted_by,
        issued_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    records[issued.token] = issued
    _store(path, records)
    return issued


def resolve(
    *,
    host_run: str,
    token: object,
    session_id: str,
    role: str,
    issuer: str,
) -> Attestation | None:
    """Return the recorded claim this token stands for, or ``None``.

    Every field of the presented claim is compared against the record the
    issuing process wrote, so a well-formed claim a caller composed resolves to
    nothing: the nonce is not in the ledger, and there is no shape to satisfy.
    """
    if not isinstance(token, str) or not token:
        return None
    record = _lookup(_load(ledger_path(host_run)), token)
    if record is None:
        return None
    if (record.session_id, record.role, record.issuer) != (session_id, role, issuer):
        return None
    if record.depth > MAX_DELEGATION_DEPTH:
        return None
    if _is_control_plane(record.role) and (record.depth != 0 or record.granted_by):
        return None
    return record


def _is_control_plane(role: str) -> bool:
    return role.strip().lower() == CONTROL_PLANE_ROLE


def _required(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AttestationDenied(f"{field} is required to attest an OpenCode claim")
    return value.strip()


def _host_run(host_run: object) -> str:
    """Reduce the host run identifier to a safe, single ledger file name."""
    candidate = str(host_run or "").strip()
    return candidate if _HOST_RUN_RE.match(candidate) else "unscoped"


def _load(path: Path) -> dict[str, Attestation]:
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    records = raw.get("records") if isinstance(raw, dict) else None
    if not isinstance(records, dict):
        return {}
    loaded: dict[str, Attestation] = {}
    for token, value in records.items():
        if not isinstance(value, dict):
            continue
        try:
            loaded[token] = Attestation(
                token=token,
                session_id=value["session_id"],
                role=value["role"],
                issuer=value["issuer"],
                depth=int(value["depth"]),
                granted_by=value.get("granted_by"),
                issued_at=value.get("issued_at", ""),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return loaded


def _store(path: Path, records: dict[str, Attestation]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "records": {
            token: {k: v for k, v in asdict(record).items() if k != "token"}
            for token, record in records.items()
        }
    }
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    os.replace(temp, path)


def _lookup(records: dict[str, Attestation], token: str) -> Attestation | None:
    return records.get(token) if token.startswith(ATTESTATION_SCHEME) else None


def _record_for_session(
    records: dict[str, Attestation], session_id: str
) -> Attestation | None:
    for record in records.values():
        if record.session_id == session_id:
            return record
    return None


def _control_plane_holder(records: dict[str, Attestation]) -> str | None:
    for record in records.values():
        if _is_control_plane(record.role):
            return record.session_id
    return None
