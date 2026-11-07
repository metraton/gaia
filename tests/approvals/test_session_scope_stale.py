"""`session_scope`: the default view drops stale rows, `--include-stale` brings every withheld row back."""

from __future__ import annotations

from gaia.approvals.reading import session_scope


def _row(approval_id, *, state="pending", stale=False, session="sess-1"):
    return {"id": approval_id, "state": state, "stale": stale, "session_id": session}


ROWS = [
    _row("fresh"),
    _row("old", stale=True),
    _row("orphan", state="orphaned"),
    _row("lapsed", state="expired", stale=True),
]


def test_default_view_excludes_stale_pending_rows_of_this_session():
    shown, _ = session_scope(ROWS, "sess-1")

    assert [r["id"] for r in shown] == ["fresh"]


def test_tally_counts_the_stale_pending_row_as_stale_not_live():
    _, tally = session_scope(ROWS, "sess-1")

    assert (tally["live_here"], tally["other_sessions"], tally["stale"]) == (1, 0, 3)


def test_include_stale_returns_stale_orphaned_and_expired():
    shown, _ = session_scope(ROWS, "sess-1", include_stale=True)

    assert {r["id"] for r in shown} == {"fresh", "old", "orphan", "lapsed"}
