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
  - test_import_from_fs: walks fixture directory, upserts briefs

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


def test_import_from_fs(tmp_db, tmp_path):
    """import_from_fs walks <status>_<name>/brief.md directories."""
    from gaia.briefs import import_from_fs, list_briefs

    src = tmp_path / "briefs-src"
    src.mkdir()

    open_dir = src / "open_first-brief"
    open_dir.mkdir()
    (open_dir / "brief.md").write_text(_SAMPLE_BRIEF_MD)

    closed_dir = src / "closed_second-brief"
    closed_dir.mkdir()
    (closed_dir / "brief.md").write_text(
        _SAMPLE_BRIEF_MD.replace("status: draft", "status: closed")
                       .replace("Sample Brief", "Second Brief")
    )

    res = import_from_fs(src, workspace="me", db_path=tmp_db)
    assert res["imported"] == 2
    assert "first-brief" in res["names"]
    assert "second-brief" in res["names"]

    briefs = list_briefs("me", db_path=tmp_db)
    statuses = {b["name"]: b["status"] for b in briefs}
    # Directory prefix should drive status:
    #   open_*  -> 'draft'
    #   closed_*-> 'closed'
    assert statuses["first-brief"] == "draft"
    assert statuses["second-brief"] == "closed"
