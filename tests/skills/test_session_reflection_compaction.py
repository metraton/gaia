"""session-reflection never makes reflection a precondition of compaction.

The PreCompact hook rebuilds the session's in-flight state from the database,
so compaction is safe at any time; reflection is a deliberate curation pass
and the resume-point hand note is optional.
"""

from __future__ import annotations

import re
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "session-reflection"
_FILES = ("SKILL.md", "examples.md")

_PRECONDITION_PHRASES = (
    r"compaction is (the )?reflection'?s? last step",
    r"compaction that skips the reflection",
    r"reflection (above )?runs first",
    r"before the reflection finishes",
    r"not durable",
    r"still write the\s+resume point",
    r"records the resume point",
)


def _violations(text: str) -> list[str]:
    flat = re.sub(r"\s+", " ", text.replace("’", "'"))
    return [p for p in _PRECONDITION_PHRASES if re.search(p, flat, re.IGNORECASE)]


def test_no_file_makes_reflection_a_precondition_of_compaction():
    found = {
        name: _violations((_SKILL_DIR / name).read_text(encoding="utf-8")) for name in _FILES
    }
    assert found == {name: [] for name in _FILES}


def test_skill_states_compaction_needs_no_reflection():
    text = re.sub(r"\s+", " ", (_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8"))
    assert "needs no reflection" in text
    assert "hand note" in text and "Optionally" in text


def test_the_old_precondition_wording_is_detected():
    old = (
        "When the user asks to compact, compaction is the reflection's last step, never\n"
        "a step of its own: a compaction that skips the reflection loses whatever had\n"
        "no home yet. Skip step 1, still write the\nresume point (step 2)."
    )
    assert len(_violations(old)) == 3
