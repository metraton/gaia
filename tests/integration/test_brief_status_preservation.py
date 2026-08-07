"""
Regression tests for the brief-edit silent status-loss defect.

Symptom (confirmed by reading `gaia/briefs/store.py` and
`gaia/briefs/serializer.py` before this fix): `acceptance_criteria.status`
and `milestones.status` were never SELECTed by `get_brief` / `get_brief_by_id`
(invisible in both `gaia brief show` and `--json`), and the interactive edit
round-trip (`get_brief` -> `serialize_brief_to_markdown` -> $EDITOR ->
`parse_brief_markdown` -> `upsert_brief`) reset every child row's status to
the schema DEFAULT `'pending'` on every save, because `upsert_brief`'s full
DELETE + re-INSERT sync never carried `status` through.

The fix: `get_brief`/`get_brief_by_id` now SELECT `status`; `upsert_brief`
snapshots each child row's status by its natural identity (`ac_id` for ACs,
`name` for milestones) before the DELETE and reapplies it on INSERT, so an
edit that does not touch a given AC/milestone leaves its status exactly as
it was. Status is preserved, never read from the markdown -- `gaia
ac/milestone set-status` remain the only way to change it.

Tests use a tmp_path-routed DB via GAIA_DATA_DIR monkeypatch (same fixture as
`test_brief_cli.py`) so they never touch the user's real ~/.gaia/gaia.db.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Route gaia.paths.db_path() to a tmp dir so tests are isolated."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


def _seed_brief_with_two_acs_and_milestones(tmp_db):
    """Create a brief with 2 ACs and 2 milestones, then drive AC-1/M1 into a
    non-default status via the canonical set-status verbs. AC-2/M2 stay at
    the schema default 'pending'.
    """
    from gaia.briefs import upsert_brief
    from gaia.store.writer import set_ac_status, set_milestone_status

    upsert_brief("me", "status-brief", {
        "status": "draft",
        "title": "Status Brief",
        "objective": "verify status preservation",
        "acceptance_criteria": [
            {"ac_id": "AC-1", "description": "first criterion"},
            {"ac_id": "AC-2", "description": "second criterion"},
        ],
        "milestones": [
            {"name": "M1: bootstrap", "description": "create schema"},
            {"name": "M2: cli", "description": "expose handlers"},
        ],
    }, db_path=tmp_db)

    set_ac_status("me", "status-brief", "AC-1", "done", db_path=tmp_db)
    set_milestone_status("me", "status-brief", "M1: bootstrap", "blocked",
                          db_path=tmp_db)


# ---------------------------------------------------------------------------
# The central regression test: the defect this brief exists to fix.
# ---------------------------------------------------------------------------

def test_interactive_edit_round_trip_preserves_untouched_status(tmp_db):
    """A full interactive-edit round-trip that touches neither AC nor
    milestone leaves every status exactly as it was.

    This is the exact defect path: `get_brief` -> `serialize_brief_to_markdown`
    -> (simulated $EDITOR touching only the objective) ->
    `parse_brief_markdown` -> `upsert_brief`. Before the fix, AC-1's 'done'
    and M1's 'blocked' would silently reset to 'pending'.
    """
    from gaia.briefs import (
        get_brief,
        serialize_brief_to_markdown,
        parse_brief_markdown,
        upsert_brief,
    )

    _seed_brief_with_two_acs_and_milestones(tmp_db)

    before = get_brief("me", "status-brief", db_path=tmp_db)
    assert before["acceptance_criteria"][0]["status"] == "done"
    assert before["acceptance_criteria"][1]["status"] == "pending"
    assert before["milestones"][0]["status"] == "blocked"
    assert before["milestones"][1]["status"] == "pending"

    # Simulate the interactive $EDITOR: only the objective changes.
    initial = serialize_brief_to_markdown(before)
    edited = initial.replace(
        "verify status preservation", "verify status preservation (edited)"
    )
    assert edited != initial, "test bug: edit substitution was a no-op"

    parsed = parse_brief_markdown(edited)
    upsert_brief("me", "status-brief", parsed, db_path=tmp_db)

    after = get_brief("me", "status-brief", db_path=tmp_db)
    assert "(edited)" in after["objective"]

    acs_by_id = {a["ac_id"]: a for a in after["acceptance_criteria"]}
    assert acs_by_id["AC-1"]["status"] == "done", (
        "AC-1's status must survive an edit that never touched it"
    )
    assert acs_by_id["AC-2"]["status"] == "pending"

    ms_by_name = {m["name"]: m for m in after["milestones"]}
    assert ms_by_name["M1: bootstrap"]["status"] == "blocked", (
        "M1's status must survive an edit that never touched it"
    )
    assert ms_by_name["M2: cli"]["status"] == "pending"


def test_headless_field_edit_preserves_child_row_status(tmp_db):
    """`gaia brief edit --headless --field=...` never touches ACs/milestones
    (it patches a single `briefs` column via `update_brief_field`), so every
    child status must remain exactly as it was.
    """
    import argparse
    from cli.brief import _cmd_edit
    from gaia.briefs import get_brief

    _seed_brief_with_two_acs_and_milestones(tmp_db)

    args = argparse.Namespace(
        name="status-brief", workspace="me", headless=True,
        field="objective", content="brand new objective", append=False,
        json=False,
    )
    rc = _cmd_edit(args)
    assert rc == 0

    brief = get_brief("me", "status-brief", db_path=tmp_db)
    assert brief["objective"] == "brand new objective"

    acs_by_id = {a["ac_id"]: a for a in brief["acceptance_criteria"]}
    assert acs_by_id["AC-1"]["status"] == "done"
    assert acs_by_id["AC-2"]["status"] == "pending"
    ms_by_name = {m["name"]: m for m in brief["milestones"]}
    assert ms_by_name["M1: bootstrap"]["status"] == "blocked"
    assert ms_by_name["M2: cli"]["status"] == "pending"


# ---------------------------------------------------------------------------
# New / removed child rows during an edit
# ---------------------------------------------------------------------------

def test_new_ac_added_in_editor_is_born_pending(tmp_db):
    """An AC hand-added in the editor (no prior row to match) starts
    'pending' -- the schema DEFAULT is correct for a genuinely new row.
    """
    from gaia.briefs import (
        get_brief,
        serialize_brief_to_markdown,
        parse_brief_markdown,
        upsert_brief,
    )

    _seed_brief_with_two_acs_and_milestones(tmp_db)

    brief = get_brief("me", "status-brief", db_path=tmp_db)
    brief["acceptance_criteria"].append({
        "ac_id": "AC-3",
        "description": "hand-added in the editor",
        "evidence_type": None,
        "evidence_shape": None,
        "artifact_path": None,
    })
    edited_text = serialize_brief_to_markdown(brief)
    parsed = parse_brief_markdown(edited_text)
    upsert_brief("me", "status-brief", parsed, db_path=tmp_db)

    final = get_brief("me", "status-brief", db_path=tmp_db)
    acs_by_id = {a["ac_id"]: a for a in final["acceptance_criteria"]}
    assert acs_by_id["AC-3"]["status"] == "pending"
    # Untouched pre-existing ACs are unaffected by the addition.
    assert acs_by_id["AC-1"]["status"] == "done"
    assert acs_by_id["AC-2"]["status"] == "pending"


def test_ac_removed_in_editor_does_not_resurrect(tmp_db):
    """An AC deleted in the editor disappears -- it is not reinserted with
    its old (or any) status once dropped from the round-trip payload.
    """
    from gaia.briefs import (
        get_brief,
        serialize_brief_to_markdown,
        parse_brief_markdown,
        upsert_brief,
    )

    _seed_brief_with_two_acs_and_milestones(tmp_db)

    brief = get_brief("me", "status-brief", db_path=tmp_db)
    brief["acceptance_criteria"] = [
        a for a in brief["acceptance_criteria"] if a["ac_id"] != "AC-2"
    ]
    edited_text = serialize_brief_to_markdown(brief)
    parsed = parse_brief_markdown(edited_text)
    upsert_brief("me", "status-brief", parsed, db_path=tmp_db)

    final = get_brief("me", "status-brief", db_path=tmp_db)
    ac_ids = {a["ac_id"] for a in final["acceptance_criteria"]}
    assert "AC-2" not in ac_ids, "a removed AC must not resurrect"
    assert "AC-1" in ac_ids

    # AC-1's own status is unaffected by AC-2's removal.
    ac1 = next(a for a in final["acceptance_criteria"] if a["ac_id"] == "AC-1")
    assert ac1["status"] == "done"

    # Re-adding an AC with the same id later starts fresh at 'pending' --
    # it is a new row, not a resurrection of the deleted one's status.
    brief_again = get_brief("me", "status-brief", db_path=tmp_db)
    brief_again["acceptance_criteria"].append({
        "ac_id": "AC-2",
        "description": "re-added after deletion",
        "evidence_type": None,
        "evidence_shape": None,
        "artifact_path": None,
    })
    text_again = serialize_brief_to_markdown(brief_again)
    upsert_brief("me", "status-brief", parse_brief_markdown(text_again),
                 db_path=tmp_db)
    reloaded = get_brief("me", "status-brief", db_path=tmp_db)
    ac2 = next(a for a in reloaded["acceptance_criteria"] if a["ac_id"] == "AC-2")
    assert ac2["status"] == "pending"


# ---------------------------------------------------------------------------
# Visibility: `gaia brief show` exposes status in markdown and JSON
# ---------------------------------------------------------------------------

def test_show_markdown_exposes_ac_and_milestone_status(tmp_db, tmp_path,
                                                         monkeypatch, capsys):
    import argparse
    from cli.brief import _cmd_show

    monkeypatch.chdir(tmp_path)
    _seed_brief_with_two_acs_and_milestones(tmp_db)

    args = argparse.Namespace(name="status-brief", workspace="me", json=False)
    rc = _cmd_show(args)
    captured = capsys.readouterr()
    assert rc == 0, captured.err

    assert "(status: done)" in captured.out
    assert "[status: blocked]" in captured.out


def test_show_json_exposes_ac_and_milestone_status(tmp_db, tmp_path,
                                                     monkeypatch, capsys):
    import argparse
    import json as _json
    from cli.brief import _cmd_show

    monkeypatch.chdir(tmp_path)
    _seed_brief_with_two_acs_and_milestones(tmp_db)

    args = argparse.Namespace(name="status-brief", workspace="me", json=True)
    rc = _cmd_show(args)
    captured = capsys.readouterr()
    assert rc == 0, captured.err

    payload = _json.loads(captured.out)
    acs_by_id = {a["ac_id"]: a for a in payload["acceptance_criteria"]}
    assert acs_by_id["AC-1"]["status"] == "done"
    assert acs_by_id["AC-2"]["status"] == "pending"
    ms_by_name = {m["name"]: m for m in payload["milestones"]}
    assert ms_by_name["M1: bootstrap"]["status"] == "blocked"
    assert ms_by_name["M2: cli"]["status"] == "pending"


# ---------------------------------------------------------------------------
# Serializer-level: the trailing display marker never corrupts round-trip data
# ---------------------------------------------------------------------------

def test_milestone_status_marker_is_display_only_not_fed_back():
    """The `[status: X]` marker appears in the rendered markdown but is
    stripped on parse -- it must never leak into `description`, and the
    parsed milestone dict carries no `status` key (status is preserved by
    the store, never read from markdown).
    """
    from gaia.briefs.serializer import (
        serialize_brief_to_markdown,
        parse_brief_markdown,
    )

    brief = {
        "title": "T",
        "milestones": [
            {"name": "M1: bootstrap", "description": "create schema",
             "status": "done"},
        ],
    }
    text = serialize_brief_to_markdown(brief)
    assert "[status: done]" in text

    parsed = parse_brief_markdown(text)
    milestone = parsed["milestones"][0]
    # The marker must never leak into the parsed description, and the
    # parser must not manufacture a `status` key from it (status flows
    # only through the DB-side preserve-by-name match in `upsert_brief`).
    # (The separator-consumption defect this comment used to note is fixed
    # in `_parse_milestones_section` -- see
    # `tests/unit/test_brief_serializer_milestone_separator.py` -- so the
    # description is asserted exactly, not merely checked for the marker's
    # absence.)
    assert milestone["description"] == "create schema"
    assert "status" not in milestone


def test_milestone_status_marker_does_not_accumulate_across_round_trips():
    """Serializing/parsing twice in a row must not accumulate additional
    `[status: X]` marker text into the description -- the corruption this
    fix specifically avoids by stripping the marker in the parser rather
    than treating it as free-form trailing description text.
    """
    from gaia.briefs.serializer import (
        serialize_brief_to_markdown,
        parse_brief_markdown,
    )

    brief = {
        "title": "T",
        "milestones": [
            {"name": "M1: bootstrap", "description": "create schema",
             "status": "done"},
        ],
    }
    text1 = serialize_brief_to_markdown(brief)
    parsed1 = parse_brief_markdown(text1)
    # Re-attach status the way get_brief would (parsed1 carries none).
    parsed1["milestones"][0]["status"] = "done"

    text2 = serialize_brief_to_markdown(parsed1)
    parsed2 = parse_brief_markdown(text2)

    assert parsed2["milestones"][0]["description"] == "create schema"
    assert text2.count("[status: done]") == 1
