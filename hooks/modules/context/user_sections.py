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

# The share of the host's per-string context cap the user's rows are sized
# for. Past it they still ship whole -- half a rule reads as a whole one --
# and overflow_line names the excess so it is curated rather than cut.
USER_ROWS_BUDGET = 4_000


def overflow_line(total: int) -> str:
    """The visible line that names how far the user's rows exceed their share."""
    return (
        f"(The user's rows add up to {total:,} chars, over the {USER_ROWS_BUDGET:,} "
        f"they are sized for; all of them ship whole. Consolidate them with "
        f"`gaia memory`.)"
    )


def render_user_sections(rows: list[dict]) -> str:
    """The facts section then the preferences section, each body whole; "" when no row has a body."""
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
    if total > USER_ROWS_BUDGET:
        sections.append(overflow_line(total))
    return "\n\n".join(sections)
