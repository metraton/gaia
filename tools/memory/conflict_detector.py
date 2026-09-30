"""Candidate contradictions in the curated memory, for the orchestrator to judge.

Pairs are drawn only inside one owner -- the user's rows, or the rows of one
project key -- because two owners never answer the same question. A pair is a
candidate when the two rows share enough vocabulary to be about the same
subject; whether they actually disagree, and which one stands, is a reading of
both bodies that this module deliberately does not attempt.
"""

from __future__ import annotations

import re
from itertools import combinations
from pathlib import Path

DEFAULT_THRESHOLD = 0.3

# Words are cut to this many characters so inflections meet across languages
# (integrates / integrated / integra / integrado all become "integ").
_STEM_LENGTH = 5

_STOPWORDS = frozenset("""
    the and for with from that this these those are was were been being have
    has had does did will would could should may might shall its than then
    not all any each both more into onto over about when what which who
    los las del por para con sin que una uno unos unas este esta estos estas
    ese esa eso son fue ser hay como pero mas muy cada todo toda todos todas
""".split())


def _stems(text: str) -> set[str]:
    words = re.findall(r"[^\W_]+", text.lower())
    return {
        w[:_STEM_LENGTH]
        for w in words
        if len(w) > 2 and not w.isdigit() and w not in _STOPWORDS
    }


def _jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _owner(row: dict) -> str:
    return "user" if row["type"] == "user" else f"project:{row['initiative']}"


def _summary(row: dict) -> dict:
    return {
        "workspace": row["workspace"],
        "name": row["name"],
        "class": row["class"],
        "description": row["description"],
        "updated_at": row["updated_at"],
    }


def detect_conflicts(
    threshold: float = DEFAULT_THRESHOLD,
    db_path: Path | None = None,
) -> list[dict]:
    """Candidate pairs of live rows of one owner whose wording overlaps above ``threshold``.

    Each candidate carries ``owner``, ``score`` (Jaccard over word stems of
    name, description and body) and the two rows as ``a`` and ``b``, the more
    recently updated first; the list is ordered by score, highest first.
    """
    from gaia.store.reader import live_owned_memory_rows

    by_owner: dict[str, list[dict]] = {}
    for row in live_owned_memory_rows(db_path):
        by_owner.setdefault(_owner(row), []).append(row)

    candidates = []
    for owner, rows in by_owner.items():
        stemmed = [
            (r, _stems(" ".join((r["name"], r["description"] or "", r["body"]))))
            for r in rows
        ]
        for (first, first_stems), (second, second_stems) in combinations(stemmed, 2):
            score = _jaccard(first_stems, second_stems)
            if score < threshold:
                continue
            newer, older = sorted(
                (first, second), key=lambda r: r["updated_at"] or "", reverse=True,
            )
            candidates.append({
                "owner": owner,
                "score": round(score, 4),
                "a": _summary(newer),
                "b": _summary(older),
            })
    candidates.sort(key=lambda c: (-c["score"], c["a"]["name"], c["b"]["name"]))
    return candidates
