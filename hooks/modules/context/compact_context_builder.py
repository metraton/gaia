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
    contracts and signatures never reach this one. This refresh is the
    snapshot's only post-compaction delivery: the compaction mod only steers.
    """
    blocks = [_build_identity_block()]
    snapshot = _build_snapshot_block(session_id)
    if snapshot:
        blocks.append(snapshot)
    return "\n\n".join(blocks)


def build_summary_instructions() -> str:
    """The brief a host that summarizes its own session is given before it writes the summary.

    Aimed at the summarizer, so only a pre-summary hook delivers it; the
    post-compaction refresh above speaks to the session that resumes.
    """
    return (
        "# Compaction Instructions\n\n"
        "Write the summary so the next turn can resume without re-deriving anything:\n"
        "- Keep the session snapshot above (resume point, open contracts, pending "
        "signatures, active brief/plan/task) verbatim; it is the source of truth for "
        "where work stands.\n"
        "- State the active objective and the exact next action.\n"
        "- Refer to durable work by identifier (memory slugs, brief/plan/task ids, "
        "contract ids); never copy their bodies.\n"
        "- Keep facts that exist only in this conversation, labeled as not durable.\n"
        "- Compress tool output and intermediate reasoning to what the next decision needs."
    )


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
