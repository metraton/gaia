"""Host-neutral approval core: seal, present, decide, consume and withdraw a signature.

Every host adapter (Claude Code, OpenCode) and the approvals CLI reach the T3
consent cycle through this module, so the invariants below have one owner:

* one constructor, :func:`seal_request`, seals every request -- reactive Bash,
  plan-first COMMAND_SET and protected file write -- with a what-it-does phrase,
  a single window of :data:`WINDOW_MINUTES` counted from the decision, the
  requesting session and agent, and per item its directory, declared non-zero
  exits, position and fingerprint;
* one key per request (``request_key``) and one per item (:func:`command_key`);
* nothing is shown without its requester's phrases: a title, a question, and
  per item what it does and its impact (:func:`check_presentable`, which every
  host runs before showing). The requesting verbs demand them; a reactive block
  seals without them and points its requester to :func:`request_line`, and the
  phrased request that follows replaces it (withdrawn as ``reemplazada``);
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
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from gaia.store.writer import APPROVAL_WINDOW_MINUTES as WINDOW_MINUTES

DECISION_OPTIONS = frozenset({"approve", "reject", "details"})
_COMMAND_KINDS = frozenset({"command", "command_set"})
_FILE_KIND = "file_write"
_FILE_OPERATION = "FILE_WRITE command intercepted: write"
_MAX_EXIT_CODE = 255
#: The withdrawal reason of a phraseless request its requester's phrased one
#: replaced; readers tell it from a user's rejection and from an expiry by it.
REPLACED_REASON = "reemplazada"


class SealError(ValueError):
    """Raised when a request cannot be sealed as it stands."""


class RequesterError(SealError):
    """Raised when the host event does not name the session or agent a request binds to."""


class NotPresentableError(SealError):
    """Raised when a request lacks a phrase its requester owes; ``missing`` names each flag."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(
            "a signature is shown only with its requester's phrases; missing: " + ", ".join(missing)
        )


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
# Phrases (PD10)
# --------------------------------------------------------------------------- #

def _payload_items(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The sealed items; a row sealed before items existed is read from its command list."""
    items = payload.get("items")
    if isinstance(items, list) and items:
        return items
    targets = payload.get("commands") or [payload.get("exact_content")]
    return [{"command": target} for target in targets if target]


def _targets(payload: Mapping[str, Any]) -> list[str]:
    return [item.get("command") or item.get("path") for item in _payload_items(payload)]


def _missing(what: object, question: object, items: Iterable[Mapping[str, Any]]) -> list[str]:
    missing = [
        flag for flag, value in (("--what (title)", what), ("--question", question))
        if _optional_text(value) is None
    ]
    for position, item in enumerate(items, start=1):
        missing.extend(
            f"--{phrase} for item {position}"
            for phrase in ("does", "impact")
            if _optional_text(item.get(phrase)) is None
        )
    return missing


def missing_phrases(payload: Mapping[str, Any]) -> list[str]:
    """Name, by the flag that supplies it, each phrase ``payload`` lacks; empty when presentable."""
    return _missing(payload.get("what"), payload.get("question"), _payload_items(payload))


def check_presentable(payload: Mapping[str, Any]) -> None:
    """The one check every host runs before showing a request: raise when a phrase is missing."""
    missing = missing_phrases(payload)
    if missing:
        raise NotPresentableError(missing)


def request_line(payload: Mapping[str, Any]) -> str:
    """The request that replaces phraseless ``payload`` with a phrased one; each ``<...>`` is the requester's."""
    items = _payload_items(payload)
    if payload.get("operation") == _FILE_OPERATION:
        words = ["gaia approvals request-file-write", "--path", shlex.quote(_targets(payload)[0])]
    else:
        words = ["gaia approvals request-set"]
        for target in _targets(payload):
            words += ["--command", shlex.quote(target)]
        cwds = [item.get("cwd") for item in items]
        if all(cwds):
            for cwd in cwds[:1] if len(set(cwds)) == 1 else cwds:
                words += ["--cwd", shlex.quote(cwd)]
    words += [
        "--what", shlex.quote("<title, in the user's language, 120 max>"),
        "--question", shlex.quote("<short question, 60 max>"),
    ]
    for position in range(1, len(items) + 1):
        words += [
            "--does", shlex.quote(f"<what item {position} does, 100 max>"),
            "--impact", shlex.quote(f"<impact of item {position}, 100 max>"),
        ]
    return " ".join(words)


def _require_phrases(what: object, question: object, items: list[Mapping[str, Any]]) -> None:
    missing = _missing(what, question, items)
    if missing:
        raise NotPresentableError(missing)


def _own_pendings(requester: Mapping[str, str]) -> list[tuple[dict, dict]]:
    """The pending rows, oldest first, whose sealed requester is ``requester``, with their payloads."""
    from gaia.approvals.store import list_pending

    own = []
    for row in list_pending(all_sessions=True):
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("requested_by") == dict(requester):
            own.append((row, payload))
    return own


def _replaceable(payload: Mapping[str, Any]) -> list[str]:
    """The requester's phraseless pending requests of the same kind whose every target ``payload`` carries."""
    is_file = payload.get("operation") == _FILE_OPERATION
    targets = set(_targets(payload))
    return [
        row["id"]
        for row, old in _own_pendings(payload["requested_by"])
        if (old.get("operation") == _FILE_OPERATION) == is_file
        and set(_targets(old)) <= targets
        and missing_phrases(old)
    ]


def _persist_replacing(payload: dict) -> str:
    """Insert a phrased request and, in the same transaction, withdraw the phraseless ones it replaces.

    Withdrawn as revoked with the reason :data:`REPLACED_REASON`: the user
    never saw them, so it is neither a rejection nor an expiry. One decided or
    withdrawn since it was listed is left as it is.
    """
    from gaia.approvals import store
    from gaia.store.writer import _retry_on_locked

    session_id = payload["requested_by"]["session_id"]
    agent_id = payload["requested_by"]["agent_id"]
    replaced = _replaceable(payload)

    def work() -> str:
        con = store._open_db()
        try:
            con.execute("BEGIN IMMEDIATE")
            try:
                approval_id = store.insert_requested(
                    payload, agent_id=agent_id, session_id=session_id, con=con,
                )
                reason = json.dumps(
                    {"reason": REPLACED_REASON, "replaced_by": approval_id,
                     "source": "gaia.approvals.core"},
                    sort_keys=True,
                )
                for old in replaced:
                    try:
                        store.revoke(old, session_id, agent_id=agent_id, metadata_json=reason, con=con)
                    except ValueError:
                        continue
                con.commit()
                return approval_id
            except Exception:
                con.rollback()
                raise
        finally:
            con.close()

    return _retry_on_locked(work)


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
    """Validate a plan-first set, seal it and persist the pending request; return its approval_id.

    Every phrase is required (:class:`NotPresentableError` names each one
    missing), and the requester's phraseless reactive requests the set covers
    are replaced.
    """
    from gaia.approvals.command_set import CommandSetValidationError, validate_request_set

    commands = [item.get("command") for item in items]
    try:
        validate_request_set(commands)
    except CommandSetValidationError as exc:
        raise SealError(str(exc)) from exc
    _require_phrases(what, question, items)
    payload = seal_request(
        "command_set", items, what=what, session_id=session_id, agent_id=agent_id,
        question=question, rollback=rollback, verification=verification, rationale=rationale,
    )
    return _persist_replacing(payload)


def _pending_file_request(path: str, requester: Mapping[str, str]) -> Optional[tuple[str, dict]]:
    """Return the requester's own pending write request for ``path`` inside the reuse bound, with its payload."""
    from gaia.approvals.store import PENDING_REUSE_WINDOW_MINUTES

    for row, payload in _own_pendings(requester):
        if (
            payload.get("operation") == _FILE_OPERATION
            and payload.get("exact_content") == path
            and float(row.get("age_seconds") or 0.0) <= PENDING_REUSE_WINDOW_MINUTES * 60
        ):
            return row["id"], payload
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
    what: Optional[str],
    question: Optional[str],
    does: Optional[str],
    impact: Optional[str],
    rollback: Optional[str] = None,
    verification: Optional[str] = None,
) -> str:
    """Seal and persist a phrased protected-path write request; return its approval_id.

    Every phrase is required. The requester's open phrased request for the
    path is reused; a phraseless reactive one is replaced.
    """
    item = {"path": path, "does": does, "impact": impact}
    _require_phrases(what, question, [item])
    payload = seal_request(
        _FILE_KIND, [item], what=what, session_id=session_id, agent_id=agent_id,
        question=question, rollback=rollback, verification=verification, impact=impact,
    )
    existing = _pending_file_request(path, payload["requested_by"])
    if existing and not missing_phrases(existing[1]):
        return existing[0]
    return _persist_replacing(payload)


def _reactive_file_request(path: str, *, session_id: str, agent_id: str) -> tuple[str, dict]:
    """Name the requester's open write request for ``path``, else seal one without phrases."""
    from gaia.approvals import store

    existing = _pending_file_request(path, resolve_requester(session_id, agent_id))
    if existing:
        return existing
    payload = seal_request(
        _FILE_KIND, [{"path": path}], what=file_write_title(path),
        session_id=session_id, agent_id=agent_id,
    )
    return store.insert_requested(payload, agent_id=agent_id, session_id=session_id), payload


def protected_write_verdict(file_path: str, *, session_id: str, agent_id: str) -> dict:
    """Decide a Write/Edit on ``file_path``: ``allow`` or ``block`` with the approval to decide.

    A protected path is allowed only by a live file grant bound to this same
    session and agent; otherwise the requester's pending request is named
    (minted on first sight). A block on a request without phrases carries the
    ``request_line`` that replaces it; a phrased one carries ``None``. Raises
    when the request cannot be persisted, so the host can fall back to its own
    consent dialog.
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
    approval_id, payload = _reactive_file_request(
        consent_path, session_id=session_id, agent_id=agent_id,
    )
    return {"decision": "block", "path": consent_path, "protected": True,
            "approval_id": approval_id, "window_minutes": WINDOW_MINUTES,
            "request_line": request_line(payload) if missing_phrases(payload) else None}


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


def _row_payload(row: Mapping[str, Any]) -> dict:
    try:
        payload = json.loads(row.get("payload_json") or "")
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def question_batch(approval_ids: list[str]) -> list:
    """Render the 1 to 4 pending requests one host question call asks, in order.

    Each must be pending and presentable (:func:`check_presentable`); the
    renderer rejects a batch whose question texts repeat.
    """
    from gaia.approvals import store, surface

    if len(set(approval_ids)) != len(approval_ids):
        raise SealError("a signature appears more than once in one question call")
    requests = []
    for approval_id in approval_ids:
        row = store.get_by_id(approval_id)
        if row is None or row.get("status") != "pending":
            raise SealError(f"{approval_id} is not a pending approval")
        payload = _row_payload(row)
        check_presentable(payload)
        requests.append((payload, approval_id))
    return surface.render_batch(requests)


def match_question_batch(questions: list[Mapping[str, Any]]) -> list:
    """Return the batch whose question objects are exactly ``questions``, in order.

    Each position is matched against every pending request rendered for that
    position, so only the object Gaia produced is recognised; a question no
    pending request renders, or one two requests render alike, raises
    :class:`SealError` naming the position.
    """
    from gaia.approvals import store, surface

    total = len(questions)
    if not 1 <= total <= surface.BATCH_MAX:
        raise SealError(f"a question call presents 1 to {surface.BATCH_MAX} signatures, not {total}")
    pending = [(row["id"], _row_payload(row)) for row in store.list_pending(all_sessions=True)]
    approval_ids = []
    for position, asked in enumerate(questions, start=1):
        asked = {"multiSelect": False, **asked} if isinstance(asked, Mapping) else {}
        matches = []
        for approval_id, payload in pending:
            try:
                rendered = surface.batch_question(payload, position, total)
            except SealError:
                continue
            if rendered == asked:
                matches.append(approval_id)
        if not matches:
            raise SealError(f"question {position} is not the one Gaia rendered for a pending approval")
        if len(matches) > 1:
            raise SealError(
                f"question {position} is rendered alike by {', '.join(matches)}; "
                "withdraw the stale one before asking"
            )
        approval_ids.append(matches[0])
    return question_batch(approval_ids)


def presented(native_ref: str) -> list[tuple[int, str]]:
    """The ``(position, approval_id)`` pairs recorded as shown in host question ``native_ref``."""
    from gaia.approvals.store import _open_db

    con = _open_db()
    try:
        rows = con.execute(
            "SELECT json_extract(metadata_json, '$.position'), approval_id "
            "FROM approval_events WHERE event_type = 'SHOWN' "
            "AND json_extract(metadata_json, '$.native_ref') = ? ORDER BY id",
            (native_ref,),
        ).fetchall()
    finally:
        con.close()
    return sorted(dict(rows).items())


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

#: The withdrawal reason of a pending that outlived the pending TTL unanswered.
EXPIRED_REASON = "expired_ttl"
_WITHDRAW_ACTIONS = {"reject": "rejected", "revoke": "revoked", "expire": "expired"}


def withdraw(
    approval_id: str,
    *,
    action: str,
    session_id: Optional[str],
    agent_id: Optional[str] = None,
    reason: Optional[str] = None,
    source: Optional[str] = None,
) -> str:
    """Reject, revoke or expire ``approval_id``; return the resulting state. Never approves.

    A pending is withdrawn with an event naming ``session_id`` and ``agent_id``
    and carrying ``reason`` (always :data:`EXPIRED_REASON` for ``expire``). Any
    other row only has its live grant closed, which reads as revoked.
    """
    from gaia.approvals import store
    from gaia.store import writer

    if action not in _WITHDRAW_ACTIONS:
        raise WithdrawError(f"withdraw accepts reject, revoke or expire, not {action!r}")
    row = store.get_by_id(approval_id)
    if row is not None and row.get("status") == "pending":
        if action == "expire":
            reason = EXPIRED_REASON
        metadata = {key: value for key, value in (("reason", reason), ("source", source)) if value}
        transition = {"reject": store.reject, "revoke": store.revoke, "expire": store.expire}[action]
        transition(
            approval_id, session_id, agent_id=agent_id,
            metadata_json=json.dumps(metadata, sort_keys=True) if metadata else None,
        )
        return _WITHDRAW_ACTIONS[action]
    if action == "expire":
        raise WithdrawError(f"{approval_id} is not a pending request, so it cannot expire")
    result = writer.revoke_approval_grant(approval_id)
    if result.get("status") != "applied":
        raise WithdrawError(f"{approval_id} has no pending request or live grant to withdraw")
    return "revoked"
