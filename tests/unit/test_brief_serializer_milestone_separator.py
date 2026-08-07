#!/usr/bin/env python3
"""Regression tests: `_parse_milestones_section` must fully consume the
``--`` separator `_serialize_milestones_section` emits.

Symptom (confirmed by reproducing against the unmodified regex before this
fix): the parser's separator pattern ``[-—–]?`` matches at most ONE dash
character. The serializer always emits a literal two-hyphen ``--`` between
``**name**`` and the description (``f"- **{name}** -- {desc}"``). The parser
therefore consumes only the FIRST hyphen of that pair and leaves the second
hyphen sitting at the front of the captured description -- so every
serialize/parse cycle (the exact path of an interactive `gaia brief edit`)
prepends one more stray ``"- "`` to `description`, and it accumulates without
bound across successive edits.

This module verifies the fix restores the idempotence the docstring in
`serializer.py` claims (`parse(serialize(x)) == x`) specifically for
milestone `description`, across N round-trips, while leaving the unrelated
`[status: X]` display-only marker handling (`_TRAILING_STATUS_MARKER_RE`)
intact and composable in either strip order.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.briefs.serializer import (
    serialize_brief_to_markdown,
    parse_brief_markdown,
)


def _roundtrip_once(brief: dict) -> dict:
    text = serialize_brief_to_markdown(brief)
    return parse_brief_markdown(text)


def _roundtrip_n(brief: dict, n: int) -> dict:
    current = brief
    for _ in range(n):
        current = _roundtrip_once(current)
    return current


# ---------------------------------------------------------------------------
# The central regression: idempotence for N successive edit cycles
# ---------------------------------------------------------------------------

def test_single_roundtrip_preserves_description_exactly():
    brief = {
        "title": "T",
        "milestones": [
            {"name": "M1: bootstrap", "description": "create schema"},
        ],
    }
    parsed = _roundtrip_once(brief)
    assert parsed["milestones"][0]["description"] == "create schema"


def test_two_roundtrips_do_not_accumulate_a_stray_dash():
    """The exact reported symptom: a second edit cycle must not add a
    second stray leading dash on top of a first.
    """
    brief = {
        "title": "T",
        "milestones": [
            {"name": "M1: bootstrap", "description": "create schema"},
        ],
    }
    parsed = _roundtrip_n(brief, 2)
    assert parsed["milestones"][0]["description"] == "create schema"


def test_ten_roundtrips_stay_idempotent():
    """Idempotence must hold for arbitrary N, not merely N=1 or N=2."""
    brief = {
        "title": "T",
        "milestones": [
            {"name": "M1: bootstrap", "description": "create schema"},
        ],
    }
    for n in (1, 2, 3, 5, 10):
        parsed = _roundtrip_n(brief, n)
        assert parsed["milestones"][0]["description"] == "create schema", (
            f"description drifted after {n} round-trip(s): "
            f"{parsed['milestones'][0]['description']!r}"
        )


# ---------------------------------------------------------------------------
# Edge cases: legitimate content that must survive verbatim
# ---------------------------------------------------------------------------

def test_description_legitimately_starting_with_single_dash():
    brief = {
        "title": "T",
        "milestones": [
            {"name": "M1: x", "description": "-first bullet of a sub-list"},
        ],
    }
    parsed = _roundtrip_n(brief, 3)
    assert parsed["milestones"][0]["description"] == (
        "-first bullet of a sub-list"
    )


def test_description_legitimately_starting_with_double_dash():
    brief = {
        "title": "T",
        "milestones": [
            {"name": "M1: x", "description": "--verbose flag explained"},
        ],
    }
    parsed = _roundtrip_n(brief, 3)
    assert parsed["milestones"][0]["description"] == (
        "--verbose flag explained"
    )


def test_description_containing_brackets_survives():
    brief = {
        "title": "T",
        "milestones": [
            {"name": "M1: x", "description": "handles [edge] cases well"},
        ],
    }
    parsed = _roundtrip_n(brief, 3)
    assert parsed["milestones"][0]["description"] == (
        "handles [edge] cases well"
    )


def test_description_containing_bracket_that_looks_like_status_marker():
    """A legitimate description that merely *contains* bracketed text
    resembling a status marker must not be mistaken for the real, trailing
    ``[status: X]`` marker -- only a TRAILING marker is display-only.
    """
    brief = {
        "title": "T",
        "milestones": [
            {
                "name": "M1: x",
                "description": "note: [status: draft] is just an example",
            },
        ],
    }
    parsed = _roundtrip_n(brief, 3)
    assert parsed["milestones"][0]["description"] == (
        "note: [status: draft] is just an example"
    )


def test_empty_description_stays_empty_across_roundtrips():
    brief = {
        "title": "T",
        "milestones": [{"name": "M1: x", "description": ""}],
    }
    parsed = _roundtrip_n(brief, 3)
    assert parsed["milestones"][0]["description"] == ""
    assert parsed["milestones"][0]["name"] == "M1: x"


# ---------------------------------------------------------------------------
# Interaction with the display-only `[status: X]` marker
# ---------------------------------------------------------------------------

def test_status_marker_and_separator_both_strip_together():
    """A milestone carrying both the ``--`` separator and a trailing
    ``[status: X]`` marker must have BOTH stripped -- the marker must not
    contaminate description, and the separator must not leave a stray dash.
    """
    brief = {
        "title": "T",
        "milestones": [
            {
                "name": "M1: bootstrap",
                "description": "create schema",
                "status": "blocked",
            },
        ],
    }
    text = serialize_brief_to_markdown(brief)
    assert "[status: blocked]" in text

    parsed = parse_brief_markdown(text)
    milestone = parsed["milestones"][0]
    assert milestone["description"] == "create schema"
    assert "status" not in milestone


def test_status_marker_and_separator_stripped_across_roundtrips():
    """Two full edit cycles (re-attaching status between them, exactly as
    `get_brief` would) must not accumulate stray dashes OR duplicate marker
    text.
    """
    brief = {
        "title": "T",
        "milestones": [
            {
                "name": "M1: bootstrap",
                "description": "create schema",
                "status": "blocked",
            },
        ],
    }
    text1 = serialize_brief_to_markdown(brief)
    parsed1 = parse_brief_markdown(text1)
    parsed1["milestones"][0]["status"] = "blocked"

    text2 = serialize_brief_to_markdown(parsed1)
    parsed2 = parse_brief_markdown(text2)

    assert parsed2["milestones"][0]["description"] == "create schema"
    assert text2.count("[status: blocked]") == 1


def test_milestone_without_status_marker_still_fixes_separator():
    """A milestone that never carries a status must also be free of the
    separator defect (the two strips are independent).
    """
    brief = {
        "title": "T",
        "milestones": [
            {"name": "M2: no-status", "description": "plain description"},
        ],
    }
    parsed = _roundtrip_n(brief, 4)
    assert parsed["milestones"][0]["description"] == "plain description"
