"""What a memory row claims (its kind), who owns it (its owner level), and the matrix between the two.

The matrix and the refusals here are the same ones the ``trg_memory_placement_*``
triggers in ``schema.sql`` enforce; the writer applies them first so a refusal
teaches the right placement instead of surfacing as a constraint error.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import date

MEMORY_KINDS = ("fact", "preference", "decision", "lesson", "pending", "defect")

OWNER_LEVELS = ("user", "workspace", "group", "project", "gaia")

PRECEDENCE = ("gaia", "user", "workspace", "group", "project")

KINDS_BY_OWNER = {
    "user": ("fact", "preference"),
    "workspace": ("fact", "preference", "decision"),
    "group": ("fact", "preference", "decision", "lesson", "pending"),
    "project": ("fact", "preference", "decision", "lesson", "pending"),
    "gaia": ("lesson", "defect"),
}

_KIND_BY_WORD = {
    "preference": "preference", "preferencia": "preference", "regla": "preference",
    "principio": "preference", "requisito": "preference",
    "fact": "fact", "hecho": "fact", "measured": "fact", "medido": "fact",
    "decision": "decision", "decisión": "decision",
    "lesson": "lesson", "aprendizaje": "lesson",
    "pending": "pending", "pendiente": "pending",
    "bug": "defect", "defect": "defect", "defecto": "defect",
}

_KIND_BY_TYPE = {
    "user": "fact", "decision": "decision", "negative": "lesson",
    "feedback": "defect", "atom": "fact", "project": "fact",
}

_MEMORY_REF_RE = re.compile(r"^memory:([^/\s]+)/([^\s]+)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _fold(word: str) -> str:
    decomposed = unicodedata.normalize("NFKD", word)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def kind_from_description(description: str | None) -> str | None:
    """The kind the first word of ``description`` names, in English or Spanish, or None."""
    words = (description or "").split(maxsplit=1)
    if not words:
        return None
    folded = _fold(words[0].rstrip(":"))
    return next((kind for word, kind in _KIND_BY_WORD.items() if _fold(word) == folded), None)


def default_kind(mem_type: str, description: str | None, class_: str | None) -> str:
    """The kind a row gets when none is given: its claim word, else its type's default; a project thread is pending."""
    worded = kind_from_description(description)
    if mem_type == "project" and worded == "defect":
        return "pending"
    if worded:
        return worded
    if mem_type == "project" and class_ == "thread":
        return "pending"
    return _KIND_BY_TYPE.get(mem_type, "fact")


def strip_kind_word(description: str | None) -> str:
    """``description`` without the claim word that opens it, so a title reads as the rule itself."""
    text = (description or "").strip()
    words = text.split(maxsplit=1)
    if words and kind_from_description(text) and len(words) == 2:
        return words[1].strip()
    return text


def names_registered_project(con: sqlite3.Connection, initiative: str) -> bool:
    """Whether ``initiative`` is the canonical key of a registered project (``projects.name`` folded)."""
    from gaia.store.writer import normalize_initiative

    rows = con.execute("SELECT name FROM projects").fetchall()
    return any(normalize_initiative(r[0]) == initiative for r in rows)


def derive_owner_level(
    mem_type: str, initiative: str | None, project_ref: str | None, con: sqlite3.Connection | None,
) -> str:
    """The owner a write lands on: user by type, gaia by host initiative, project by anchor or project key, else group, else workspace."""
    from gaia.store.writer import HOST_SCOPED_INITIATIVES

    if mem_type == "user":
        return "user"
    if initiative in HOST_SCOPED_INITIATIVES:
        return "gaia"
    if project_ref:
        return "project"
    if initiative:
        if con is not None and names_registered_project(con, initiative):
            return "project"
        return "group"
    return "workspace"


def placement_refusal(owner_level: str | None, kind: str | None, until: str | None = None) -> str | None:
    """Why ``kind`` may not sit at ``owner_level`` (or carry ``until``), as the triggers phrase it, or None."""
    if kind is not None and kind not in MEMORY_KINDS:
        return f"memory kind must be one of {', '.join(MEMORY_KINDS)}, not {kind!r}"
    if owner_level is not None and owner_level not in OWNER_LEVELS:
        return f"memory owner_level must be one of {', '.join(OWNER_LEVELS)}, not {owner_level!r}"
    if owner_level and kind and kind not in KINDS_BY_OWNER[owner_level]:
        return _matrix_refusal(owner_level, kind)
    if until is not None and kind != "preference":
        return ("until belongs to a preference only: the date or the memory:<ws>/<slug> row "
                "while which it holds")
    return None


def _matrix_refusal(owner_level: str, kind: str) -> str:
    if owner_level == "user":
        return (f"the user owns facts and preferences only; a {kind} belongs to a workspace, "
                f"group or project (--workspace, --initiative or --project), a defect to gaia "
                f"(--initiative gaia_system)")
    if owner_level == "workspace":
        return (f"a workspace owns facts, preferences and decisions; a {kind} belongs to a "
                f"group or project (--initiative or --project), a defect to gaia")
    if owner_level == "gaia":
        return (f"gaia owns lessons and defects only; a {kind} about Gaia belongs to the gaia "
                f"project (--project gaia)")
    return ("a defect is Gaia failing in use and belongs to gaia (--initiative gaia_system); "
            "a bug in the product is this project's pending (--kind pending)")


def parse_until(value: str) -> str:
    """``value`` as a stored until: an ISO date, or ``memory:<ws>/<slug>``; ValueError otherwise."""
    text = (value or "").strip()
    if _DATE_RE.match(text):
        date.fromisoformat(text)
        return text
    if _MEMORY_REF_RE.match(text):
        return text
    raise ValueError(
        f"--until {value!r} is neither a date (2026-12-31) nor a memory coordinate "
        f"(memory:<ws>/<slug>, held while that row is live)"
    )


def coordinate_workspace(word: str) -> str:
    """The stored workspace a ``memory:<word>/<slug>`` coordinate names: ``user`` and ``gaia`` are the sentinels."""
    from gaia.store.writer import HOST_WORKSPACE, USER_WORKSPACE

    return {"user": USER_WORKSPACE, "gaia": HOST_WORKSPACE}.get(word, word)


def coordinate_word(workspace: str) -> str:
    """The word a coordinate uses for ``workspace``: the inverse of :func:`coordinate_workspace`."""
    from gaia.store.writer import HOST_WORKSPACE, USER_WORKSPACE

    return {USER_WORKSPACE: "user", HOST_WORKSPACE: "gaia"}.get(workspace, workspace)


def until_holds(until: str | None, con: sqlite3.Connection, today: date | None = None) -> bool:
    """Whether a preference with ``until`` still holds: the date has not passed, or the row it names is live."""
    if not until:
        return True
    if _DATE_RE.match(until):
        return (today or date.today()) <= date.fromisoformat(until)
    match = _MEMORY_REF_RE.match(until)
    if not match:
        return True
    from gaia.store.reader import not_superseded

    workspace, name = match.groups()
    workspace = coordinate_workspace(workspace)
    row = con.execute(
        "SELECT 1 FROM memory WHERE workspace = ? AND name = ? AND deleted_at IS NULL "
        f"AND COALESCE(status, '') NOT IN ('closed', 'graduated') AND {not_superseded('memory')}",
        (workspace, name),
    ).fetchone()
    return row is not None
