"""Pending-approval formatting helpers.

Nothing here reads pendings: they live in the DB and are read on demand via
``gaia approvals``. ``scan_pending_approvals`` is a stub that returns ``[]``;
``format_pending_summary`` (with ``_truncate_smart``) renders a list of pending
dicts and is exercised by ``tests/hooks/modules/session/test_pending_scanner.py``.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def scan_pending_approvals(
    approvals_dir: Path,
    session_id: Optional[str] = None,
    current_session_id: Optional[str] = None,
    exclude_live_sessions: bool = False,
) -> List[Dict]:
    """Return ``[]`` unconditionally: pendings are in the DB, not in pending-*.json files."""
    return []


def _truncate_smart(cmd: str, max_len: int = 100) -> str:
    """Truncate a command string with head+tail context when it exceeds max_len.

    Preserves the beginning (verb + first args) and the end (last argument or
    target path) so the summary stays meaningful without occupying a full line.
    """
    if len(cmd) <= max_len:
        return cmd
    head_len = max_len * 2 // 3
    tail_len = max_len - head_len - 1
    return f"{cmd[:head_len]}…{cmd[-tail_len:]}"


def format_pending_summary(pendings: List[Dict]) -> str:
    """Format pending approvals as a readable summary for injection."""
    if not pendings:
        return ""

    lines = [f"## {len(pendings)} aprobaciones pendientes\n"]
    for i, p in enumerate(pendings, 1):
        ctx = p["context"]
        source = ctx.get("source", "unknown")
        desc = ctx.get("description", p["command"])
        risk = ctx.get("risk", "unknown")

        cross_tag = " [session anterior]" if p.get("cross_session") else ""
        lines.append(f"**#{i} [P-{p['nonce_short']}]** `{_truncate_smart(p['command'])}`{cross_tag}")
        lines.append(f"  Hace: {p['age_human']} | Source: {source} | Risk: {risk}")
        if desc != p["command"]:
            lines.append(f"  {desc}")
        lines.append("")

    lines.append('Di "ver P-XXXX" para detalles o "aprobar P-XXXX" para ejecutar.')
    return "\n".join(lines)

