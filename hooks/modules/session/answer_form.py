"""Measure, never block: the bold spans, headers and tables a final answer carries when a conversation preference forbids them."""

from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

FORM_MEASURED_EVENT = "answer.form_measured"

_FORM_WORDS = {
    "bold": {"bold", "negrita", "negritas"},
    "headers": {"header", "headers", "heading", "headings", "encabezado", "encabezados"},
    "tables": {"table", "tables", "tabla", "tablas"},
}
_NEGATIONS = {"no", "never", "without", "avoid", "sin", "nunca", "evita", "evitar"}

_FENCE = re.compile(r"^\s*(```|~~~).*?^\s*\1", re.MULTILINE | re.DOTALL)
_BOLD = re.compile(r"\*\*[^*\n]+?\*\*|__[^_\n]+?__")
_HEADER = re.compile(r"^ {0,3}#{1,6}\s+\S", re.MULTILINE)
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", re.MULTILINE)


def _words(text: str) -> set[str]:
    folded = unicodedata.normalize("NFKD", text).casefold()
    return set(re.findall(r"[a-z]+", "".join(c for c in folded if not unicodedata.combining(c))))


def forbidden_forms(titles: list[str]) -> list[str]:
    """The forms (bold, headers, tables) some title both names and negates, in that order."""
    forbidden = set()
    for title in titles:
        words = _words(title)
        if words & _NEGATIONS:
            forbidden |= {form for form, names in _FORM_WORDS.items() if words & names}
    return [form for form in _FORM_WORDS if form in forbidden]


def count_forms(text: str) -> dict[str, int]:
    """Bold spans, header lines and tables in ``text``, fenced code excluded."""
    prose = _FENCE.sub("", text or "")
    return {
        "bold": len(_BOLD.findall(prose)),
        "headers": len(_HEADER.findall(prose)),
        "tables": len(_TABLE_RULE.findall(prose)),
    }


def measure_final_answer(session_id: str, answer: str, rows: list[dict]) -> dict | None:
    """Record one ``answer.form_measured`` event for ``answer`` when a conversation row forbids a form; return its meta, else None. Never raises."""
    try:
        from modules.context.user_sections import is_conversation_preference, memory_ref, title_of

        governing = [row for row in rows if is_conversation_preference(row)]
        forbidden = forbidden_forms([title_of(row) for row in governing])
        if not answer or not forbidden:
            return None
        counts = count_forms(answer)
        meta = {
            "session_id": session_id,
            "forbidden": forbidden,
            "counts": {form: counts[form] for form in forbidden},
            "violations": sum(counts[form] for form in forbidden),
            "rows": [memory_ref(row) for row in governing],
        }
        from gaia.store.writer import write_harness_event

        write_harness_event(event_type=FORM_MEASURED_EVENT, source="hook", meta=meta)
        return meta
    except Exception as exc:
        logger.debug("answer form not measured (non-fatal): %s", exc)
        return None
