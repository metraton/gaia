"""The one renderer of a signature's surface: one one-line question per command (D33).

Gaia composes every line a user reads; the requester only seals short phrases
(title, question, and per item what it does and its impact) and a host only
shows the result. Each command of a signature is its own question, on one
line, the same text in both hosts; a Details re-ask replaces each command's
question with its one-line Details text. Phrase limits are checked when a
request is sealed (:func:`check_phrases`, called by ``core.seal_request``), so
an over-long phrase is returned to its author instead of reaching a
presentation; nothing is truncated. Fixed strings follow D12 in the user's
language with full orthography (D17); only the option labels are English (D8,
D11).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from gaia.approvals.core import WINDOW_MINUTES, SealError, _ensure_hooks_importable

TITLE_MAX = 120
QUESTION_MAX = 60
LINE_MAX = 100
#: Host limits of one AskUserQuestion call: its header and its question count.
#: A question is one command, so one signature covers at most BATCH_MAX commands.
HEADER_MAX = 12
BATCH_MAX = 4
OPTION_WORDS_MAX = 8

HEADER = "Aprobación"
#: The header of a Details re-ask of a lone question.
DETAILS_HEADER = "Detalles"
DEFAULT_QUESTION = "¿Apruebo esta solicitud?"
OPTIONS = (
    ("Approve", "Autoriza exactamente este comando"),
    ("Reject", "Rechaza toda la solicitud; no se ejecuta nada"),
    ("Details", "Ver qué hace, su impacto y rollback"),
)
#: The core decision each option label stands for (``core.DECISION_OPTIONS``).
OPTION_KEYS = {label: label.lower() for label, _ in OPTIONS}

#: "revisión", not "aprobación": the host harness reacted to that word in a question.
_HEADING = "Solicitud de revisión"
#: Headings a model's hand-typed copy may carry; each reads as Gaia's signature to the user.
_SIGNATURE_HEADINGS = (_HEADING, "Solicitud de aprobación")
_DETAILS_HEADING = "Detalle"
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


def _command_text(payload: Mapping[str, Any], item: Mapping[str, Any]) -> str:
    """One command's question: who asks and the exact command, with its folder when it runs elsewhere."""
    text = f"{_HEADING} · {_agent(payload)}: {_target(item)}"
    folder = _folder(payload, item)
    return f"{text} (en {_one_line(folder)})" if folder else text


def _details_text(
    payload: Mapping[str, Any], item: Mapping[str, Any], approval_id: str,
    index: int, count: int,
) -> str:
    """One command's Details question: what it does, its impact, the rollback, the window and the ID."""
    heading = _DETAILS_HEADING if count == 1 else f"{_DETAILS_HEADING} {index}/{count}"
    window = payload.get("window_minutes") or WINDOW_MINUTES
    parts = [
        f"{heading} · {_agent(payload)}: {_one_line(item.get('does') or _NO_DOES)}",
        f"Impacto: {_one_line(item.get('impact') or _NO_IMPACT)}",
        f"Rollback: {_one_line(payload.get('rollback_hint') or _NO_ROLLBACK)}",
        f"Vale {window} min",
        f"ID {approval_id}",
    ]
    return " · ".join(parts)


def _question(text: str, header: str) -> dict:
    question = {
        "question": text,
        "header": header,
        "options": [{"label": label, "description": description} for label, description in OPTIONS],
        "multiSelect": False,
    }
    check_question(question)
    return question


def batch_header(position: int, total: int, *, details: bool = False) -> str:
    """The header of the question, or of its Details re-ask, at 1-based ``position`` among ``total`` asked in one call."""
    if total == 1:
        return DETAILS_HEADER if details else HEADER
    return f"{'Detalle' if details else 'Aprob.'} {position}/{total}"


def question_count(payload: Mapping[str, Any]) -> int:
    """How many questions a sealed request asks: one per command."""
    return len(_items(payload))


def render_at(
    payload: Mapping[str, Any], approval_id: str, start: int, total: int,
) -> Surface:
    """Render one request whose first question sits at 1-based ``start`` among ``total`` asked in one call."""
    items = _items(payload)
    if not items:
        raise SurfaceLimitError(f"approval {approval_id} seals nothing to present")
    if len(items) > BATCH_MAX:
        raise SurfaceLimitError(
            f"approval {approval_id} seals {len(items)} commands; a signature is "
            f"asked with at most {BATCH_MAX}"
        )
    texts = [_command_text(payload, item) for item in items]
    details = [
        _details_text(payload, item, approval_id, index, len(items))
        for index, item in enumerate(items, start=1)
    ]
    return Surface(
        approval_id=approval_id,
        text="\n".join(texts),
        details="\n".join(details),
        questions=tuple(
            _question(text, batch_header(start + offset, total))
            for offset, text in enumerate(texts)
        ),
        details_questions=tuple(
            _question(text, batch_header(start + offset, total, details=True))
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
        or re.fullmatch(r"(Aprob\.|Detalle) \d+/\d+", header) is not None
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
        or any(heading in str(question.get("question") or "") for heading in _SIGNATURE_HEADINGS)
    )


def render_batch(
    requests: Sequence[tuple[Mapping[str, Any], str]],
) -> list[Surface]:
    """Render the requests asked in one question call: one question per command, at most BATCH_MAX in all.

    A lone question keeps the header ``Aprobación``; otherwise each header
    names its position, ``Aprob. N/M``. Question texts must differ: the host
    indexes each answer by its question text.
    """
    counts = [question_count(payload) for payload, _ in requests]
    total = sum(counts)
    if not requests or not 1 <= total <= BATCH_MAX:
        raise SurfaceLimitError(
            f"a question call asks 1 to {BATCH_MAX} questions, one per command, "
            f"and these signatures carry {total}; ask fewer signatures per call"
        )
    surfaces = []
    start = 1
    for (payload, approval_id), count in zip(requests, counts):
        surfaces.append(render_at(payload, approval_id, start, total))
        start += count
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
