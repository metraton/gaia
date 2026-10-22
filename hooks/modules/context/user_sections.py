"""The user's standing rows as the two sections a session and a dispatch both carry.

The session birth block renders the selection
(``gaia.store.reader.user_anchor_rows``) through this one renderer; the
dispatch kernel renders the same selection minus the rows whose ``audience`` is
the orchestrator alone, so a subagent knows the user by the orchestrator's text
less those rows. Facts and
preferences are told apart by the claim word the row's description opens
with (``gaia.store.memory_claims``); a row that opens with no preference word is
a fact about the user.
"""

from __future__ import annotations

USER_HEADER = "## The user"
PREFERENCES_HEADER = "## User preferences"

# Claude Code replaces a hook's additionalContext string over 10,000 chars
# with a 2,000-char preview and a file path, without telling the model; every
# context Gaia injects is held under this, on either host.
CONTEXT_BUDGET = 9_500

# The share of that cap the user's rows are sized for. Past it they still ship
# whole while the block has room, and overflow_line names the excess so it is
# curated rather than cut.
USER_ROWS_BUDGET = 4_000


def overflow_line(total: int) -> str:
    """The visible line that names how far the user's rows exceed their share."""
    return (
        f"(The user's rows add up to {total:,} chars, over the {USER_ROWS_BUDGET:,} "
        f"they are sized for; all of them ship whole. Consolidate them with "
        f"`gaia memory`.)"
    )


def omitted_line(names: list[str]) -> str:
    """The one line that names the user rows left out of a full context."""
    return (
        f"(Left out to fit the host's context cap, stalest first: {', '.join(names)}. "
        f"Read one with `gaia memory show <slug>`; consolidate them with `gaia memory`.)"
    )


def fit_user_sections(rows: list[dict], max_chars: int) -> tuple[str, list[dict]]:
    """The sections within ``max_chars`` and the rows they carry, dropping whole rows from the end.

    ``rows`` come freshest first (``gaia.store.reader.user_anchor_rows``), so
    the stalest row leaves first; a row is never cut.
    """
    kept = [row for row in rows if (row.get("body") or "").strip()]
    omitted: list[str] = []
    text = render_user_sections(kept)
    while kept and len(text) > max_chars:
        omitted.insert(0, kept.pop()["name"])
        text = render_user_sections(kept, omitted)
    return text, kept


def render_user_sections(rows: list[dict], omitted: list[str] = ()) -> str:
    """The facts section then the preferences section, each body whole, then the line naming ``omitted``."""
    from gaia.store.memory_claims import PREFERENCE_KIND, memory_claim_kind

    facts: list[str] = []
    preferences: list[str] = []
    for row in rows:
        body = (row.get("body") or "").strip()
        if not body:
            continue
        if memory_claim_kind(row.get("description")) == PREFERENCE_KIND:
            preferences.append(body)
        else:
            facts.append(body)

    sections = [
        header + "\n\n" + "\n\n".join(bodies)
        for header, bodies in ((USER_HEADER, facts), (PREFERENCES_HEADER, preferences))
        if bodies
    ]
    total = sum(len(body) for body in facts + preferences)
    if omitted:
        sections.append(omitted_line(list(omitted)))
    elif total > USER_ROWS_BUDGET:
        sections.append(overflow_line(total))
    return "\n\n".join(sections)
