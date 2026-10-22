"""The brief a host's summarizer gets before a compaction."""
from __future__ import annotations


def build_summary_instructions() -> str:
    """The brief a host that summarizes its own session is given before it writes the summary."""
    return (
        "# Compaction Instructions\n\n"
        "Write the summary so the next turn can resume without re-deriving anything:\n"
        "- Do not restate the session snapshot (resume point, open contracts, pending "
        "signatures, active brief/plan/task); the next turn's birth block carries the "
        "resume point whole and the rest or the command that lists it.\n"
        "- State the active objective and the exact next action.\n"
        "- Refer to durable work by identifier (memory slugs, brief/plan/task ids, "
        "contract ids); never copy their bodies.\n"
        "- Keep facts that exist only in this conversation, labeled as not durable.\n"
        "- Compress tool output and intermediate reasoning to what the next decision needs."
    )
