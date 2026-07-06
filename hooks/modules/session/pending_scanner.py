"""Retired pending-scanner surfacing helpers and formatters.

Cross-session surfacing of pending approvals has been removed:

  * ``scan_pending_db()`` -- the DB feed that surfaced pendings into the
    SessionStart [ACTIONABLE] block -- has been deleted. Nothing injects
    pendings into the orchestrator context anymore. The DB remains the
    canonical pending store; TTL hygiene (``approval_cleanup``) keeps it free
    of orphans by reading ``gaia.approvals.store.list_pending`` directly, and
    the user inspects/acts on pendings on demand via ``gaia approvals``
    (which reads the store through its own ``_scan_pending_shared`` path).

  * ``scan_pending_approvals()`` was retired at the Task E FS cutover: no
    pending-*.json files have been written since. The stub returns [] so any
    residual callers fail safely without raising.

The ``format_pending_summary`` helper (with its ``_truncate_smart`` support
helper) is retained as a generic formatting utility, locked in by
``tests/hooks/modules/session/test_pending_scanner.py``. ``format_pending_detail``
and the module-level ``_format_age`` had no remaining callers (production or
test) after the redesign and were removed as dead code (see Task T10 sweep).
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
    """Filesystem pending scan — retired as of Task E FS retirement.

    No new pending-*.json files are written after the M2 cutover. The DB is the
    sole pending store, read on demand via `gaia approvals`.

    Signature preserved for backward compatibility with callers that still
    reference the function.  Returns [] unconditionally.
    """
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

