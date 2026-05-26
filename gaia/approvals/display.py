"""gaia.approvals.display -- Table and detail formatters for the approvals CLI.

All output functions write to stdout (print). JSON output is handled
by the CLI commands themselves; these formatters produce human-readable
text only.

Public API:
    format_age(seconds) -> str
    print_approvals_table(rows) -> None
    print_approval_detail(approval, events) -> None
    print_events_table(events) -> None
    print_history_table(rows) -> None
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def format_age(seconds: float) -> str:
    """Format seconds into a compact human-readable age string."""
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    if seconds < 86400:
        return f"{int(seconds / 3600)}h"
    return f"{int(seconds / 86400)}d"


def _short_id(approval_id: str) -> str:
    """Return the first 8 chars after the P- prefix for compact display."""
    if approval_id.startswith("P-"):
        tail = approval_id[2:]
        return f"P-{tail[:8]}"
    return approval_id[:10]


def _command_summary(payload_json: Optional[str]) -> str:
    """Extract a one-line command summary from a sealed_payload JSON string."""
    if not payload_json:
        return "-"
    try:
        payload = json.loads(payload_json)
    except (json.JSONDecodeError, TypeError):
        return "-"
    # Prefer exact_content for human readability; fall back to operation.
    content = payload.get("exact_content") or payload.get("operation") or "-"
    # Truncate for table display.
    content = str(content).replace("\n", " ")
    if len(content) > 50:
        return content[:47] + "..."
    return content


def print_approvals_table(rows: List[Dict[str, Any]]) -> None:
    """Print a compact table of approval rows.

    Columns: ID (short), STATUS, AGE, STALE, COMMAND_SUMMARY
    Each row must have the fields produced by store.list_pending() or
    store.list_all():
        id, status, age_seconds, payload_json
    The optional ``stale`` field is shown when present.

    Args:
        rows: List of approval dicts from store.list_pending() / list_all().
    """
    if not rows:
        print("No approvals found.")
        return

    header = f"{'ID':<14}  {'STATUS':<10}  {'AGE':<6}  {'STALE':<5}  COMMAND_SUMMARY"
    print(header)
    print("-" * 80)
    for row in rows:
        short = _short_id(row.get("id", ""))
        status = row.get("status", "-")[:10]
        age = format_age(row.get("age_seconds", 0.0))
        stale = "yes" if row.get("stale") else "no"
        summary = _command_summary(row.get("payload_json"))
        print(f"{short:<14}  {status:<10}  {age:<6}  {stale:<5}  {summary}")

    print(f"\n{len(rows)} approval(s).")


def print_approval_detail(
    approval: Dict[str, Any],
    events: List[Dict[str, Any]],
) -> None:
    """Print full detail for a single approval including its event chain.

    Args:
        approval: Single approval dict from store.get_by_id().
        events: Ordered list of approval_events from store.get_history().
    """
    approval_id = approval.get("id", "?")
    status = approval.get("status", "?")
    created_at = approval.get("created_at", "?")
    decided_at = approval.get("decided_at") or "-"
    session_id = approval.get("session_id") or "-"
    agent_id = approval.get("agent_id") or "-"
    fingerprint = approval.get("fingerprint") or "-"

    # Compute age from created_at.
    age_str = "-"
    try:
        created_dt = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        age_secs = (datetime.now(timezone.utc) - created_dt).total_seconds()
        age_str = format_age(age_secs)
    except (ValueError, TypeError):
        pass

    # Parse sealed_payload for detail display.
    payload = {}
    payload_json = approval.get("payload_json")
    if payload_json:
        try:
            payload = json.loads(payload_json)
        except (json.JSONDecodeError, TypeError):
            pass

    lines = [
        f"Approval {approval_id}",
        "",
        f"  Status      : {status}",
        f"  Age         : {age_str}",
        f"  Created     : {created_at}",
        f"  Decided     : {decided_at}",
        f"  Session     : {session_id}",
        f"  Agent       : {agent_id}",
        f"  Fingerprint : {fingerprint[:16]}..." if len(fingerprint) > 16 else f"  Fingerprint : {fingerprint}",
    ]

    if payload:
        lines.append("")
        lines.append("  Payload:")
        operation = payload.get("operation")
        if operation:
            lines.append(f"    operation    : {operation}")
        exact_content = payload.get("exact_content")
        if exact_content:
            lines.append(f"    exact_content: {exact_content}")
        scope = payload.get("scope")
        if scope:
            lines.append(f"    scope        : {scope}")
        risk = payload.get("risk_level")
        if risk:
            lines.append(f"    risk_level   : {risk}")
        rollback = payload.get("rollback_hint")
        if rollback:
            lines.append(f"    rollback     : {rollback}")
        rationale = payload.get("rationale")
        if rationale:
            lines.append(f"    rationale    : {rationale}")
        commands = payload.get("commands")
        if commands:
            lines.append(f"    commands ({len(commands)}):")
            for i, cmd in enumerate(commands):
                lines.append(f"      [{i}] {cmd}")

    if events:
        lines.append("")
        lines.append(f"  Event chain ({len(events)} event(s)):")
        lines.append(f"  {'#':<4}  {'TYPE':<12}  {'CREATED':<22}  THIS_HASH (first 12)")
        lines.append("  " + "-" * 60)
        for ev in events:
            idx = ev.get("id", "?")
            etype = ev.get("event_type", "?")[:12]
            ev_created = ev.get("created_at", "-")
            this_hash = ev.get("this_hash") or "-"
            hash_preview = this_hash[:12] if len(this_hash) >= 12 else this_hash
            lines.append(f"  {str(idx):<4}  {etype:<12}  {ev_created:<22}  {hash_preview}")
    else:
        lines.append("")
        lines.append("  No events in chain.")

    lines.append("")
    lines.append(f"  Full id     : {approval_id}")
    print("\n".join(lines))


def print_events_table(events: List[Dict[str, Any]]) -> None:
    """Print a compact table of approval_events rows.

    Used by ``gaia approvals history <id>`` to show the chain for one approval.

    Args:
        events: Ordered list of approval_events from store.get_history().
    """
    if not events:
        print("No events found for this approval.")
        return

    print(f"  {'#':<4}  {'TYPE':<12}  {'CREATED':<22}  {'AGENT':<16}  THIS_HASH (first 16)")
    print("  " + "-" * 78)
    for ev in events:
        idx = ev.get("id", "?")
        etype = ev.get("event_type", "?")[:12]
        ev_created = ev.get("created_at", "-")
        agent = (ev.get("agent_id") or "-")[:16]
        this_hash = ev.get("this_hash") or "-"
        hash_preview = this_hash[:16] if len(this_hash) >= 16 else this_hash
        print(f"  {str(idx):<4}  {etype:<12}  {ev_created:<22}  {agent:<16}  {hash_preview}")

    print(f"\n  {len(events)} event(s).")


def print_history_table(rows: List[Dict[str, Any]]) -> None:
    """Print a temporal history table of approvals (any status).

    Used by ``gaia approvals history`` (no id) to show recent approvals.

    Args:
        rows: List of approval dicts from store.list_all().
    """
    if not rows:
        print("No approvals in history.")
        return

    header = f"{'ID':<14}  {'STATUS':<10}  {'AGE':<6}  {'SESSION':<20}  COMMAND_SUMMARY"
    print(header)
    print("-" * 80)
    for row in rows:
        short = _short_id(row.get("id", ""))
        status = row.get("status", "-")[:10]
        age = format_age(row.get("age_seconds", 0.0))
        session = (row.get("session_id") or "-")[:20]
        summary = _command_summary(row.get("payload_json"))
        print(f"{short:<14}  {status:<10}  {age:<6}  {session:<20}  {summary}")

    print(f"\n{len(rows)} approval(s).")
