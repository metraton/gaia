"""The one renderer of a signature's surface: its block, short question and Details (D27, D29).

Gaia composes every line a user reads; the requester only seals short phrases
(title, question, and per item what it does and its impact) and a host only
shows the result. The signature is one block outside the question -- shown in
Claude Code by the PreToolUse decision reason, posted in OpenCode by the
plugin, the same bytes in both -- and the question carries only the short
question. Phrase limits are checked when a request is sealed
(:func:`check_phrases`, called by ``core.seal_request``), so an over-long
phrase is returned to its author instead of reaching a presentation; nothing
is truncated. Fixed strings follow D12 in the user's language with full
orthography (D17); only the option labels are English (D8, D11).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from gaia.approvals.command_set import command_fingerprint
from gaia.approvals.core import WINDOW_MINUTES, SealError, _ensure_hooks_importable

TITLE_MAX = 120
QUESTION_MAX = 60
LINE_MAX = 100
#: Host limits of one AskUserQuestion call: its header and its question count.
HEADER_MAX = 12
BATCH_MAX = 4
OPTION_WORDS_MAX = 8

HEADER = "Aprobación"
#: The header of a Details re-ask: the question text is the same short question,
#: so the header is what tells the hook to show the Details block.
DETAILS_HEADER = "Detalles"
DEFAULT_QUESTION = "¿Apruebo esta solicitud?"
OPTIONS = (
    ("Approve", "Autoriza exactamente lo que se muestra arriba"),
    ("Reject", "Rechaza la solicitud; no se ejecuta nada"),
    ("Details", "Ver qué hace cada paso y su impacto"),
)
#: The core decision each option label stands for (``core.DECISION_OPTIONS``).
OPTION_KEYS = {label: label.lower() for label, _ in OPTIONS}

_HEADING = "Solicitud de aprobación"
_NO_AGENT = "agente sin identificar"
_NO_TITLE = "Solicitud sin título declarado."
_NO_DOES = "(sin descripción declarada)"
_NO_IMPACT = "no declarado."
_NO_ROLLBACK = "no declarado; no supongas que se puede deshacer."

class SurfaceLimitError(SealError):
    """Raised when a phrase or a question does not fit the signature surface."""


@dataclass(frozen=True)
class Surface:
    """One signature as both hosts show it (D27, D29).

    ``block`` (``text`` as a Markdown code block) is shown outside the question,
    and ``details_block`` (``details`` likewise) on a Details re-ask.
    ``question`` and ``details_question`` are the AskUserQuestion objects; both
    ask only ``short_question`` and differ by header.
    """

    approval_id: str
    text: str
    details: str
    question: dict
    details_question: dict
    short_question: str
    block: str
    details_block: str


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
    """Reject a request whose phrases would not fit the surface as written."""
    _check_line("title (what)", title, TITLE_MAX)
    _check_line("question", question, QUESTION_MAX)
    for position, item in enumerate(items, start=1):
        _check_line(f"item {position} does", item.get("does"), LINE_MAX)
        _check_line(f"item {position} impact", item.get("impact"), LINE_MAX)


def check_question(question: Mapping[str, Any]) -> None:
    """Reject a question object whose header or options exceed the host limits.

    Its question text is the whole signature: only the short question inside
    it is held to ``QUESTION_MAX``.
    """
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


def _target(item: Mapping[str, Any]) -> str:
    return item.get("command") or item.get("path") or ""


def _numbered(position: int, width: int) -> str:
    return f"{position:>{width}}  "


def _heading(payload: Mapping[str, Any]) -> list[str]:
    """The block's first two lines: who asks, and what for in human words."""
    requester = payload.get("requested_by") or {}
    return [
        f"{_HEADING} · {requester.get('agent_id') or _NO_AGENT}",
        payload.get("what") or _NO_TITLE,
    ]


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


def _text(payload: Mapping[str, Any], items: Sequence[Mapping[str, Any]]) -> str:
    """The signature block: each command exact on one line, under ``en <carpeta>`` when it runs elsewhere."""
    lines = _heading(payload)
    width = len(str(len(items)))
    for position, item in enumerate(items, start=1):
        prefix = _numbered(position, width)
        folder = _folder(payload, item)
        lines.append("")
        if folder:
            lines += [f"{prefix}en {folder}", " " * len(prefix) + _target(item)]
        else:
            lines.append(prefix + _target(item))
    return "\n".join(lines)


def _validity(
    payload: Mapping[str, Any], items: Sequence[Mapping[str, Any]], approval_id: str
) -> str:
    fingerprints = [command_fingerprint(_target(item)) for item in items]
    if len(fingerprints) == 1:
        prints = f"huella {fingerprints[0]}"
    else:
        prints = "huellas " + " · ".join(
            f"{position} {fingerprint}"
            for position, fingerprint in enumerate(fingerprints, start=1)
        )
    window = payload.get("window_minutes") or WINDOW_MINUTES
    return f"Vale {window} min · ID {approval_id} · {prints}"


def _details(
    payload: Mapping[str, Any], items: Sequence[Mapping[str, Any]], approval_id: str
) -> str:
    """The Details block: the signature's layout with what each command does and its impact in place of the command."""
    lines = _heading(payload)
    width = len(str(len(items)))
    for position, item in enumerate(items, start=1):
        prefix = _numbered(position, width)
        lines += [
            "",
            prefix + (item.get("does") or _NO_DOES),
            " " * len(prefix) + f"Impacto: {item.get('impact') or _NO_IMPACT}",
        ]
    lines += [
        "",
        f"Rollback: {payload.get('rollback_hint') or _NO_ROLLBACK}",
        _validity(payload, items, approval_id),
    ]
    return "\n".join(lines)


def _code_block(text: str) -> str:
    """``text`` as a Markdown code block whose fence no backtick run inside it can close."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{text}\n{fence}"


def _short_question(payload: Mapping[str, Any]) -> str:
    short = payload.get("question") or DEFAULT_QUESTION
    _check_line("question", short, QUESTION_MAX)
    return short


def _question(short: str, header: str) -> dict:
    question = {
        "question": short,
        "header": header,
        "options": [{"label": label, "description": text} for label, text in OPTIONS],
        "multiSelect": False,
    }
    check_question(question)
    return question


def _render(payload: Mapping[str, Any], approval_id: str, position: int, total: int) -> Surface:
    items = _items(payload)
    if not items:
        raise SurfaceLimitError(f"approval {approval_id} seals nothing to present")
    text = _text(payload, items)
    details = _details(payload, items, approval_id)
    short = _short_question(payload)
    return Surface(
        approval_id=approval_id,
        text=text,
        details=details,
        question=_question(short, batch_header(position, total)),
        details_question=_question(short, batch_header(position, total, details=True)),
        short_question=short,
        block=_code_block(text),
        details_block=_code_block(details),
    )


def render(payload: Mapping[str, Any], approval_id: str) -> Surface:
    """Render one sealed request; a phrase the payload does not declare is stated as absent."""
    return _render(payload, approval_id, 1, 1)


def batch_header(position: int, total: int, *, details: bool = False) -> str:
    """The header of the signature, or of its Details re-ask, at 1-based ``position`` among ``total`` asked in one call."""
    if total == 1:
        return DETAILS_HEADER if details else HEADER
    return f"{'Detalle' if details else 'Aprob.'} {position}/{total}"


def batch_questions(
    payload: Mapping[str, Any], approval_id: str, position: int, total: int,
) -> tuple[dict, dict]:
    """The signature and Details question objects a request shows at ``position`` of a ``total``-signature call."""
    rendered = _render(payload, approval_id, position, total)
    return rendered.question, rendered.details_question


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
        or _HEADING in str(question.get("question") or "")
    )


def render_batch(
    requests: Sequence[tuple[Mapping[str, Any], str]],
) -> list[Surface]:
    """Render 1 to 4 requests asked in one question call, one question per signature.

    A lone signature keeps the header ``Aprobación``; in a batch each header
    names its position, ``Aprob. N/M``. Short questions must differ: the host
    indexes each answer by its question text, and the same short question
    would leave a signature and its Details re-ask indistinguishable from
    another's.
    """
    total = len(requests)
    if not 1 <= total <= BATCH_MAX:
        raise SurfaceLimitError(
            f"a question call presents 1 to {BATCH_MAX} signatures, not {total}"
        )
    surfaces = [
        _render(payload, approval_id, position, total)
        for position, (payload, approval_id) in enumerate(requests, start=1)
    ]
    texts = [_short_question(payload) for payload, _ in requests]
    if len(set(texts)) != len(texts):
        raise SurfaceLimitError(
            "the signatures of one question call need distinct question texts"
        )
    return surfaces
