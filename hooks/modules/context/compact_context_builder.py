"""Compact context builder for post-compaction re-injection."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_TRANSCRIPT_TAIL_BYTES = 262_144


def snapshot_marker(pre_tokens: int) -> str:
    """First line of the message the compaction mod appends to one compaction.

    The token count is the one the host records in that compaction's boundary
    entry (``compactMetadata.preTokens``) and the mod reads back as
    ``tokensBefore``; hooks/mods/compaction/register.ts builds the same line.
    """
    return f"[gaia:session-snapshot tokens-before={pre_tokens}]"


def build_compact_context(*, session_id: str = "", transcript_path: str = "") -> str:
    """Build the orchestrator identity reminder plus the session's own snapshot.

    The snapshot is keyed by ``session_id``; the host event that triggers the
    refresh carries the same id across compaction, so another session's
    contracts and signatures never reach this one. It is left out only when
    the compaction mod appended it to this compaction.
    """
    blocks = [_build_identity_block()]
    snapshot = None if _mod_appended_snapshot(transcript_path) else _build_snapshot_block(session_id)
    if snapshot:
        blocks.append(snapshot)
    return "\n\n".join(blocks)


def _mod_appended_snapshot(transcript_path: str) -> bool:
    """True when a user entry after the latest compact boundary starts with that boundary's marker.

    Any doubt reads as False so the snapshot is delivered twice rather than
    never: no boundary in the tail, an unreadable or unparsable transcript, a
    boundary without a token count, or a marker that belongs to another
    compaction or is merely quoted in a message.
    """
    if not transcript_path:
        return False
    try:
        with open(transcript_path, "rb") as transcript:
            size = transcript.seek(0, 2)
            transcript.seek(max(0, size - _TRANSCRIPT_TAIL_BYTES))
            lines = transcript.read().decode("utf-8", errors="replace").splitlines()
    except OSError as exc:
        logger.debug("Transcript tail unreadable (non-fatal): %s", exc)
        return False

    entries = [entry for entry in map(_parse_entry, lines) if entry is not None]
    boundaries = [
        i for i, entry in enumerate(entries)
        if entry.get("type") == "system" and entry.get("subtype") == "compact_boundary"
    ]
    if not boundaries:
        return False
    pre_tokens = (entries[boundaries[-1]].get("compactMetadata") or {}).get("preTokens")
    if not isinstance(pre_tokens, int):
        return False
    marker = snapshot_marker(pre_tokens)
    return any(
        entry.get("type") == "user" and _entry_text(entry).startswith(marker)
        for entry in entries[boundaries[-1] + 1:]
    )


def _parse_entry(line: str) -> dict | None:
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    return entry if isinstance(entry, dict) else None


def _entry_text(entry: dict) -> str:
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return str(block.get("text", ""))
    return ""


def _build_identity_block() -> str:
    return (
        "# Post-Compaction Context Refresh\n\n"
        "You are the orchestrator. Dispatch work via Agent, resume agents via "
        "SendMessage(to: agentId), get user approval via AskUserQuestion."
    )


def _build_snapshot_block(session_id: str) -> str | None:
    repo_root = str(Path(__file__).resolve().parent.parent.parent.parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    try:
        from gaia.session_snapshot import build_snapshot, render_snapshot

        return render_snapshot(build_snapshot(session_id))
    except Exception as exc:
        logger.debug("Failed to build session snapshot (non-fatal): %s", exc)
        return None
