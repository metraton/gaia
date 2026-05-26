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

    first_cmd = command_set[0].get("command", "") if command_set else ""

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
        "command_count": len(command_set),
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

def cmd_revoke(args) -> int:
    """Revoke an active command_set grant by its approval_id.

    Calls ``writer.revoke_approval_grant(approval_id)`` to mark the grant
    REVOKED in the DB.  After revocation, any unconsumed commands in the
    command_set will require fresh approval.

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
# Plugin registration (called by bin/gaia dispatcher)
# ---------------------------------------------------------------------------

def register(subparsers) -> None:
    """Register the 'approvals' subcommand group with the root parser."""
    p = subparsers.add_parser(
        "approvals",
        help="Manage T3 pending approvals",
        description="View, reject, and clean up Gaia approval requests.",
    )
    sub = p.add_subparsers(dest="approvals_cmd", metavar="SUBCOMMAND")
    sub.required = True

    # list
    p_list = sub.add_parser("list", help="List pending approvals")
    p_list.add_argument("--json", action="store_true", help="JSON output")
    p_list.add_argument("--session", metavar="SESSION_ID", help="Filter by session ID")
    p_list.add_argument(
        "--orphans-only",
        action="store_true",
        dest="orphans_only",
        help="Show only pendings from sessions no longer alive (via session_registry)",
    )
    p_list.set_defaults(func=cmd_list)

    # show
    p_show = sub.add_parser("show", help="Show detail for a specific approval")
    p_show.add_argument("approval_id", metavar="APPROVAL_ID", help="P-XXXX identifier, nonce prefix, or DB approval_id")
    p_show.add_argument("--json", action="store_true", help="JSON output")
    p_show.set_defaults(func=cmd_show)

    # revoke
    p_revoke = sub.add_parser(
        "revoke",
        help="Revoke an active command_set grant by approval_id",
        description=(
            "Revoke a COMMAND_SET grant identified by its approval_id.\n\n"
            "After revocation, any unconsumed commands in the command_set will\n"
            "require fresh approval from the user."
        ),
    )
    p_revoke.add_argument(
        "approval_id",
        metavar="APPROVAL_ID",
        help="Full approval_id (32-char hex) of the grant to revoke",
    )
    p_revoke.set_defaults(func=cmd_revoke)

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
    print("Usage: gaia approvals {list,show,revoke,reject,reject-all,clean,stats} [options]")
    print("       gaia approvals revoke APPROVAL_ID              # revoke a command_set grant")
    print("       gaia approvals reject --all [--reason TEXT]   # bulk reject (flag form)")
    print("       gaia approvals reject-all [--dry-run] [--workspace PATH]  # bulk reject (subcommand)")
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

    p_show = subparsers.add_parser("show", help="Show approval detail")
    p_show.add_argument("approval_id", metavar="APPROVAL_ID")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_show)

    p_revoke = subparsers.add_parser("revoke", help="Revoke an active command_set grant")
    p_revoke.add_argument("approval_id", metavar="APPROVAL_ID")
    p_revoke.set_defaults(func=cmd_revoke)

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
