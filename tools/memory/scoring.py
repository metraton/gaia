#!/usr/bin/env python3
"""
Memory Scoring Module for GAIA-OPS

Provides strength-based scoring for episodic memories using a decay formula
inspired by the hippo-memory model. Memories decay over time (recency bias)
and are strengthened by repeated retrieval (usage reinforcement).

Formula:
    strength = base_strength * (0.5 ^ (days_old / half_life))
               * (1 + log(1 + retrieval_count) * boost_factor)

Functions:
    score_memory    -- compute strength score for a single memory
    rank_episodes   -- rank a list of episode dicts by combined relevance + strength
"""

import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Core scoring
# ---------------------------------------------------------------------------

def score_memory(
    days_old: float,
    retrieval_count: int,
    half_life: float = 7.0,
    boost_factor: float = 0.3,
    base_strength: float = 1.0,
) -> float:
    """Compute a strength score for a single memory.

    Parameters
    ----------
    days_old:
        Age of the memory in days (>= 0).
    retrieval_count:
        Number of times the memory has been retrieved (>= 0).
    half_life:
        Number of days after which unaccessed memory retains 50% strength.
        Default: 7.0 days.
    boost_factor:
        Multiplier that scales the logarithmic retrieval boost.
        Default: 0.3.
    base_strength:
        Starting strength before decay and boost are applied.
        Default: 1.0.

    Returns
    -------
    float
        Strength score in the range (0, base_strength * (1 + boost)].
        A higher value means the memory is more relevant/fresh.

    Examples
    --------
    >>> score_memory(days_old=0, retrieval_count=0)
    1.0
    >>> score_memory(days_old=7, retrieval_count=0)   # half-life point
    0.5
    """
    if half_life <= 0:
        raise ValueError("half_life must be positive")
    if days_old < 0:
        raise ValueError("days_old must be non-negative")
    if retrieval_count < 0:
        raise ValueError("retrieval_count must be non-negative")

    decay = 0.5 ** (days_old / half_life)
    retrieval_boost = 1.0 + math.log(1 + retrieval_count) * boost_factor
    return base_strength * decay * retrieval_boost


# ---------------------------------------------------------------------------
# Episode ranking
# ---------------------------------------------------------------------------

def _extract_text(episode: Dict[str, Any]) -> str:
    """Combine all text fields from an episode into a single string for matching."""
    parts: List[str] = []
    for key in ("prompt", "enriched_prompt", "title", "type"):
        value = episode.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    tags = episode.get("tags")
    if isinstance(tags, list):
        parts.extend(t for t in tags if isinstance(t, str))
    keywords = episode.get("keywords")
    if isinstance(keywords, list):
        parts.extend(k for k in keywords if isinstance(k, str))
    return " ".join(parts)


def _tokenize(text: str) -> set:
    """Return a set of lowercase word tokens from *text*."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _keyword_overlap(episode_text: str, user_task: str) -> float:
    """Compute a normalised keyword overlap score in [0, 1].

    Uses the Jaccard-like formula:
        overlap_count / max(1, len(task_tokens))

    This measures what fraction of the user's task words appear in the
    episode text, so short tasks aren't penalised.
    """
    task_tokens = _tokenize(user_task)
    if not task_tokens:
        return 0.0
    episode_tokens = _tokenize(episode_text)
    common = task_tokens & episode_tokens
    return len(common) / len(task_tokens)


def _days_old_from_episode(episode: Dict[str, Any]) -> float:
    """Derive days_old from the episode's 'timestamp' field.

    Falls back to 0.0 if the field is absent or unparseable.
    """
    ts = episode.get("timestamp")
    if not ts:
        return 0.0
    try:
        # Support ISO-8601 strings with or without timezone info.
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        recorded = datetime.fromisoformat(ts)
        if recorded.tzinfo is None:
            recorded = recorded.replace(tzinfo=timezone.utc)
        now = datetime.now(tz=timezone.utc)
        delta = now - recorded
        return max(0.0, delta.total_seconds() / 86400.0)
    except (ValueError, AttributeError):
        return 0.0


def rank_episodes(
    episodes: List[Dict[str, Any]],
    user_task: str,
    half_life: float = 7.0,
    boost_factor: float = 0.3,
) -> List[Dict[str, Any]]:
    """Rank episodes by combined keyword relevance and memory strength.

    The composite score is:
        final_score = keyword_overlap * score_memory(...)

    Episodes with no keyword overlap receive a score of 0 and are still
    included so the caller can decide whether to filter them.

    Parameters
    ----------
    episodes:
        List of episode dicts. Each dict may contain: ``prompt``,
        ``enriched_prompt``, ``timestamp``, ``retrieval_count``, ``title``,
        ``type``, ``tags``, ``keywords``.
    user_task:
        Free-text description of what the user is trying to accomplish.
    half_life:
        Forwarded to :func:`score_memory`.
    boost_factor:
        Forwarded to :func:`score_memory`.

    Returns
    -------
    list
        A new list of episode dicts sorted by ``_score`` descending.
        Each returned dict has an additional ``_score`` key (float) for
        inspection and debugging.
    """
    scored: List[Dict[str, Any]] = []
    for episode in episodes:
        days_old = _days_old_from_episode(episode)
        retrieval_count = int(episode.get("retrieval_count", 0))
        strength = score_memory(
            days_old=days_old,
            retrieval_count=retrieval_count,
            half_life=half_life,
            boost_factor=boost_factor,
        )
        overlap = _keyword_overlap(_extract_text(episode), user_task)
        final_score = overlap * strength
        entry = dict(episode)
        entry["_score"] = final_score
        scored.append(entry)

    scored.sort(key=lambda e: e["_score"], reverse=True)
    return scored
