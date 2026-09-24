"""The one renderer of a signature's surface: one one-line question per command (D33, D37).

Gaia composes every line a user reads; the requester only seals short phrases
(title, question, and per item what it does and its impact, plus the rollback)
and a host only shows the result. Each command of a signature is its own
question, on one line, the same text in both hosts, in the machine format of
D37: bracketed fields under a ``[GAIA-SECURITY]`` prefix whose labels are fixed
English, while the requester's phrases stay in the user's language. A Details
re-ask replaces each command's question with its one-line Details text. Phrase
limits are checked when a request is sealed (:func:`check_phrases`, called by
``core.seal_request``), so an over-long phrase is returned to its author
instead of reaching a presentation; nothing is truncated.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from gaia.approvals.core import SealError, _ensure_hooks_importable

TITLE_MAX = 120
QUESTION_MAX = 60
LINE_MAX = 100
#: Host limits of one AskUserQuestion call: its header and its question count.
#: A question is one command, so one signature covers at most BATCH_MAX commands.
HEADER_MAX = 12
BATCH_MAX = 4
OPTION_WORDS_MAX = 8

#: Headers of the pre-D33 surface, still recognised on a question a model copied.
HEADER = "Aprobación"
DETAILS_HEADER = "Detalles"
DEFAULT_QUESTION = "¿Apruebo esta solicitud?"
OPTIONS = (
    ("Approve", "Autoriza exactamente este comando"),
    ("Reject", "Rechaza la firma; no se ejecuta nada"),
    ("Details", "Qué hace, impacto y cómo deshacerlo"),
)
#: The core decision each option label stands for (``core.DECISION_OPTIONS``).
OPTION_KEYS = {label: label.lower() for label, _ in OPTIONS}

#: Opens every question Gaia asks, so the user reads it as Gaia's security and not the model's (D37).
_PREFIX = "[GAIA-SECURITY]"
_RELATIVE_START = re.compile(r"\.{1,2}(/|$)")
_FILE_NAME = re.compile(r"[\w-]\.[A-Za-z][A-Za-z0-9]{0,7}$")
_NO_AGENT = "agente sin identificar"
_NO_DOES = "(sin descripción declarada)"
_NO_IMPACT = "no declarado"
_NO_ROLLBACK = "no declarado; no supongas que se puede deshacer"


class SurfaceLimitError(SealError):
    """Raised when a phrase or a question does not fit the signature surface."""


@dataclass(frozen=True)
class Surface:
    """One signature as both hosts show it: one question per sealed command (D33).

    ``questions[i]`` asks command ``i``; ``details_questions[i]`` is its Details
    re-ask. ``text`` and ``details`` are those question texts, one per line.
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
    if len(items) > BATCH_MAX:
        raise SurfaceLimitError(
            f"a signature covers at most {BATCH_MAX} commands, one question each, "
            f"and this request carries {len(items)}; split it into signatures of "
            f"up to {BATCH_MAX} commands and request each one"
        )
    _check_line("title (what)", title, TITLE_MAX)
    _check_line("question", question, QUESTION_MAX)
    for position, item in enumerate(items, start=1):
        _check_line(f"item {position} does", item.get("does"), LINE_MAX)
        _check_line(f"item {position} impact", item.get("impact"), LINE_MAX)


def check_question(question: Mapping[str, Any]) -> None:
    """Reject a question object whose header or options exceed the host limits."""
    _check_line("header", question.get("header"), HEADER_MAX)
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
    return _one_line(item.get("command") or item.get("path") or "")


def _agent(payload: Mapping[str, Any]) -> str:
    return (payload.get("requested_by") or {}).get("agent_id") or _NO_AGENT


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


def _command_text(payload: Mapping[str, Any], item: Mapping[str, Any]) -> str:
    """One command's signature question: who asks and the exact command (D37)."""
    return f"{_PREFIX} [ AGENT-REQUEST ] [ {_agent(payload)} ] [ COMMAND ] [ {_target(item)} ]"


def _details_text(payload: Mapping[str, Any], item: Mapping[str, Any]) -> str:
    """One command's Details question: the command, what it does, impact, rollback, and its folder when it matters (D37)."""
    folder = _details_folder(payload, item)
    fields = [
        "[ DETAILS ]",
        f"[ {_agent(payload)} ]",
        f"[ COMMAND: {_target(item)} ]",
        f"[ DOES: {_one_line(item.get('does') or _NO_DOES)} ]",
        f"[ IMPACT: {_one_line(item.get('impact') or _NO_IMPACT)} ]",
        f"[ ROLLBACK: {_one_line(payload.get('rollback_hint') or _NO_ROLLBACK)} ]",
        *([f"[ CWD: {_one_line(folder)} ]"] if folder else []),
    ]
    return " ".join([_PREFIX, *fields])


def _question(text: str, header: str) -> dict:
    question = {
        "question": text,
        "header": header,
        "options": [{"label": label, "description": description} for label, description in OPTIONS],
        "multiSelect": False,
    }
    check_question(question)
    return question


def batch_header(
    position: int, total: int, *, details: bool = False, signature: Optional[str] = None,
) -> str:
    """The header of a question, or of its Details re-ask: ``Firma 1/2``, or ``Firma A 1/2`` inside signature ``A`` (D38).

    ``Detalle A1/2`` drops the space ``Firma A 1/2`` keeps: with it the header
    would run to 13 characters, one past the host's :data:`HEADER_MAX`.
    """
    if signature is None:
        return f"{'Detalle' if details else 'Firma'} {position}/{total}"
    if details:
        return f"Detalle {signature}{position}/{total}"
    return f"Firma {signature} {position}/{total}"


def signature_letter(index: int) -> str:
    """The letter naming the 0-based ``index``-th signature of a mixed call: ``A``, ``B``..."""
    return chr(ord("A") + index)


def question_count(payload: Mapping[str, Any]) -> int:
    """How many questions a sealed request asks: one per command."""
    return len(_items(payload))


def render_at(
    payload: Mapping[str, Any], approval_id: str, start: int, total: int,
    *, signature: Optional[str] = None,
) -> Surface:
    """Render one request whose first question sits at 1-based ``start`` among ``total``.

    Without ``signature`` the positions count the whole call; with it they
    count only this request's commands, headed by its letter.
    """
    items = _items(payload)
    if not items:
        raise SurfaceLimitError(f"approval {approval_id} seals nothing to present")
    if len(items) > BATCH_MAX:
        raise SurfaceLimitError(
            f"approval {approval_id} seals {len(items)} commands; a signature is "
            f"asked with at most {BATCH_MAX}"
        )
    texts = [_command_text(payload, item) for item in items]
    details = [_details_text(payload, item) for item in items]
    return Surface(
        approval_id=approval_id,
        text="\n".join(texts),
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


def is_signature_question(question: Mapping[str, Any]) -> bool:
    """Whether a host question object has the shape of a signature: its header or its options."""
    header = str(question.get("header") or "")
    labels = [
        option.get("label") for option in question.get("options") or ()
        if isinstance(option, Mapping)
    ]
    return (
        header in (HEADER, DETAILS_HEADER)
        or re.fullmatch(r"(Firma|Aprob\.|Detalle)( [A-Z] ?| )\d+/\d+", header) is not None
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
        or re.match(r"(?i)(aprob|approv)", header) is not None
        or str(question.get("question") or "").startswith(_PREFIX)
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
    for kind in ("questions", "details_questions"):
        texts = [q["question"] for s in surfaces for q in getattr(s, kind)]
        if len(set(texts)) != len(texts):
            raise SurfaceLimitError(
                "two questions of one call read the same; ask those signatures in separate calls"
            )
    return surfaces


def slots(surfaces: Sequence[Surface]) -> list[tuple[str, dict, dict]]:
    """``(approval_id, question, details_question)`` for every question of a call, in order."""
    return [
        (s.approval_id, question, details)
        for s in surfaces
        for question, details in zip(s.questions, s.details_questions)
    ]
