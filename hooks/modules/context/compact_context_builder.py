"""Compact context builder for post-compaction re-injection."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# First line of the message the compaction mod appends; hooks/mods/compaction/
# register.ts declares the same string, and a test holds the two equal.
MOD_SNAPSHOT_MARKER = "[gaia:session-snapshot]"
_TRANSCRIPT_TAIL_BYTES = 32_768


def build_compact_context(*, session_id: str = "", transcript_path: str = "") -> str:
    """Build the orchestrator identity reminder plus the session's own snapshot.

    The snapshot is keyed by ``session_id``; the host event that triggers the
    refresh carries the same id across compaction, so another session's
    contracts and signatures never reach this one. It is left out when the
    compaction mod already appended it to the transcript.
    """
    blocks = [_build_identity_block()]
    snapshot = None if _mod_appended_snapshot(transcript_path) else _build_snapshot_block(session_id)
    if snapshot:
        blocks.append(snapshot)
    return "\n\n".join(blocks)


def _mod_appended_snapshot(transcript_path: str) -> bool:
    """True when the end of the transcript carries the mod's marker; any doubt reads as False so the snapshot is delivered twice rather than never."""
    if not transcript_path:
        return False
    try:
        with open(transcript_path, "rb") as transcript:
            transcript.seek(0, 2)
            transcript.seek(max(0, transcript.tell() - _TRANSCRIPT_TAIL_BYTES))
            return MOD_SNAPSHOT_MARKER.encode() in transcript.read()
    except OSError as exc:
        logger.debug("Transcript tail unreadable (non-fatal): %s", exc)
        return False


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
