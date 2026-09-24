"""The one renderer of a signature's surface: visible text, question and Details (brief D12).

Gaia composes every line a user reads; the requester only seals short phrases
(title, question, and per item what it does and its impact) and a host only
shows the result. Phrase limits are checked when a request is sealed
(:func:`check_phrases`, called by ``core.seal_request``), so an over-long
phrase is returned to its author instead of reaching a presentation; nothing
is truncated. Fixed strings follow D12 in the user's language with full
orthography (D17); only the option labels are English (D8, D11).
"""

from __future__ import annotations

import re
import shlex
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
DEFAULT_QUESTION = "¿Apruebo esta solicitud?"
OPTIONS = (
    ("Approve", "Autoriza exactamente lo que se muestra arriba"),
    ("Reject", "Rechaza la solicitud; no se ejecuta nada"),
    ("Details", "Ver qué hace cada paso y su impacto"),
)
#: The core decision each option label stands for (``core.DECISION_OPTIONS``).
OPTION_KEYS = {label: label.lower() for label, _ in OPTIONS}

_NO_AGENT = "agente sin identificar"
_NO_TITLE = "Solicitud sin título declarado."
_NO_DOES = "(sin descripción declarada)"
_NO_IMPACT = "no declarado."
_NO_ROLLBACK = "no declarado; no supongas que se puede deshacer."

class SurfaceLimitError(SealError):
    """Raised when a phrase or a question does not fit the signature surface."""


@dataclass(frozen=True)
class Surface:
    """One signature as both hosts ask it (D23).

    ``asked`` is the single string a host asks: the visible ``text``, a blank
    line, the short question. Details re-asks the signature with
    ``asked_details`` (``details``, a blank line, the short question).
    ``question`` and ``details_question`` are the AskUserQuestion objects that
    carry those two strings.
    """

    approval_id: str
    text: str
    details: str
    asked: str
    asked_details: str
    question: dict
    details_question: dict


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

def _tokens(command: str) -> Optional[list[str]]:
    """Split on whitespace keeping each token's quoting; ``None`` when unbalanced."""
    lexer = shlex.shlex(command, posix=False)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return None


def _command_lines(command: str) -> list[str]:
    """Lay one command out as D12 shows it, every token exact (D23): each flag, with its values, on its own line."""
    tokens = None if "\n" in command else _tokens(command)
    if not tokens:
        return command.split("\n")
    groups: list[list[str]] = [[]]
    for token in tokens:
        if token.startswith("-") and token != "-" and groups[-1]:
            groups.append([])
        groups[-1].append(token)
    lines = [" ".join(group) for group in groups]
    return [line + " \\" for line in lines[:-1]] + lines[-1:]


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
    return f"  {position:>{width}}  "


def _text(payload: Mapping[str, Any], items: Sequence[Mapping[str, Any]]) -> str:
    requester = payload.get("requested_by") or {}
    noun = "Archivos" if items and "path" in items[0] else "Comandos"
    lines = [
        f"Solicitud de aprobación · {requester.get('agent_id') or _NO_AGENT}",
        payload.get("what") or _NO_TITLE,
        "",
        f"{noun} ({len(items)})",
    ]
    width = len(str(len(items)))
    for position, item in enumerate(items, start=1):
        prefix = _numbered(position, width)
        first, *rest = _command_lines(_target(item))
        lines.append(prefix + first)
        lines.extend(" " * (len(prefix) + 4) + line for line in rest)
    return "\n".join(lines)


def _details(
    payload: Mapping[str, Any], items: Sequence[Mapping[str, Any]], approval_id: str
) -> str:
    lines: list[str] = []
    for position, item in enumerate(items, start=1):
        lead = f"{position}  "
        lines.append(lead + (item.get("does") or _NO_DOES))
        lines.append(" " * len(lead) + f"Impacto: {item.get('impact') or _NO_IMPACT}")
    lines.append(f"Rollback: {payload.get('rollback_hint') or _NO_ROLLBACK}")
    lines.append("Comando exacto:" if len(items) == 1 else "Comandos exactos:")
    width = len(str(len(items)))
    lines.extend(
        _numbered(position, width) + _target(item)
        for position, item in enumerate(items, start=1)
    )
    fingerprints = [command_fingerprint(_target(item)) for item in items]
    if len(fingerprints) == 1:
        prints = f"huella {fingerprints[0]}"
    else:
        prints = "huellas " + " · ".join(
            f"{position} {fingerprint}"
            for position, fingerprint in enumerate(fingerprints, start=1)
        )
    window = payload.get("window_minutes") or WINDOW_MINUTES
    lines.append(f"Vale {window} min · ID {approval_id} · {prints}")
    return "\n".join(lines)


def _short_question(payload: Mapping[str, Any]) -> str:
    short = payload.get("question") or DEFAULT_QUESTION
    _check_line("question", short, QUESTION_MAX)
    return short


def _question(asked: str, header: str) -> dict:
    question = {
        "question": asked,
        "header": header,
        "options": [{"label": label, "description": text} for label, text in OPTIONS],
        "multiSelect": False,
    }
    check_question(question)
    return question


def _render(payload: Mapping[str, Any], approval_id: str, header: str) -> Surface:
    items = _items(payload)
    if not items:
        raise SurfaceLimitError(f"approval {approval_id} seals nothing to present")
    text = _text(payload, items)
    details = _details(payload, items, approval_id)
    short = _short_question(payload)
    asked = f"{text}\n\n{short}"
    asked_details = f"{details}\n\n{short}"
    return Surface(
        approval_id=approval_id,
        text=text,
        details=details,
        asked=asked,
        asked_details=asked_details,
        question=_question(asked, header),
        details_question=_question(asked_details, header),
    )


def render(payload: Mapping[str, Any], approval_id: str) -> Surface:
    """Render one sealed request; a phrase the payload does not declare is stated as absent."""
    return _render(payload, approval_id, HEADER)


def batch_header(position: int, total: int) -> str:
    """The header of the signature at 1-based ``position`` among ``total`` asked in one call."""
    return HEADER if total == 1 else f"Aprob. {position}/{total}"


def batch_questions(
    payload: Mapping[str, Any], approval_id: str, position: int, total: int,
) -> tuple[dict, dict]:
    """The signature and Details question objects a request shows at ``position`` of a ``total``-signature call."""
    rendered = _render(payload, approval_id, batch_header(position, total))
    return rendered.question, rendered.details_question


def is_signature_question(question: Mapping[str, Any]) -> bool:
    """Whether a host question object has the shape of a signature: its header or its options."""
    header = str(question.get("header") or "")
    labels = [
        option.get("label") for option in question.get("options") or ()
        if isinstance(option, Mapping)
    ]
    return (
        header == HEADER
        or re.fullmatch(r"Aprob\. \d+/\d+", header) is not None
        or labels == [label for label, _ in OPTIONS]
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
        _render(payload, approval_id, batch_header(position, total))
        for position, (payload, approval_id) in enumerate(requests, start=1)
    ]
    texts = [_short_question(payload) for payload, _ in requests]
    if len(set(texts)) != len(texts):
        raise SurfaceLimitError(
            "the signatures of one question call need distinct question texts"
        )
    return surfaces
