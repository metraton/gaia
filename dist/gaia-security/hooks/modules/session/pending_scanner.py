"""Scan for deferred pending approvals and format a human-readable summary.

DB-only since Task E FS retirement:
  ``scan_pending_db()`` queries the approvals table directly and is the
  sole canonical source for pending-approvals surfacing.  All pending
  types -- T3 commands, COMMAND_SET batches, and SCOPE_FILE_PATH
  file-write blocks -- are written exclusively to gaia.db via
  gaia.approvals.store.insert_requested().

  ``scan_pending_approvals()`` has been retired: no pending-*.json files
  have been written since the M2 cutover.  The stub returns [] so any
  residual callers fail safely without raising.
  ``build_pending_approvals_block`` in session_manifest.py calls
  ``scan_pending_db()`` exclusively.
"""

import json
import logging
import time
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB-primary path (canonical since T2.1 cutover / Brief 71)
# ---------------------------------------------------------------------------

def scan_pending_db() -> List[Dict]:
    """Query the DB approvals table for currently-pending rows.

    Returns the same dict shape as scan_pending_approvals() so the existing
    format_pending_summary() and format_pending_detail() formatters work
    unchanged.  Scopes to ALL pending rows (no session filter) because:
      * The DB is per-machine (~/.gaia/gaia.db), so cross-machine leakage is
        impossible.
      * The session_id stored in approvals rows is the main session_id, while
        $CLAUDE_SESSION_ID inside a subagent is the subagent's id — filtering
        by session would silently drop all subagent-originated pendings (the
        known bug owned by another task; see CONFIRMED FINDINGS, Task C).

    Returns [] on any error (never raises) so the caller's fail-safe catches it.
    """
    try:
        # Lazy import: keeps gaia.approvals out of modules that only use the
        # filesystem path.  Falls back to the repo root path when the installed
        # package is not importable (e.g. running directly from the source tree).
        try:
            from gaia.approvals.store import list_pending
        except ImportError:
            import pathlib as _pl
            import sys as _sys
            _repo = _pl.Path(__file__).resolve().parent.parent.parent.parent.parent
            _sys.path.insert(0, str(_repo))
            from gaia.approvals.store import list_pending

        rows = list_pending(all_sessions=True)
    except Exception as exc:
        logger.debug("scan_pending_db: DB query failed (non-fatal): %s", exc)
        return []

    results = []
    now = time.time()
    for row in rows:
        try:
            approval_id = row.get("id", "unknown")
            # Short display id: strip the "P-" prefix and take first 8 chars.
            nonce_short = approval_id[2:10] if approval_id.startswith("P-") else approval_id[:8]
            nonce_full = approval_id[2:] if approval_id.startswith("P-") else approval_id

            payload_json = row.get("payload_json") or "{}"
            try:
                payload = json.loads(payload_json)
            except (json.JSONDecodeError, TypeError):
                payload = {}

            # Extract command: prefer exact_content, fall back to first
            # command in the commands list, then the operation description.
            command_set_items = payload.get("command_set") or []
            commands_list = payload.get("commands") or []
            command = (
                payload.get("exact_content")
                or (commands_list[0] if commands_list else None)
                or payload.get("operation")
                or "unknown"
            )
            # For COMMAND_SET: the "command" shown is a summary of all commands.
            is_command_set = len(command_set_items) > 1 or len(commands_list) > 1
            if is_command_set:
                all_cmds = (
                    [it["command"] for it in command_set_items if isinstance(it, dict) and it.get("command")]
                    if command_set_items
                    else commands_list
                )
                if len(all_cmds) > 1:
                    command = f"[{len(all_cmds)} commands] " + (all_cmds[0] if all_cmds else command)

            # Reconstruct verb and category from operation field.
            # operation format: "{CATEGORY} command intercepted: {verb}"
            operation = payload.get("operation", "")
            verb = "unknown"
            category = "MUTATIVE"
            if ": " in operation:
                verb = operation.rsplit(": ", 1)[-1].strip()
            if " command intercepted" in operation:
                category = operation.split(" command intercepted")[0].strip()

            # Build a context dict that format_pending_summary can use.
            context = {
                "source": "db",
                "description": payload.get("rationale", ""),
                "risk": payload.get("risk_level", "medium"),
                "rollback": payload.get("rollback_hint"),
            }

            # Age from created_at timestamp.
            age_seconds: float = row.get("age_seconds", 0.0)
            if not age_seconds:
                created_at = row.get("created_at", "")
                if created_at:
                    try:
                        from datetime import datetime, timezone
                        dt = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(
                            tzinfo=timezone.utc
                        )
                        age_seconds = (datetime.now(timezone.utc) - dt).total_seconds()
                    except (ValueError, TypeError):
                        age_seconds = 0.0
            age_human = _format_age(age_seconds)
            timestamp = now - age_seconds

            results.append({
                "nonce_short": nonce_short,
                "nonce_full": nonce_full,
                "command": command,
                "verb": verb,
                "category": category,
                "age_human": age_human,
                "timestamp": timestamp,
                "context": context,
                "scope_type": "db",
                # DB-sourced pendings are not cross-session (all sessions on
                # this machine are the same user); mark False so the formatter
                # does not add the "[session anterior]" tag.
                "cross_session": False,
                "pending_session_id": row.get("session_id", "unknown"),
                # Extra field for deduplication in the UNION path.
                "_approval_id": approval_id,
            })
        except Exception as exc:
            logger.debug("scan_pending_db: skipping row %s: %s", row.get("id"), exc)
            continue

    results.sort(key=lambda x: x["timestamp"])
    return results


def scan_pending_approvals(
    approvals_dir: Path,
    session_id: Optional[str] = None,
    current_session_id: Optional[str] = None,
    exclude_live_sessions: bool = False,
) -> List[Dict]:
    """Filesystem pending scan — retired as of Task E FS retirement.

    No new pending-*.json files are written after the M2 cutover.
    The DB is the sole pending store; use scan_pending_db() instead.

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


def format_pending_detail(pending: Dict) -> str:
    """Format a single pending approval with full details."""
    ctx = pending["context"]
    lines = [
        f"## Detalle P-{pending['nonce_short']}",
        "",
        f"**OPERACION:** {pending['verb']} ({pending['category']})",
        f"**COMANDO:** `{pending['command']}`",
    ]
    if ctx.get("description"):
        lines.append(f"**CONTEXTO:** {ctx['description']}")
    if ctx.get("source"):
        lines.append(f"**SOURCE:** {ctx['source']}")
    if ctx.get("branch"):
        lines.append(f"**BRANCH:** {ctx['branch']}")
    if ctx.get("files_changed"):
        lines.append(f"**ARCHIVOS:** {', '.join(ctx['files_changed'])}")
    if ctx.get("risk"):
        lines.append(f"**RIESGO:** {ctx['risk']}")
    if ctx.get("rollback"):
        lines.append(f"**ROLLBACK:** {ctx['rollback']}")
    lines.append(f"**EDAD:** {pending['age_human']}")
    lines.append("")
    lines.append(f'"aprobar P-{pending["nonce_short"]}" o "rechazar P-{pending["nonce_short"]}"')
    return "\n".join(lines)


def _format_age(seconds: float) -> str:
    """Format seconds into human-readable age."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds/60)} min"
    elif seconds < 86400:
        hours = int(seconds / 3600)
        return f"{hours} hora{'s' if hours > 1 else ''}"
    else:
        days = int(seconds / 86400)
        return f"{days} dia{'s' if days > 1 else ''}"
