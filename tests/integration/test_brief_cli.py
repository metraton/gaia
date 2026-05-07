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
    """draft -> closed is NOT a legal one-step transition; reports clear error."""
    import argparse
    from cli.brief import _cmd_set_status
    from gaia.briefs import upsert_brief, get_brief

    monkeypatch.chdir(tmp_path)
    upsert_brief("me", "stuck-draft",
                 {"status": "draft", "title": "S"}, db_path=tmp_db)

    args = argparse.Namespace(
        name="stuck-draft",
        new_status="closed",
        workspace="me",
        json=False,
    )
    rc = _cmd_set_status(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "illegal transition" in captured.err.lower()

    # State unchanged
    brief = get_brief("me", "stuck-draft", db_path=tmp_db)
    assert brief["status"] == "draft"


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
