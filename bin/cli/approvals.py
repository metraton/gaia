"""
gaia approvals -- Approval System v2 Track 1 CLI subcommand.

Subcommands:
  list [--json] [--session SESSION_ID] [--orphans-only]
                                         -- list pending approvals
                                            (--orphans-only filters to
                                             pendings from dead sessions)
  show APPROVAL_ID [--json]              -- show full detail of one approval
  revoke APPROVAL_ID                     -- revoke an active command_set grant by approval_id
  reject NONCE [--reason REASON]         -- reject a pending approval
  reject --all [--reason REASON]         -- reject ALL pending approvals in one call
  reject-all [--dry-run] [--workspace W] -- reject all pending approvals (subcommand alias)
  clean [--dry-run]                      -- remove expired/stale approvals
  stats [--json]                         -- approval system statistics

All subcommands exit 0 on success, 1 on error.
"""

from __future__ import annotations

import argparse
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


def _import_approval_grants():
    """Import approval_grants lazily to allow mocking in tests."""
    from modules.security.approval_grants import (
        cleanup_expired_grants,
        get_pending_approvals_for_session,
        load_pending_by_nonce_prefix,
        reject_pending,
    )
    return {
        "cleanup_expired_grants": cleanup_expired_grants,
        "get_pending_approvals_for_session": get_pending_approvals_for_session,
        "load_pending_by_nonce_prefix": load_pending_by_nonce_prefix,
        "reject_pending": reject_pending,
    }


def _import_grants_dir():
    """Get the grants directory path for approval files.

    Resolution order mirrors get_plugin_data_dir() in paths.py:
    1. CLAUDE_PLUGIN_DATA env var (set by Claude Code at runtime) -- data
       lives at <CLAUDE_PLUGIN_DATA>/cache/approvals/.
    2. Delegate to the approval_grants module which calls get_plugin_data_dir(),
       which in turn walks up from CWD to find .claude/.

    Keeping CLAUDE_PLUGIN_DATA as the first check ensures the CLI finds the
    same approvals directory the hooks use when invoked from any working
    directory (e.g. from inside gaia-ops-dev/ during development).
    """
    import os
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        return Path(plugin_data) / "cache" / "approvals"
    from modules.security.approval_grants import _get_grants_dir
    return _get_grants_dir()


def _import_approval_grants_module():
    """Return the approval_grants module object for direct attribute access.

    Separate from _import_approval_grants() so cmd_clean can reset
    _last_cleanup_time and call cleanup_expired_grants atomically on the
    same module reference.  Kept as a separate injectable function so tests
    can mock it without touching sys.modules.
    """
    import modules.security.approval_grants as ag_mod
    return ag_mod


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


# ---------------------------------------------------------------------------
# Subcommand: list
# ---------------------------------------------------------------------------

def _scan_pending_shared(exclude_live_sessions: bool = False) -> list:
    """Return all non-expired, non-rejected pending approvals across all sessions.

    Thin wrapper around the shared ``scan_pending_approvals`` in
    ``modules.session.pending_scanner`` so CLI and hook consumers share one
    implementation of pending discovery + liveness filtering.

    When ``exclude_live_sessions=True``, only pendings whose owning session
    is NOT currently alive (orphans) are returned — this backs the
    ``--orphans-only`` flag.

    Raises:
        Exception: propagated from ``_import_grants_dir()`` so ``cmd_list``
            can catch it and return exit code 1 consistently.
    """
    # Let ImportError / other failures from _import_grants_dir propagate up.
    grants_dir = _import_grants_dir()

    from modules.session.pending_scanner import scan_pending_approvals

    scanned = scan_pending_approvals(
        grants_dir, exclude_live_sessions=exclude_live_sessions
    )

    # scan_pending_approvals returns a display-ish shape; we rehydrate each
    # scanned result back into the on-disk pending dict keys that
    # _pending_to_display expects. Single source of truth for the scan,
    # but the CLI's display contract is preserved unchanged.
    results = []
    for s in scanned:
        results.append({
            "nonce": s.get("nonce_full") or s.get("nonce_short", ""),
            "session_id": s.get("pending_session_id", ""),
            "command": s.get("command", ""),
            "danger_verb": s.get("verb", ""),
            "danger_category": s.get("category", ""),
            "scope_type": s.get("scope_type", ""),
            "timestamp": s.get("timestamp", 0),
            "context": s.get("context", {}),
        })

    results.sort(key=lambda d: d.get("timestamp", 0), reverse=True)
    return results


def _grant_to_display(g: dict) -> dict:
    """Convert a DB approval_grants row to a display-friendly dict."""
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

    return {
        "approval_id": approval_id,
        "status": g.get("status", ""),
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
    }


def cmd_list(args) -> int:
    """List approval grants from the DB.

    Without ``--session``, all grants are shown.  With ``--session SESSION_ID``,
    only that session's grants are shown.

    Also supports ``--orphans-only`` (filesystem pending approvals from dead
    sessions) via the legacy scan path for backward compatibility.
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

    # Legacy filesystem pending listing (for filesystem-based pending approvals)
    fs_pending = []
    if not orphans_only:
        try:
            if session_id is None:
                fs_pending = _scan_pending_shared(exclude_live_sessions=False)
            else:
                ag = _import_approval_grants()
                fs_pending = ag["get_pending_approvals_for_session"](session_id)
        except Exception:
            pass
    else:
        try:
            fs_pending = _scan_pending_shared(exclude_live_sessions=True)
        except Exception:
            pass

    db_items = [_grant_to_display(g) for g in db_grants]
    fs_items = [_pending_to_display(p) for p in fs_pending]

    if getattr(args, "json", False):
        print(json.dumps({
            "grants": db_items,
            "pending_fs": fs_items,
            "count": len(db_items) + len(fs_items),
        }, indent=2))
        return 0

    if not db_items and not fs_items:
        print("No active grants or pending approvals.")
        return 0

    if db_items:
        print(f"\n{'APPROVAL_ID':<34}  {'STATUS':<10}  {'AGE':<6}  {'CMD_COUNT':<10}  FIRST_COMMAND")
        print("-" * 80)
        for item in db_items:
            cmd_preview = item["first_command"][:30]
            print(
                f"{item['approval_id']:<34}  "
                f"{item['status']:<10}  "
                f"{item['age']:<6}  "
                f"{str(item['command_count']):<10}  "
                f"{cmd_preview}"
            )
        print(f"\n{len(db_items)} DB grant(s).")

    if fs_items:
        print(f"\n{'ID':<12}  {'AGE':<6}  {'VERB':<10}  {'SOURCE':<16}  COMMAND")
        print("-" * 70)
        for item in fs_items:
            cmd_preview = item["command"][:40]
            source = item["source"][:14] if item["source"] else "-"
            print(
                f"{item['approval_id']:<12}  "
                f"{item['age']:<6}  "
                f"{item['verb']:<10}  "
                f"{source:<16}  "
                f"{cmd_preview}"
            )
        print(f"\n{len(fs_items)} filesystem pending approval(s).")

    return 0


# ---------------------------------------------------------------------------
# Subcommand: show
# ---------------------------------------------------------------------------

def cmd_show(args) -> int:
    """Show full details of a specific approval grant or pending approval.

    Checks the DB first (COMMAND_SET grants by full approval_id), then falls
    back to the filesystem scan (pending approvals by nonce prefix).
    """
    raw_id: str = args.approval_id.strip()
    # Strip leading 'P-' prefix if present
    if raw_id.upper().startswith("P-"):
        raw_id = raw_id[2:]

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
            print(json.dumps(db_row, indent=2))
            return 0
        lines = [
            f"Grant {item['approval_id']}",
            "",
            f"  Status    : {item['status']}",
            f"  Scope     : {item['scope']}",
            f"  Age       : {item['age']}",
            f"  Session   : {item['session_id']}",
            f"  Agent     : {item['agent_id']}",
            f"  Created   : {item['created_at']}",
            f"  Expires   : {item['expires_at']}",
            f"  Commands  : {item['command_count']}",
        ]
        for i, cmd_item in enumerate(item["command_set"]):
            lines.append(f"  [{i}] {cmd_item.get('command', '')}")
            if cmd_item.get("rationale"):
                lines.append(f"      rationale: {cmd_item['rationale']}")
        lines.append("")
        lines.append(f"  To revoke : gaia approvals revoke {item['approval_id']}")
        print("\n".join(lines))
        return 0

    # 2. Fall back to filesystem pending lookup by nonce prefix
    try:
        ag = _import_approval_grants()
        raw = ag["load_pending_by_nonce_prefix"](raw_id)
    except Exception as exc:
        _print_error(f"Failed to load approval: {exc}", args)
        return 1

    if raw is None:
        _print_error(f"No approval found for ID: {raw_id}", args)
        return 1

    item = _pending_to_display(raw)
    env = raw.get("environment") or {}
    cwd = raw.get("cwd", "")

    if getattr(args, "json", False):
        detail = dict(item)
        detail["environment"] = env
        detail["cwd"] = cwd
        print(json.dumps(detail, indent=2))
        return 0

    # Human-readable detail
    lines = [
        f"Approval {item['approval_id']}",
        "",
        f"  Command   : {item['command']}",
        f"  Verb      : {item['verb']} ({item['category']})",
        f"  Age       : {item['age']}",
        f"  Session   : {item['session_id']}",
        f"  Scope type: {item['scope_type']}",
    ]
    if item["source"]:
        lines.append(f"  Source    : {item['source']}")
    if item["description"] and item["description"] != item["command"]:
        lines.append(f"  Desc      : {item['description']}")
    if item["risk"]:
        lines.append(f"  Risk      : {item['risk']}")
    if item["rollback"]:
        lines.append(f"  Rollback  : {item['rollback']}")
    if item["branch"]:
        lines.append(f"  Branch    : {item['branch']}")
    if item["files_changed"]:
        lines.append(f"  Files     : {', '.join(item['files_changed'])}")
    if cwd:
        lines.append(f"  CWD       : {cwd}")
    if env:
        lines.append(f"  Env keys  : {', '.join(sorted(env.keys()))}")
    lines.append("")
    lines.append(f"  To reject : gaia approvals reject {raw_id}")
    print("\n".join(lines))
    return 0


# ---------------------------------------------------------------------------
# Subcommand: revoke
# ---------------------------------------------------------------------------

def _revoke_grant(args) -> int:
    """Revoke an active command_set grant by its approval_id (legacy path).

    Calls ``writer.revoke_approval_grant(approval_id)`` to mark the grant
    REVOKED in the DB.  After revocation, any unconsumed commands in the
    command_set will require fresh approval.

    This is the legacy ``approval_grants``-table path. It is invoked as the
    fallback by the unified :func:`cmd_revoke` when an id is not found in the
    new ``approvals`` table.

    Exits 0 on success, 1 if the grant is not found or already in a terminal
    state.
    """
    approval_id: str = args.approval_id.strip()

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

def cmd_reject(args) -> int:
    """Reject a pending approval by nonce prefix, or all pending approvals.

    With ``--all``: rejects every non-expired pending approval across all
    sessions.  Exits 0 whether or not any approvals existed.

    Without ``--all``: rejects the single approval identified by NONCE
    (P-XXXX label or raw hex prefix).  Exits 1 when not found.
    """
    reject_all = getattr(args, "all", False)
    reason = getattr(args, "reason", None)

    if reject_all:
        return _cmd_reject_all(args, reason)

    # Single-reject path (original behavior)
    nonce = getattr(args, "nonce", None)
    if nonce is None:
        _print_error("NONCE is required when --all is not specified.", args)
        return 1

    nonce = nonce.strip()
    # Accept P-XXXX or raw hex prefix
    if nonce.upper().startswith("P-"):
        nonce = nonce[2:]

    try:
        ag = _import_approval_grants()
        ok = ag["reject_pending"](nonce)
    except Exception as exc:
        _print_error(f"Failed to reject approval: {exc}", args)
        return 1

    if ok:
        msg = f"Rejected P-{nonce}"
        if reason:
            msg += f" (reason: {reason})"
        if getattr(args, "json", False):
            print(json.dumps({"status": "rejected", "nonce_prefix": nonce, "reason": reason}))
        else:
            print(msg)
        return 0
    else:
        _print_error(f"No pending approval found for P-{nonce}", args)
        return 1


def _cmd_reject_all(args, reason: str | None) -> int:
    """Reject all pending approvals across all sessions.

    Scans the same queue that ``gaia approvals list`` shows, then calls
    ``reject_pending`` for each non-expired, non-rejected pending approval.
    Exits 0 always -- an empty queue is not an error.
    """
    try:
        # Bulk reject operates on the full queue regardless of liveness;
        # we intentionally pass exclude_live_sessions=False so the operator
        # can clear orphaned and live-session pendings in one call.
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

    try:
        ag = _import_approval_grants()
        reject_fn = ag["reject_pending"]
    except Exception as exc:
        _print_error(f"Failed to load approval module: {exc}", args)
        return 1

    rejected_ids = []
    failed_ids = []
    for pending in raw:
        nonce = pending.get("nonce", "")
        nonce_prefix = _nonce_short(nonce)
        try:
            ok = reject_fn(nonce_prefix)
            if ok:
                rejected_ids.append(f"P-{nonce_prefix}")
            else:
                failed_ids.append(f"P-{nonce_prefix}")
        except Exception:
            failed_ids.append(f"P-{nonce_prefix}")

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

    Scans the approval cache for every non-expired, non-rejected pending
    approval and calls ``reject_pending()`` on each nonce.  This is the
    canonical subcommand surface documented in the pending-approvals skill.

    Flags:
      --dry-run     Preview what would be rejected without writing changes.
      --workspace   Operate on a different workspace's approval cache.
    """
    dry_run: bool = getattr(args, "dry_run", False)
    workspace: str | None = getattr(args, "workspace", None)

    # Resolve grants directory, honoring --workspace if provided.
    try:
        grants_dir = _grants_dir_for_workspace(workspace)
    except Exception as exc:
        _print_error(f"Cannot resolve approvals directory: {exc}", args)
        return 1

    # Scan pending approvals using the resolved directory.
    try:
        from modules.session.pending_scanner import scan_pending_approvals
        scanned = scan_pending_approvals(grants_dir, exclude_live_sessions=False)
        raw: list = []
        for s in scanned:
            raw.append({
                "nonce": s.get("nonce_full") or s.get("nonce_short", ""),
                "command": s.get("command", ""),
            })
    except Exception as exc:
        _print_error(f"Failed to load approvals: {exc}", args)
        return 1

    if not raw:
        print("No active pendings -- nothing to reject.")
        return 0

    if dry_run:
        print("[dry-run] would reject:")
        for item in raw:
            nonce_prefix = _nonce_short(item["nonce"])
            cmd_preview = item["command"][:60]
            print(f"  P-{nonce_prefix}  {cmd_preview}")
        print(f"\n{len(raw)} pending(s) would be rejected.")
        return 0

    # Live rejection via the canonical reject_pending() path.
    try:
        ag = _import_approval_grants()
        reject_fn = ag["reject_pending"]
    except Exception as exc:
        _print_error(f"Failed to load approval module: {exc}", args)
        return 1

    rejected_ids = []
    failed_ids = []
    for item in raw:
        nonce = item["nonce"]
        nonce_prefix = _nonce_short(nonce)
        try:
            ok = reject_fn(nonce_prefix)
            if ok:
                rejected_ids.append(f"P-{nonce_prefix}")
            else:
                failed_ids.append(f"P-{nonce_prefix}")
        except Exception:
            failed_ids.append(f"P-{nonce_prefix}")

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
    """Remove expired and stale approvals."""
    dry_run = getattr(args, "dry_run", False)

    if dry_run:
        # Inspect without deleting -- count files that would be removed
        try:
            grants_dir = _import_grants_dir()
        except Exception as exc:
            _print_error(f"Cannot access approvals directory: {exc}", args)
            return 1

        if not grants_dir.exists():
            msg = "Approvals directory does not exist. Nothing to clean."
            if getattr(args, "json", False):
                print(json.dumps({"dry_run": True, "would_remove": 0, "message": msg}))
            else:
                print(msg)
            return 0

        would_remove = _count_stale_files(grants_dir)
        if getattr(args, "json", False):
            print(json.dumps({"dry_run": True, "would_remove": would_remove}))
        else:
            print(f"Dry run: {would_remove} expired/stale file(s) would be removed.")
        return 0

    # Real cleanup -- reset throttle to force run
    try:
        ag_mod = _import_approval_grants_module()
        ag_mod._last_cleanup_time = 0.0
        cleaned = ag_mod.cleanup_expired_grants()
    except Exception as exc:
        _print_error(f"Cleanup failed: {exc}", args)
        return 1

    if getattr(args, "json", False):
        print(json.dumps({"status": "ok", "cleaned": cleaned}))
    else:
        print(f"Cleaned {cleaned} expired/stale approval file(s).")
    return 0


def _count_stale_files(grants_dir: Path) -> int:
    """Count expired grant and pending files without deleting them."""
    count = 0
    now = time.time()

    for f in grants_dir.glob("grant-*.json"):
        try:
            data = json.loads(f.read_text())
            granted_at = float(data.get("granted_at", 0))
            ttl = int(data.get("ttl_minutes", 5))
            if ttl > 0 and (now - granted_at) / 60 > ttl:
                count += 1
        except Exception:
            count += 1

    for f in grants_dir.glob("pending-*.json"):
        if "index" in f.name:
            continue
        try:
            data = json.loads(f.read_text())
            if data.get("status") == "rejected":
                count += 1
                continue
            ts = float(data.get("timestamp", 0))
            ttl = int(data.get("ttl_minutes", 5))
            if ttl > 0 and (now - ts) / 60 > ttl:
                count += 1
        except Exception:
            count += 1

    return count


# ---------------------------------------------------------------------------
# Subcommand: stats
# ---------------------------------------------------------------------------

def cmd_stats(args) -> int:
    """Show approval system statistics."""
    try:
        ag = _import_approval_grants()
        grants_dir = _import_grants_dir()
    except Exception as exc:
        _print_error(f"Failed to access approval system: {exc}", args)
        return 1

    # Gather data
    all_sessions_pending = []
    active_grants = []
    rejected_count = 0
    expired_pending_count = 0
    now = time.time()

    if grants_dir.exists():
        for f in grants_dir.glob("pending-*.json"):
            if "index" in f.name:
                continue
            try:
                data = json.loads(f.read_text())
                if data.get("status") == "rejected":
                    rejected_count += 1
                    continue
                ts = float(data.get("timestamp", 0))
                ttl = int(data.get("ttl_minutes", 5))
                if ttl > 0 and (now - ts) / 60 > ttl:
                    expired_pending_count += 1
                    continue
                all_sessions_pending.append(data)
            except Exception:
                pass

        for f in grants_dir.glob("grant-*.json"):
            try:
                data = json.loads(f.read_text())
                granted_at = float(data.get("granted_at", 0))
                ttl = int(data.get("ttl_minutes", 5))
                if ttl == 0 or (now - granted_at) / 60 <= ttl:
                    active_grants.append(data)
            except Exception:
                pass

    # Current session pending
    session_pending = ag["get_pending_approvals_for_session"]()

    # Verb breakdown
    verb_counts: dict = {}
    for p in all_sessions_pending:
        verb = p.get("danger_verb", "unknown")
        verb_counts[verb] = verb_counts.get(verb, 0) + 1

    stats = {
        "pending_current_session": len(session_pending),
        "pending_all_sessions": len(all_sessions_pending),
        "active_grants": len(active_grants),
        "rejected": rejected_count,
        "expired_pending": expired_pending_count,
        "verb_breakdown": verb_counts,
    }

    if getattr(args, "json", False):
        print(json.dumps(stats, indent=2))
        return 0

    print("Approval System Stats")
    print("---------------------")
    print(f"  Pending (this session) : {stats['pending_current_session']}")
    print(f"  Pending (all sessions) : {stats['pending_all_sessions']}")
    print(f"  Active grants          : {stats['active_grants']}")
    print(f"  Rejected (pending)     : {stats['rejected']}")
    print(f"  Expired (pending)      : {stats['expired_pending']}")
    if verb_counts:
        print("  Verb breakdown:")
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


def _import_approval_revert():
    """Import gaia.approvals.revert lazily."""
    from gaia.approvals import revert as revert_mod
    return revert_mod


# ---------------------------------------------------------------------------
# T3.1: gaia approvals pending -- shortcut for list --status=pending
# ---------------------------------------------------------------------------

def cmd_pending(args) -> int:
    """Show pending approvals from the new approvals table.

    With ``--all-sessions``, returns pending approvals from all sessions
    (cross-session recovery, D9). Without it, returns pending for the
    current session when SESSION_ID env var is set, or all sessions.

    Exits 0 on success, 1 on error.
    """
    all_sessions = getattr(args, "all_sessions", False)
    session_id = getattr(args, "session", None)
    output_json = getattr(args, "json", False)

    # If no session_id provided, default to all_sessions behavior.
    if session_id is None and not all_sessions:
        # Try to detect session from environment (mirrors hook behavior).
        session_id = os.environ.get("CLAUDE_SESSION_ID") or None

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

    The input is passed through unchanged otherwise -- both the full
    ``P-{uuid4hex}`` form and the ``P-XXXX`` short form are returned as-is for
    exact or prefix lookup downstream. (A bare hex string with no ``P-`` prefix
    is also returned untouched; the lookup layer handles that case.)
    """
    return raw_id.strip()


def cmd_show_v2(args) -> int:
    """Show full detail for an approval from the new approvals table.

    Looks up by full P-{uuid4} id or by prefix match. Falls back to the
    old filesystem-based show (cmd_show) when not found in the new table.

    Exits 0 on success, 1 when not found.
    """
    raw_id = _resolve_approval_id(args.approval_id)
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

    if output_json:
        print(json.dumps({"approval": approval, "events": events}, indent=2, default=str))
        return 0

    display = _import_approval_display()
    display.print_approval_detail(approval, events)
    return 0


def cmd_history_single(args) -> int:
    """Show the event chain for a single approval (by id).

    Exits 0 on success, 1 when not found.
    """
    raw_id = _resolve_approval_id(args.approval_id)
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
    If the id is not present in the new table, falls back to the legacy
    command_set grant path (:func:`_revoke_grant`).

    With ``--yes``, skips the interactive confirmation prompt.
    Exits 0 on success, 1 on error.
    """
    raw_id = _resolve_approval_id(args.approval_id)
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
        _print_error(
            f"Cannot revoke approval {raw_id}: status is {current_status!r} (must be 'pending')",
            args,
        )
        return 1

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
    raw_id = _resolve_approval_id(args.approval_id)
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
# T3.5: gaia approvals revert <id> -- interactive inverse-command UX (D14)
# ---------------------------------------------------------------------------

def cmd_revert(args) -> int:
    """Revert an approval by executing inverse commands for its EXECUTED events.

    Per D14:
    - Interactive by default: shows numbered list of candidate inverse commands,
      user selects by number, comma-separated, 'all', or 'none'.
    - ``--yes`` suppresses per-event confirmation.
    - ``--file ids.txt`` reads event_ids from a file (one per line) for batch mode.
    - ``--dry-run`` shows the inverse commands without executing them.

    Exits 0 on success or when no reversible events exist.
    Exits 1 on error.
    """
    raw_id = _resolve_approval_id(args.approval_id)
    skip_confirm = getattr(args, "yes", False)
    dry_run = getattr(args, "dry_run", False)
    batch_file = getattr(args, "file", None)
    output_json = getattr(args, "json", False)

    # Resolve the approval and its EXECUTED events.
    try:
        store = _import_approval_store()
        approval = store.get_by_id(raw_id)
        if approval is None:
            _print_error(f"No approval found for id: {raw_id}", args)
            return 1
    except Exception as exc:
        _print_error(f"Failed to look up approval: {exc}", args)
        return 1

    # Derive inverse commands.
    try:
        revert_mod = _import_approval_revert()
        store = _import_approval_store()
        con = store._open_db()
        try:
            inverses = revert_mod.derive_inverses_for_approval(raw_id, con)
        finally:
            con.close()
    except Exception as exc:
        _print_error(f"Failed to derive inverse commands: {exc}", args)
        return 1

    if not inverses:
        print(f"No EXECUTED events found for approval {raw_id}. Nothing to revert.")
        return 0

    # Display the candidate inverse commands.
    print(f"\nCandidate inverse commands for approval {raw_id}:")
    print("-" * 60)
    for i, ic in enumerate(inverses):
        reversible_marker = "" if ic.reversible else " [NOT REVERSIBLE]"
        inverse_display = ic.inverse_command if ic.inverse_command else "N/A"
        print(f"  [{i}] Original : {ic.original_command}")
        print(f"      Inverse  : {inverse_display}{reversible_marker}")
        print(f"      Notes    : {ic.notes}")
        print()

    # Filter to only reversible ones.
    reversible = [ic for ic in inverses if ic.reversible and ic.inverse_command]
    if not reversible:
        print("None of the events have derivable inverse commands.")
        return 0

    if dry_run:
        print("[dry-run] Would execute the following inverse commands:")
        for ic in reversible:
            print(f"  {ic.inverse_command}")
        return 0

    # Batch file mode: read event_ids to revert.
    selected = reversible
    if batch_file:
        try:
            with open(batch_file) as fh:
                event_ids_str = {line.strip() for line in fh if line.strip()}
        except OSError as exc:
            _print_error(f"Cannot read batch file {batch_file!r}: {exc}", args)
            return 1
        event_ids = set()
        for eid in event_ids_str:
            try:
                event_ids.add(int(eid))
            except ValueError:
                pass
        selected = [ic for ic in reversible if ic.event_id in event_ids]
        if not selected:
            print(f"No matching reversible events found in batch file.")
            return 0
    elif not skip_confirm:
        # Interactive selection.
        print("Select events to revert (comma-separated numbers, 'all', or 'none'):")
        try:
            choice = input("> ").strip().lower()
        except EOFError:
            choice = "none"

        if choice == "none" or choice == "":
            print("Revert cancelled.")
            return 0
        elif choice == "all":
            selected = reversible
        else:
            try:
                indices = [int(x.strip()) for x in choice.split(",") if x.strip()]
                selected = [reversible[i] for i in indices if 0 <= i < len(reversible)]
            except (ValueError, IndexError):
                _print_error("Invalid selection. Use numbers, 'all', or 'none'.", args)
                return 1

    if not selected:
        print("No events selected. Revert cancelled.")
        return 0

    # Final confirmation.
    if not skip_confirm:
        print("\nWill execute:")
        for ic in selected:
            print(f"  {ic.inverse_command}")
        try:
            confirm = input("\nProceed? [y/N] ").strip().lower()
        except EOFError:
            confirm = "n"
        if confirm not in ("y", "yes"):
            print("Revert cancelled.")
            return 0

    # Execute inverse commands.
    import subprocess
    results = []
    session_id = os.environ.get("CLAUDE_SESSION_ID") or "cli-session"
    all_ok = True

    for ic in selected:
        print(f"Executing: {ic.inverse_command}")
        try:
            proc = subprocess.run(
                ic.inverse_command,
                shell=True,
                capture_output=True,
                text=True,
            )
            ok = proc.returncode == 0
            results.append({
                "event_id": ic.event_id,
                "command": ic.inverse_command,
                "exit_code": proc.returncode,
                "stdout": proc.stdout.strip(),
                "stderr": proc.stderr.strip(),
            })
            if ok:
                print(f"  OK (exit 0)")
                # Record REVERTED event in the chain.
                try:
                    store = _import_approval_store()
                    import json as _json
                    metadata = _json.dumps({
                        "original_event_id": ic.event_id,
                        "inverse_command": ic.inverse_command,
                    })
                    store.record_event(
                        raw_id,
                        "REVERTED",
                        session_id=session_id,
                        metadata_json=metadata,
                    )
                except Exception:
                    pass  # Chain write failure is non-fatal for the revert operation.
            else:
                print(f"  FAILED (exit {proc.returncode})")
                if proc.stderr:
                    print(f"  stderr: {proc.stderr.strip()}")
                all_ok = False
        except Exception as exc:
            print(f"  ERROR: {exc}")
            results.append({
                "event_id": ic.event_id,
                "command": ic.inverse_command,
                "exit_code": -1,
                "error": str(exc),
            })
            all_ok = False

    if output_json:
        print(json.dumps({"results": results, "all_ok": all_ok}))

    return 0 if all_ok else 1


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
    raw_id = _resolve_approval_id(args.approval_id)
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
        # Non-fatal for replay -- warn but continue.
        print(f"Warning: fingerprint validation failed: {exc}", file=sys.stderr)

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

    # Execute the commands sequentially.
    import subprocess
    all_ok = True
    for cmd in commands:
        print(f"Executing: {cmd}")
        try:
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if proc.returncode == 0:
                print(f"  OK")
            else:
                print(f"  FAILED (exit {proc.returncode})")
                if proc.stderr:
                    print(f"  stderr: {proc.stderr.strip()}")
                all_ok = False
        except Exception as exc:
            print(f"  ERROR: {exc}")
            all_ok = False

    return 0 if all_ok else 1


# ---------------------------------------------------------------------------
# Plugin registration (called by bin/gaia dispatcher)
# ---------------------------------------------------------------------------

def register(subparsers) -> None:
    """Register the 'approvals' subcommand group with the root parser."""
    p = subparsers.add_parser(
        "approvals",
        help="Manage T3 pending approvals",
        description="View, approve, reject, revert, and replay Gaia approval requests.",
    )
    sub = p.add_subparsers(dest="approvals_cmd", metavar="SUBCOMMAND")
    sub.required = True

    # list (legacy + new DB path via pending)
    p_list = sub.add_parser("list", help="List pending approvals (legacy + DB)")
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
            "Use --all-sessions to see pending approvals from all sessions\n"
            "(cross-session recovery -- local machine only)."
        ),
    )
    p_pending.add_argument("--json", action="store_true", help="JSON output")
    p_pending.add_argument("--session", metavar="SESSION_ID", help="Filter by session ID")
    p_pending.add_argument(
        "--all-sessions",
        action="store_true",
        dest="all_sessions",
        help="Show pending from all sessions (cross-session recovery)",
    )
    p_pending.set_defaults(func=cmd_pending)

    # show (T3.2) -- now checks new DB first
    p_show = sub.add_parser(
        "show",
        help="Show detail for a specific approval",
        description=(
            "Show full detail for an approval including its event chain.\n\n"
            "Checks the new approvals table first, then falls back to the\n"
            "legacy filesystem-based pending lookup."
        ),
    )
    p_show.add_argument("approval_id", metavar="APPROVAL_ID", help="P-XXXX identifier or full DB approval_id")
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

    # revert (T3.5) -- interactive inverse-command UX
    p_revert = sub.add_parser(
        "revert",
        help="Revert an approval by executing inverse commands (interactive)",
        description=(
            "Per D14: interactive inverse-command UX for reverting executed approvals.\n\n"
            "Shows candidate inverse commands, prompts for selection, then executes\n"
            "the selected inverses sequentially. Uses --yes to skip per-event prompts.\n"
            "Use --dry-run to preview without executing."
        ),
    )
    p_revert.add_argument(
        "approval_id",
        metavar="APPROVAL_ID",
        help="P-{uuid4hex} of the approval to revert",
    )
    p_revert.add_argument("--yes", action="store_true", help="Skip confirmation prompts")
    p_revert.add_argument("--dry-run", action="store_true", dest="dry_run", help="Preview only")
    p_revert.add_argument(
        "--file",
        metavar="PATH",
        default=None,
        help="File of event_ids to revert (one per line) for batch mode",
    )
    p_revert.add_argument("--json", action="store_true", help="JSON output for results")
    p_revert.set_defaults(func=cmd_revert)

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
            "Single reject: provide NONCE (P-XXXX or raw hex prefix).\n"
            "Bulk reject:   use --all to reject every pending approval in one call."
        ),
    )
    p_reject.add_argument(
        "nonce",
        metavar="NONCE",
        nargs="?",
        help="P-XXXX identifier or nonce prefix (omit when using --all)",
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
    print("  revert APPROVAL_ID [--dry-run]    -- interactive inverse-command revert")
    print("  replay APPROVAL_ID [--dry-run]    -- replay an executed approval")
    print("  list [--session S] [--orphans-only]  -- list (legacy + DB grants)")
    print("  reject NONCE [--all]              -- reject pending (legacy)")
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

    p_revert = subparsers.add_parser("revert", help="Revert an approval (interactive)")
    p_revert.add_argument("approval_id", metavar="APPROVAL_ID")
    p_revert.add_argument("--yes", action="store_true")
    p_revert.add_argument("--dry-run", action="store_true", dest="dry_run")
    p_revert.add_argument("--file", metavar="PATH", default=None)
    p_revert.add_argument("--json", action="store_true")
    p_revert.set_defaults(func=cmd_revert)

    p_replay = subparsers.add_parser("replay", help="Replay an executed approval")
    p_replay.add_argument("approval_id", metavar="APPROVAL_ID")
    p_replay.add_argument("--dry-run", action="store_true", dest="dry_run")
    p_replay.add_argument("--yes", action="store_true")
    p_replay.add_argument("--json", action="store_true")
    p_replay.set_defaults(func=cmd_replay)

    p_reject = subparsers.add_parser("reject", help="Reject a pending approval (or all with --all)")
    p_reject.add_argument("nonce", metavar="NONCE", nargs="?")
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
