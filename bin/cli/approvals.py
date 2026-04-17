"""
gaia approvals -- Approval System v2 Track 1 CLI subcommand.

Subcommands:
  list [--json] [--session SESSION_ID]   -- list pending approvals
  show APPROVAL_ID [--json]              -- show full detail of one approval
  reject NONCE [--reason REASON]         -- reject a pending approval
  reject --all [--reason REASON]         -- reject ALL pending approvals in one call
  clean [--dry-run]                      -- remove expired/stale approvals
  stats [--json]                         -- approval system statistics

All subcommands exit 0 on success, 1 on error.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Ensure hooks/ is on sys.path so approval_grants resolves correctly.
# This mirrors the pattern used in bin/gaia-scan.py.
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

def _list_all_pending() -> list:
    """Return all non-expired, non-rejected pending approvals across all sessions.

    Used when no ``--session`` filter is given.  Mirrors the scan in
    ``cmd_stats`` so that ``gaia approvals list`` shows everything a human
    reviewer would care about regardless of which session created it.

    Raises:
        Exception: propagated from _import_grants_dir() so that cmd_list
            can catch it and return exit code 1 consistently.
    """
    # Let ImportError / other failures from _import_grants_dir propagate up.
    grants_dir = _import_grants_dir()

    results = []
    now = time.time()

    try:
        for pending_file in grants_dir.glob("pending-*.json"):
            if pending_file.name.startswith("pending-index-"):
                continue
            try:
                data = json.loads(pending_file.read_text())
            except Exception:
                continue
            if data.get("status") == "rejected":
                continue
            ts = float(data.get("timestamp", 0))
            ttl = int(data.get("ttl_minutes", 1440))
            if ttl > 0 and (now - ts) / 60 > ttl:
                continue
            results.append(data)
    except Exception:
        pass

    results.sort(key=lambda d: d.get("timestamp", 0), reverse=True)
    return results


def cmd_list(args) -> int:
    """List pending approvals.

    Without ``--session``, all sessions are shown so the CLI is useful as a
    cross-session review tool.  With ``--session SESSION_ID``, only that
    session's approvals are shown.
    """
    session_id = getattr(args, "session", None)

    try:
        if session_id is None:
            # All sessions -- scan directly so we don't filter by current session.
            raw = _list_all_pending()
        else:
            ag = _import_approval_grants()
            raw = ag["get_pending_approvals_for_session"](session_id)
    except Exception as exc:
        _print_error(f"Failed to load approvals: {exc}", args)
        return 1

    items = [_pending_to_display(p) for p in raw]

    if getattr(args, "json", False):
        print(json.dumps({"pending": items, "count": len(items)}, indent=2))
        return 0

    if not items:
        print("No pending approvals.")
        return 0

    # Table output
    print(f"{'ID':<12}  {'AGE':<6}  {'VERB':<10}  {'SOURCE':<16}  COMMAND")
    print("-" * 70)
    for item in items:
        cmd_preview = item["command"][:40]
        source = item["source"][:14] if item["source"] else "-"
        print(
            f"{item['approval_id']:<12}  "
            f"{item['age']:<6}  "
            f"{item['verb']:<10}  "
            f"{source:<16}  "
            f"{cmd_preview}"
        )
    print(f"\n{len(items)} pending approval(s).")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: show
# ---------------------------------------------------------------------------

def cmd_show(args) -> int:
    """Show full details of a specific pending approval."""
    approval_id: str = args.approval_id.lstrip("P-").lstrip("p-")
    # Strip leading 'P-' prefix if present
    if approval_id.upper().startswith("P-"):
        approval_id = approval_id[2:]

    try:
        ag = _import_approval_grants()
        raw = ag["load_pending_by_nonce_prefix"](approval_id)
    except Exception as exc:
        _print_error(f"Failed to load approval: {exc}", args)
        return 1

    if raw is None:
        _print_error(f"No pending approval found for ID: P-{approval_id}", args)
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
    lines.append(f"  To reject : gaia approvals reject {approval_id}")
    print("\n".join(lines))
    return 0


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
        raw = _list_all_pending()
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
    p_list.set_defaults(func=cmd_list)

    # show
    p_show = sub.add_parser("show", help="Show detail for a specific approval")
    p_show.add_argument("approval_id", metavar="APPROVAL_ID", help="P-XXXX identifier or nonce prefix")
    p_show.add_argument("--json", action="store_true", help="JSON output")
    p_show.set_defaults(func=cmd_show)

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
    print("Usage: gaia approvals {list,show,reject,clean,stats} [options]")
    print("       gaia approvals reject --all [--reason TEXT]  # bulk reject")
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
    p_list.set_defaults(func=cmd_list)

    p_show = subparsers.add_parser("show", help="Show approval detail")
    p_show.add_argument("approval_id", metavar="APPROVAL_ID")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_show)

    p_reject = subparsers.add_parser("reject", help="Reject a pending approval (or all with --all)")
    p_reject.add_argument("nonce", metavar="NONCE", nargs="?")
    p_reject.add_argument("--all", action="store_true", dest="all", help="Reject all pending approvals")
    p_reject.add_argument("--reason", metavar="REASON")
    p_reject.add_argument("--json", action="store_true")
    p_reject.set_defaults(func=cmd_reject)

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
