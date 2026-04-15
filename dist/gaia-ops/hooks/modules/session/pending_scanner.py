"""Scan for deferred pending approvals and format a human-readable summary."""

import json
import os
import time
from pathlib import Path
from typing import List, Dict, Optional


def scan_pending_approvals(
    approvals_dir: Path,
    session_id: Optional[str] = None,
    current_session_id: Optional[str] = None,
) -> List[Dict]:
    """Scan approvals directory for pending files.

    Returns list of dicts with: nonce (short), command, verb, category,
    age_human (e.g. "14 hours ago"), context (if enriched), timestamp,
    cross_session (bool), pending_session_id.

    If session_id provided, filter to that session. Otherwise return all.
    current_session_id is used to annotate items from prior sessions
    (cross_session=True when pending.session_id != current_session_id).
    """
    results = []

    if not approvals_dir.exists():
        return results

    for f in approvals_dir.glob("pending-*.json"):
        if "index" in f.name:
            continue
        try:
            data = json.loads(f.read_text())
            # Clean up expired pendings (ttl > 0 and expired)
            ttl = data.get("ttl_minutes", 0)
            if ttl > 0:
                elapsed = (time.time() - data.get("timestamp", 0)) / 60
                if elapsed > ttl:
                    try:
                        os.unlink(str(f))
                    except OSError:
                        pass
                    continue
            # Clean up rejected pendings
            if data.get("status") == "rejected":
                try:
                    os.unlink(str(f))
                except OSError:
                    pass
                continue
            # Filter by session if requested
            if session_id and data.get("session_id") != session_id:
                continue
            # Format age
            age_seconds = time.time() - data.get("timestamp", 0)
            age_human = _format_age(age_seconds)

            # Detect cross-session pending approvals
            pending_sid = data.get("session_id", "unknown")
            cross_session = bool(
                current_session_id and pending_sid != current_session_id
            )

            results.append({
                "nonce_short": data["nonce"][:8],
                "nonce_full": data["nonce"],
                "command": data.get("command", data.get("file_path", "unknown")),
                "verb": data.get("danger_verb", "unknown"),
                "category": data.get("danger_category", "UNKNOWN"),
                "age_human": age_human,
                "timestamp": data.get("timestamp", 0),
                "context": data.get("context", {}),
                "scope_type": data.get("scope_type", "semantic_signature"),
                "cross_session": cross_session,
                "pending_session_id": pending_sid,
            })
        except Exception:
            continue

    # Sort by timestamp (oldest first)
    results.sort(key=lambda x: x["timestamp"])
    return results


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
        lines.append(f"**#{i} [P-{p['nonce_short']}]** `{p['command'][:60]}`{cross_tag}")
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
