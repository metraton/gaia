"""The memory index sections a session birth and a dispatch kernel carry: titles and coordinates, grouped by kind.

The birth block renders the user's live facts and preferences
(``gaia.store.reader.user_memory_rows``) as ``## User memory``; the dispatch
kernel renders the preferences, facts and lessons of the dispatch's scope chain
(``gaia.store.reader.scope_memory_rows``) as ``# Scope memory``, each line
marked with the owner that holds it. Only the title and the coordinate travel;
a body is opened through its ``ref``.
"""

from __future__ import annotations

USER_HEADER = "## User memory"
SCOPE_HEADER = "# Scope memory"

# Claude Code shows only a preview of a hook context over 10,000 chars.
CONTEXT_BUDGET = 9_500
USER_ROWS_BUDGET = 4_000

KIND_ORDER = ("preference", "fact", "lesson")


def memory_ref(row: dict) -> str:
    """``memory:<owner word>/<slug>``: the coordinate ``gaia open`` and ``gaia memory show`` read."""
    from gaia.store.memory_placement import coordinate_word

    return f"memory:{coordinate_word(row['workspace'])}/{row['name']}"


def title_of(row: dict) -> str:
    """The row's one-line title: its description without the claim word, else the first line of its body."""
    from gaia.store.memory_placement import strip_kind_word

    title = strip_kind_word(row.get("description")) or (row.get("body") or "").strip().splitlines()[0:1]
    if isinstance(title, list):
        title = title[0] if title else ""
    return " ".join(title.split())


def memory_line(row: dict, *, scope: bool = False) -> str:
    """One indexed row: its quoted title, then ``until=``, ``scope=`` when asked, and its ``ref=``."""
    parts = [f'  "{title_of(row)}"']
    if row.get("until"):
        parts.append(f"until={row['until']}")
    if scope and row.get("scope"):
        parts.append(f"scope={row['scope']}")
    parts.append(f"ref={memory_ref(row)}")
    return "  ".join(parts)


def render_memory_index(rows: list[dict], *, scope: bool = False) -> list[str]:
    """``kind=<k>`` groups in :data:`KIND_ORDER`, each followed by its rows' lines; ``[]`` for no rows."""
    lines: list[str] = []
    for kind in KIND_ORDER:
        members = [row for row in rows if row.get("kind") == kind]
        if members:
            lines.append(f"kind={kind}")
            lines.extend(memory_line(row, scope=scope) for row in members)
    return lines


def tool_lines(rows: list[dict], *, cap: int | None = None, which=None) -> list[str]:
    """``tool=<binary> "<title>" [replaces=<cli>] ref=memory:user/<slug>`` for each tool whose binary resolves on PATH, at most ``cap``."""
    import shutil

    resolve = which or shutil.which
    lines: list[str] = []
    for row in rows:
        if not row.get("binary") or not resolve(row["binary"]):
            continue
        parts = [f"tool={row['binary']}", f'"{title_of(row)}"']
        if row.get("replaces"):
            parts.append(f"replaces={row['replaces']}")
        parts.append(f"ref={memory_ref(row)}")
        lines.append(" ".join(parts))
        if cap is not None and len(lines) >= cap:
            break
    return lines


def omitted_line(names: list[str]) -> str:
    """The one line that names the user rows left out of a full context."""
    return (
        f"(Left out to fit the host's context cap, stalest first: {', '.join(names)}. "
        f"Read one with `gaia memory show <slug>`.)"
    )


def fit_user_sections(rows: list[dict], max_chars: int) -> tuple[str, list[dict]]:
    """The section within ``max_chars`` and the rows it carries; whole rows leave from the end, stalest first."""
    kept = list(rows)
    omitted: list[str] = []
    text = render_user_sections(kept)
    while kept and len(text) > max_chars:
        omitted.append(kept.pop()["name"])
        text = render_user_sections(kept, omitted)
    return text, kept


def render_user_sections(rows: list[dict], omitted: list[str] = ()) -> str:
    """``## User memory`` as a kind-grouped index, then the line naming ``omitted``; "" for no rows."""
    lines = render_memory_index(rows)
    if not lines and not omitted:
        return ""
    body = [USER_HEADER, *lines]
    if omitted:
        body.append(omitted_line(list(omitted)))
    return "\n".join(body)


def render_scope_section(chain: str, rows: list[dict]) -> str:
    """``# Scope memory: <chain>`` as a kind-grouped index with ``scope=`` on every line; "" for no rows."""
    lines = render_memory_index(rows, scope=True)
    if not lines:
        return ""
    return "\n".join([f"{SCOPE_HEADER}: {chain}", *lines])
