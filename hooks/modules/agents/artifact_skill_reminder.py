"""
Per-turn, per-artifact-class advisory reminder for PreToolUse Write/Edit.

This is the PREVENTION half of the skill-gap story that
``skill_injection_verifier`` only ever DETECTED, and only after the fact: that
module runs at SubagentStop, once the file is already written, and can only
report that the governing skill's fingerprint never showed up in the
transcript. By then the artifact exists without the convention it was
supposed to follow. This module moves the same governance -- still derived
from ``artifact_skill_map``, never from a semantic match on the dispatch goal
-- to the moment the agent is ABOUT to write the file, so the requirement
reaches the agent while the artifact is still being formed, not once it is
history.

Two constraints shape the design:

- **PreToolUse cannot see what the agent already loaded this turn.** The
  Claude Code payload for a subagent's Write/Edit call carries no
  ``transcript_path`` (only SubagentStop gets ``agent_transcript_path``), and
  the ``Skill`` tool itself is not wired into any PreToolUse matcher in
  ``hooks/hooks.json`` -- a ``Skill(...)`` call never reaches this hook at
  all. So this module does not attempt to answer "was the skill already
  loaded?" (unanswerable from here); it answers a narrower, always-answerable
  question instead -- "does this file's class require a skill, and have we
  already said so this turn?" -- and reminds unconditionally on the first
  "no."
- **Noise is the real failure mode.** Reminding on every Write of every file
  in a governed class (e.g. every ``.py`` write in a hook module plus its
  test) would be as invisible as the ``pipe_retroactive`` false-positive
  flood. The reminder fires at most once per (session, agent, skill) triple
  -- once per turn, per artifact CLASS, never per file -- by checking and
  marking a small on-disk marker before returning the advisory.

Persistence mirrors the existing PreToolUse -> SubagentStart bridges in
``hooks/adapters/claude_code.py`` (``_cache_context_for_subagent`` /
``_cache_resume_mapping``): a TTL-bounded file under ``/tmp``, not the
project's own ``.claude/`` state directory, because this marker carries no
audit value once the turn ends -- it exists only to suppress a repeat
reminder within one subagent's lifetime.
"""

import json
import re
import time
from pathlib import Path
from typing import Optional

REMINDER_CACHE_DIR = Path("/tmp/gaia-artifact-skill-reminders")

# Generous relative to any single subagent turn -- long enough that a slow
# turn never sees the marker expire mid-turn and re-remind, short enough that
# the directory does not accumulate forever across sessions.
REMINDER_TTL_SECONDS = 6 * 60 * 60


def _sanitize(value: str) -> str:
    """Make an id safe to use as a filename component.

    session_id (a UUID) and agent_id are already filename-safe in practice,
    but any stray character is collapsed to ``_`` so a malformed id can
    never escape the cache directory.
    """
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value or "")


def _marker_path(session_id: str, agent_id: str) -> Path:
    """Path to the one marker file for this (session_id, agent_id) turn."""
    key = f"{_sanitize(session_id)}__{_sanitize(agent_id)}.json"
    return REMINDER_CACHE_DIR / key


def _load_reminded(path: Path) -> set:
    """Read the set of skills already reminded for this turn, or empty."""
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    skills = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(skills, list):
        return set()
    return {s for s in skills if isinstance(s, str)}


def should_remind(session_id: str, agent_id: str, skill: str) -> bool:
    """Return True the first time ``skill`` is seen for this turn, else False.

    Also records ``skill`` as reminded for this (session_id, agent_id) pair,
    so a second file of the same governed class in the same turn returns
    False and the reminder is not repeated. Missing ``session_id``,
    ``agent_id``, or ``skill`` never reminds (nothing stable to key on).

    A persistence failure (unwritable /tmp) degrades to "always remind" --
    at worst a noisier but still advisory reminder, never a block, and never
    an exception raised into the caller.
    """
    if not session_id or not agent_id or not skill:
        return False

    path = _marker_path(session_id, agent_id)
    reminded = _load_reminded(path)
    if skill in reminded:
        return False

    reminded.add(skill)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"skills": sorted(reminded), "updated_at": time.time()}
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass
    return True


def cleanup_stale_markers(now: Optional[float] = None) -> None:
    """Remove marker files older than ``REMINDER_TTL_SECONDS``.

    Best-effort background hygiene, mirroring
    ``ClaudeCodeAdapter._cleanup_stale_cache``. Never raises -- a failure to
    clean up stale markers must not affect the Write/Edit decision.
    """
    if not REMINDER_CACHE_DIR.exists():
        return
    now = now if now is not None else time.time()
    for entry in REMINDER_CACHE_DIR.glob("*.json"):
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
            updated_at = data.get("updated_at", 0) if isinstance(data, dict) else 0
            if now - updated_at > REMINDER_TTL_SECONDS:
                entry.unlink(missing_ok=True)
        except (json.JSONDecodeError, OSError):
            entry.unlink(missing_ok=True)


def build_reminder_reason(file_path: str, skill: str) -> str:
    """Build the advisory ``permissionDecisionReason`` text for ``skill``.

    Deliberately phrased as a reminder, not an accusation: this fires before
    the agent's own skill choices for the turn are knowable from here (see
    module docstring), so it cannot claim the skill is missing -- only that
    this artifact class is governed by it.
    """
    return (
        f"[SKILL_REMINDER] '{file_path}' belongs to an artifact class "
        f"governed by the '{skill}' skill. If it has not been loaded this "
        f"turn, load it (Skill('{skill}')) before finishing this artifact."
    )
