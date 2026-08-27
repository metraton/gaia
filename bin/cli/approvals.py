"""
gaia approvals -- Approval System v2 Track 1 CLI subcommand.

Subcommands:
  list [--json] [--session SESSION_ID] [--orphans-only]
                                         -- list pending approvals
                                            (--orphans-only filters to
                                             pendings from dead sessions)
  show APPROVAL_ID [--json]              -- show full detail of one approval
  revoke APPROVAL_ID                     -- revoke an active command_set grant by approval_id
  reject APPROVAL_ID [--reason REASON]   -- reject an exact pending approval
  reject --all [--reason REASON]         -- reject ALL pending approvals in one call
  reject-all [--dry-run] [--workspace W] -- reject all pending approvals (subcommand alias)
  clean [--dry-run]                      -- remove expired/stale approvals
  stats [--json]                         -- approval system statistics

All subcommands exit 0 on success, 1 on error.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

# Ensure hooks/ is on sys.path so approval_grants resolves correctly.
# Walks up from this script to the plugin root to include hooks/ and the
# plugin root itself, allowing imports like `approval_grants` to resolve.
_SCRIPT_DIR = Path(__file__).resolve().parent
_BIN_DIR = _SCRIPT_DIR.parent
_PLUGIN_ROOT = _BIN_DIR.parent
_HOOKS_DIR = _PLUGIN_ROOT / "hooks"

for _p in [str(_HOOKS_DIR), str(_PLUGIN_ROOT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _import_grants_dir():
    """Get the grants directory path for approval files.

    Resolution order mirrors get_plugin_data_dir() in paths.py:
    1. CLAUDE_PLUGIN_DATA env var (set by Claude Code at runtime) -- data
       lives at <CLAUDE_PLUGIN_DATA>/cache/approvals/.
    2. Delegate to the approval_grants module which calls get_plugin_data_dir(),
       which in turn walks up from CWD to find .claude/.

    Keeping CLAUDE_PLUGIN_DATA as the first check ensures the CLI finds the
    same approvals directory the hooks use when invoked from any working
    directory (e.g. from inside gaia-dev/ during development).
    """
    import os
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        return Path(plugin_data) / "cache" / "approvals"
    from modules.security.approval_grants import _get_grants_dir
    return _get_grants_dir()


def _import_writer():
    """Import gaia.store.writer lazily to allow mocking in tests."""
    from gaia.store import writer
    return writer


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_age(seconds: float) -> str:
    """Format seconds into a human-readable age string."""
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    if seconds < 86400:
        return f"{int(seconds / 3600)}h"
    return f"{int(seconds / 86400)}d"


def _nonce_short(nonce: str) -> str:
    """Return the 8-char short form used in P-XXXX display."""
    return nonce[:8] if nonce else "?"


def _approval_id_label(nonce: str) -> str:
    """Return the P-XXXX label for display."""
    return f"P-{_nonce_short(nonce)}"


def _is_canonical_approval_id(value: object) -> bool:
    """Return whether value is the complete opaque approval machine id."""
    return (
        isinstance(value, str)
        and len(value) == 34
        and value.startswith("P-")
        and all(char in "0123456789abcdef" for char in value[2:])
    )


def _require_canonical_approval_id(value: object, args=None) -> str | None:
    """Return a trimmed canonical id or print the explicit identity error."""
    approval_id = value.strip() if isinstance(value, str) else ""
    if _is_canonical_approval_id(approval_id):
        return approval_id
    _print_error(
        "Approval lookup requires the canonical approval_id P-<32 lowercase hex>; "
        "short display labels and raw nonces are not lookup keys.",
        args,
    )
    return None


def _pending_to_display(p: dict) -> dict:
    """Convert a raw pending dict to a display-friendly dict."""
    nonce = p.get("nonce", "")
    ts = float(p.get("timestamp", 0))
    age_secs = time.time() - ts if ts else 0
    ctx = p.get("context") or {}
    return {
        "approval_id": _approval_id_label(nonce),
        "nonce_prefix": _nonce_short(nonce),
        "command": p.get("command", ""),
        "verb": p.get("danger_verb", ""),
        "category": p.get("danger_category", ""),
        "age": _format_age(age_secs),
        "age_seconds": round(age_secs),
        "session_id": p.get("session_id", ""),
        "source": ctx.get("source", ""),
        "description": ctx.get("description", ""),
        "risk": ctx.get("risk", ""),
        "rollback": ctx.get("rollback", ""),
        "branch": ctx.get("branch", ""),
        "files_changed": ctx.get("files_changed", []),
        "scope_type": p.get("scope_type", ""),
        "timestamp": ts,
    }


def _pending_to_machine(p: dict) -> dict:
    """Return the complete pending representation used by JSON consumers."""
    payload = p.get("_sealed_payload")
    if not isinstance(payload, dict):
        payload = {}

    from adapters.consent_presentation import payload_commands

    approval_id = p.get("approval_id", "")
    if not approval_id:
        nonce = p.get("nonce", "")
        approval_id = f"P-{nonce}" if nonce else ""

    binding = payload.get("binding")
    if not isinstance(binding, dict):
        binding = {
            key: value
            for key, value in (
                ("agent_id", p.get("agent_id")),
                ("session_id", p.get("session_id")),
                ("call_id", payload.get("call_id")),
            )
            if value
        }

    return {
        "approval_id": approval_id,
        "display_label": _approval_id_label(
            approval_id[2:] if approval_id.startswith("P-") else approval_id
        ),
        "status": p.get("status"),
        "operation": payload.get("operation"),
        "exact_content": payload.get("exact_content"),
        "commands": list(payload_commands(payload)),
        "command_set": payload.get("command_set"),
        "scope": payload.get("scope"),
        "impact": payload.get("impact"),
        "risk_level": payload.get("risk_level"),
        "rollback": payload.get("rollback_hint", payload.get("rollback")),
        "verification": payload.get("verification"),
        "rationale": payload.get("rationale"),
        "request_fingerprint": payload.get("request_fingerprint"),
        "payload_fingerprint": p.get("fingerprint"),
        "correlation_id": payload.get("correlation_id"),
        "binding": binding,
        "agent_id": p.get("agent_id"),
        "session_id": p.get("session_id"),
        "created_at": p.get("created_at"),
        "decided_at": p.get("decided_at"),
        "age_seconds": p.get("age_seconds"),
        "stale": p.get("stale"),
        "sealed_payload": payload,
    }


# ---------------------------------------------------------------------------
# Subcommand: list
# ---------------------------------------------------------------------------

def _scan_pending_shared(exclude_live_sessions: bool = False) -> list:
    """Return all non-expired, non-rejected pending approvals across all sessions.

    DB-primary since Task E: queries gaia.approvals.store (all_sessions=True).
    All pending types (T3 commands, COMMAND_SET batches, SCOPE_FILE_PATH
    file-write blocks) are now written exclusively to the DB.

    When ``exclude_live_sessions=True``, only pendings whose owning session
    is NOT currently alive (orphans) are returned -- this backs the
    ``--orphans-only`` flag.  Session liveness is checked via
    session_registry.get_live_sessions() when available.

    Returns a list of dicts in the shape _pending_to_display() expects.

    Raises:
        Exception: propagated from the store import so cmd_list can catch it
            and return exit code 1 consistently.
    """
    store = _import_approval_store()
    rows = store.list_pending(all_sessions=True)

    # Optional liveness filter.
    if exclude_live_sessions:
        try:
            import sys as _sys
            import pathlib as _pl
            # Ensure hooks/ is importable (mirrors the top-of-file sys.path setup).
            _hooks_dir = str(_PLUGIN_ROOT / "hooks")
            if _hooks_dir not in _sys.path:
                _sys.path.insert(0, _hooks_dir)
            from modules.session.session_registry import get_live_sessions
            live = get_live_sessions(include_headless=False)
            rows = [r for r in rows if r.get("session_id") not in live]
        except Exception:
            pass  # Conservative: return all on registry failure

    results = []
    for row in rows:
        payload_json = row.get("payload_json") or "{}"
        try:
            payload = json.loads(payload_json)
        except (json.JSONDecodeError, TypeError):
            payload = {}

        # Extract command: prefer exact_content, fall back to first command.
        command = (
            payload.get("exact_content")
            or (payload.get("commands") or [None])[0]
            or payload.get("operation")
            or ""
        )

        # Extract verb and category from operation field.
        operation = payload.get("operation", "")
        danger_verb = "unknown"
        danger_category = "MUTATIVE"
        if ": " in operation:
            danger_verb = operation.rsplit(": ", 1)[-1].strip()
        if " command intercepted" in operation:
            danger_category = operation.split(" command intercepted")[0].strip()

        # Compute timestamp from created_at.
        created_at_str = row.get("created_at", "")
        ts: float = 0.0
        if created_at_str:
            try:
                from datetime import datetime as _dt, timezone as _tz
                dt = _dt.strptime(created_at_str, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=_tz.utc
                )
                ts = dt.timestamp()
            except (ValueError, TypeError):
                ts = 0.0

        approval_id = row.get("id", "")
        # nonce: strip the "P-" prefix so _pending_to_display's
        # _approval_id_label("P-" + nonce_prefix) works correctly.
        nonce = approval_id[2:] if approval_id.startswith("P-") else approval_id

        results.append({
            "approval_id": approval_id,
            "nonce": nonce,
            "status": row.get("status"),
            "fingerprint": row.get("fingerprint"),
            "agent_id": row.get("agent_id"),
            "session_id": row.get("session_id", ""),
            "created_at": row.get("created_at"),
            "decided_at": row.get("decided_at"),
            "age_seconds": row.get("age_seconds"),
            "stale": row.get("stale"),
            "_sealed_payload": payload,
            "command": command,
            "danger_verb": danger_verb,
            "danger_category": danger_category,
            "scope_type": payload.get("scope", "semantic_signature"),
            "timestamp": ts,
            "context": {
                "description": payload.get("rationale", ""),
                "risk": payload.get("risk_level", "medium"),
                "rollback": payload.get("rollback_hint"),
                "source": "db",
            },
        })

    results.sort(key=lambda d: d.get("timestamp", 0), reverse=True)
    return results


# approval_grants rows are written ONLY by insert_semantic_grant() and
# insert_plan_command_set() (gaia/store/writer.py), and both run strictly
# AFTER a human decision is recorded in the approvals table -- there is no
# code path that creates a grant row ahead of that decision. So a grant with
# no matching approvals-table row (a pre-v12 or otherwise orphaned row) still
# implies "approved"; this is the fallback for that case only, never a guess
# used in place of a lookup that succeeded.
_GRANT_DECISION_FALLBACK = "approved"


def _resolve_decision_status(approval_id: str) -> str:
    """Look up the one authoritative consent decision for a single grant.

    Used by call sites that render exactly one approval_grants row (cmd_show)
    where a bulk lookup would be overkill. cmd_list resolves the same field
    for every row in one query via gaia.approvals.store.get_status_map()
    instead of calling this per row.
    """
    try:
        store = _import_approval_store()
        row = store.get_by_id(approval_id)
    except Exception:
        return _GRANT_DECISION_FALLBACK
    if row is None:
        return _GRANT_DECISION_FALLBACK
    return row.get("status") or _GRANT_DECISION_FALLBACK


def _grant_to_display(g: dict, decision_status: str | None = None) -> dict:
    """Convert a DB approval_grants row to a display-friendly dict.

    ``status`` in the returned dict is the consent decision -- pending,
    approved, rejected, revoked, expired -- read from the approvals table
    (the same table cmd_revoke/cmd_approve/cmd_reject consult), never the raw
    approval_grants.status column. That column is a different, orthogonal
    fact -- whether this already-approved grant's commands are still
    consumable -- and is returned separately as ``grant_state``. Conflating
    the two under one "status" is exactly the defect this split fixes: an
    approved grant sitting on unconsumed commands has grant_state='PENDING',
    which is not a pending DECISION and must never be labeled as one.

    Args:
        g: The raw approval_grants row.
        decision_status: The approvals.status value for this approval_id,
            pre-resolved by the caller (cmd_list batches this for every row in
            one query). When omitted, resolved here with a single lookup --
            the right choice only for a single-row call site.
    """
    approval_id = g.get("approval_id", "")
    created_at = g.get("created_at", "")
    # Compute age from ISO8601 created_at
    age_secs = 0.0
    try:
        from datetime import datetime, timezone
        created = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        age_secs = (datetime.now(timezone.utc) - created).total_seconds()
    except Exception:
        pass

    command_set = []
    try:
        command_set = json.loads(g.get("command_set_json") or "[]")
    except Exception:
        pass

    # Normalize command_set shape. SCOPE_SEMANTIC_SIGNATURE grants (the dominant
    # case) store a single command as a dict; COMMAND_SET grants store a list of
    # command dicts. A dict indexed as command_set[0] raises KeyError: 0.
    if isinstance(command_set, dict):
        first_cmd = command_set.get("command", "")
        command_count = 1
    elif isinstance(command_set, list):
        first_cmd = command_set[0].get("command", "") if command_set else ""
        command_count = len(command_set)
    else:
        first_cmd = ""
        command_count = 0

    if decision_status is None:
        decision_status = _resolve_decision_status(approval_id)

    return {
        "approval_id": approval_id,
        "status": decision_status,
        "grant_state": g.get("status", ""),
        "scope": g.get("scope", ""),
        "session_id": g.get("session_id", ""),
        "agent_id": g.get("agent_id", ""),
        "created_at": created_at,
        "expires_at": g.get("expires_at", ""),
        "age": _format_age(age_secs),
        "age_seconds": round(age_secs),
        "command_count": command_count,
        "first_command": first_cmd,
        "command_set": command_set,
        "completed_indexes": json.loads(g.get("consumed_indexes_json") or "[]"),
        "next_index": g.get("next_index", 0),
        "failed_index": g.get("failed_index"),
        "failure_reason": g.get("failure_reason"),
        "request_fingerprint": g.get("request_fingerprint"),
        "source": g.get("source", "legacy"),
    }


def cmd_list(args) -> int:
    """List approval grants and pending approvals from the DB.

    Without ``--session``, all grants are shown.  With ``--session SESSION_ID``,
    only that session's grants are shown.

    ``--orphans-only`` filters pending approvals to rows whose owning session
    is no longer alive (orphaned pendings from dead sessions).

    Each DB-grants row carries two independent statuses -- ``status``, the
    consent decision resolved from the approvals table (batched below via
    ``get_status_map`` so this never drifts from what cmd_revoke/cmd_approve/
    cmd_reject act on), and ``grant_state``, the row's own consumption
    lifecycle (PENDING/CONSUMED/FAILED/REVOKED/EXPIRED). Never collapse the
    two: a grant can be an approved decision (``status``) with unconsumed
    commands still live (``grant_state='PENDING'``), which is not a pending
    decision.
    """
    session_id = getattr(args, "session", None)
    orphans_only = getattr(args, "orphans_only", False)

    # DB-backed grant listing (primary path for COMMAND_SET grants)
    try:
        writer = _import_writer()
        db_grants = writer.list_approval_grants(
            session_id=session_id,
            limit=200,
        )
    except Exception:
        db_grants = []

    # DB-backed pending listing (canonical since Task E filesystem retirement).
    # Keep the shared scanner helper, but do not expose its historical `_fs`
    # implementation name as the primary API vocabulary.
    pending_rows = []
    try:
        pending_rows = _scan_pending_shared(exclude_live_sessions=orphans_only)
    except Exception:
        pass

    # One query resolves every row's real consent decision from the approvals
    # table, instead of letting each row echo its own approval_grants.status
    # (a grant-consumption field, not a decision) under the same "status" name.
    # See _grant_to_display's docstring for why the two must never collide.
    decision_statuses: dict = {}
    if db_grants:
        try:
            store = _import_approval_store()
            decision_statuses = store.get_status_map(
                [g.get("approval_id", "") for g in db_grants]
            )
        except Exception:
            decision_statuses = {}

    db_items = [
        _grant_to_display(
            g, decision_statuses.get(g.get("approval_id", ""), _GRANT_DECISION_FALLBACK)
        )
        for g in db_grants
    ]
    pending_display_items = [_pending_to_display(p) for p in pending_rows]

    if getattr(args, "json", False):
        pending_items = [_pending_to_machine(p) for p in pending_rows]
        print(json.dumps({
            "grants": db_items,
            "pending": pending_items,
            # Backward-compatible alias; pending approvals have been DB-backed
            # since Task E. New consumers must use `pending`.
            "pending_fs": pending_items,
            "count": len(db_items) + len(pending_items),
        }, indent=2))
        return 0

    if not db_items and not pending_display_items:
        print("No active grants or pending approvals.")
        return 0

    if db_items:
        # STATUS is the consent decision (always APPROVED for a row that made
        # it into this table -- see _grant_to_display); GRANT_STATE is the
        # separate consumption lifecycle (PENDING=still consumable, CONSUMED,
        # FAILED, REVOKED, EXPIRED). Keeping them in two columns is the fix:
        # an approved grant with unconsumed commands must never print
        # "PENDING" as if it were still awaiting a decision.
        print(
            f"\n{'APPROVAL_ID':<34}  {'STATUS':<10}  {'GRANT_STATE':<12}  "
            f"{'AGE':<6}  {'CMD_COUNT':<10}  FIRST_COMMAND"
        )
        print("-" * 92)
        for item in db_items:
            cmd_preview = item["first_command"][:30]
            print(
                f"{item['approval_id']:<34}  "
                f"{item['status'].upper():<10}  "
                f"{item['grant_state']:<12}  "
                f"{item['age']:<6}  "
                f"{str(item['command_count']):<10}  "
                f"{cmd_preview}"
            )
        print(f"\n{len(db_items)} DB grant(s).")

    if pending_display_items:
        print(f"\n{'ID':<12}  {'AGE':<6}  {'VERB':<10}  {'SOURCE':<16}  COMMAND")
        print("-" * 70)
        for item in pending_display_items:
            cmd_preview = item["command"][:40]
            source = item["source"][:14] if item["source"] else "-"
            print(
                f"{item['approval_id']:<12}  "
                f"{item['age']:<6}  "
                f"{item['verb']:<10}  "
                f"{source:<16}  "
                f"{cmd_preview}"
            )
        print(f"\n{len(pending_display_items)} pending approval(s).")

    return 0


# ---------------------------------------------------------------------------
# Subcommand: show
# ---------------------------------------------------------------------------

def cmd_show(args) -> int:
    """Show full details of a specific approval grant or pending approval.

    Grant compatibility accepts an exact stored grant id. Pending approvals
    require the canonical ``P-<32 lowercase hex>`` id and resolve by exact
    equality in the approvals store. Display labels and raw nonces are never
    lookup keys.
    """
    raw_id = _require_canonical_approval_id(args.approval_id, args)
    if raw_id is None:
        return 1

    # 1. Try DB lookup by full approval_id
    db_row = None
    try:
        writer = _import_writer()
        rows = writer.list_approval_grants(limit=1000)
        for row in rows:
            if row.get("approval_id") == raw_id:
                db_row = row
                break
    except Exception:
        pass

    if db_row is not None:
        item = _grant_to_display(db_row)
        if getattr(args, "json", False):
            detail = dict(db_row)
            # db_row's own "status" is the grant-consumption field (see
            # _grant_to_display); expose the real consent decision alongside
            # it under its own name instead of overwriting or hiding either.
            detail["decision_status"] = item["status"]
            detail["grant_state"] = item["grant_state"]
            print(json.dumps(detail, indent=2))
            return 0
        lines = [
            f"Grant {item['approval_id']}",
            "",
            f"  Status      : {item['status'].upper()}",
            f"  Grant state : {item['grant_state']}",
            f"  Scope       : {item['scope']}",
            f"  Age         : {item['age']}",
            f"  Session     : {item['session_id']}",
            f"  Agent       : {item['agent_id']}",
            f"  Created     : {item['created_at']}",
            f"  Expires     : {item['expires_at']}",
            f"  Commands    : {item['command_count']}",
        ]
        for i, cmd_item in enumerate(item["command_set"]):
            lines.append(f"  [{i}] {cmd_item.get('command', '')}")
            if cmd_item.get("rationale"):
                lines.append(f"      rationale: {cmd_item['rationale']}")
        lines.append("")
        lines.append(f"  To revoke : gaia approvals revoke {item['approval_id']}")
        print("\n".join(lines))
        return 0

    # 2. Pending approvals are DB-only and resolve by exact canonical id.
    try:
        store = _import_approval_store()
        raw = store.get_by_id(raw_id)
    except Exception as exc:
        _print_error(f"Failed to load approval: {exc}", args)
        return 1

    if raw is None:
        _print_error(f"No approval found for ID: {raw_id}", args)
        return 1

    events = store.get_history(raw_id)

    if getattr(args, "json", False):
        print(json.dumps({"approval": raw, "events": events}, indent=2, default=str))
        return 0

    display = _import_approval_display()
    display.print_approval_detail(raw, events)
    return 0


# ---------------------------------------------------------------------------
# Subcommand: revoke
# ---------------------------------------------------------------------------

def _revoke_grant(args, approval_id: str | None = None) -> int:
    """Revoke an active command_set grant by its approval_id (legacy path).

    Calls ``writer.revoke_approval_grant(approval_id)`` to mark the grant
    REVOKED in the DB.  After revocation, any unconsumed commands in the
    command_set will require fresh approval.

    This is the legacy ``approval_grants``-table path. It is invoked as the
    fallback by the unified :func:`cmd_revoke` when an id is not found in the
    new ``approvals`` table.

    ``approval_id`` overrides the one carried by ``args`` for callers that
    already hold the exact stored id.

    Exits 0 on success, 1 if the grant is not found or already in a terminal
    state.
    """
    approval_id = (approval_id or args.approval_id).strip()

    try:
        writer = _import_writer()
        result = writer.revoke_approval_grant(approval_id)
    except Exception as exc:
        _print_error(f"Failed to revoke grant: {exc}", args)
        return 1

    status = result.get("status")
    if status == "applied":
        print(f"Revoked approval_id={approval_id}")
        return 0
    elif status == "not_found":
        _print_error(f"No active grant found for approval_id={approval_id}", args)
        return 1
    elif status == "no_op":
        current = result.get("current_status", "unknown")
        _print_error(
            f"Grant {approval_id} is already in terminal state: {current}",
            args,
        )
        return 1
    else:
        reason = result.get("reason", "unknown error")
        _print_error(f"Revoke failed: {reason}", args)
        return 1


# ---------------------------------------------------------------------------
# Subcommand: reject
# ---------------------------------------------------------------------------

def _reject_live_grant(args, approval_id: str) -> int:
    """Close the exact still-live grant when no pending decision row matches.

    ``list_pending`` cannot see an approval whose decision was already taken, so
    a set that was approved and never consumed matched nothing here and the
    command failed -- on exactly the id with something left to close. Reject
    means "do not let this run"; honoring that intent on a live grant is the
    same close :func:`cmd_revoke` performs, and it leaves the recorded decision
    untouched.

    Exits 0 when that exact grant was closed, otherwise 1.
    """
    try:
        writer = _import_writer()
        live = [
            row for row in writer.list_approval_grants(status="PENDING", limit=500)
            if row.get("approval_id") == approval_id
        ]
    except Exception as exc:
        _print_error(f"Failed to look up grants: {exc}", args)
        return 1

    if len(live) != 1:
        _print_error(f"Cannot reject {approval_id}: no exact live grant exists", args)
        return 1

    return _revoke_grant(args, live[0]["approval_id"])


def cmd_reject(args) -> int:
    """Reject one exact pending approval, or all pending approvals.

    With ``--all``: rejects every non-expired pending approval across all
    sessions.  Exits 0 whether or not any approvals existed.

    Without ``--all``: requires the complete canonical approval_id. Short
    display labels and raw nonces are rejected. Exits 1 when not found.
    """
    reject_all = getattr(args, "all", False)
    reason = getattr(args, "reason", None)

    if reject_all:
        return _cmd_reject_all(args, reason)

    # Single-reject path (original behavior)
    supplied_id = getattr(args, "approval_id", None)
    if supplied_id is None:
        _print_error("APPROVAL_ID is required when --all is not specified.", args)
        return 1

    approval_id = _require_canonical_approval_id(supplied_id, args)
    if approval_id is None:
        return 1

    # DB-primary since Task E: exact identity only, never enumeration/first-match.
    session_id = os.environ.get("CLAUDE_SESSION_ID") or "cli-reject"
    try:
        store = _import_approval_store()
        row = store.get_by_id(approval_id)
        if row is None or row.get("status") != "pending":
            return _reject_live_grant(args, approval_id)
        store.revoke(approval_id, session_id)
    except Exception as exc:
        _print_error(f"Failed to reject approval: {exc}", args)
        return 1

    msg = f"Rejected {approval_id}"
    if reason:
        msg += f" (reason: {reason})"
    if getattr(args, "json", False):
        print(json.dumps({"status": "rejected", "approval_id": approval_id, "reason": reason}))
    else:
        print(msg)
    return 0


def _cmd_reject_all(args, reason: str | None) -> int:
    """Reject all pending approvals across all sessions.

    DB-primary since Task E: queries gaia.approvals.store for all pending
    rows and revokes each via store.revoke(). Exits 0 always -- an empty
    queue is not an error.
    """
    try:
        # Bulk reject operates on the full queue regardless of liveness.
        raw = _scan_pending_shared(exclude_live_sessions=False)
    except Exception as exc:
        _print_error(f"Failed to load approvals: {exc}", args)
        return 1

    if not raw:
        if getattr(args, "json", False):
            print(json.dumps({"status": "ok", "rejected": 0, "ids": []}))
        else:
            print("No pending approvals to reject.")
        return 0

    session_id = os.environ.get("CLAUDE_SESSION_ID") or "cli-reject-all"
    try:
        store = _import_approval_store()
    except Exception as exc:
        _print_error(f"Failed to load approval store: {exc}", args)
        return 1

    rejected_ids = []
    failed_ids = []
    for pending in raw:
        approval_id = pending.get("approval_id") or f"P-{pending.get('nonce', '')}"
        try:
            store.revoke(approval_id, session_id)
            rejected_ids.append(approval_id)
        except Exception:
            failed_ids.append(approval_id)

    n = len(rejected_ids)
    if getattr(args, "json", False):
        payload: dict = {
            "status": "ok" if not failed_ids else "partial",
            "rejected": n,
            "ids": rejected_ids,
        }
        if reason:
            payload["reason"] = reason
        if failed_ids:
            payload["failed"] = failed_ids
        print(json.dumps(payload))
    else:
        summary = f"Rejected {n} approval(s): {', '.join(rejected_ids)}"
        if reason:
            summary += f" (reason: {reason})"
        print(summary)
        if failed_ids:
            _print_error(f"Failed to reject: {', '.join(failed_ids)}", args)

    return 0 if not failed_ids else 1


# ---------------------------------------------------------------------------
# Subcommand: reject-all
# ---------------------------------------------------------------------------

def _grants_dir_for_workspace(workspace: str | None):
    """Resolve the approvals grants directory for the given workspace path.

    When ``workspace`` is provided, returns
    ``<workspace>/.claude/cache/approvals/`` directly, bypassing the
    CLAUDE_PLUGIN_DATA / CWD-walk resolution used by ``_import_grants_dir``.
    When ``workspace`` is None, delegates to ``_import_grants_dir``.
    """
    if workspace is not None:
        return Path(workspace).resolve() / ".claude" / "cache" / "approvals"
    return _import_grants_dir()


def cmd_reject_all(args) -> int:
    """Reject all active pending approvals in one pass.

    Scans the DB for every non-expired, non-rejected pending approval and
    calls ``store.revoke()`` on each approval_id.  This is the canonical
    subcommand surface documented in the pending-approvals skill.

    Flags:
      --dry-run     Preview what would be rejected without writing changes.
      --workspace   Operate on a different workspace's approval cache.
    """
    dry_run: bool = getattr(args, "dry_run", False)
    workspace: str | None = getattr(args, "workspace", None)

    # Scan pending approvals from the DB (all_sessions, no workspace filter
    # needed -- the DB is per-machine, not per-workspace).
    # When --workspace was supplied, emit an informational note that it is
    # ignored (the DB is the authoritative store since Task E).
    if workspace is not None:
        import sys as _sys
        print(
            f"Note: --workspace is ignored; pending approvals are stored in "
            f"~/.gaia/gaia.db (per-machine DB), not in the workspace FS.",
            file=_sys.stderr,
        )

    try:
        raw_pending = _scan_pending_shared(exclude_live_sessions=False)
        raw: list = [
            {
                "approval_id": p.get("approval_id") or f"P-{p.get('nonce', '')}",
                "command": p.get("command", ""),
            }
            for p in raw_pending
        ]
    except Exception as exc:
        _print_error(f"Failed to load approvals: {exc}", args)
        return 1

    if not raw:
        print("No active pendings -- nothing to reject.")
        return 0

    if dry_run:
        print("[dry-run] would reject:")
        for item in raw:
            cmd_preview = item["command"][:60]
            print(f"  {item['approval_id']}  {cmd_preview}")
        print(f"\n{len(raw)} pending(s) would be rejected.")
        return 0

    # Live rejection via store.revoke() (DB path -- all pendings are in DB now).
    session_id = os.environ.get("CLAUDE_SESSION_ID") or "cli-reject-all"
    try:
        store = _import_approval_store()
    except Exception as exc:
        _print_error(f"Failed to load approval store: {exc}", args)
        return 1

    rejected_ids = []
    failed_ids = []
    for item in raw:
        approval_id = item["approval_id"]
        try:
            store.revoke(approval_id, session_id)
            rejected_ids.append(approval_id)
        except Exception:
            failed_ids.append(approval_id)

    n = len(rejected_ids)
    if n > 0:
        print(f"{n} pending(s) rejected: {', '.join(rejected_ids)}")
    if failed_ids:
        _print_error(f"Failed to reject: {', '.join(failed_ids)}", args)

    return 0 if not failed_ids else 1


# ---------------------------------------------------------------------------
# Subcommand: clean
# ---------------------------------------------------------------------------

def cmd_clean(args) -> int:
    """Remove expired approvals and grants from the DB.

    DB-only since FS retirement: all pending approvals and grants live in
    gaia.db.  Expired DB pending rows (status='pending', older than 24h TTL)
    are transitioned to 'revoked' so the append-only event chain is preserved.
    Expired approval_grants rows (status='PENDING', past expires_at) are
    transitioned to 'EXPIRED'.
    """
    dry_run = getattr(args, "dry_run", False)

    if dry_run:
        # Count DB rows that would be cleaned (pending rows older than 24h).
        db_expired = 0
        try:
            store = _import_approval_store()
            rows = store.list_pending(all_sessions=True)
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            for row in rows:
                created_at_str = row.get("created_at", "")
                if created_at_str:
                    try:
                        created_dt = datetime.strptime(
                            created_at_str, "%Y-%m-%dT%H:%M:%SZ"
                        ).replace(tzinfo=timezone.utc)
                        age_hours = (now - created_dt).total_seconds() / 3600
                        if age_hours > 24:
                            db_expired += 1
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass

        # Count expired DB grant rows.
        db_expired_grants = _count_expired_db_grants()

        would_remove = db_expired + db_expired_grants
        msg = f"Dry run: {db_expired} expired DB pending(s) + {db_expired_grants} expired DB grant(s)"
        if getattr(args, "json", False):
            print(json.dumps({
                "dry_run": True,
                "would_remove": would_remove,
                "db_expired": db_expired,
                "db_expired_grants": db_expired_grants,
                "message": msg,
            }))
        else:
            print(msg)
        return 0

    # Real cleanup.
    db_cleaned = 0
    try:
        store = _import_approval_store()
        rows = store.list_pending(all_sessions=True)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        session_id = os.environ.get("CLAUDE_SESSION_ID") or "cli-cleanup"
        for row in rows:
            created_at_str = row.get("created_at", "")
            if not created_at_str:
                continue
            try:
                created_dt = datetime.strptime(
                    created_at_str, "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=timezone.utc)
                age_hours = (now - created_dt).total_seconds() / 3600
                if age_hours > 24:
                    try:
                        store.revoke(row["id"], session_id)
                        db_cleaned += 1
                    except Exception:
                        pass
            except (ValueError, TypeError):
                pass
    except Exception as exc:
        _print_error(f"DB cleanup failed: {exc}", args)

    # Expire DB grant rows past their deadline. Delegated rather than reproduced:
    # this verb used to carry its own copy of the rule, which skipped the TTL-less
    # plan-first rows the writer's sweep now reaches -- so `clean` reported success
    # while leaving live keys behind.
    db_grants_expired = 0
    try:
        db_grants_expired = _import_writer().cleanup_expired_db_grants()
    except Exception:
        pass

    total = db_cleaned + db_grants_expired
    if getattr(args, "json", False):
        print(json.dumps({
            "status": "ok",
            "cleaned": total,
            "db_cleaned": db_cleaned,
            "db_grants_expired": db_grants_expired,
        }))
    else:
        print(f"Cleaned {db_cleaned} expired DB pending(s) and {db_grants_expired} expired DB grant(s).")
    return 0


def _count_expired_db_grants() -> int:
    """Count the PENDING grant rows a real ``clean`` would mark EXPIRED."""
    try:
        return _import_writer().count_expired_db_grants()
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Subcommand: stats
# ---------------------------------------------------------------------------

def cmd_stats(args) -> int:
    """Show approval system statistics from the DB.

    DB-only since FS retirement: all pending approvals and grants live in
    gaia.db.  Counts are derived from the approvals table (all statuses) and
    the approval_grants table (active grants).
    """
    # DB counts.
    db_pending = 0
    db_approved = 0
    db_rejected = 0
    db_revoked = 0
    verb_counts: dict = {}
    try:
        store = _import_approval_store()
        all_rows = store.list_all(limit=1000)
        for row in all_rows:
            status = row.get("status", "")
            if status == "pending":
                db_pending += 1
                # Extract verb from payload for breakdown.
                payload_json = row.get("payload_json") or "{}"
                try:
                    payload = json.loads(payload_json)
                    operation = payload.get("operation", "")
                    verb = "unknown"
                    if ": " in operation:
                        verb = operation.rsplit(": ", 1)[-1].strip()
                    verb_counts[verb] = verb_counts.get(verb, 0) + 1
                except Exception:
                    pass
            elif status == "approved":
                db_approved += 1
            elif status == "rejected":
                db_rejected += 1
            elif status == "revoked":
                db_revoked += 1
    except Exception as exc:
        _print_error(f"Failed to query DB statistics: {exc}", args)
        return 1

    # Active DB grants.
    db_active_grants = 0
    try:
        writer = _import_writer()
        db_grants = writer.list_approval_grants(limit=500)
        db_active_grants = len(db_grants)
    except Exception:
        pass

    stats = {
        "pending_all_sessions": db_pending,
        "approved": db_approved,
        "rejected": db_rejected,
        "revoked": db_revoked,
        "active_db_grants": db_active_grants,
        "verb_breakdown": verb_counts,
    }

    if getattr(args, "json", False):
        print(json.dumps(stats, indent=2))
        return 0

    print("Approval System Stats")
    print("---------------------")
    print(f"  Pending (all sessions) : {stats['pending_all_sessions']}")
    print(f"  Approved               : {stats['approved']}")
    print(f"  Rejected               : {stats['rejected']}")
    print(f"  Revoked                : {stats['revoked']}")
    print(f"  Active DB grants       : {stats['active_db_grants']}")
    if verb_counts:
        print("  Verb breakdown (pending):")
        for verb, cnt in sorted(verb_counts.items(), key=lambda x: -x[1]):
            print(f"    {verb:<16} {cnt}")
    return 0


# ---------------------------------------------------------------------------
# Error helper
# ---------------------------------------------------------------------------

def _print_error(msg: str, args=None) -> None:
    """Print error in the appropriate format."""
    if args and getattr(args, "json", False):
        print(json.dumps({"error": msg}))
    else:
        print(f"Error: {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Approval store import helper (lazy, for test monkeypatching)
# ---------------------------------------------------------------------------

def _import_approval_store():
    """Import gaia.approvals.store lazily to allow mocking in tests."""
    from gaia.approvals import store
    return store


def _import_approval_display():
    """Import gaia.approvals.display lazily."""
    from gaia.approvals import display
    return display


# ---------------------------------------------------------------------------
# T3.1: gaia approvals pending -- shortcut for list --status=pending
# ---------------------------------------------------------------------------

def cmd_pending(args) -> int:
    """Show pending approvals from the new approvals table.

    With no arguments, returns all pending approvals from all sessions on this
    machine (the DB is per-machine, so all-sessions is the correct default
    scope).  This avoids the Bug B / P-a11d14e0 silent-drop: inside a
    subagent ``$CLAUDE_SESSION_ID`` is the subagent's own session id, not the
    orchestrator session id stored on the approval row, so an exact-match
    filter would silently return nothing.

    With ``--session SESSION_ID``, filters to that explicit session id only
    (useful when the caller holds a known-good orchestrator session id).
    With ``--all-sessions``, same as the default (kept for backwards
    compatibility with callers that pass the flag explicitly).

    Exits 0 on success, 1 on error.
    """
    all_sessions = getattr(args, "all_sessions", False)
    session_id = getattr(args, "session", None)
    output_json = getattr(args, "json", False)

    # No auto-derivation from $CLAUDE_SESSION_ID.  Inside a subagent that env
    # var holds the subagent's own session id, which does NOT match the
    # orchestrator session_id stored on approval rows -- exact-match filtering
    # would silently drop all pending rows.  When no explicit --session is
    # supplied, pass session_id=None so get_pending() uses the all-sessions
    # query (``WHERE status='pending'`` with no session filter).

    try:
        store = _import_approval_store()
        rows = store.list_pending(
            all_sessions=all_sessions,
            session_id=session_id,
        )
    except Exception as exc:
        _print_error(f"Failed to query pending approvals: {exc}", args)
        return 1

    if output_json:
        print(json.dumps(rows, indent=2, default=str))
        return 0

    display = _import_approval_display()
    display.print_approvals_table(rows)
    return 0


# ---------------------------------------------------------------------------
# T3.2: gaia approvals show-v2 <id> -- detail from new approvals table
# (Registered as 'show' overlay -- checks new DB first, falls back to old)
# T3.2: gaia approvals history <id> -- event chain for one approval
# ---------------------------------------------------------------------------

def _resolve_approval_id(raw_id: str) -> str:
    """Normalize a raw approval_id input by trimming surrounding whitespace.

    The input is passed through unchanged otherwise. Pending lookup accepts
    only the canonical full id; trimming is not a translation from a display
    label or raw nonce into machine identity.
    """
    return raw_id.strip()


def _grant_row_for(approval_id: str) -> dict | None:
    """Read the approval_grants row an approval armed, when it armed one.

    Two rows describe one approval: ``approvals`` holds the decision the user
    made, ``approval_grants`` holds whether that decision is still usable. A
    detail view built from the first alone answers "was this approved?" with
    ``approved`` for a capability whose window closed minutes later.
    """
    try:
        writer = _import_writer()
        for row in writer.list_approval_grants(limit=1000):
            if row.get("approval_id") == approval_id:
                return row
    except Exception:
        return None
    return None


def cmd_show_v2(args) -> int:
    """Show full detail for an approval from the new approvals table.

    Looks up the full canonical id by exact equality. The fallback preserves
    exact legacy grant-id lookup only; it does not introduce pending-prefix
    matching.

    Renders the grant plane alongside the decision -- grant_state, expires_at
    and whether the window is still open -- because those live in
    ``approval_grants``, which this command never read.

    Exits 0 on success, 1 when not found.
    """
    raw_id = _require_canonical_approval_id(args.approval_id, args)
    if raw_id is None:
        return 1
    output_json = getattr(args, "json", False)

    try:
        store = _import_approval_store()
        approval = store.get_by_id(raw_id)
        if approval is None:
            # Fall back to old show command.
            return cmd_show(args)

        events = store.get_history(raw_id)
    except Exception as exc:
        # Try old path on error.
        try:
            return cmd_show(args)
        except Exception:
            _print_error(f"Failed to load approval: {exc}", args)
            return 1

    grant = _grant_row_for(raw_id)

    if output_json:
        print(json.dumps(
            {"approval": approval, "events": events, "grant": grant},
            indent=2,
            default=str,
        ))
        return 0

    display = _import_approval_display()
    display.print_approval_detail(approval, events, grant=grant)
    return 0


def cmd_history_single(args) -> int:
    """Show the event chain for a single approval (by id).

    Exits 0 on success, 1 when not found.
    """
    raw_id = _require_canonical_approval_id(args.approval_id, args)
    if raw_id is None:
        return 1
    output_json = getattr(args, "json", False)

    try:
        store = _import_approval_store()
        approval = store.get_by_id(raw_id)
        if approval is None:
            _print_error(f"No approval found for id: {raw_id}", args)
            return 1
        events = store.get_history(raw_id)
    except Exception as exc:
        _print_error(f"Failed to load events: {exc}", args)
        return 1

    if output_json:
        print(json.dumps({"approval_id": raw_id, "events": events}, indent=2, default=str))
        return 0

    print(f"Event chain for approval {raw_id}:")
    display = _import_approval_display()
    display.print_events_table(events)
    return 0


# ---------------------------------------------------------------------------
# gaia approvals revoke <id> -- unified revoke (auto-detects pending vs grant)
# ---------------------------------------------------------------------------

def cmd_revoke(args) -> int:
    """Revoke an approval, auto-detecting which store owns it.

    First looks the id up in the new ``approvals`` table. If found and
    ``pending``, inserts a REVOKED event and updates status to 'revoked'.
    Otherwise -- the id is unknown to the decision log, or the decision was
    already taken -- falls through to the grant path (:func:`_revoke_grant`),
    which closes the capability in ``approval_grants``.

    An already-decided approval is the case that matters most: an approved grant
    that no execution ever consumed is still live, and it is the only state in
    which a loose key can exist. Refusing to act on it because the row is not
    'pending' left the one closable state unreachable from the tool. What is
    closed is the ability to USE the approval; the recorded decision itself is
    never rewritten.

    With ``--yes``, skips the interactive confirmation prompt.
    Exits 0 on success, 1 on error.
    """
    raw_id = _require_canonical_approval_id(args.approval_id, args)
    if raw_id is None:
        return 1
    skip_confirm = getattr(args, "yes", False)

    try:
        store = _import_approval_store()
        approval = store.get_by_id(raw_id)
    except Exception as exc:
        _print_error(f"Failed to look up approval: {exc}", args)
        return 1

    if approval is None:
        # Fall back to legacy grant revoke if not found in new table.
        return _revoke_grant(args)

    current_status = approval.get("status", "?")
    if current_status != "pending":
        return _revoke_grant(args)

    if not skip_confirm:
        display = _import_approval_display()
        print(f"Revoke approval {raw_id}?")
        print(f"  Status  : {current_status}")
        op = ""
        payload_json = approval.get("payload_json")
        if payload_json:
            try:
                payload = json.loads(payload_json)
                op = payload.get("operation") or payload.get("exact_content") or ""
            except (json.JSONDecodeError, TypeError):
                pass
        if op:
            print(f"  Command : {op}")
        try:
            confirm = input("Confirm revoke? [y/N] ").strip().lower()
        except EOFError:
            confirm = "n"
        if confirm not in ("y", "yes"):
            print("Revoke cancelled.")
            return 0

    session_id = os.environ.get("CLAUDE_SESSION_ID") or "cli-session"
    try:
        store = _import_approval_store()
        store.revoke(raw_id, session_id)
    except ValueError as exc:
        _print_error(str(exc), args)
        return 1
    except Exception as exc:
        _print_error(f"Revoke failed: {exc}", args)
        return 1

    print(f"Revoked {raw_id}")
    return 0


# ---------------------------------------------------------------------------
# T3.3: gaia approvals approve <id> -- cross-session grant
# ---------------------------------------------------------------------------

def cmd_approve(args) -> int:
    """Approve a pending approval (cross-session).

    A user in any session can approve a pending approval created in any
    other session on the same machine. Inserts an APPROVED event and
    updates status to 'approved'.

    With ``--yes``, skips the interactive confirmation prompt.
    Exits 0 on success, 1 on error.
    """
    raw_id = _require_canonical_approval_id(args.approval_id, args)
    if raw_id is None:
        return 1
    skip_confirm = getattr(args, "yes", False)
    output_json = getattr(args, "json", False)

    try:
        store = _import_approval_store()
        approval = store.get_by_id(raw_id)
    except Exception as exc:
        _print_error(f"Failed to look up approval: {exc}", args)
        return 1

    if approval is None:
        _print_error(f"No approval found for id: {raw_id}", args)
        return 1

    current_status = approval.get("status", "?")
    if current_status != "pending":
        _print_error(
            f"Cannot approve approval {raw_id}: status is {current_status!r} (must be 'pending')",
            args,
        )
        return 1

    if not skip_confirm:
        print(f"Approve {raw_id}?")
        payload_json = approval.get("payload_json")
        if payload_json:
            try:
                payload = json.loads(payload_json)
                op = payload.get("exact_content") or payload.get("operation") or ""
                if op:
                    print(f"  Command : {op}")
                risk = payload.get("risk_level")
                if risk:
                    print(f"  Risk    : {risk}")
            except (json.JSONDecodeError, TypeError):
                pass
        try:
            confirm = input("Confirm approve? [y/N] ").strip().lower()
        except EOFError:
            confirm = "n"
        if confirm not in ("y", "yes"):
            print("Approval cancelled.")
            return 0

    session_id = os.environ.get("CLAUDE_SESSION_ID") or "cli-session"
    try:
        store = _import_approval_store()
        payload = json.loads(approval.get("payload_json") or "{}")
        if payload.get("request_type") == "COMMAND_SET":
            from gaia.store.writer import insert_plan_command_set
            con = store._open_db()
            try:
                con.execute("BEGIN IMMEDIATE")
                applied = insert_plan_command_set(
                    raw_id, payload["command_set"],
                    request_fingerprint=payload["request_fingerprint"],
                    agent_id=approval.get("agent_id"),
                    session_id=approval.get("session_id"), con=con,
                )
                if applied.get("status") != "applied":
                    raise RuntimeError(applied.get("reason", "grant persistence failed"))
                store.approve(raw_id, approver_session=session_id, con=con)
                con.commit()
            except Exception:
                con.rollback()
                raise
            finally:
                con.close()
        else:
            store.approve(raw_id, approver_session=session_id)
    except ValueError as exc:
        _print_error(str(exc), args)
        return 1
    except Exception as exc:
        _print_error(f"Approve failed: {exc}", args)
        return 1

    if output_json:
        print(json.dumps({"status": "approved", "approval_id": raw_id}))
    else:
        print(f"Approved {raw_id}")
    return 0


def cmd_request_set(args) -> int:
    """Validate and persist a plan-first COMMAND_SET approval request.

    ``--verification`` and ``--rollback`` are sealed into the payload rather
    than left for the consent surface to fill in: the requesting agent already
    owes both on the ``approval_request`` block it will emit (verification
    blocking, rollback advisory -- see
    ``modules.agents.contract_validator._APPROVAL_REQUIRED_FIELDS``), so the
    value the user is shown is the one its author wrote and not a sentence
    composed downstream. Omitted, the surface states the field was never
    declared; it never invents one.
    """
    try:
        from gaia.approvals.command_set import request_fingerprint, validate_request_set
        store = _import_approval_store()
        items = validate_request_set(list(args.command))
        fingerprint = request_fingerprint(item["command"] for item in items)
        payload = {
            "request_type": "COMMAND_SET",
            "operation": "Execute an ordered T3 command set",
            "exact_content": "\n".join(item["command"] for item in items),
            "commands": [item["command"] for item in items],
            "command_set": items,
            "request_fingerprint": fingerprint,
            "scope": "COMMAND_SET",
            "risk_level": "high",
            "rollback_hint": (getattr(args, "rollback", None) or "").strip() or None,
            "verification": (getattr(args, "verification", None) or "").strip() or None,
            "rationale": args.rationale or "Plan-first ordered execution",
        }
        approval_id = store.insert_requested(
            payload,
            agent_id=args.agent_id,
            session_id=args.session_id,
        )
    except Exception as exc:
        _print_error(f"COMMAND_SET request rejected: {exc}", args)
        return 1
    result = {"status": "pending", "approval_id": approval_id, "command_set": items}
    if args.json:
        print(json.dumps(result))
    else:
        print(f"Requested {approval_id} for {len(items)} ordered T3 commands")
    return 0


def _opencode_binding(args) -> tuple[dict | None, str | None]:
    """Verify that a native OpenCode permission owns this approval decision."""
    approval_id = _resolve_approval_id(args.approval_id)
    if not _is_canonical_approval_id(approval_id):
        return None, (
            "Approval lookup requires the canonical approval_id P-<32 lowercase hex>; "
            "short display labels and raw nonces are not lookup keys."
        )
    session_id = args.session_id.strip()
    call_id = args.call_id.strip()
    token = args.token.strip()
    if not session_id or not call_id or not token:
        return None, "session ID, call ID, and token are required"

    try:
        store = _import_approval_store()
        approval = store.get_by_id(approval_id)
    except Exception as exc:
        return None, f"Failed to load approval: {exc}"

    if approval is None:
        return None, f"No approval found for id: {approval_id}"
    if approval.get("status") != "pending":
        return None, f"Approval {approval_id} is not pending"
    if approval.get("session_id") != session_id:
        return None, "OpenCode session does not own this approval"

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    try:
        events = store.get_history(approval_id)
    except Exception as exc:
        return None, f"Failed to load approval history: {exc}"
    for event in reversed(events):
        if event.get("event_type") != "SHOWN":
            continue
        try:
            metadata = json.loads(event.get("metadata_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if (
            metadata.get("host") == "opencode"
            and metadata.get("call_id") == call_id
            and hmac.compare_digest(metadata.get("token_sha256", ""), token_hash)
        ):
            return approval, None
    return None, "No matching OpenCode permission presentation exists"


def _opencode_presentation(approval: dict, session_id: str, call_id: str) -> dict:
    """Build the native payload OpenCode presents for one pending approval.

    Returns the visible surface and its structured mirror, both rendered from
    one sealed envelope so the host edge carries them and composes neither. A
    payload that cannot be sealed completely returns ``presentation_error``
    instead: the SHOWN record still stands, and the plugin refuses to raise a
    permission it cannot show the user in full.
    """
    approval_id = approval.get("id") or ""
    try:
        presentation = _import_consent_presentation()
        consent = _import_consent_events()
        binding = consent.binding_from_mapping(
            {
                "agent_id": approval.get("agent_id") or "opencode-plugin",
                "session_id": session_id,
                "call_id": call_id,
            }
        )
        sealed_payload = json.loads(approval.get("payload_json") or "{}")
        envelope = presentation.envelope_from_sealed_payload(
            sealed_payload,
            approval_id=approval_id,
            binding=binding,
        )
        return presentation.native_presentation(envelope, sealed_payload)
    except Exception as exc:
        return {"presentation_error": str(exc)}


def cmd_opencode_present(args) -> int:
    """Record an OpenCode-native presentation before requesting user consent."""
    approval, error = _opencode_binding(args)
    if approval is not None:
        # A matching event already exists. Presentation is idempotent so plugin
        # reloads do not create a second UI request for the same tool call.
        if getattr(args, "json", False):
            print(json.dumps({
                "status": "presented",
                "approval_id": approval["id"],
                **_opencode_presentation(
                    approval, args.session_id.strip(), args.call_id.strip()
                ),
            }))
        return 0
    if error != "No matching OpenCode permission presentation exists":
        _print_error(error or "Invalid OpenCode approval presentation", args)
        return 1

    approval_id = _resolve_approval_id(args.approval_id)
    session_id = args.session_id.strip()
    call_id = args.call_id.strip()
    token = args.token.strip()
    try:
        store = _import_approval_store()
        approval = store.get_by_id(approval_id)
        if approval is None or approval.get("status") != "pending":
            raise ValueError(f"Approval {approval_id} is not pending")
        if approval.get("session_id") != session_id:
            raise ValueError("OpenCode session does not own this approval")
        store.record_event(
            approval_id,
            "SHOWN",
            agent_id="opencode-plugin",
            session_id=session_id,
            metadata_json=json.dumps(
                {
                    "host": "opencode",
                    "call_id": call_id,
                    "token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
                },
                sort_keys=True,
            ),
        )
    except Exception as exc:
        _print_error(f"OpenCode presentation failed: {exc}", args)
        return 1
    if getattr(args, "json", False):
        print(json.dumps({
            "status": "presented",
            "approval_id": approval_id,
            **_opencode_presentation(approval, session_id, call_id),
        }))
    return 0


def _import_consent_presentation():
    """Import the harness-neutral consent presentation renderer."""
    import sys as _sys

    hooks_dir = str(_PLUGIN_ROOT / "hooks")
    if hooks_dir not in _sys.path:
        _sys.path.insert(0, hooks_dir)
    from adapters import consent_presentation

    return consent_presentation


def _import_consent_events():
    """Import the harness-neutral consent vocabulary from the hooks package."""
    import sys as _sys

    hooks_dir = str(_PLUGIN_ROOT / "hooks")
    if hooks_dir not in _sys.path:
        _sys.path.insert(0, hooks_dir)
    from adapters import consent_events

    return consent_events


def cmd_opencode_decide(args) -> int:
    """Apply a native OpenCode permission reply to its bound Gaia approval.

    The reply is normalized into one neutral, correlated decision before any
    state moves: the lane a host delivered it on is carried as a neutral token,
    never as a host event name, and the correlation is a pure function of the
    approval and its binding so two deliveries of the same reply -- in
    different lanes or different processes -- collapse onto one identity.

    A ``once`` reply activates the single-use grant for this approval and a
    ``reject`` reply grants nothing; ``always`` is refused outright, because
    this protocol version issues no standing grant it could stand for.
    """
    approval, error = _opencode_binding(args)
    if error:
        _print_error(error, args)
        return 1
    try:
        consent = _import_consent_events()
        lane = getattr(args, "decision_lane", None) or consent.PREFERRED_DECISION_LANE
        consent.lane_rank(lane)
        binding = consent.binding_from_mapping(
            {
                "agent_id": approval.get("agent_id") or "opencode-plugin",
                "session_id": args.session_id,
                "call_id": args.call_id,
            }
        )
        decision = consent.build_decision(approval["id"], binding, args.reply)
    except Exception as exc:
        _print_error(f"OpenCode approval decision was not normalized: {exc}", args)
        return 1
    # Refused before any store access, so the refusal provably grants nothing.
    # Narrowing a standing grant to a single-use one would hand the user a
    # weaker grant than the one they answered for, without telling them.
    if decision.decision is consent.ConsentDecision.ALWAYS:
        _print_error(
            "OpenCode 'always' consent is unsupported: this protocol version issues "
            "only single-use grants, and a standing grant would have to be bounded and "
            "auditable before it could be honored. Reply 'once' per operation.",
            args,
        )
        return 1
    try:
        store = _import_approval_store()
        if decision.decision is consent.ConsentDecision.REJECT:
            store.reject(approval["id"], args.session_id, agent_id="opencode-plugin")
            status = "rejected"
        else:
            payload = json.loads(approval.get("payload_json") or "{}")
            if payload.get("request_type") == "COMMAND_SET":
                store.activate_command_set_atomically(
                    approval["id"],
                    payload.get("command_set") or [],
                    request_fingerprint=payload.get("request_fingerprint", ""),
                    shown_payload=payload,
                    approver_session=args.session_id,
                    agent_id=binding.agent_id,
                    binding={
                        "agent_id": binding.agent_id,
                        "session_id": binding.session_id,
                        "call_id": binding.call_id,
                    },
                )
            else:
                store.approve(approval["id"], args.session_id, agent_id="opencode-plugin")
            status = "approved"
    except Exception as exc:
        _print_error(f"OpenCode approval decision failed: {exc}", args)
        return 1
    if getattr(args, "json", False):
        print(json.dumps({
            "status": status,
            "approval_id": approval["id"],
            "decision": decision.decision.value,
            "decision_lane": lane,
            "correlation_id": decision.correlation_id,
            "request_fingerprint": decision.request_fingerprint,
            "protocol_version": decision.protocol_version,
        }))
    return 0


# ---------------------------------------------------------------------------
# T3.4: gaia approvals history [--limit N] -- temporal view
# ---------------------------------------------------------------------------

def cmd_history(args) -> int:
    """Show a temporal history of approvals across all sessions.

    Without a positional id, shows the most recent N approvals regardless
    of status (pending, approved, rejected, revoked). Use --limit to
    control how many rows to show.

    With a positional id, shows the event chain for that specific approval
    (delegates to cmd_history_single).

    Exits 0 always.
    """
    approval_id = getattr(args, "approval_id", None)
    if approval_id:
        return cmd_history_single(args)

    limit = getattr(args, "limit", 50)
    status_filter = getattr(args, "status", None)
    output_json = getattr(args, "json", False)

    try:
        store = _import_approval_store()
        rows = store.list_all(status=status_filter, limit=limit)
    except Exception as exc:
        _print_error(f"Failed to query history: {exc}", args)
        return 1

    if output_json:
        print(json.dumps(rows, indent=2, default=str))
        return 0

    display = _import_approval_display()
    display.print_history_table(rows)
    return 0


# ---------------------------------------------------------------------------
# T3.5: gaia approvals replay <id> [--dry-run]
# ---------------------------------------------------------------------------

def cmd_replay(args) -> int:
    """Replay the commands from an executed approval.

    Re-presents the sealed_payload of an approval so the user can confirm
    and re-execute the same commands. Validates fingerprint before showing.

    With ``--dry-run``, prints the commands that would be re-executed without
    prompting or running them.

    Exits 0 on success.
    Exits 1 when the approval is not found or has no EXECUTED payload.
    """
    raw_id = _require_canonical_approval_id(args.approval_id, args)
    if raw_id is None:
        return 1
    dry_run = getattr(args, "dry_run", False)
    skip_confirm = getattr(args, "yes", False)
    output_json = getattr(args, "json", False)

    try:
        store = _import_approval_store()
        approval = store.get_by_id(raw_id)
        if approval is None:
            _print_error(f"No approval found for id: {raw_id}", args)
            return 1
    except Exception as exc:
        _print_error(f"Failed to look up approval: {exc}", args)
        return 1

    # Retrieve and validate the payload.
    try:
        store = _import_approval_store()
        payload = store.get_executed_payload(raw_id)
    except Exception as exc:
        _print_error(f"Failed to retrieve payload: {exc}", args)
        return 1

    if payload is None:
        _print_error(
            f"No executed payload found for approval {raw_id}. "
            "Cannot replay an approval that was never executed.",
            args,
        )
        return 1

    # Validate fingerprint against REQUESTED event.
    try:
        from gaia.approvals.chain import verify_fingerprint
        import json as _json
        canon_json = _json.dumps(payload, sort_keys=True, separators=(",", ":"))
        store = _import_approval_store()
        con = store._open_db()
        try:
            verify_fingerprint(raw_id, canon_json, con)
        finally:
            con.close()
    except Exception as exc:
        _print_error(f"Replay fingerprint validation failed: {exc}", args)
        return 1

    commands = payload.get("commands") or []
    exact_content = payload.get("exact_content") or ""
    if not commands and exact_content:
        commands = [l.strip() for l in exact_content.splitlines() if l.strip()]

    if output_json:
        print(json.dumps({"approval_id": raw_id, "payload": payload, "commands": commands}))
        return 0

    print(f"\nReplay approval {raw_id}")
    print("-" * 60)
    op = payload.get("operation") or ""
    if op:
        print(f"  Operation : {op}")
    risk = payload.get("risk_level") or ""
    if risk:
        print(f"  Risk      : {risk}")
    if commands:
        print(f"  Commands  ({len(commands)}):")
        for i, cmd in enumerate(commands):
            print(f"    [{i}] {cmd}")
    else:
        print("  (No commands recorded)")

    if dry_run:
        print("\n[dry-run] -- commands not executed.")
        return 0

    if not skip_confirm:
        try:
            confirm = input("\nRe-execute these commands? [y/N] ").strip().lower()
        except EOFError:
            confirm = "n"
        if confirm not in ("y", "yes"):
            print("Replay cancelled.")
            return 0

    _print_error(
        "Replay does not execute shell text. Create a new request-set and issue "
        "each approved command as a separate Bash tool call in recorded order.",
        args,
    )
    return 1


# ---------------------------------------------------------------------------
# Plugin registration (called by bin/gaia dispatcher)
# ---------------------------------------------------------------------------

def register(subparsers) -> None:
    """Register the 'approvals' subcommand group with the root parser."""
    p = subparsers.add_parser(
        "approvals",
        help="Manage T3 pending approvals",
        description="View, approve, reject, and replay Gaia approval requests.",
    )
    sub = p.add_subparsers(dest="approvals_cmd", metavar="SUBCOMMAND")
    sub.required = True

    # list (legacy + new DB path via pending)
    p_list = sub.add_parser(
        "list",
        help="List pending approvals (legacy + DB)",
        description=(
            "List DB-backed command_set/semantic-signature grants, then the\n"
            "genuinely undecided pending approvals below them.\n\n"
            "The DB-grants table has two status columns, and they answer two\n"
            "different questions:\n"
            "  STATUS       -- the consent decision (approved/rejected/revoked/\n"
            "                  expired), read from the approvals table. A row only\n"
            "                  ever appears in this table after a decision was\n"
            "                  made, so STATUS is APPROVED here in practice.\n"
            "  GRANT_STATE  -- whether this already-approved grant's commands\n"
            "                  are still usable: PENDING (unconsumed, can still\n"
            "                  be replayed), CONSUMED, FAILED, REVOKED, EXPIRED.\n\n"
            "GRANT_STATE=PENDING is never a decision awaiting your input --\n"
            "that only ever appears in the separate 'pending approval(s)'\n"
            "section beneath the DB-grants table, or via 'gaia approvals\n"
            "pending'. Use 'gaia approvals show APPROVAL_ID' for full detail."
        ),
    )
    p_list.add_argument("--json", action="store_true", help="JSON output")
    p_list.add_argument("--session", metavar="SESSION_ID", help="Filter by session ID")
    p_list.add_argument(
        "--orphans-only",
        action="store_true",
        dest="orphans_only",
        help="Show only pendings from sessions no longer alive (via session_registry)",
    )
    p_list.set_defaults(func=cmd_list)

    # pending (T3.1) -- shortcut for new DB pending
    p_pending = sub.add_parser(
        "pending",
        help="List pending approvals from the new approvals table",
        description=(
            "Show pending T3 approvals from the DB-backed approvals table.\n\n"
            "Default (no flags): returns ALL pending approvals on this machine\n"
            "across every session.  The DB is per-machine so all-sessions is the\n"
            "correct default scope.  This avoids a silent-drop that occurred when\n"
            "the command ran inside a subagent (whose $CLAUDE_SESSION_ID differs\n"
            "from the orchestrator session_id stored on the approval row).\n\n"
            "Use --session SESSION_ID to filter to one specific session when you\n"
            "hold a known-good orchestrator session id.\n\n"
            "--all-sessions is accepted for backwards compatibility but is\n"
            "equivalent to the default behaviour."
        ),
    )
    p_pending.add_argument("--json", action="store_true", help="JSON output")
    p_pending.add_argument(
        "--session",
        metavar="SESSION_ID",
        help=(
            "Filter to this exact session id.  Pass an orchestrator session id;\n"
            "do NOT rely on $CLAUDE_SESSION_ID inside a subagent -- it holds the\n"
            "subagent's own id, not the orchestrator's."
        ),
    )
    p_pending.add_argument(
        "--all-sessions",
        action="store_true",
        dest="all_sessions",
        help="Show pending from all sessions (default; kept for backwards compatibility)",
    )
    p_pending.set_defaults(func=cmd_pending)

    # show (T3.2) -- now checks new DB first
    p_show = sub.add_parser(
        "show",
        help="Show detail for a specific approval",
        description=(
            "Show full detail for an approval including its event chain.\n\n"
            "Requires the complete canonical approval_id and resolves it by\n"
            "exact equality. Short display labels and raw nonces are invalid."
        ),
    )
    p_show.add_argument(
        "approval_id", metavar="APPROVAL_ID",
        help="Full canonical approval_id P-<32 lowercase hex>",
    )
    p_show.add_argument("--json", action="store_true", help="JSON output")
    p_show.set_defaults(func=cmd_show_v2)

    # revoke (T3.2) -- now checks new DB first
    p_revoke = sub.add_parser(
        "revoke",
        help="Revoke a pending approval",
        description=(
            "Revoke a pending approval from the new approvals table.\n\n"
            "Inserts a REVOKED event and updates status. For legacy\n"
            "command_set grants, falls back to the old revoke path."
        ),
    )
    p_revoke.add_argument(
        "approval_id",
        metavar="APPROVAL_ID",
        help="Full approval_id (P-{uuid4hex}) of the approval to revoke",
    )
    p_revoke.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    p_revoke.set_defaults(func=cmd_revoke)

    # approve (T3.3) -- cross-session grant
    p_approve = sub.add_parser(
        "approve",
        help="Approve a pending approval (cross-session)",
        description=(
            "Approve a pending T3 approval from any session.\n\n"
            "Inserts an APPROVED event and updates status to 'approved'.\n"
            "This is the cross-session path: session S2 can approve a\n"
            "pending approval created in session S1."
        ),
    )
    p_approve.add_argument(
        "approval_id",
        metavar="APPROVAL_ID",
        help="Full approval_id (P-{uuid4hex}) of the approval to approve",
    )
    p_approve.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    p_approve.add_argument("--json", action="store_true", help="JSON output")
    p_approve.set_defaults(func=cmd_approve)

    p_request_set = sub.add_parser(
        "request-set", help="Create a governed plan-first COMMAND_SET request"
    )
    p_request_set.add_argument("--command", action="append", required=True)
    p_request_set.add_argument("--rationale")
    p_request_set.add_argument(
        "--verification",
        help="How the resulting state will be confirmed; sealed and shown verbatim",
    )
    p_request_set.add_argument(
        "--rollback",
        help="How the set is undone; sealed and shown verbatim",
    )
    p_request_set.add_argument("--agent-id")
    p_request_set.add_argument("--session-id")
    p_request_set.add_argument("--json", action="store_true")
    p_request_set.set_defaults(func=cmd_request_set)

    for name, handler, help_text in (
        ("opencode-present", cmd_opencode_present, "Record an OpenCode approval presentation"),
        ("opencode-decide", cmd_opencode_decide, "Apply an OpenCode approval reply"),
    ):
        p_opencode = sub.add_parser(name, help=help_text)
        p_opencode.add_argument("approval_id", metavar="APPROVAL_ID")
        p_opencode.add_argument("--session-id", required=True)
        p_opencode.add_argument("--call-id", required=True)
        p_opencode.add_argument("--token", required=True)
        p_opencode.add_argument("--json", action="store_true", help="JSON output")
        if name == "opencode-decide":
            p_opencode.add_argument("--reply", choices=("once", "always", "reject"), required=True)
            # A neutral lane token, never a host event name: the harness edge
            # translates its own event spelling before it reaches this CLI.
            p_opencode.add_argument(
                "--decision-lane",
                choices=("preferred", "compatibility"),
                default="preferred",
            )
        p_opencode.set_defaults(func=handler)

    # history (T3.4) -- temporal view or per-approval chain
    p_history = sub.add_parser(
        "history",
        help="Show temporal history of approvals or event chain for one approval",
        description=(
            "Without APPROVAL_ID: show the N most recent approvals across all\n"
            "sessions (any status). Use --limit to control how many.\n\n"
            "With APPROVAL_ID: show the full event chain for that approval."
        ),
    )
    p_history.add_argument(
        "approval_id",
        metavar="APPROVAL_ID",
        nargs="?",
        help="Optional P-{uuid4hex} to show events for one approval",
    )
    p_history.add_argument(
        "--limit",
        metavar="N",
        type=int,
        default=50,
        help="Maximum number of approvals to show (default: 50)",
    )
    p_history.add_argument(
        "--status",
        metavar="STATUS",
        default=None,
        help="Filter by status (pending, approved, rejected, revoked)",
    )
    p_history.add_argument("--json", action="store_true", help="JSON output")
    p_history.set_defaults(func=cmd_history)

    # replay (T3.5) -- re-run commands from an executed approval
    p_replay = sub.add_parser(
        "replay",
        help="Replay commands from an executed approval",
        description=(
            "Re-present and optionally re-execute the commands from an executed\n"
            "approval. Validates the fingerprint against the REQUESTED event before\n"
            "showing. Use --dry-run to print commands without executing."
        ),
    )
    p_replay.add_argument(
        "approval_id",
        metavar="APPROVAL_ID",
        help="P-{uuid4hex} of the approval to replay",
    )
    p_replay.add_argument("--dry-run", action="store_true", dest="dry_run", help="Preview only")
    p_replay.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    p_replay.add_argument("--json", action="store_true", help="JSON output")
    p_replay.set_defaults(func=cmd_replay)

    # reject
    p_reject = sub.add_parser(
        "reject",
        help="Reject a pending approval (or all with --all)",
        description=(
            "Reject a pending T3 approval.\n\n"
            "Single reject: provide the complete canonical APPROVAL_ID.\n"
            "Bulk reject:   use --all to reject every pending approval in one call."
        ),
    )
    p_reject.add_argument(
        "approval_id",
        metavar="APPROVAL_ID",
        nargs="?",
        help="Full canonical approval_id P-<32 lowercase hex> (omit with --all)",
    )
    p_reject.add_argument(
        "--all",
        action="store_true",
        dest="all",
        help="Reject ALL pending approvals (ignores NONCE)",
    )
    p_reject.add_argument("--reason", metavar="REASON", help="Rejection reason applied to all rejected approvals")
    p_reject.add_argument("--json", action="store_true", help="JSON output")
    p_reject.set_defaults(func=cmd_reject)

    # reject-all
    p_reject_all = sub.add_parser(
        "reject-all",
        help="Reject all active pending approvals in one pass",
        description=(
            "Mark every active (non-expired, non-rejected) pending approval as rejected.\n\n"
            "Functionally equivalent to 'reject --all' but exposed as a first-class\n"
            "subcommand matching the pending-approvals skill's documented interface.\n\n"
            "Use --dry-run to preview what would be rejected without writing changes.\n"
            "Use --workspace to operate on a different workspace's approval cache."
        ),
    )
    p_reject_all.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Preview rejections without writing changes",
    )
    p_reject_all.add_argument(
        "--workspace",
        metavar="PATH",
        default=None,
        help="Operate on a different workspace's approval cache",
    )
    p_reject_all.set_defaults(func=cmd_reject_all)

    # clean
    p_clean = sub.add_parser("clean", help="Remove expired/stale approvals")
    p_clean.add_argument("--dry-run", action="store_true", dest="dry_run",
                         help="Show what would be removed without deleting")
    p_clean.add_argument("--json", action="store_true", help="JSON output")
    p_clean.set_defaults(func=cmd_clean)

    # stats
    p_stats = sub.add_parser("stats", help="Show approval system statistics")
    p_stats.add_argument("--json", action="store_true", help="JSON output")
    p_stats.set_defaults(func=cmd_stats)

    p.set_defaults(func=_approvals_default)


def cmd_approvals(args) -> int:
    """Top-level dispatcher for 'gaia approvals'.

    Called by bin/gaia which invokes cmd_{subcommand}(args). For grouped
    subcommands like approvals, this function delegates to the specific
    handler set via set_defaults(func=...) in register().
    """
    func = getattr(args, "func", None)
    if func is not None and func is not _approvals_default:
        return func(args)
    return _approvals_default(args)


def _approvals_default(args) -> int:
    """Default handler when no sub-subcommand is given."""
    print("Usage: gaia approvals SUBCOMMAND [options]")
    print("")
    print("  pending [--all-sessions]          -- list pending approvals (new DB)")
    print("  show APPROVAL_ID                  -- full detail with event chain")
    print("  approve APPROVAL_ID               -- cross-session approve")
    print("  revoke APPROVAL_ID                -- revoke a pending approval")
    print("  history [APPROVAL_ID] [--limit N] -- temporal history or per-approval chain")
    print("  replay APPROVAL_ID [--dry-run]    -- replay an executed approval")
    print("  list [--session S] [--orphans-only]  -- list (legacy + DB grants)")
    print("  reject APPROVAL_ID [--all]        -- reject exact canonical id")
    print("  reject-all [--dry-run]            -- bulk reject (legacy)")
    print("  clean [--dry-run]                 -- remove expired approvals")
    print("  stats                             -- approval system statistics")
    print("")
    print("Run 'gaia approvals --help' for more information.")
    return 0


# ---------------------------------------------------------------------------
# Standalone shim (for development/testing without bin/gaia)
# ---------------------------------------------------------------------------

def _build_standalone_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python bin/cli/approvals.py",
        description="Gaia approvals subcommand (standalone mode)",
    )
    subparsers = parser.add_subparsers(dest="approvals_cmd", metavar="SUBCOMMAND")
    subparsers.required = True

    p_list = subparsers.add_parser("list", help="List pending approvals")
    p_list.add_argument("--json", action="store_true")
    p_list.add_argument("--session", metavar="SESSION_ID")
    p_list.add_argument(
        "--orphans-only", action="store_true", dest="orphans_only",
        help="Show only pendings from sessions no longer alive",
    )
    p_list.set_defaults(func=cmd_list)

    p_pending = subparsers.add_parser("pending", help="List pending approvals (new DB)")
    p_pending.add_argument("--json", action="store_true")
    p_pending.add_argument("--session", metavar="SESSION_ID")
    p_pending.add_argument("--all-sessions", action="store_true", dest="all_sessions")
    p_pending.set_defaults(func=cmd_pending)

    p_show = subparsers.add_parser("show", help="Show approval detail")
    p_show.add_argument("approval_id", metavar="APPROVAL_ID")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_show_v2)

    p_approve = subparsers.add_parser("approve", help="Approve a pending approval")
    p_approve.add_argument("approval_id", metavar="APPROVAL_ID")
    p_approve.add_argument("--yes", action="store_true")
    p_approve.add_argument("--json", action="store_true")
    p_approve.set_defaults(func=cmd_approve)

    p_revoke = subparsers.add_parser("revoke", help="Revoke a pending approval")
    p_revoke.add_argument("approval_id", metavar="APPROVAL_ID")
    p_revoke.add_argument("--yes", action="store_true")
    p_revoke.set_defaults(func=cmd_revoke)

    p_history = subparsers.add_parser("history", help="Show approval history")
    p_history.add_argument("approval_id", metavar="APPROVAL_ID", nargs="?")
    p_history.add_argument("--limit", metavar="N", type=int, default=50)
    p_history.add_argument("--status", metavar="STATUS", default=None)
    p_history.add_argument("--json", action="store_true")
    p_history.set_defaults(func=cmd_history)

    p_replay = subparsers.add_parser("replay", help="Replay an executed approval")
    p_replay.add_argument("approval_id", metavar="APPROVAL_ID")
    p_replay.add_argument("--dry-run", action="store_true", dest="dry_run")
    p_replay.add_argument("--yes", action="store_true")
    p_replay.add_argument("--json", action="store_true")
    p_replay.set_defaults(func=cmd_replay)

    p_reject = subparsers.add_parser("reject", help="Reject a pending approval (or all with --all)")
    p_reject.add_argument("approval_id", metavar="APPROVAL_ID", nargs="?")
    p_reject.add_argument("--all", action="store_true", dest="all", help="Reject all pending approvals")
    p_reject.add_argument("--reason", metavar="REASON")
    p_reject.add_argument("--json", action="store_true")
    p_reject.set_defaults(func=cmd_reject)

    p_reject_all = subparsers.add_parser("reject-all", help="Reject all active pending approvals")
    p_reject_all.add_argument("--dry-run", action="store_true", dest="dry_run")
    p_reject_all.add_argument("--workspace", metavar="PATH", default=None)
    p_reject_all.set_defaults(func=cmd_reject_all)

    p_clean = subparsers.add_parser("clean", help="Remove expired approvals")
    p_clean.add_argument("--dry-run", action="store_true", dest="dry_run")
    p_clean.add_argument("--json", action="store_true")
    p_clean.set_defaults(func=cmd_clean)

    p_stats = subparsers.add_parser("stats", help="Approval system stats")
    p_stats.add_argument("--json", action="store_true")
    p_stats.set_defaults(func=cmd_stats)

    return parser


if __name__ == "__main__":
    parser = _build_standalone_parser()
    parsed = parser.parse_args()
    sys.exit(parsed.func(parsed))
