"""Compact context builder for post-compaction re-injection."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def build_compact_context(*, session_id: str = "", record_injection: bool = False) -> str:
    """Build the orchestrator identity reminder, the session's Environment block, the user's rows, and its own snapshot.

    The Environment block and the user's sections are the birth block's own,
    so the trusted `gaia CLI:` path and the user's standing rules the
    compaction discarded are published again. The snapshot is keyed by
    ``session_id``; the host event that triggers the refresh carries the same
    id across compaction, so another session's contracts and signatures never
    reach this one. On both hosts this refresh is the only post-compaction
    delivery, outside any summary: the summarizer is only steered. Each user
    row counts one injection only when ``record_injection`` is set, as at birth.
    """
    from modules.context.user_sections import render_user_sections
    from modules.session.session_manifest import (
        build_environment_section,
        orchestrator_user_rows,
        record_user_rows_injected,
    )

    user_rows = orchestrator_user_rows()
    blocks = [
        _build_identity_block(),
        build_environment_section(session_id),
        render_user_sections(user_rows),
        _build_snapshot_block(session_id),
    ]
    text = "\n\n".join(b for b in blocks if b)
    if record_injection:
        record_user_rows_injected(user_rows)
    return text


def build_summary_instructions() -> str:
    """The brief a host that summarizes its own session is given before it writes the summary.

    Aimed at the summarizer, so only a pre-summary hook delivers it; the
    post-compaction refresh above speaks to the session that resumes.
    """
    return (
        "# Compaction Instructions\n\n"
        "Write the summary so the next turn can resume without re-deriving anything:\n"
        "- Do not restate the session snapshot (resume point, open contracts, pending "
        "signatures, active brief/plan/task); it is delivered verbatim to the next turn.\n"
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
