"""Compact context builder for post-compaction re-injection."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def build_compact_context(*, session_id: str = "") -> str:
    """Build the orchestrator identity reminder plus the session's own snapshot.

    The snapshot is keyed by ``session_id``; the host event that triggers the
    refresh carries the same id across compaction, so another session's
    contracts and signatures never reach this one.
    """
    blocks = [_build_identity_block()]
    snapshot = _build_snapshot_block(session_id)
    if snapshot:
        blocks.append(snapshot)
    return "\n\n".join(blocks)


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
