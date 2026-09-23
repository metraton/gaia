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

_NO_AGENT = "agente sin identificar"
_NO_TITLE = "Solicitud sin título declarado."
_NO_DOES = "(sin descripción declarada)"
_NO_IMPACT = "no declarado."
_NO_ROLLBACK = "no declarado; no supongas que se puede deshacer."

#: A path token longer than this is shown as its last components behind "...";
#: a kept component longer than the second bound keeps its first characters.
#: Only the visible text abbreviates: Details carries every exact command.
_PATH_MAX = 40
_PATH_TAIL = 3
_COMPONENT_MAX = 24
_COMPONENT_KEEP = 8


class SurfaceLimitError(SealError):
    """Raised when a phrase or a question does not fit the signature surface."""


@dataclass(frozen=True)
class Surface:
    """One signature as a host shows it.

    Claude Code shows ``text`` and then asks ``question``; OpenCode asks the
    single string ``opencode`` (the text, a blank line, the short question).
    ``details`` answers the Details option on either host.
    """

    approval_id: str
    text: str
    question: dict
    details: str
    opencode: str


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
    """Reject a question object that exceeds the question, header or option limits."""
    _check_line("question", question.get("question"), QUESTION_MAX)
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

def _abbreviate_path(path: str) -> str:
    if len(path) <= _PATH_MAX:
        return path
    parts = path.strip("/").split("/")
    kept = [
        part[:_COMPONENT_KEEP] + "..." if len(part) > _COMPONENT_MAX else part
        for part in parts[-_PATH_TAIL:]
    ]
    prefix = ".../" if len(parts) > _PATH_TAIL else "/"
    return prefix + "/".join(kept)


def _abbreviate_token(token: str) -> str:
    if token.startswith("/"):
        return _abbreviate_path(token)
    flag, equals, value = token.partition("=")
    if equals and flag.startswith("-") and value.startswith("/"):
        return f"{flag}={_abbreviate_path(value)}"
    return token


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
    """Lay one command out as D12 shows it: each flag, with its values, on its own line."""
    tokens = None if "\n" in command else _tokens(command)
    if not tokens:
        return command.split("\n")
    groups: list[list[str]] = [[]]
    for token in tokens:
        if token.startswith("-") and token != "-" and groups[-1]:
            groups.append([])
        groups[-1].append(_abbreviate_token(token))
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


def _question(payload: Mapping[str, Any], header: str) -> dict:
    question = {
        "question": payload.get("question") or DEFAULT_QUESTION,
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
    question = _question(payload, header)
    return Surface(
        approval_id=approval_id,
        text=text,
        question=question,
        details=_details(payload, items, approval_id),
        opencode=f"{text}\n\n{question['question']}",
    )


def render(payload: Mapping[str, Any], approval_id: str) -> Surface:
    """Render one sealed request; a phrase the payload does not declare is stated as absent."""
    return _render(payload, approval_id, HEADER)


def render_batch(
    requests: Sequence[tuple[Mapping[str, Any], str]],
) -> list[Surface]:
    """Render 1 to 4 requests asked in one question call, one question per signature.

    A lone signature keeps the header ``Aprobación``; in a batch each header
    names its position, ``Aprob. N/M``. Question texts must differ: the host
    indexes each answer by its question text, so a repeated text could not be
    tied back to its own signature.
    """
    total = len(requests)
    if not 1 <= total <= BATCH_MAX:
        raise SurfaceLimitError(
            f"a question call presents 1 to {BATCH_MAX} signatures, not {total}"
        )
    surfaces = [
        _render(payload, approval_id, HEADER if total == 1 else f"Aprob. {position}/{total}")
        for position, (payload, approval_id) in enumerate(requests, start=1)
    ]
    texts = [surface.question["question"] for surface in surfaces]
    if len(set(texts)) != len(texts):
        raise SurfaceLimitError(
            "the signatures of one question call need distinct question texts"
        )
    return surfaces
