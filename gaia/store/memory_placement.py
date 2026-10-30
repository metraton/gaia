"""What a memory row claims (its kind), who owns it (its owner level), and the matrix between the two.

The matrix and the refusals here are the same ones the ``trg_memory_placement_*``
triggers in ``schema.sql`` enforce; the writer applies them first so a refusal
teaches the right placement instead of surfacing as a constraint error. The
same tables render ``gaia map memory`` and the owner matrix in
``gaia memory add --help``, so the two surfaces cannot drift apart.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import sys
import unicodedata
from datetime import date
from pathlib import Path

MEMORY_KINDS = ("fact", "preference", "decision", "lesson", "pending", "defect", "tool")

OWNER_LEVELS = ("user", "workspace", "group", "project", "gaia")

PRECEDENCE = ("gaia", "user", "workspace", "group", "project")

KINDS_BY_OWNER = {
    "user": ("fact", "preference", "tool"),
    "workspace": ("fact", "preference", "decision"),
    "group": ("fact", "preference", "decision", "lesson", "pending"),
    "project": ("fact", "preference", "decision", "lesson", "pending"),
    "gaia": ("lesson", "defect"),
}

KIND_MEANING = {
    "fact": "true, commands nothing",
    "preference": "a rule every output on a surface obeys; the title IS the rule",
    "decision": "one choice among alternatives, with its why",
    "lesson": "learned, or a dead end",
    "pending": "work owed",
    "defect": "Gaia failing in use (a bug in a product is that project's pending)",
    "tool": "a program built for the user; --binary names it, the title says how to use it",
}

OWNER_FLAGS = {
    "user": "--type=user (no scope flag)",
    "workspace": "--workspace=<ws> alone",
    "group": "--initiative=<group key> --workspace=<ws>",
    "project": "--project=<name> --workspace=<ws>",
    "gaia": "--initiative=gaia_system --workspace=<ws>",
}

TOOL_TITLE_MAX = 80

_KIND_BY_WORD = {
    "preference": "preference", "preferencia": "preference", "regla": "preference",
    "principio": "preference", "requisito": "preference",
    "fact": "fact", "hecho": "fact", "measured": "fact", "medido": "fact",
    "decision": "decision", "decisión": "decision",
    "lesson": "lesson", "aprendizaje": "lesson",
    "pending": "pending", "pendiente": "pending",
    "bug": "defect", "defect": "defect", "defecto": "defect",
    "tool": "tool", "herramienta": "tool",
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
    if kind == "tool":
        return ("a tool belongs to the user (--type=user --kind=tool --binary=<name>): it is a "
                "program built for him, whichever project it serves")
    if owner_level == "user":
        return (f"the user owns facts, preferences and tools only; a {kind} belongs to a workspace, "
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


def classifier_models_cli(name: str) -> bool:
    """Whether the security classifier has rules keyed on ``name``, so a wrapper may declare it replaces that CLI."""
    hooks = Path(__file__).resolve().parents[2] / "hooks"
    if str(hooks) not in sys.path:
        sys.path.insert(0, str(hooks))
    try:
        from modules.security.mutative_verbs import _is_unmodelled_cli
    except ImportError:
        return False
    return not _is_unmodelled_cli(name)


def tool_refusal(
    kind: str | None, description: str | None, binary: str | None, replaces: str | None,
    *, which=shutil.which,
) -> str | None:
    """Why a row's tool columns are refused: a non-tool carrying them, or a tool without a detected binary, a modelled ``replaces`` and a short title."""
    if kind != "tool":
        if binary or replaces:
            return ("--binary and --replaces belong to kind=tool only: a tool is a program "
                    "built for the user; another kind is described without them")
        return None
    if not binary:
        return "a tool names its program: --binary <name>, the executable on PATH the row is about"
    if not which(binary):
        return (f"--binary {binary!r} is not on PATH: a tool row describes a program this machine "
                f"can run; install it or fix the name first")
    if replaces and not classifier_models_cli(replaces):
        return (f"--replaces {replaces!r} names no CLI the classifier models (gh, kubectl, git, "
                f"...): replaces tells the hooks to judge {binary} as that CLI, so it must be one "
                f"they know")
    title = strip_kind_word(description)
    if not title:
        return ("a tool's title is how to use it: --description '<how to invoke it, when>' "
                f"(at most {TOOL_TITLE_MAX} characters); the why goes in the body")
    if len(title) > TOOL_TITLE_MAX:
        return (f"a tool's title is at most {TOOL_TITLE_MAX} characters (how to use it; the why "
                f"goes in the body), this one has {len(title)}")
    return None


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


def owner_matrix_text() -> str:
    """The owner -> kinds matrix with the flags that place a row on each owner; one text for help and map."""
    width = max(len(owner) for owner in OWNER_LEVELS)
    lines = ["Owners and the kinds each may hold (any other pair is refused with the right home):"]
    for owner in OWNER_LEVELS:
        lines.append(f"  {owner.ljust(width)}  {', '.join(KINDS_BY_OWNER[owner]).ljust(44)}  {OWNER_FLAGS[owner]}")
    return "\n".join(lines)


def kinds_text() -> str:
    """Every kind with its meaning; the list is closed."""
    width = max(len(kind) for kind in MEMORY_KINDS)
    return "\n".join(f"  {kind.ljust(width)}  {KIND_MEANING[kind]}" for kind in MEMORY_KINDS)


def model_page() -> str:
    """``gaia map memory``: the objects, who writes, where each kind is delivered, supersession and until."""
    return "\n".join([
        "memory -- what Gaia keeps across sessions and where it reaches",
        "",
        "OBJECTS",
        "  A row: (workspace, name) with a title (description), a body, a kind and an owner.",
        "  Three floors: events and episodes are automatic evidence; curated rows are deliberate.",
        "  A link: supersedes | relates_to | derived_from | graduated_to between two rows.",
        "",
        f"KINDS -- a closed list of {len(MEMORY_KINDS)}: {', '.join(MEMORY_KINDS)}",
        kinds_text(),
        "",
        owner_matrix_text(),
        "",
        "PRECEDENCE  " + " < ".join(PRECEDENCE) + "  (most specific wins; consent, security and the contract never move)",
        "",
        "WHO WRITES",
        "  The orchestrator, from its console (add, append, reclassify, link), after Skill memory;",
        "  gaia-operator on its adjudicated instruction; every other specialist proposes in memory_delta.",
        "  --kind is required; the title is the rule for a preference and the how-to for a tool.",
        "  Something built for the user (a script, a wrapper, a launcher) is --type=user --kind=tool",
        "  --binary=<name> [--replaces=<cli>]: the hooks then judge <name> as <cli>.",
        "",
        "DELIVERED WHERE",
        "  ## User memory (birth, orchestrator only): the user's live facts and preferences as an",
        "    index, kind-grouped, each line the title and ref=memory:user/<slug>; a body is opened",
        "    with gaia memory show <slug>.",
        "  # Scope memory (kernel, dispatched agent): preferences, facts and lessons of its",
        "    workspace > group > project chain, each line scope=; never a user row -- the user's",
        "    preferences that touch the output travel written in the goal.",
        "  Environment (birth and kernel, one renderer): tool=<binary> \"<title>\" [replaces=<cli>]",
        "    ref=memory:user/<slug>, only while the binary is detected; the kernel carries at most 5.",
        "  Project card (gaia context project <name>): the project's rows indexed by kind.",
        "  Standing notes (kernel): the project's anchors, slug and one line.",
        "",
        "SUPERSESSION",
        "  Knowledge changes by a new row plus gaia memory link <new> <old> --kind=supersedes, only",
        "  between rows of one owner and kind; the old row leaves every injection. --replace rewrites",
        "  in place only to correct an error (T3). A kind change is graduated_to or derived_from.",
        "",
        "UNTIL",
        "  A preference may carry --until <date> or --until memory:<ws>/<slug>: injected until that",
        "  date, or while that row (a defect it works around) is live. Refused on any other kind.",
        "",
        "VERBS  gaia memory add --help | show <slug> | search | get-relevant --initiative <k> | link",
        "       gaia doctor lists tool rows whose binary is absent and authored executables with no row.",
    ])
