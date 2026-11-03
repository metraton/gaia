"""
Per-turn, per-artifact-class advisory reminder for PreToolUse Write/Edit.

Where ``skill_injection_verifier`` detects at SubagentStop that a governing
skill's fingerprint never showed up in the transcript, this module reminds the
agent before the write. The skill comes from ``artifact_skill_map``, never
from the dispatch goal.

Two constraints shape the design:

- **PreToolUse cannot see what the agent already loaded this turn.** The
  Write/Edit payload carries no transcript path, and ``hooks/hooks.json`` has
  no PreToolUse matcher for the ``Skill`` tool. So the module does not ask
  whether the skill was loaded; it asks whether this file's class requires a
  skill and whether the agent was already reminded this turn, and reminds on
  the first "no".
- **Noise is the real failure mode.** The reminder fires at most once per
  (session, agent, skill) triple, by checking and marking a small on-disk
  marker before returning the advisory.

The marker is a TTL-bounded file under ``gaia.paths.tmp_dir()``, like the
PreToolUse -> SubagentStart bridges in ``hooks/adapters/claude_code.py``
(``_cache_context_for_subagent`` / ``_cache_resume_mapping``); it only
suppresses a repeat reminder within one subagent's lifetime.

The caller (``ClaudeCodeAdapter._adapt_write_edit``) carries the text this
module builds in ``hookSpecificOutput.additionalContext``, not in
``permissionDecisionReason``.
"""

import json
import re
import time
from pathlib import Path
from typing import Optional

from gaia.paths import tmp_dir

REMINDER_CACHE_DIR = tmp_dir() / "gaia-artifact-skill-reminders"

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

    A persistence failure (unwritable tmp dir) degrades to "always remind" --
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


def build_reminder_context(file_path: str, skill: str) -> str:
    """Build the advisory ``additionalContext`` text for ``skill``.

    Phrased as a factual observation, not an instruction: from here the
    skill's presence is unknowable (see module docstring), so the text claims
    only that this artifact class is governed by it, and avoids wording shaped
    like an out-of-band system directive.
    """
    return (
        f"[SKILL_REMINDER] '{file_path}' falls under an artifact class "
        f"governed by the '{skill}' skill, describing the conventions that "
        f"apply to it. That skill is loadable as Skill('{skill}')."
    )
