"""Host-neutral approval core: seal, present, decide, consume and withdraw a signature.

Every host adapter (Claude Code, OpenCode) and the approvals CLI reach the T3
consent cycle through this module, so the invariants below have one owner:

* one constructor, :func:`seal_request`, seals every request -- reactive Bash,
  plan-first COMMAND_SET and protected file write -- with a what-it-does phrase,
  a single window of :data:`WINDOW_MINUTES` counted from the decision, the
  requesting session and agent, and per item its directory, declared non-zero
  exits, position and fingerprint;
* one key per request (``request_key``) and one per item (:func:`command_key`);
* a decision counts only when it is a structured option answering a recorded
  presentation (:func:`record_presentation` then :func:`decide`), and the
  resulting grant is bound to the requesting session and agent;
* an item matches only in its sealed directory, byte for byte, at the expected
  index (:func:`match_command`), and a declared non-zero exit advances the set
  (:func:`close_command`);
* a call closes only from its own terminal event, correlated by tool_use_id
  (:func:`close_call`); a call with no terminal event has no result;
* withdrawal rejects or revokes and never approves (:func:`withdraw`).

The window, the reuse bound and the persisted states stay expressed through the
existing ``approvals`` / ``approval_grants`` / ``approval_events`` tables: no
event type is added to the ``approval_events`` CHECK.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from gaia.store.writer import APPROVAL_WINDOW_MINUTES as WINDOW_MINUTES

DECISION_OPTIONS = frozenset({"approve", "reject", "details"})
_COMMAND_KINDS = frozenset({"command", "command_set"})
_FILE_KIND = "file_write"
_MAX_EXIT_CODE = 255


class SealError(ValueError):
    """Raised when a request cannot be sealed as it stands."""


class RequesterError(SealError):
    """Raised when the host event does not name the session or agent a request binds to."""


class WithdrawError(ValueError):
    """Raised when a withdrawal asks for anything other than reject or revoke."""


@dataclass(frozen=True)
class DecisionResult:
    """Outcome of one structured answer: ``activated``, ``rejected`` or ``no_decision``."""

    status: str
    approval_id: Optional[str] = None
    reason: str = ""


def _ensure_hooks_importable() -> None:
    hooks_dir = str(Path(__file__).resolve().parents[2] / "hooks")
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)


def command_key(position: int, fingerprint: str) -> str:
    """Return the per-item key: its position inside the request and its byte fingerprint."""
    return f"{position}:{fingerprint}"


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SealError(f"{name} is required to seal a request")
    return value.strip()


def _optional_text(value: object) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def resolve_requester(session_id: object, agent_id: object) -> dict:
    """Name the requester of a request, or of a grant lookup, from the host event alone.

    The one resolver sealing and grant lookup share. Nothing is borrowed from
    the environment and nothing is defaulted: a main-session call arrives with
    the explicit primary identity its adapter names as ``agent_id``.
    """
    session = _optional_text(session_id)
    if session is None:
        raise RequesterError(
            "the host event carries no session, so the request cannot be bound to its requester"
        )
    agent = _optional_text(agent_id)
    if agent is None:
        raise RequesterError(
            "the host event carries no agent, so the request cannot be bound to its requester"
        )
    return {"session_id": session, "agent_id": agent}


def _expected_exits(raw: object, position: int) -> list[int]:
    codes = list(raw or [])
    for code in codes:
        if isinstance(code, bool) or not isinstance(code, int) or not 1 <= code <= _MAX_EXIT_CODE:
            raise SealError(
                f"item {position}: expected exit codes must be non-zero integers 1-{_MAX_EXIT_CODE}"
            )
    return sorted(set(codes))


def _seal_items(kind: str, items: Iterable[Mapping[str, Any]]) -> list[dict]:
    from gaia.approvals.command_set import command_fingerprint

    sealed: list[dict] = []
    for position, raw in enumerate(items):
        if kind == _FILE_KIND:
            target = _required_text(raw.get("path"), f"item {position} path")
            cwd = os.path.dirname(target)
            expect_exit: list[int] = []
            item = {"path": target}
        else:
            target = raw.get("command")
            if not isinstance(target, str) or not target or target != target.strip():
                raise SealError(f"item {position}: command must be a non-empty exact string")
            cwd = raw.get("cwd")
            expect_exit = _expected_exits(raw.get("expect_exit"), position)
            item = {"command": target, "rationale": raw.get("rationale") or ""}
        for phrase in ("does", "impact"):
            if _optional_text(raw.get(phrase)):
                item[phrase] = raw[phrase].strip()
        if not isinstance(cwd, str) or not os.path.isabs(cwd):
            raise SealError(f"item {position}: cwd must be an absolute directory")
        fingerprint = command_fingerprint(target)
        item.update(
            cwd=cwd,
            expect_exit=expect_exit,
            position=position,
            fingerprint=fingerprint,
            key=command_key(position, fingerprint),
        )
        sealed.append(item)
    if not sealed:
        raise SealError("a request needs at least one item")
    return sealed


def seal_request(
    kind: str,
    items: Iterable[Mapping[str, Any]],
    *,
    what: str,
    session_id: str,
    agent_id: str,
    question: Optional[str] = None,
    rollback: Optional[str] = None,
    verification: Optional[str] = None,
    impact: Optional[str] = None,
    rationale: Optional[str] = None,
    operation: Optional[str] = None,
    risk_level: str = "medium",
) -> dict:
    """Build the sealed payload for any request kind: ``command``, ``command_set`` or ``file_write``.

    ``what`` is the signature's title and ``question`` its question; each item
    may carry ``does`` and ``impact``. All four are checked against the
    signature surface limits here, when the request is made
    (``surface.SurfaceLimitError``).

    ``operation`` is required for ``command`` (the reactive Bash block): it is
    the ``<CATEGORY> command intercepted: <verb>`` line activation reads to
    rebuild the semantic signature.
    """
    from gaia.approvals.command_set import request_fingerprint
    from gaia.approvals.surface import check_phrases

    if kind not in _COMMAND_KINDS and kind != _FILE_KIND:
        raise SealError(f"unknown request kind {kind!r}")
    what_text = _required_text(what, "what")
    question_text = _optional_text(question)
    requester = resolve_requester(session_id, agent_id)
    sealed = _seal_items(kind, items)
    check_phrases(title=what_text, question=question_text, items=sealed)
    targets = [item.get("command") or item["path"] for item in sealed]
    request_key = request_fingerprint(targets)

    payload: dict[str, Any] = {
        "what": what_text,
        "question": question_text,
        "window_minutes": WINDOW_MINUTES,
        "window_starts": "decision",
        "requested_by": requester,
        "items": sealed,
        "request_key": request_key,
        "commands": targets,
        "exact_content": "\n".join(targets) if kind == "command_set" else targets[0],
        "rollback_hint": _optional_text(rollback),
        "verification": _optional_text(verification),
        "impact": _optional_text(impact),
        "rationale": _optional_text(rationale) or what_text,
        "risk_level": risk_level,
    }
    if kind == "command_set":
        payload.update(
            request_type="COMMAND_SET",
            operation="Execute an ordered T3 command set",
            command_set=sealed,
            request_fingerprint=request_key,
            scope="COMMAND_SET",
            risk_level="high",
        )
    elif kind == "command":
        payload.update(
            operation=_required_text(operation, "operation"),
            scope=targets[0].split()[0],
        )
        if len(sealed) > 1:
            payload["command_set"] = sealed
    else:
        _ensure_hooks_importable()
        from modules.security.approval_scopes import SCOPE_FILE_PATH, build_file_path_signature

        signature = build_file_path_signature(targets[0])
        if signature is None:
            raise SealError("file_write: could not build the file-path signature")
        payload.update(
            operation="FILE_WRITE command intercepted: write",
            scope=SCOPE_FILE_PATH,
            scope_signature=signature.to_dict(),
        )
    return payload


# --------------------------------------------------------------------------- #
# Request
# --------------------------------------------------------------------------- #

def request_command_set(
    items: list[Mapping[str, Any]],
    *,
    what: str,
    session_id: str,
    agent_id: str,
    question: Optional[str] = None,
    rollback: Optional[str] = None,
    verification: Optional[str] = None,
    rationale: Optional[str] = None,
) -> str:
    """Validate a plan-first set, seal it and persist the pending request; return its approval_id."""
    from gaia.approvals import store
    from gaia.approvals.command_set import CommandSetValidationError, validate_request_set

    commands = [item.get("command") for item in items]
    try:
        validate_request_set(commands)
    except CommandSetValidationError as exc:
        raise SealError(str(exc)) from exc
    payload = seal_request(
        "command_set", items, what=what, session_id=session_id, agent_id=agent_id,
        question=question, rollback=rollback, verification=verification, rationale=rationale,
    )
    return store.insert_requested(payload, agent_id=agent_id, session_id=session_id)


def _pending_file_request(path: str, requester: Mapping[str, str]) -> Optional[str]:
    """Return the requester's own pending write request for ``path`` inside the reuse bound."""
    from gaia.approvals.store import PENDING_REUSE_WINDOW_MINUTES, list_pending

    for row in list_pending(all_sessions=True):
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if (
            payload.get("operation") == "FILE_WRITE command intercepted: write"
            and payload.get("exact_content") == path
            and payload.get("requested_by") == dict(requester)
            and float(row.get("age_seconds") or 0.0) <= PENDING_REUSE_WINDOW_MINUTES * 60
        ):
            return row["id"]
    return None


def file_write_title(path: str) -> str:
    """The default title of a protected write: the file's name, never its full path.

    The full path is already listed under the surface's files, and a deep path
    (a worktree, a test's temporary directory) would not fit the title limit.
    """
    return f"Modificar el archivo protegido {os.path.basename(path)}."


def request_file_write(
    path: str,
    *,
    session_id: str,
    agent_id: str,
    what: Optional[str] = None,
    question: Optional[str] = None,
    rollback: Optional[str] = None,
    verification: Optional[str] = None,
    impact: Optional[str] = None,
) -> str:
    """Seal and persist a protected-path write request, reusing the requester's open one."""
    from gaia.approvals import store

    requester = {"session_id": session_id, "agent_id": agent_id}
    existing = _pending_file_request(path, requester)
    if existing:
        return existing
    payload = seal_request(
        _FILE_KIND, [{"path": path, "impact": impact}],
        what=what or file_write_title(path),
        session_id=session_id, agent_id=agent_id, question=question,
        rollback=rollback, verification=verification, impact=impact,
    )
    return store.insert_requested(payload, agent_id=agent_id, session_id=session_id)


def protected_write_verdict(file_path: str, *, session_id: str, agent_id: str) -> dict:
    """Decide a Write/Edit on ``file_path``: ``allow`` or ``block`` with the approval to decide.

    A protected path is allowed only by a live file grant bound to this same
    session and agent; otherwise the requester's pending request is named
    (minted on first sight). Raises when the request cannot be persisted, so the
    host can fall back to its own consent dialog.
    """
    _ensure_hooks_importable()
    from modules.security.protected_paths import is_protected_hook_path, resolved_write_target
    from gaia.store.writer import check_db_file_path_grant

    consent_path = resolved_write_target(file_path)
    if not is_protected_hook_path(file_path):
        return {"decision": "allow", "path": consent_path, "protected": False}
    grant = check_db_file_path_grant(consent_path)
    if grant is not None and _grant_bound_to(grant, session_id, agent_id):
        return {"decision": "allow", "path": consent_path, "protected": True,
                "approval_id": grant.get("approval_id")}
    approval_id = request_file_write(consent_path, session_id=session_id, agent_id=agent_id)
    return {"decision": "block", "path": consent_path, "protected": True,
            "approval_id": approval_id, "window_minutes": WINDOW_MINUTES}


def _grant_bound_to(grant: Mapping[str, Any], session_id: str, agent_id: str) -> bool:
    """A grant carrying a requester binds to it; a pre-binding grant keeps its old reach."""
    bound_agent = grant.get("agent_id")
    if not bound_agent:
        return True
    return bound_agent == agent_id and grant.get("session_id") == session_id


# --------------------------------------------------------------------------- #
# Present and decide
# --------------------------------------------------------------------------- #

def record_presentation(
    approval_id: str,
    *,
    native_ref: str,
    session_id: str,
    agent_id: str,
    position: int = 0,
) -> None:
    """Record that ``approval_id`` was shown at ``position`` of the host question ``native_ref``."""
    from gaia.approvals import store

    row = store.get_by_id(approval_id)
    if row is None or row.get("status") != "pending":
        raise ValueError(f"cannot present {approval_id!r}: not a pending approval")
    store.record_event(
        approval_id, "SHOWN", agent_id=agent_id, session_id=session_id,
        metadata_json=json.dumps(
            {"native_ref": _required_text(native_ref, "native_ref"), "position": position},
            sort_keys=True,
        ),
    )


def _presented_approval(native_ref: str, position: int) -> Optional[str]:
    from gaia.approvals.store import _open_db

    con = _open_db()
    try:
        row = con.execute(
            "SELECT approval_id FROM approval_events WHERE event_type = 'SHOWN' "
            "AND json_extract(metadata_json, '$.native_ref') = ? "
            "AND json_extract(metadata_json, '$.position') = ? "
            "ORDER BY id DESC LIMIT 1",
            (native_ref, position),
        ).fetchone()
    finally:
        con.close()
    return row[0] if row else None


def decide(
    *,
    native_ref: str,
    session_id: str,
    option_key: Optional[str] = None,
    free_text: Optional[str] = None,
    position: int = 0,
) -> DecisionResult:
    """Apply one structured answer to the request presented at ``native_ref``/``position``.

    Free text and ``details`` never decide. ``approve`` activates the grant
    bound to the requesting session and agent, never to whoever answered.
    """
    from gaia.approvals import store

    if option_key not in DECISION_OPTIONS:
        reason = "free text never decides" if free_text is not None else "no option was selected"
        return DecisionResult("no_decision", reason=reason)
    approval_id = _presented_approval(native_ref, position)
    if approval_id is None:
        return DecisionResult("no_decision", reason="no recorded presentation for this answer")
    if option_key == "details":
        return DecisionResult("no_decision", approval_id, "details does not decide")
    if option_key == "reject":
        try:
            store.reject(approval_id, session_id)
        except ValueError as exc:
            return DecisionResult("no_decision", approval_id, str(exc))
        return DecisionResult("rejected", approval_id)

    row = store.get_by_id(approval_id) or {}
    binding = {
        "session_id": row.get("session_id"),
        "agent_id": row.get("agent_id"),
        "call_id": native_ref,
    }
    result = store.activate_approval_atomically(
        approval_id,
        approver_session=session_id,
        agent_id=row.get("agent_id"),
        binding=binding,
        ttl_minutes=WINDOW_MINUTES,
    )
    if not result.success:
        return DecisionResult("no_decision", approval_id, result.reason)
    return DecisionResult("activated", approval_id)


# --------------------------------------------------------------------------- #
# Consume and close
# --------------------------------------------------------------------------- #

def match_command(
    command: str,
    *,
    cwd: Optional[str],
    session_id: str,
    agent_id: Optional[str],
    tool_use_id: str,
) -> Optional[dict]:
    """Reserve ``command`` if it is exactly the next sealed item for this directory and requester."""
    from gaia.store.writer import reserve_plan_command

    return reserve_plan_command(
        command, session_id=session_id, tool_use_id=tool_use_id, cwd=cwd, agent_id=agent_id,
    )


def close_command(approval_id: str, *, session_id: str, tool_use_id: str, exit_code: int) -> str:
    """Settle the reserved item from its exit code: ``executed``, ``failed`` or ``unmatched``.

    Exit 0, or a non-zero exit the item declared when it was sealed, advances
    the set; any other exit freezes it at that index.
    """
    from gaia.approvals.store import _open_db
    from gaia.store.writer import settle_plan_command

    con = _open_db()
    try:
        row = con.execute(
            "SELECT command_set_json, reservation_index, reservation_session_id, "
            "reservation_tool_use_id FROM approval_grants WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
    finally:
        con.close()
    if row is None or (row[2], row[3]) != (session_id, tool_use_id) or row[1] is None:
        return "unmatched"
    item = json.loads(row[0])[int(row[1])]
    advances = exit_code == 0 or exit_code in (item.get("expect_exit") or [])
    settled = settle_plan_command(
        approval_id, session_id=session_id, tool_use_id=tool_use_id,
        success=advances, failure_reason=None if advances else f"exit code {exit_code}",
    )
    if not settled:
        return "unmatched"
    return "executed" if advances else "failed"


def close_call(
    approval_id: str,
    *,
    command: str,
    session_id: str,
    tool_use_id: str,
    exit_code: int,
    reserved: bool,
    terminal_event: str,
    error: str = "",
) -> str:
    """Close one authorized call from its own terminal event; return ``executed``, ``failed`` or ``unmatched``.

    Only the host event that reports this ``tool_use_id``'s outcome may call
    this; a turn or session ending is never one. A ``reserved`` set item settles
    through :func:`close_command`; a single-command grant was already spent at
    its match, so its outcome is exit 0 or not. ``command`` is the sealed bytes
    the call matched, which the EXECUTED or FAILED event records.
    """
    from gaia.approvals import store

    if reserved:
        outcome = close_command(
            approval_id, session_id=session_id, tool_use_id=tool_use_id, exit_code=exit_code,
        )
        if outcome == "unmatched":
            return outcome
    else:
        outcome = "executed" if exit_code == 0 else "failed"
    payload = {
        "command": command,
        "exit_code": exit_code,
        "outcome": "success" if outcome == "executed" else "failure",
    }
    if error:
        payload["error"] = error
    store.record_event(
        approval_id, "EXECUTED" if outcome == "executed" else "FAILED",
        session_id=session_id or None,
        payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
        metadata_json=json.dumps(
            {"source": terminal_event, "tool_use_id": tool_use_id}, sort_keys=True,
        ),
    )
    return outcome


def grant_lookup_filter(*, cwd: str, session_id: object, agent_id: object) -> dict:
    """Return the ``requester`` filter a single-command grant lookup applies.

    Resolved like the seal, so a retry names the directory and requester its
    request was sealed with. An unresolved requester carries ``None`` values,
    which match only grants sealed before requesters were recorded.
    """
    try:
        requester = resolve_requester(session_id, agent_id)
    except RequesterError:
        requester = {"session_id": None, "agent_id": None}
    return {**requester, "cwd": cwd}


# --------------------------------------------------------------------------- #
# Withdraw
# --------------------------------------------------------------------------- #

_WITHDRAW_ACTIONS = frozenset({"reject", "revoke"})


def withdraw(approval_id: str, *, action: str, session_id: str, agent_id: Optional[str] = None) -> str:
    """Reject or revoke ``approval_id``; return the resulting state. Never approves."""
    from gaia.approvals import store
    from gaia.store import writer

    if action not in _WITHDRAW_ACTIONS:
        raise WithdrawError(f"withdraw accepts reject or revoke, not {action!r}")
    row = store.get_by_id(approval_id)
    if row is not None and row.get("status") == "pending":
        if action == "reject":
            store.reject(approval_id, session_id, agent_id=agent_id)
            return "rejected"
        store.revoke(approval_id, session_id, agent_id=agent_id)
        return "revoked"
    result = writer.revoke_approval_grant(approval_id)
    if result.get("status") != "applied":
        raise WithdrawError(f"{approval_id} has no pending request or live grant to withdraw")
    return "revoked"
