"""The one renderer of a signature's surface, in each host's layout.

Gaia composes every line a user reads; the requester only seals short phrases
(title, question, and per item what it does and its impact, plus the rollback)
and a host only shows the result. Claude Code asks one question per signature
(:func:`render_signatures`), in English: who asks, its commands numbered one per
line, and what the set does, with real line breaks. OpenCode, whose question shows no line
break, asks one one-line question per command (:func:`render_batch`, D33) in
the machine format of D37: bracketed fields under a ``[ GAIA-SECURITY ]`` prefix.
Either way a Details re-ask replaces each question with its Details text. Phrase
limits are checked when a request is sealed (:func:`check_phrases`, called by
``core.seal_request``), so an over-long phrase is returned to its author
instead of reaching a presentation; nothing is truncated.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from gaia.approvals.core import SealError, _ensure_hooks_importable, is_signed

TITLE_MAX = 120
QUESTION_MAX = 60
LINE_MAX = 100
#: Host limits of one question call: its header and its question count. An
#: OpenCode question is one command, so a signature is sealed with at most BATCH_MAX.
HEADER_MAX = 12
#: ``Signature N/M``: Claude Code's schema describes 12 header characters but does not enforce it.
SIGNATURE_HEADER_MAX = 13
BATCH_MAX = 4
OPTION_WORDS_MAX = 10

#: Headers of the pre-D33 surface, still recognised on a question a model copied.
HEADER = "Aprobación"
DETAILS_HEADER = "Detalles"
DEFAULT_QUESTION = "¿Apruebo esta solicitud?"
OPTIONS = (
    ("Approve", "Autoriza exactamente este comando"),
    ("Reject", "Rechaza la firma; no se ejecuta nada"),
    ("Details", "Qué hace, impacto, verificación y cómo deshacerlo"),
)
SIGNATURE_OPTIONS = (
    ("Approve", "Authorizes exactly this command"),
    ("Reject", "Rejects the signature; nothing runs"),
    ("Details", "What it does, impact, verification and how to undo it"),
)
#: The core decision each option label stands for (``core.DECISION_OPTIONS``).
OPTION_KEYS = {label: label.lower() for label, _ in OPTIONS}

#: Opens every question Gaia asks, so the user reads it as Gaia's security and not the model's (D37).
_PREFIX = "[ GAIA-SECURITY ]"
#: The prefix however a model spaced it, unspaced included: the user reads any spacing as Gaia's.
_TYPED_PREFIX = re.compile(r"\[\s*GAIA-SECURITY\s*\]")
#: The opening of Claude Code's question, however a model worded the count, the earlier Spanish one included.
_TYPED_REQUEST = re.compile(r"(?i)\b(pide\s+\S+\s+(comandos?|archivos?)|requests\s+\S+\s+(commands?|files?))\b")
_SIGNATURE_HEADER = re.compile(r"(Signature|Details|Firma|Aprob\.|Detalle)( [A-Z] ?| )\d+/\d+")
_GIT_IN_FOLDER = re.compile(r"git -C (/[\w./@+,=%-]*) (.+)", re.S)
_RELATIVE_START = re.compile(r"\.{1,2}(/|$)")
_FILE_NAME = re.compile(r"[\w-]\.[A-Za-z][A-Za-z0-9]{0,7}$")
_NO_AGENT = "agente sin identificar"
_NO_DOES = "(sin descripción declarada)"
_NO_IMPACT = "no declarado"
_NO_ROLLBACK = "no declarado; no supongas que se puede deshacer"
_NO_VERIFICATION = "no declarada"
_NO_SHARED_STATE = "no declarado; no supongas que no toca estado compartido"
_ABSENT = {
    "agent": "unidentified agent",
    "does": "(no description declared)",
    "impact": "not declared",
    "verification": "not declared",
    "shared_state": "not declared; do not assume it leaves shared state alone",
    "rollback_hint": "not declared; do not assume it can be undone",
}


class SurfaceLimitError(SealError):
    """Raised when a phrase or a question does not fit the signature surface."""


@dataclass(frozen=True)
class Surface:
    """One signature as a host shows it: its questions, their Details re-asks and their texts.

    ``details_questions[i]`` is the Details re-ask of ``questions[i]``. In
    Claude Code's layout there is one of each; in OpenCode's, one per signed
    command, each carrying the unsigned steps around it.
    """

    approval_id: str
    text: str
    details: str
    questions: tuple
    details_questions: tuple


# --------------------------------------------------------------------------- #
# Limits
# --------------------------------------------------------------------------- #

def _check_line(name: str, value: Optional[str], limit: int) -> None:
    if value is None:
        return
    if "\n" in value or "\r" in value:
        raise SurfaceLimitError(f"{name} must fit on one line")
    if len(value) > limit:
        raise SurfaceLimitError(
            f"{name} has {len(value)} characters; the limit is {limit}"
        )


def check_phrases(
    *, title: str, question: Optional[str], items: Sequence[Mapping[str, Any]]
) -> None:
    """Reject a request whose phrases or command count would not fit the surface as written."""
    asked = [item for item in items if is_signed(item)]
    if len(asked) > BATCH_MAX:
        raise SurfaceLimitError(
            f"a signature covers at most {BATCH_MAX} commands, one question each, "
            f"and this request carries {len(asked)}; split it into signatures of "
            f"up to {BATCH_MAX} commands and request each one"
        )
    _check_line("title (what)", title, TITLE_MAX)
    _check_line("question", question, QUESTION_MAX)
    for position, item in enumerate(items, start=1):
        _check_line(f"item {position} does", item.get("does"), LINE_MAX)
        _check_line(f"item {position} impact", item.get("impact"), LINE_MAX)


def check_question(question: Mapping[str, Any], *, header_max: int = HEADER_MAX) -> None:
    """Reject a question object whose header or options exceed the host limits."""
    _check_line("header", question.get("header"), header_max)
    for option in question.get("options") or ():
        words = len(str(option.get("description") or "").split())
        if words > OPTION_WORDS_MAX:
            raise SurfaceLimitError(
                f"option {option.get('label')!r} description has {words} words; "
                f"the limit is {OPTION_WORDS_MAX} words"
            )


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #

def _items(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The sealed items in order; a pre-core row is read from its command list."""
    items = payload.get("items")
    if isinstance(items, list) and items:
        return items
    _ensure_hooks_importable()
    from adapters.consent_presentation import payload_commands

    return [{"command": command} for command in payload_commands(payload)]


def _one_line(text: str) -> str:
    """``text`` with its line breaks written as escapes, so a question never wraps.

    A sealed command with a line break (a reactive block of a multi-line
    command) is still shown whole: ``\\n`` stands for the break it carries.
    """
    return text.replace("\r", "\\r").replace("\n", "\\n")


def _target(item: Mapping[str, Any]) -> str:
    from gaia.redaction import mask_clear_secrets

    return _one_line(mask_clear_secrets(item.get("command") or item.get("path") or ""))


def _agent(payload: Mapping[str, Any], absent: str = _NO_AGENT) -> str:
    return (payload.get("requested_by") or {}).get("agent_id") or absent


def _home_relative(folder: str) -> str:
    home = str(Path.home())
    if home != "/" and (folder == home or folder.startswith(home + "/")):
        return "~" + folder[len(home):]
    return folder


def _lifted_folder(items: Sequence[Mapping[str, Any]]) -> Optional[str]:
    """The plain absolute folder every step names as ``git -C <folder>``, so lifting it is exactly reversible; else ``None``."""
    folders = set()
    for item in items:
        match = _GIT_IN_FOLDER.fullmatch(str(item.get("command") or ""))
        if "command" not in item or match is None:
            return None
        folders.add(match.group(1))
    return folders.pop() if len(folders) == 1 else None


def _folder(payload: Mapping[str, Any], item: Mapping[str, Any]) -> Optional[str]:
    """The folder a command runs in, only when it is not the requester's shell folder (D3).

    ``requested_from`` is sealed by the request; a payload sealed without it
    (a reactive block, which seals the folder it ran in, or an older row) shows
    no folder.
    """
    origin = payload.get("requested_from")
    cwd = item.get("cwd")
    if "command" not in item or not origin or not cwd or cwd == origin:
        return None
    return cwd


def _uses_relative_path(command: str) -> bool:
    """Whether an argument of ``command`` reads as a path relative to its folder.

    A token counts when it starts at ``.``/``..`` or ends in a file name with a
    letter-led extension (``nota-6.txt``, ``src/app.py``); a bare ``a/b`` does
    not, so a branch such as ``feature/demo-login`` shows no folder.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    for position, token in enumerate(tokens):
        value = token.split("=", 1)[1] if token.startswith("-") and "=" in token else token
        if position == 0 and not value.startswith("."):
            continue
        if not value or value.startswith(("-", "/", "~", "$")) or "://" in value:
            continue
        if _RELATIVE_START.match(value) or _FILE_NAME.search(value):
            return True
    return False


def _details_folder(payload: Mapping[str, Any], item: Mapping[str, Any]) -> Optional[str]:
    """The sealed folder, shown when it differs from the requester's or the command reads relative paths (D37)."""
    folder = _folder(payload, item)
    if folder:
        return folder
    command = item.get("command")
    cwd = item.get("cwd") or payload.get("requested_from")
    if command and cwd and _uses_relative_path(command):
        return cwd
    return None


def shown_folders(payload: Mapping[str, Any]) -> list[str]:
    """Each folder Details names for the request's commands, once each, in order: none when every command reads absolute paths in the requester's folder."""
    folders = (_details_folder(payload, item) for item in _items(payload))
    return list(dict.fromkeys(folder for folder in folders if folder))


def _allowed_exit(item: Mapping[str, Any]) -> list[str]:
    """The ``ALLOWED EXIT`` field of a command that declared non-zero exits it may end with, else none."""
    codes = item.get("expect_exit") or []
    return [f"[ ALLOWED EXIT: {','.join(str(code) for code in codes)} ]"] if codes else []


def _steps_text(payload: Mapping[str, Any], steps: Sequence[Mapping[str, Any]]) -> str:
    """Who asks and each exact step in order, a signed one as ``COMMAND`` and any other as ``UNSIGNED STEP`` (D37).

    A signed command that declared exits it may end with non-zero shows them
    after its text, so the user signs what the set tolerates as well as what it runs.
    """
    fields = (
        " ".join([
            f"[ {'COMMAND' if is_signed(step) else 'UNSIGNED STEP'} ] [ {_target(step)} ]",
            *(_allowed_exit(step) if is_signed(step) else []),
        ])
        for step in steps
    )
    return " ".join([f"{_PREFIX} [ AGENT-REQUEST ] [ {_agent(payload)} ]", *fields])


def _question_texts(payload: Mapping[str, Any], items: Sequence[Mapping[str, Any]]) -> list[str]:
    """One question text per signed item, carrying the unsigned steps of the set in their order.

    The unsigned steps before a signed item open its question and those after
    the last signed item close the last question, so the questions the user
    answers show every step while asking only the signed ones.
    """
    groups: list[list[Mapping[str, Any]]] = []
    pending: list[Mapping[str, Any]] = []
    for item in items:
        pending.append(item)
        if is_signed(item):
            groups.append(pending)
            pending = []
    groups[-1].extend(pending)
    return [_steps_text(payload, group) for group in groups]


def _forced_worktree_removal(command: str) -> Optional[str]:
    """The worktree path a ``git worktree remove --force`` command deletes, else ``None``."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    for position in range(len(tokens) - 1):
        if tokens[position:position + 2] != ["worktree", "remove"]:
            continue
        rest = tokens[position + 2:]
        forced = any(
            token == "--force" or (re.fullmatch(r"-[A-Za-z]*f[A-Za-z]*", token) is not None)
            for token in rest
        )
        paths = [token for token in rest if not token.startswith("-")]
        return paths[-1] if forced and paths else None
    return None


def _diff_summary(diff_text: str) -> str:
    """``N file(s), +added -removed`` counted from a unified diff."""
    files = diff_text.count("\ndiff --git ") + diff_text.startswith("diff --git ")
    lines = diff_text.splitlines()
    added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    return f"{files} file{'' if files == 1 else 's'}, +{added} -{removed}"


def _captured_work(command: Optional[str]) -> Optional[str]:
    """What a forced worktree removal would destroy that ``gaia worktree release`` already captured.

    Names the deposited diff, its size and a summary of it, or states that no
    capture is recorded, so a ``--force`` is never signed over contents the
    signer cannot see.
    """
    worktree_path = _forced_worktree_removal(command or "")
    if worktree_path is None:
        return None
    from gaia import worktree
    from gaia.evidence.fs import read_blob
    from gaia.store import writer

    metadata = worktree.read_worktree_metadata(worktree_path)
    capture = writer.get_contract_worktree_capture(metadata.contract_id) if metadata else None
    if not capture:
        return "none recorded for this worktree; its uncommitted work is not preserved"
    artifact = capture.get("artifact_path", "")
    payload = read_blob(artifact)
    summary = _diff_summary(payload.decode("utf-8", errors="replace")) if payload is not None else "diff unreadable"
    return f"{artifact} ({capture.get('size_bytes')} bytes, {summary})"


def _details_text(payload: Mapping[str, Any], item: Mapping[str, Any]) -> str:
    """One command's Details question (D37).

    The command, what it does, its impact, how the result is verified, whether
    it rewrites shared state, the rollback, its folder when it matters, and
    for a forced worktree removal the diff already captured from it.
    """
    folder = _details_folder(payload, item)
    captured = _captured_work(item.get("command"))
    fields = [
        "[ DETAILS ]",
        f"[ {_agent(payload)} ]",
        f"[ COMMAND: {_target(item)} ]",
        *_allowed_exit(item),
        f"[ DOES: {_one_line(item.get('does') or _NO_DOES)} ]",
        f"[ IMPACT: {_one_line(item.get('impact') or _NO_IMPACT)} ]",
        f"[ VERIFICATION: {_one_line(payload.get('verification') or _NO_VERIFICATION)} ]",
        f"[ SHARED-STATE: {_one_line(payload.get('shared_state') or _NO_SHARED_STATE)} ]",
        f"[ ROLLBACK: {_one_line(payload.get('rollback_hint') or _NO_ROLLBACK)} ]",
        *([f"[ CWD: {_one_line(folder)} ]"] if folder else []),
        *([f"[ CAPTURED: {_one_line(captured)} ]"] if captured else []),
    ]
    return " ".join([_PREFIX, *fields])


def _signed_noun(items: Sequence[Mapping[str, Any]]) -> tuple[int, str]:
    """How many items a request signs and their noun, ``command(s)`` or ``file(s)``, agreeing with that count."""
    signed = [item for item in items if is_signed(item)]
    noun = "command" if any("command" in item for item in signed) else "file"
    return len(signed), f"{noun}{'' if len(signed) == 1 else 's'}"


def _request_opening(
    payload: Mapping[str, Any], items: Sequence[Mapping[str, Any]], folder: Optional[str] = None,
) -> str:
    """``<agent> requests <k> command(s)[ in <folder>]:``, counting only the signed items."""
    count, noun = _signed_noun(items)
    where = f" in {_home_relative(folder)}" if folder else ""
    return f"{_agent(payload, _ABSENT['agent'])} requests {count} {noun}{where}:"


def _approve_description(items: Sequence[Mapping[str, Any]]) -> str:
    """The Approve option of Claude Code's question, naming what one signature authorizes."""
    count, noun = _signed_noun(items)
    return f"Authorizes exactly {'this' if count == 1 else f'these {count}'} {noun}"


def _numbered_step(position: int, item: Mapping[str, Any], folder: Optional[str] = None) -> str:
    codes = item.get("expect_exit") or []
    notes = [] if is_signed(item) else ["unsigned step"]
    if codes and is_signed(item):
        notes.append(f"allowed exit: {','.join(str(code) for code in codes)}")
    shown = _target(item)
    if folder:
        shown = "git " + _target({"command": _GIT_IN_FOLDER.fullmatch(item["command"]).group(2)})
    return f"{position}. {shown}" + (f" ({'; '.join(notes)})" if notes else "")


def _what_line(payload: Mapping[str, Any], items: Sequence[Mapping[str, Any]]) -> str:
    plural = len([item for item in items if is_signed(item)]) > 1
    lead = "What they do" if plural else "What it does"
    return f"{lead}: {_one_line(payload.get('what') or _ABSENT['does'])}"


def _request_text(payload: Mapping[str, Any], items: Sequence[Mapping[str, Any]]) -> str:
    """Claude Code's question: who asks, every step numbered on its own line, and what the set does."""
    folder = _lifted_folder(items)
    steps = [_numbered_step(position, item, folder) for position, item in enumerate(items, start=1)]
    return "\n".join([_request_opening(payload, items, folder), "", *steps, "", _what_line(payload, items)])


def _request_details_text(payload: Mapping[str, Any], items: Sequence[Mapping[str, Any]]) -> str:
    """Claude Code's Details re-ask: each full command with what it does and its impact, then the set's verification, shared state and rollback."""
    lines = [_request_opening(payload, items), ""]
    for position, item in enumerate(items, start=1):
        lines.append(_numbered_step(position, item))
        lines.append(f"   Does: {_one_line(item.get('does') or _ABSENT['does'])}")
        lines.append(f"   Impact: {_one_line(item.get('impact') or _ABSENT['impact'])}")
        folder = _details_folder(payload, item)
        if folder:
            lines.append(f"   Folder: {_one_line(folder)}")
        captured = _captured_work(item.get("command"))
        if captured:
            lines.append(f"   Captured: {_one_line(captured)}")
    lines += [
        "",
        _what_line(payload, items),
        *(
            f"{label}: {_one_line(payload.get(key) or _ABSENT[key])}"
            for label, key in (
                ("Verification", "verification"),
                ("Shared state", "shared_state"),
                ("Undo", "rollback_hint"),
            )
        ),
    ]
    return "\n".join(lines)


def _question(
    text: str, header: str, approve: Optional[str] = None,
    *, options: Sequence[tuple[str, str]] = OPTIONS, header_max: int = HEADER_MAX,
) -> dict:
    question = {
        "question": text,
        "header": header,
        "options": [
            {"label": label, "description": approve if label == "Approve" and approve else description}
            for label, description in options
        ],
        "multiSelect": False,
    }
    check_question(question, header_max=header_max)
    return question


def batch_header(
    position: int, total: int, *, details: bool = False, signature: Optional[str] = None,
) -> str:
    """The header of an OpenCode question, or of its Details re-ask: ``Firma 1/2``, or ``Firma A 1/2`` inside signature ``A`` (D38).

    ``Detalle A1/2`` drops the space ``Firma A 1/2`` keeps: with it the header
    would run to 13 characters, one past the host's :data:`HEADER_MAX`.
    """
    if signature is None:
        return f"{'Detalle' if details else 'Firma'} {position}/{total}"
    if details:
        return f"Detalle {signature}{position}/{total}"
    return f"Firma {signature} {position}/{total}"


def signature_header(position: int, total: int, *, details: bool = False) -> str:
    """The header of Claude Code's question, ``Signature 1/2``, or of its Details re-ask, ``Details 1/2``."""
    return f"{'Details' if details else 'Signature'} {position}/{total}"


def signature_letter(index: int) -> str:
    """The letter naming the 0-based ``index``-th signature of a mixed call: ``A``, ``B``..."""
    return chr(ord("A") + index)


def question_count(payload: Mapping[str, Any]) -> int:
    """How many questions a sealed request asks: one per signed command."""
    return len([item for item in _items(payload) if is_signed(item)])


def render_at(
    payload: Mapping[str, Any], approval_id: str, start: int, total: int,
    *, signature: Optional[str] = None,
) -> Surface:
    """Render one request whose first question sits at 1-based ``start`` among ``total``.

    Without ``signature`` the positions count the whole call; with it they
    count only this request's commands, headed by its letter.
    """
    items = _items(payload)
    asked = [item for item in items if is_signed(item)]
    if not asked:
        raise SurfaceLimitError(f"approval {approval_id} seals nothing to present")
    if len(asked) > BATCH_MAX:
        raise SurfaceLimitError(
            f"approval {approval_id} seals {len(asked)} commands; a signature is "
            f"asked with at most {BATCH_MAX}"
        )
    texts = _question_texts(payload, items)
    details = [_details_text(payload, item) for item in asked]
    return Surface(
        approval_id=approval_id,
        text="\n".join(_steps_text(payload, [item]) for item in items),
        details="\n".join(details),
        questions=tuple(
            _question(text, batch_header(start + offset, total, signature=signature))
            for offset, text in enumerate(texts)
        ),
        details_questions=tuple(
            _question(text, batch_header(start + offset, total, details=True, signature=signature))
            for offset, text in enumerate(details)
        ),
    )


def render(payload: Mapping[str, Any], approval_id: str) -> Surface:
    """Render one sealed request asked alone; a phrase the payload does not declare is stated as absent."""
    return render_at(payload, approval_id, 1, question_count(payload))


def render_signature(
    payload: Mapping[str, Any], approval_id: str, position: int, total: int,
) -> Surface:
    """Render one request as Claude Code asks it: one question, the ``position``-th of ``total`` signatures."""
    items = _items(payload)
    asked = [item for item in items if is_signed(item)]
    if not asked:
        raise SurfaceLimitError(f"approval {approval_id} seals nothing to present")
    if len(asked) > BATCH_MAX:
        raise SurfaceLimitError(
            f"approval {approval_id} seals {len(asked)} commands; a signature is "
            f"asked with at most {BATCH_MAX}"
        )
    text = _request_text(payload, items)
    details = _request_details_text(payload, items)
    approve = _approve_description(items)
    english = {"options": SIGNATURE_OPTIONS, "header_max": SIGNATURE_HEADER_MAX}
    return Surface(
        approval_id=approval_id,
        text=text,
        details=details,
        questions=(_question(text, signature_header(position, total), approve, **english),),
        details_questions=(
            _question(details, signature_header(position, total, details=True), approve, **english),
        ),
    )


def render_signatures(
    requests: Sequence[tuple[Mapping[str, Any], str]],
) -> list[Surface]:
    """Render the requests one Claude Code question call asks: one question per signature, ``Signature N/M``, at most BATCH_MAX."""
    if not 1 <= len(requests) <= BATCH_MAX:
        raise SurfaceLimitError(
            f"a question call asks 1 to {BATCH_MAX} signatures, one question each, "
            f"not {len(requests)}; ask fewer signatures per call"
        )
    surfaces = [
        render_signature(payload, approval_id, position, len(requests))
        for position, (payload, approval_id) in enumerate(requests, start=1)
    ]
    _require_distinct(surfaces)
    return surfaces


def _require_distinct(surfaces: Sequence[Surface]) -> None:
    """Refuse a call two of whose questions read the same: the host indexes each answer by its text."""
    for kind in ("questions", "details_questions"):
        texts = [q["question"] for s in surfaces for q in getattr(s, kind)]
        if len(set(texts)) != len(texts):
            raise SurfaceLimitError(
                "two questions of one call read the same; ask those signatures in separate calls"
            )


def is_signature_question(question: Mapping[str, Any]) -> bool:
    """Whether a host question object has the shape of a signature: its header or its options."""
    header = str(question.get("header") or "")
    labels = [
        option.get("label") for option in question.get("options") or ()
        if isinstance(option, Mapping)
    ]
    return (
        header in (HEADER, DETAILS_HEADER)
        or _SIGNATURE_HEADER.fullmatch(header) is not None
        or labels == [label for label, _ in OPTIONS]
    )


def looks_like_signature(question: Mapping[str, Any]) -> bool:
    """Whether a question reads as a signature to the user, however it was typed.

    Wider than :func:`is_signature_question`: a model's copy carries a translated
    header, other option descriptions or only the heading, and the user reads
    it as Gaia's signature all the same.
    """
    header = str(question.get("header") or "")
    return (
        is_signature_question(question)
        or re.match(r"(?i)(aprob|approv|firma|signature)", header) is not None
        or _TYPED_PREFIX.match(str(question.get("question") or "")) is not None
    )


def reads_as_request(question: Mapping[str, Any]) -> bool:
    """Whether a Claude Code question reads as a signature: :func:`looks_like_signature` or a ``requests … command`` opening."""
    return looks_like_signature(question) or (
        _TYPED_REQUEST.search(str(question.get("question") or "")) is not None
    )


def render_batch(
    requests: Sequence[tuple[Mapping[str, Any], str]],
) -> list[Surface]:
    """Render the requests asked in one question call: one question per command, at most BATCH_MAX in all.

    A call asking one signature heads each question with its position in the
    call, ``Firma N/M``; a call mixing signatures names each one by a letter
    and counts inside it, ``Firma A 1/2``, ``Firma A 2/2``, ``Firma B 1/1``
    (D38). Question texts must differ: the host indexes each answer by its
    question text.
    """
    counts = [question_count(payload) for payload, _ in requests]
    total = sum(counts)
    if not requests or not 1 <= total <= BATCH_MAX:
        raise SurfaceLimitError(
            f"a question call asks 1 to {BATCH_MAX} questions, one per command, "
            f"and these signatures carry {total}; ask fewer signatures per call"
        )
    if len(requests) == 1:
        (payload, approval_id), = requests
        surfaces = [render_at(payload, approval_id, 1, total)]
    else:
        surfaces = [
            render_at(payload, approval_id, 1, count, signature=signature_letter(index))
            for index, ((payload, approval_id), count) in enumerate(zip(requests, counts))
        ]
    _require_distinct(surfaces)
    return surfaces


def slots(surfaces: Sequence[Surface]) -> list[tuple[str, dict, dict]]:
    """``(approval_id, question, details_question)`` for every question of a call, in order."""
    return [
        (s.approval_id, question, details)
        for s in surfaces
        for question, details in zip(s.questions, s.details_questions)
    ]
