"""
Host Transcript Access for Gaia.

Adapter-owned utility that encapsulates how the host CLI (Claude Code) persists
a subagent transcript on disk. The host-specific format -- a JSONL file whose
lines are JSON objects with the role/content nested inside a ``message`` field
-- lives ONLY here (inside ``hooks/adapters/``). Business logic modules iterate
over normalized ``(role, content)`` entries via :func:`iter_transcript_entries`
instead of opening the file and calling ``json.loads`` themselves, so the core
stays agnostic to the host CLI's transcript-serialization convention.

Mirrors the ``host_session.py`` pattern: a small standalone module under
``adapters/`` that owns a single host-specific detail and is imported by
business logic, with no dependency on the heavier ``ClaudeCodeAdapter`` (avoids
any circular-import or instantiation concern in low-level modules).

If a future host advertises its transcript in a different shape (e.g. a single
JSON array, or a different nesting), only this module changes; the readers in
``modules/agents/transcript_reader.py`` keep iterating normalized entries.
"""

import json
import logging
from pathlib import Path
from typing import Iterator, Tuple

logger = logging.getLogger(__name__)

# A normalized transcript entry as seen by business logic: (role, content).
# ``content`` is the raw host content -- a str, a list of content blocks, or
# None -- left for the reader to normalize per its own needs.
TranscriptEntry = Tuple[str, object]


def iter_transcript_entries(transcript_path: str) -> Iterator[TranscriptEntry]:
    """Yield ``(role, content)`` for each message entry in the host transcript.

    Encapsulates the host CLI's transcript format: the file at
    ``transcript_path`` is JSONL (one JSON object per line); each object nests
    the role/content inside a ``message`` field, falling back to the object
    itself for a flat ``{role, content}`` shape. Lines that are blank or fail
    to parse as JSON are skipped silently so a partially-written transcript
    never crashes a hook.

    Performs path expansion (``~``) and an existence check. A missing/empty
    path or a nonexistent file yields nothing. Callers receive a uniform
    stream of normalized entries and never see JSONL or ``json.loads``.
    """
    if not transcript_path:
        return
    try:
        path = Path(transcript_path).expanduser()
        if not path.exists():
            logger.debug("Transcript file not found: %s", path)
            return
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(entry, dict):
                    continue
                # Host format: role/content nested inside ``message``; fall
                # back to the entry itself for a flat shape.
                msg = entry.get("message", entry)
                if not isinstance(msg, dict):
                    continue
                yield msg.get("role", ""), msg.get("content", "")
    except Exception as e:  # pragma: no cover - defensive, never crash a hook
        logger.debug("Failed to read transcript from %s: %s", transcript_path, e)
        return
