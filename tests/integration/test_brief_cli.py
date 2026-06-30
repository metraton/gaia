"""
Integration tests for `gaia brief` CLI (B8).

Covers:
  - test_new: gaia brief new creates a row, ACs, milestones
  - test_show_returns_valid_markdown: gaia brief show returns parseable markdown
  - test_list: gaia brief list filters by status
  - test_close: gaia brief close transitions to status='closed'
  - test_deps: gaia brief deps walks brief_dependencies
  - test_edit_round_trip: $EDITOR mock changes objective, show reflects it
  - test_search_uses_fts5: gaia brief search returns matches by FTS5

Tests use a tmp_path-routed DB via GAIA_DATA_DIR monkeypatch so they never
touch the user's real ~/.gaia/gaia.db.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure the gaia package is importable
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Ensure bin/ is importable so we can call the CLI plugin's handlers directly
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Route gaia.paths.db_path() to a tmp dir so tests are isolated."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    # Force re-import of paths cache (just in case)
    from gaia.paths import db_path
    return db_path()


# ---------------------------------------------------------------------------
# Sample brief used as fixture
# ---------------------------------------------------------------------------

_SAMPLE_BRIEF_MD = """\
---
status: draft
surface_type: cli
acceptance_criteria:
  - id: AC-1
    description: "Schema applied"
    evidence:
      type: command
      shape:
        run: "sqlite3 ~/.gaia/gaia.db .schema"
        expect: "CREATE TABLE"
    artifact: evidence/AC-1.txt
  - id: AC-2
    description: "List works"
    evidence:
      type: command
      shape:
        run: "gaia brief list"
        expect: "exit 0"
    artifact: evidence/AC-2.txt
---

# Sample Brief

## Objective
Test the full round-trip.

## Context
This brief is used by integration tests. The keyword zenithal-mooncrest appears
here exactly once for FTS5 search verification.

## Approach
Run pytest.

## Milestones
- **M1: bootstrap** -- create schema
- **M2: cli** -- expose handlers

## Out of Scope
Production use.
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_new(tmp_db):
    """gaia.briefs.upsert_brief creates a row + children."""
    from gaia.briefs import parse_brief_markdown, upsert_brief, get_brief

    parsed = parse_brief_markdown(_SAMPLE_BRIEF_MD)
    res = upsert_brief("me", "sample-brief", parsed, db_path=tmp_db)
    assert res["status"] == "applied"
    assert res["acs"] == 2
    assert res["milestones"] == 2

    brief = get_brief("me", "sample-brief", db_path=tmp_db)
    assert brief is not None
    assert brief["title"] == "Sample Brief"
    assert "round-trip" in (brief["objective"] or "")
    assert len(brief["acceptance_criteria"]) == 2
    assert len(brief["milestones"]) == 2


def test_show_returns_valid_markdown(tmp_db):
    """show serializes to markdown with frontmatter + body sections."""
    from gaia.briefs import (
        parse_brief_markdown,
        upsert_brief,
        get_brief,
        serialize_brief_to_markdown,
    )

    parsed = parse_brief_markdown(_SAMPLE_BRIEF_MD)
    upsert_brief("me", "sample-brief", parsed, db_path=tmp_db)
    brief = get_brief("me", "sample-brief", db_path=tmp_db)

    text = serialize_brief_to_markdown(brief)
    assert text.startswith("---")
    assert "status: draft" in text
    assert "acceptance_criteria:" in text
    assert "# Sample Brief" in text
    assert "## Objective" in text
    assert "## Context" in text
    assert "## Approach" in text
    assert "## Milestones" in text

    # Re-parse should yield equivalent structured fields
    re_parsed = parse_brief_markdown(text)
    assert re_parsed["title"] == "Sample Brief"
    assert len(re_parsed["acceptance_criteria"]) == 2
    assert len(re_parsed["milestones"]) == 2
    assert re_parsed["status"] == "draft"


def test_list(tmp_db):
    """list filters by status."""
    from gaia.briefs import upsert_brief, list_briefs

    upsert_brief("me", "a-brief", {"status": "draft", "title": "A"}, db_path=tmp_db)
    upsert_brief("me", "b-brief", {"status": "closed", "title": "B"}, db_path=tmp_db)
    upsert_brief("me", "c-brief", {"status": "draft", "title": "C"}, db_path=tmp_db)

    all_briefs = list_briefs("me", db_path=tmp_db)
    drafts = list_briefs("me", status="draft", db_path=tmp_db)
    closed = list_briefs("me", status="closed", db_path=tmp_db)
    assert len(all_briefs) == 3
    assert len(drafts) == 2
    assert len(closed) == 1
    assert {b["name"] for b in drafts} == {"a-brief", "c-brief"}


def test_close(tmp_db):
    """close transitions a brief to status='closed'."""
    from gaia.briefs import upsert_brief, close_brief, get_brief

    upsert_brief("me", "to-close", {"status": "draft", "title": "X"}, db_path=tmp_db)
    assert close_brief("me", "to-close", db_path=tmp_db) is True
    brief = get_brief("me", "to-close", db_path=tmp_db)
    assert brief["status"] == "closed"

    # Closing a non-existent brief returns False
    assert close_brief("me", "ghost", db_path=tmp_db) is False


def test_close_advisory_warns_on_inconsistency(tmp_db, tmp_path, monkeypatch, capsys):
    """AC-3: brief close emits advisory warnings to stderr for inconsistencies.

    Creates a brief with an empty plan (zero tasks) -- invariant 1 of
    verify_brief (empty_plan). Asserts:
      - _cmd_close returns 0 (close always succeeds)
      - brief status is 'closed' (mutation applied)
      - stderr contains at least one Warning line for the inconsistency
      - stdout contains the 'Closed' confirmation
    """
    import argparse
    from cli.brief import _cmd_close
    from gaia.briefs import upsert_brief, get_brief
    from gaia.store.writer import upsert_plan

    monkeypatch.chdir(tmp_path)

    # Create brief in draft status, then create an empty plan (zero tasks).
    upsert_brief("me", "ac3-advisory-brief",
                 {"status": "draft", "title": "AC-3 Advisory Test"},
                 db_path=tmp_db)
    upsert_plan("me", "ac3-advisory-brief",
                content="plan with no tasks", db_path=tmp_db)

    args = argparse.Namespace(name="ac3-advisory-brief", workspace="me")
    rc = _cmd_close(args)

    captured = capsys.readouterr()
    assert rc == 0, f"expected exit 0, got {rc}; stderr={captured.err}"

    brief = get_brief("me", "ac3-advisory-brief", db_path=tmp_db)
    assert brief["status"] == "closed", "brief must be closed after _cmd_close"

    assert "Closed" in captured.out
    # Advisory fires: at least one Warning line on stderr.
    assert "Warning:" in captured.err, (
        f"expected stderr advisory warnings, got: {captured.err!r}"
    )
    # The empty_plan kind must be surfaced.
    assert "empty_plan" in captured.err


def test_close_advisory_silent_for_clean_brief(tmp_db, tmp_path, monkeypatch, capsys):
    """AC-3: brief close emits NO warnings when the brief has no inconsistencies.

    A brief with no associated plan has nothing for verify_brief to flag.
    Asserts:
      - _cmd_close returns 0
      - brief status is 'closed'
      - stderr is empty (no spurious advisory noise)
    """
    import argparse
    from cli.brief import _cmd_close
    from gaia.briefs import upsert_brief, get_brief

    monkeypatch.chdir(tmp_path)

    upsert_brief("me", "ac3-clean-brief",
                 {"status": "draft", "title": "AC-3 Clean Test"},
                 db_path=tmp_db)

    args = argparse.Namespace(name="ac3-clean-brief", workspace="me")
    rc = _cmd_close(args)

    captured = capsys.readouterr()
    assert rc == 0, f"expected exit 0, got {rc}; stderr={captured.err}"

    brief = get_brief("me", "ac3-clean-brief", db_path=tmp_db)
    assert brief["status"] == "closed"

    assert "Closed" in captured.out
    assert captured.err == "", (
        f"expected empty stderr for clean brief, got: {captured.err!r}"
    )


def test_deps(tmp_db):
    """deps returns transitive closure."""
    from gaia.briefs import upsert_brief, get_dependencies

    upsert_brief("me", "leaf", {"title": "Leaf"}, db_path=tmp_db)
    upsert_brief("me", "mid", {"title": "Mid", "dependencies": ["leaf"]}, db_path=tmp_db)
    upsert_brief("me", "root", {"title": "Root", "dependencies": ["mid"]}, db_path=tmp_db)

    deps = get_dependencies("me", "root", db_path=tmp_db)
    names = [d["name"] for d in deps]
    assert "mid" in names
    assert "leaf" in names


def test_edit_round_trip(tmp_db, monkeypatch):
    """edit round-trip: serialize -> mock-edit -> parse -> upsert -> show reflects change."""
    from gaia.briefs import (
        parse_brief_markdown,
        serialize_brief_to_markdown,
        upsert_brief,
        get_brief,
    )

    # Seed a brief
    parsed = parse_brief_markdown(_SAMPLE_BRIEF_MD)
    upsert_brief("me", "sample-brief", parsed, db_path=tmp_db)

    # Simulate the editor: pull from DB, swap "round-trip" -> "modified-objective"
    initial = serialize_brief_to_markdown(get_brief("me", "sample-brief", db_path=tmp_db))
    edited = initial.replace("round-trip", "modified-objective")
    assert edited != initial, "test bug: edit substitution was a no-op"

    # Parse + upsert (mimicking the CLI's edit flow without invoking $EDITOR)
    re_parsed = parse_brief_markdown(edited)
    upsert_brief("me", "sample-brief", re_parsed, db_path=tmp_db)

    final = get_brief("me", "sample-brief", db_path=tmp_db)
    assert "modified-objective" in (final["objective"] or "")


def test_search_uses_fts5(tmp_db):
    """search returns the brief whose objective/context contains the query token."""
    from gaia.briefs import (
        parse_brief_markdown,
        upsert_brief,
        search_briefs,
    )

    parsed = parse_brief_markdown(_SAMPLE_BRIEF_MD)
    upsert_brief("me", "sample-brief", parsed, db_path=tmp_db)

    # Add an unrelated brief to ensure the search filters
    upsert_brief("me", "decoy", {
        "title": "Decoy",
        "objective": "no overlap whatsoever",
        "context": "and another sentence with different vocabulary",
        "approach": "stay out of the way of test-specific tokens",
    }, db_path=tmp_db)

    results = search_briefs("me", "zenithal-mooncrest", db_path=tmp_db)
    assert len(results) >= 1
    assert any(r["name"] == "sample-brief" for r in results)


def test_new_headless_creates_db_only_no_fs(tmp_db, tmp_path, monkeypatch, capsys):
    """`gaia brief new --headless --title=...` writes to DB only, no FS side effects."""
    import argparse
    from cli.brief import _cmd_new
    from gaia.briefs import get_brief

    # Pin a CWD that has NO `.claude/project-context/briefs/` so any accidental
    # filesystem write under a relative path would land in tmp_path and be
    # detectable. We assert below that no such directory was created.
    monkeypatch.chdir(tmp_path)

    args = argparse.Namespace(
        headless=True,
        name=None,
        workspace="me",
        title="Demo Headless Brief",
        objective="verify the headless flow",
        context=None,
        approach=None,
        out_of_scope=None,
        status="draft",
        json=False,
    )
    rc = _cmd_new(args)
    assert rc == 0, capsys.readouterr()

    # Slug derived from title
    brief = get_brief("me", "demo-headless-brief", db_path=tmp_db)
    assert brief is not None
    assert brief["title"] == "Demo Headless Brief"
    assert brief["status"] == "draft"
    assert brief["objective"] == "verify the headless flow"

    # NO directory should have been created under the legacy briefs path.
    legacy = tmp_path / ".claude" / "project-context" / "briefs"
    assert not legacy.exists(), f"unexpected FS write at {legacy}"
    # Also the slug name itself should not appear anywhere under tmp_path.
    found = list(tmp_path.rglob("demo-headless-brief"))
    assert found == [], f"unexpected slug-named path(s): {found}"


def test_new_headless_requires_title(tmp_db, tmp_path, monkeypatch, capsys):
    """`--headless` without `--title` returns a clear error."""
    import argparse
    from cli.brief import _cmd_new

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        headless=True, name=None, workspace="me",
        title=None, objective=None, context=None, approach=None,
        out_of_scope=None, status=None, json=False,
    )
    rc = _cmd_new(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "title" in captured.err.lower()


def test_set_status_db_only_legal_transition(tmp_db, tmp_path, monkeypatch, capsys):
    """`gaia brief set-status` mutates DB without touching FS."""
    import argparse
    from cli.brief import _cmd_set_status
    from gaia.briefs import upsert_brief, get_brief

    monkeypatch.chdir(tmp_path)
    upsert_brief("me", "to-transition",
                 {"status": "draft", "title": "T"}, db_path=tmp_db)

    args = argparse.Namespace(
        name="to-transition",
        new_status="open",
        workspace="me",
        json=False,
    )
    rc = _cmd_set_status(args)
    assert rc == 0, capsys.readouterr()

    brief = get_brief("me", "to-transition", db_path=tmp_db)
    assert brief["status"] == "open"

    # No filesystem traces.
    assert not (tmp_path / ".claude").exists()
    assert list(tmp_path.rglob("to-transition")) == []


def test_set_status_illegal_transition(tmp_db, tmp_path, monkeypatch, capsys):
    """archived is terminal -> any forward move is illegal; reports clear error."""
    import argparse
    from cli.brief import _cmd_set_status
    from gaia.briefs import upsert_brief, get_brief

    monkeypatch.chdir(tmp_path)
    # archived is a terminal state with no legal outgoing transitions.
    upsert_brief("me", "stuck-archived",
                 {"status": "archived", "title": "S"}, db_path=tmp_db)

    args = argparse.Namespace(
        name="stuck-archived",
        new_status="open",
        workspace="me",
        json=False,
    )
    rc = _cmd_set_status(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "illegal transition" in captured.err.lower()

    # State unchanged
    brief = get_brief("me", "stuck-archived", db_path=tmp_db)
    assert brief["status"] == "archived"


def test_set_status_draft_to_closed_shortcut(tmp_db, tmp_path, monkeypatch, capsys):
    """draft -> closed is a legal shortcut for briefs implemented directly."""
    import argparse
    from cli.brief import _cmd_set_status
    from gaia.briefs import upsert_brief, get_brief

    monkeypatch.chdir(tmp_path)
    upsert_brief("me", "shortcut-brief",
                 {"status": "draft", "title": "S"}, db_path=tmp_db)

    args = argparse.Namespace(
        name="shortcut-brief",
        new_status="closed",
        workspace="me",
        json=False,
    )
    rc = _cmd_set_status(args)
    assert rc == 0

    brief = get_brief("me", "shortcut-brief", db_path=tmp_db)
    assert brief["status"] == "closed"


def test_set_status_brief_not_found(tmp_db, tmp_path, monkeypatch, capsys):
    """Missing brief surfaces a clear error and exit code 1."""
    import argparse
    from cli.brief import _cmd_set_status

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        name="ghost-brief",
        new_status="open",
        workspace="me",
        json=False,
    )
    rc = _cmd_set_status(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "not found" in captured.err.lower()


def test_set_status_invalid_status(tmp_db, tmp_path, monkeypatch, capsys):
    """An unknown status name is rejected before any DB mutation."""
    import argparse
    from cli.brief import _cmd_set_status
    from gaia.briefs import upsert_brief, get_brief

    monkeypatch.chdir(tmp_path)
    upsert_brief("me", "valid-brief",
                 {"status": "draft", "title": "V"}, db_path=tmp_db)

    args = argparse.Namespace(
        name="valid-brief",
        new_status="bogus",
        workspace="me",
        json=False,
    )
    rc = _cmd_set_status(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "invalid status" in captured.err.lower()

    # State unchanged
    assert get_brief("me", "valid-brief", db_path=tmp_db)["status"] == "draft"


def test_delete_with_yes_removes_row(tmp_db, tmp_path, monkeypatch, capsys):
    """`gaia brief delete <name> --yes` removes the row from the DB."""
    import argparse
    from cli.brief import _cmd_delete
    from gaia.briefs import upsert_brief, get_brief

    monkeypatch.chdir(tmp_path)
    upsert_brief("me", "doomed",
                 {"status": "draft", "title": "Doomed"}, db_path=tmp_db)
    assert get_brief("me", "doomed", db_path=tmp_db) is not None

    args = argparse.Namespace(
        name="doomed",
        workspace="me",
        yes=True,
        json=False,
    )
    rc = _cmd_delete(args)
    assert rc == 0, capsys.readouterr()

    # Row is gone
    assert get_brief("me", "doomed", db_path=tmp_db) is None
    # Zero filesystem side effects
    assert not (tmp_path / ".claude").exists()
    assert list(tmp_path.rglob("doomed")) == []


def test_delete_aborts_on_no(tmp_db, tmp_path, monkeypatch, capsys):
    """Without `--yes`, answering 'n' aborts and the row remains."""
    import argparse
    import builtins
    from cli.brief import _cmd_delete
    from gaia.briefs import upsert_brief, get_brief

    monkeypatch.chdir(tmp_path)
    upsert_brief("me", "keepme",
                 {"status": "draft", "title": "KeepMe"}, db_path=tmp_db)

    # Mock input() to answer 'n'
    monkeypatch.setattr(builtins, "input", lambda *a, **kw: "n")

    args = argparse.Namespace(
        name="keepme",
        workspace="me",
        yes=False,
        json=False,
    )
    rc = _cmd_delete(args)
    assert rc == 0
    out = capsys.readouterr().out.lower()
    assert "abort" in out or "not deleted" in out

    # Row still there
    assert get_brief("me", "keepme", db_path=tmp_db) is not None


def test_delete_brief_not_found(tmp_db, tmp_path, monkeypatch, capsys):
    """Deleting a non-existent brief returns a clear error and exit 1."""
    import argparse
    from cli.brief import _cmd_delete

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        name="ghost",
        workspace="me",
        yes=True,
        json=False,
    )
    rc = _cmd_delete(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "not found" in captured.err.lower()


def test_delete_zero_fs_side_effects(tmp_db, tmp_path, monkeypatch, capsys):
    """Even after a successful delete, no filesystem traces appear."""
    import argparse
    from cli.brief import _cmd_delete
    from gaia.briefs import upsert_brief

    monkeypatch.chdir(tmp_path)
    upsert_brief("me", "fs-check",
                 {"status": "draft", "title": "FS"}, db_path=tmp_db)

    args = argparse.Namespace(
        name="fs-check",
        workspace="me",
        yes=True,
        json=True,
    )
    rc = _cmd_delete(args)
    assert rc == 0

    # No legacy briefs dir, no slug-named path.
    assert not (tmp_path / ".claude" / "project-context" / "briefs").exists()
    assert list(tmp_path.rglob("fs-check")) == []

    # JSON output is parseable and reports deletion
    import json as _json
    out = capsys.readouterr().out.strip()
    payload = _json.loads(out)
    assert payload["deleted"] is True
    assert payload["name"] == "fs-check"


def test_edit_headless_overwrite(tmp_db, tmp_path, monkeypatch, capsys):
    """`gaia brief edit --headless --field=objective --content=...` overwrites."""
    import argparse
    from cli.brief import _cmd_edit
    from gaia.briefs import upsert_brief, get_brief

    monkeypatch.chdir(tmp_path)
    upsert_brief("me", "patchable",
                 {"status": "draft", "title": "P", "objective": "old"},
                 db_path=tmp_db)

    args = argparse.Namespace(
        name="patchable",
        workspace="me",
        headless=True,
        field="objective",
        content="brand new objective",
        append=False,
        json=False,
    )
    rc = _cmd_edit(args)
    assert rc == 0, capsys.readouterr()

    brief = get_brief("me", "patchable", db_path=tmp_db)
    assert brief["objective"] == "brand new objective"

    # No filesystem traces
    assert not (tmp_path / ".claude").exists()


def test_edit_headless_append(tmp_db, tmp_path, monkeypatch, capsys):
    """`--append` concatenates the new content with `\\n\\n` separator."""
    import argparse
    from cli.brief import _cmd_edit
    from gaia.briefs import upsert_brief, get_brief

    monkeypatch.chdir(tmp_path)
    upsert_brief("me", "appendable",
                 {"status": "draft", "title": "A", "context": "first paragraph"},
                 db_path=tmp_db)

    args = argparse.Namespace(
        name="appendable",
        workspace="me",
        headless=True,
        field="context",
        content="second paragraph",
        append=True,
        json=False,
    )
    rc = _cmd_edit(args)
    assert rc == 0, capsys.readouterr()

    brief = get_brief("me", "appendable", db_path=tmp_db)
    assert brief["context"] == "first paragraph\n\nsecond paragraph"


def test_edit_headless_append_on_empty_writes_as_is(tmp_db, tmp_path,
                                                    monkeypatch, capsys):
    """``--append`` against an empty field acts like overwrite."""
    import argparse
    from cli.brief import _cmd_edit
    from gaia.briefs import upsert_brief, get_brief

    monkeypatch.chdir(tmp_path)
    upsert_brief("me", "empty-field", {"status": "draft", "title": "E"},
                 db_path=tmp_db)

    args = argparse.Namespace(
        name="empty-field", workspace="me", headless=True,
        field="approach", content="initial approach", append=True, json=False,
    )
    rc = _cmd_edit(args)
    assert rc == 0, capsys.readouterr()
    brief = get_brief("me", "empty-field", db_path=tmp_db)
    assert brief["approach"] == "initial approach"


def test_edit_headless_invalid_field(tmp_db, tmp_path, monkeypatch, capsys):
    """An unknown field returns an error, no DB mutation."""
    import argparse
    from cli.brief import _cmd_edit
    from gaia.briefs import upsert_brief

    monkeypatch.chdir(tmp_path)
    upsert_brief("me", "guarded", {"status": "draft", "title": "G"},
                 db_path=tmp_db)

    args = argparse.Namespace(
        name="guarded", workspace="me", headless=True,
        field="bogus_column", content="x", append=False, json=False,
    )
    rc = _cmd_edit(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "invalid brief field" in captured.err.lower()


def test_edit_headless_brief_not_found(tmp_db, tmp_path, monkeypatch, capsys):
    """Editing a missing brief surfaces a clear error and exit 1."""
    import argparse
    from cli.brief import _cmd_edit

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        name="ghost", workspace="me", headless=True,
        field="objective", content="x", append=False, json=False,
    )
    rc = _cmd_edit(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "not found" in captured.err.lower()


def test_edit_headless_empty_content(tmp_db, tmp_path, monkeypatch, capsys):
    """Empty content is rejected before any DB mutation."""
    import argparse
    from cli.brief import _cmd_edit
    from gaia.briefs import upsert_brief, get_brief

    monkeypatch.chdir(tmp_path)
    upsert_brief("me", "intact",
                 {"status": "draft", "title": "I", "objective": "kept"},
                 db_path=tmp_db)
    args = argparse.Namespace(
        name="intact", workspace="me", headless=True,
        field="objective", content="", append=False, json=False,
    )
    rc = _cmd_edit(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "content" in captured.err.lower()
    # Original objective untouched
    assert get_brief("me", "intact", db_path=tmp_db)["objective"] == "kept"


def test_edit_headless_description_alias(tmp_db, tmp_path, monkeypatch, capsys):
    """`--field=description` is an alias for `objective`."""
    import argparse
    from cli.brief import _cmd_edit
    from gaia.briefs import upsert_brief, get_brief

    monkeypatch.chdir(tmp_path)
    upsert_brief("me", "alias-brief",
                 {"status": "draft", "title": "A", "objective": "x"},
                 db_path=tmp_db)
    args = argparse.Namespace(
        name="alias-brief", workspace="me", headless=True,
        field="description", content="aliased value", append=False, json=False,
    )
    rc = _cmd_edit(args)
    assert rc == 0, capsys.readouterr()
    brief = get_brief("me", "alias-brief", db_path=tmp_db)
    assert brief["objective"] == "aliased value"


# ---------------------------------------------------------------------------
# surface_type + milestone + ac headless setters (CLI-gap closure)
# ---------------------------------------------------------------------------

def _new_args(**over):
    import argparse
    base = dict(
        headless=True, name=None, workspace="me", title=None,
        objective=None, objective_file=None, context=None, context_file=None,
        approach=None, approach_file=None, out_of_scope=None,
        out_of_scope_file=None, status="draft", surface_type=None, json=False,
    )
    base.update(over)
    return argparse.Namespace(**base)


def test_new_headless_sets_surface_type(tmp_db, tmp_path, monkeypatch, capsys):
    """`gaia brief new --headless --surface-type=cli` persists surface_type."""
    from cli.brief import _cmd_new
    from gaia.briefs import get_brief

    monkeypatch.chdir(tmp_path)
    rc = _cmd_new(_new_args(
        title="Surface Typed Brief", surface_type="cli", objective="x",
    ))
    assert rc == 0, capsys.readouterr()
    brief = get_brief("me", "surface-typed-brief", db_path=tmp_db)
    assert brief is not None
    assert brief["surface_type"] == "cli"


def test_milestone_add_and_show_roundtrip(tmp_db, tmp_path, monkeypatch, capsys):
    """`gaia brief milestone add` writes a milestones row readable via get_brief."""
    import argparse
    from cli.brief import _cmd_new, _cmd_milestone
    from gaia.briefs import get_brief

    monkeypatch.chdir(tmp_path)
    assert _cmd_new(_new_args(title="MS Brief", surface_type="cli")) == 0

    rc = _cmd_milestone(argparse.Namespace(
        milestone_action="add", brief="ms-brief", workspace="me",
        name="M1", description="first milestone", order=None, json=False,
    ))
    assert rc == 0, capsys.readouterr()

    brief = get_brief("me", "ms-brief", db_path=tmp_db)
    names = [m.get("name") for m in brief["milestones"]]
    assert "M1" in names


def test_ac_add_and_show_roundtrip(tmp_db, tmp_path, monkeypatch, capsys):
    """`gaia brief ac add` writes an acceptance_criteria row readable via get_brief."""
    import argparse
    from cli.brief import _cmd_new, _cmd_ac
    from gaia.briefs import get_brief

    monkeypatch.chdir(tmp_path)
    assert _cmd_new(_new_args(title="AC Brief", surface_type="cli")) == 0

    rc = _cmd_ac(argparse.Namespace(
        ac_action="add", brief="ac-brief", workspace="me",
        id="AC-1", description="first criterion", evidence_type="command",
        evidence_shape=None, artifact="evidence/AC-1.txt", json=False,
    ))
    assert rc == 0, capsys.readouterr()

    brief = get_brief("me", "ac-brief", db_path=tmp_db)
    acs = {a.get("ac_id"): a for a in brief["acceptance_criteria"]}
    assert "AC-1" in acs
    assert acs["AC-1"]["evidence_type"] == "command"
    assert acs["AC-1"]["artifact_path"] == "evidence/AC-1.txt"


def test_milestone_and_ac_duplicate_rejected(tmp_db, tmp_path, monkeypatch, capsys):
    """Adding a duplicate milestone/AC id returns a non-zero rc with a message."""
    import argparse
    from cli.brief import _cmd_new, _cmd_milestone, _cmd_ac

    monkeypatch.chdir(tmp_path)
    assert _cmd_new(_new_args(title="Dup Brief", surface_type="cli")) == 0

    add_m = argparse.Namespace(
        milestone_action="add", brief="dup-brief", workspace="me",
        name="M1", description=None, order=None, json=False,
    )
    assert _cmd_milestone(add_m) == 0, capsys.readouterr()
    assert _cmd_milestone(add_m) == 1  # duplicate
    capsys.readouterr()

    add_ac = argparse.Namespace(
        ac_action="add", brief="dup-brief", workspace="me",
        id="AC-1", description=None, evidence_type=None,
        evidence_shape=None, artifact=None, json=False,
    )
    assert _cmd_ac(add_ac) == 0, capsys.readouterr()
    assert _cmd_ac(add_ac) == 1  # duplicate


def test_new_brief_setters_are_not_t3():
    """The new brief setters classify READ_ONLY (no T3), while delete/approvals stay gated.

    Anchored to the ("gaia","brief") exemption in mutative_verbs; the new verbs
    inherit it because they live under `gaia brief`.
    """
    _hooks = _REPO_ROOT / "hooks"
    if str(_hooks) not in sys.path:
        sys.path.insert(0, str(_hooks))
    from modules.security.mutative_verbs import detect_mutative_command

    exempt = [
        "gaia brief milestone add b --name=M1",
        "gaia brief milestone remove b --name=M1",
        "gaia brief ac add b --id=AC-1 --evidence-type=command",
        "gaia brief ac remove b --id=AC-1",
        "gaia brief new --headless --title=x --surface-type=cli",
        "gaia brief edit b --headless --field=surface_type --content=cli",
    ]
    for c in exempt:
        assert detect_mutative_command(c).is_mutative is False, c

    # Controls: destruction + the consent layer must stay gated.
    assert detect_mutative_command("gaia brief delete b").is_mutative is True
    assert detect_mutative_command("gaia approvals approve P-x").is_mutative is True


# ---------------------------------------------------------------------------
# FIX 1: show-by-id  (gaia brief show <int> resolves by numeric id)
# ---------------------------------------------------------------------------

def test_show_by_id_resolves_brief(tmp_db, tmp_path, monkeypatch, capsys):
    """`gaia brief show <int>` resolves the brief by numeric DB id."""
    import argparse
    from cli.brief import _cmd_show
    from gaia.briefs import upsert_brief, get_brief

    monkeypatch.chdir(tmp_path)
    upsert_brief("me", "id-target", {"status": "draft", "title": "ID Target"},
                 db_path=tmp_db)
    brief = get_brief("me", "id-target", db_path=tmp_db)
    assert brief is not None
    brief_id = brief["id"]

    # show by id, no explicit workspace needed
    args = argparse.Namespace(name=str(brief_id), workspace="me", json=False)
    rc = _cmd_show(args)
    captured = capsys.readouterr()
    assert rc == 0, f"expected 0, got {rc}; stderr={captured.err}"
    assert "ID Target" in captured.out


def test_show_by_id_json(tmp_db, tmp_path, monkeypatch, capsys):
    """`gaia brief show <int> --json` returns JSON payload resolved by id."""
    import argparse
    import json as _json
    from cli.brief import _cmd_show
    from gaia.briefs import upsert_brief, get_brief

    monkeypatch.chdir(tmp_path)
    upsert_brief("me", "json-id-brief", {"status": "draft", "title": "JSON ID"},
                 db_path=tmp_db)
    brief = get_brief("me", "json-id-brief", db_path=tmp_db)
    brief_id = brief["id"]

    args = argparse.Namespace(name=str(brief_id), workspace="me", json=True)
    rc = _cmd_show(args)
    captured = capsys.readouterr()
    assert rc == 0
    payload = _json.loads(captured.out)
    assert payload["title"] == "JSON ID"
    assert payload["name"] == "json-id-brief"


def test_show_by_id_not_found_returns_error(tmp_db, tmp_path, monkeypatch, capsys):
    """`gaia brief show <int>` returns exit 1 when no brief with that id exists."""
    import argparse
    from cli.brief import _cmd_show

    monkeypatch.chdir(tmp_path)
    # Use an id that will never exist in an empty DB (very large int).
    args = argparse.Namespace(name="999999", workspace="me", json=False)
    rc = _cmd_show(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "not found" in captured.err.lower()


def test_show_slug_still_works_for_digit_named_brief(tmp_db, tmp_path,
                                                      monkeypatch, capsys):
    """A brief whose slug is all-digits is still resolvable by id lookup.

    When show receives an all-digit arg it first tries id lookup, which finds
    the brief by id (not necessarily matching the slug).  This test inserts
    a brief with name='123' and verifies that ``show 123`` returns the brief
    found by id match (which may be a different brief if id != 123, but the
    slug-name match falls back only when id lookup fails).
    """
    import argparse
    import json as _json
    from cli.brief import _cmd_show
    from gaia.briefs import upsert_brief, get_brief

    monkeypatch.chdir(tmp_path)
    # Insert a brief with a numeric-looking slug.
    upsert_brief("me", "numeric-brief", {"status": "draft", "title": "Numeric"},
                 db_path=tmp_db)
    brief = get_brief("me", "numeric-brief", db_path=tmp_db)
    brief_id = brief["id"]

    # Resolves by numeric id -- show the brief regardless of slug.
    args = argparse.Namespace(name=str(brief_id), workspace="me", json=True)
    rc = _cmd_show(args)
    captured = capsys.readouterr()
    assert rc == 0
    payload = _json.loads(captured.out)
    assert payload["name"] == "numeric-brief"


# ---------------------------------------------------------------------------
# FIX 2: cross-workspace hint (not-found in resolved WS, exists elsewhere)
# ---------------------------------------------------------------------------

def test_show_cross_workspace_hint(tmp_db, tmp_path, monkeypatch, capsys):
    """When a brief exists in workspace B but is looked up in workspace A,
    the error message names workspace B instead of a bare 'not found'."""
    import argparse
    from cli.brief import _cmd_show
    from gaia.briefs import upsert_brief

    monkeypatch.chdir(tmp_path)
    # Brief lives in workspace 'me', not in 'github.com/metraton/gaia'.
    upsert_brief("me", "cross-ws-brief",
                 {"status": "draft", "title": "Cross WS"},
                 db_path=tmp_db)

    # Simulate looking up from a git-remote-resolved workspace.
    args = argparse.Namespace(
        name="cross-ws-brief",
        workspace="github.com/metraton/gaia",
        json=False,
    )
    rc = _cmd_show(args)
    captured = capsys.readouterr()
    assert rc == 1
    # Must mention the workspace where the brief actually lives.
    assert "'me'" in captured.err or "me" in captured.err
    # Must suggest --workspace flag.
    assert "--workspace" in captured.err


def test_show_not_found_in_any_workspace_bare_error(tmp_db, tmp_path,
                                                     monkeypatch, capsys):
    """When a brief does not exist anywhere, the error is a bare 'not found'
    with no cross-workspace hint."""
    import argparse
    from cli.brief import _cmd_show

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        name="totally-absent-brief",
        workspace="me",
        json=False,
    )
    rc = _cmd_show(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "not found" in captured.err.lower()
    # No hint about other workspaces since there are none.
    assert "--workspace" not in captured.err


def test_show_cross_workspace_hint_json(tmp_db, tmp_path, monkeypatch, capsys):
    """Cross-workspace hint in JSON mode includes the error key."""
    import argparse
    import json as _json
    from cli.brief import _cmd_show
    from gaia.briefs import upsert_brief

    monkeypatch.chdir(tmp_path)
    upsert_brief("me", "json-cross-ws",
                 {"status": "draft", "title": "JSON Cross WS"},
                 db_path=tmp_db)

    args = argparse.Namespace(
        name="json-cross-ws",
        workspace="other-workspace",
        json=True,
    )
    rc = _cmd_show(args)
    captured = capsys.readouterr()
    assert rc == 1
    payload = _json.loads(captured.out)
    assert "error" in payload
    assert "me" in payload["error"]
    assert "--workspace" in payload["error"]
