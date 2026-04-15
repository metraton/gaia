"""
gaia cleanup -- mirror of gaia-cleanup.js

Modes:
  --prune / --retain  Apply data retention policy only (no symlink/settings removal)
  (default)           Remove CLAUDE.md, settings.json, symlinks + run retention

Flags:
  --dry-run           Print what would be pruned/removed without modifying files
  --json              Machine-readable output
"""

import json
import os
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Project root detection (mirrors JS findProjectRoot)
# ---------------------------------------------------------------------------

def _find_project_root() -> Path:
    """Walk upward from cwd until .claude/ is found."""
    start = Path(os.environ.get("INIT_CWD", "")) if os.environ.get("INIT_CWD") else None
    if start and (start / ".claude").exists():
        return start

    current = Path.cwd()
    while True:
        if (current / ".claude").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent

    return Path(os.environ.get("INIT_CWD", str(Path.cwd())))


# ---------------------------------------------------------------------------
# Retention policy (mirrors RETENTION_POLICY in gaia-cleanup.js)
# ---------------------------------------------------------------------------

RETENTION_POLICY = [
    {
        "key": "auditLogs",
        "type": "files",
        "pattern": "audit-*.jsonl",
        "dir": ".claude/logs",
        "max_days": 30,
        "label": "Audit logs",
    },
    {
        "key": "hookLogs",
        "type": "files",
        "pattern": "hooks-*.log",
        "dir": ".claude/logs",
        "max_days": 14,
        "label": "Hook logs",
    },
    {
        "key": "monthlyMetrics",
        "type": "files",
        "pattern": "metrics-*.jsonl",
        "dir": ".claude/metrics",
        "max_days": 90,
        "label": "Monthly metrics",
    },
    {
        "key": "responseContract",
        "type": "dirs",
        "dir": ".claude/session/active/response-contract",
        "max_days": 7,
        "label": "Response contract sessions",
    },
    {
        "key": "episodicEpisodes",
        "type": "files",
        "pattern": "*.json",
        "dir": ".claude/project-context/episodic-memory/episodes",
        "max_days": 90,
        "label": "Episodic memory episodes",
    },
    {
        "key": "workflowMetrics",
        "type": "truncate-jsonl",
        "file": ".claude/project-context/workflow-episodic-memory/metrics.jsonl",
        "max_days": 90,
        "label": "Workflow metrics",
    },
    {
        "key": "anomalies",
        "type": "truncate-jsonl",
        "file": ".claude/project-context/workflow-episodic-memory/anomalies.jsonl",
        "max_days": 90,
        "label": "Anomalies",
    },
    {
        "key": "legacyLogs",
        "type": "legacy",
        "dir": ".claude/logs",
        "patterns": ["pre_tool_use_v2-*.log", "post_tool_use_v2-*.log", "subagent_stop-*.log"],
        "label": "Legacy logs",
    },
    {
        "key": "anomalyFlag",
        "type": "flag-ttl",
        "file": ".claude/project-context/workflow-episodic-memory/signals/needs_analysis.flag",
        "max_hours": 1,
        "label": "Anomaly signal flag",
    },
]


def _matches_pattern(filename: str, pattern: str) -> bool:
    """Glob-style pattern match supporting * wildcard."""
    import fnmatch
    return fnmatch.fnmatch(filename, pattern)


def _prune_old_files(root: Path, dir_rel: str, pattern: str, max_days: int, label: str, dry_run: bool) -> list:
    """Return list of action dicts for files matching pattern older than max_days."""
    actions = []
    full_dir = root / dir_rel
    if not full_dir.exists():
        return actions

    import time
    cutoff = time.time() - max_days * 86400

    for entry in full_dir.iterdir():
        if not _matches_pattern(entry.name, pattern):
            continue
        if not entry.is_file():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                actions.append({
                    "action": "delete-file",
                    "path": str(entry.relative_to(root)),
                    "label": label,
                })
                if not dry_run:
                    entry.unlink()
        except OSError:
            pass

    return actions


def _prune_old_dirs(root: Path, dir_rel: str, max_days: int, label: str, dry_run: bool) -> list:
    """Return list of action dicts for directories older than max_days."""
    import shutil
    import time
    actions = []
    full_dir = root / dir_rel
    if not full_dir.exists():
        return actions

    cutoff = time.time() - max_days * 86400

    for entry in full_dir.iterdir():
        if not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                actions.append({
                    "action": "delete-dir",
                    "path": str(entry.relative_to(root)),
                    "label": label,
                })
                if not dry_run:
                    shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            pass

    return actions


def _truncate_jsonl(root: Path, file_rel: str, max_days: int, label: str, dry_run: bool) -> list:
    """Remove JSONL lines with timestamp older than max_days."""
    import time
    actions = []
    full_path = root / file_rel
    if not full_path.exists():
        return actions

    cutoff = time.time() - max_days * 86400
    removed = 0

    try:
        lines = full_path.read_text(encoding="utf-8").splitlines()
        kept = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts_str = entry.get("timestamp")
                if ts_str:
                    from datetime import datetime, timezone
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
                    if ts < cutoff:
                        removed += 1
                        continue
            except (json.JSONDecodeError, ValueError):
                pass
            kept.append(line)

        if removed > 0:
            actions.append({
                "action": "truncate-jsonl",
                "path": file_rel,
                "removed": removed,
                "label": label,
            })
            if not dry_run:
                content = "\n".join(kept) + ("\n" if kept else "")
                full_path.write_text(content, encoding="utf-8")
    except OSError:
        pass

    return actions


def _prune_legacy_logs(root: Path, dir_rel: str, patterns: list, label: str, dry_run: bool) -> list:
    """Remove legacy log files matching any of the patterns (no age check)."""
    actions = []
    full_dir = root / dir_rel
    if not full_dir.exists():
        return actions

    for entry in full_dir.iterdir():
        if not entry.is_file():
            continue
        if any(_matches_pattern(entry.name, p) for p in patterns):
            actions.append({
                "action": "delete-legacy",
                "path": str(entry.relative_to(root)),
                "label": label,
            })
            if not dry_run:
                try:
                    entry.unlink()
                except OSError:
                    pass

    return actions


def _prune_flag_by_ttl(root: Path, file_rel: str, max_hours: int, label: str, dry_run: bool) -> list:
    """Remove a flag file if older than max_hours (by mtime or created_at field)."""
    import time
    actions = []
    full_path = root / file_rel
    if not full_path.exists():
        return actions

    cutoff = time.time() - max_hours * 3600
    expired = False

    try:
        if full_path.stat().st_mtime < cutoff:
            expired = True
        else:
            try:
                data = json.loads(full_path.read_text(encoding="utf-8"))
                created_str = data.get("created_at") or data.get("timestamp")
                if created_str:
                    from datetime import datetime
                    created_ts = datetime.fromisoformat(created_str.replace("Z", "+00:00")).timestamp()
                    if created_ts < cutoff:
                        expired = True
            except (json.JSONDecodeError, ValueError, OSError):
                pass
    except OSError:
        return actions

    if expired:
        actions.append({
            "action": "expire-flag",
            "path": file_rel,
            "label": label,
        })
        if not dry_run:
            try:
                full_path.unlink()
            except OSError:
                pass

    return actions


def _apply_retention_policy(root: Path, dry_run: bool) -> list:
    """Apply all retention policy rules and return list of action dicts."""
    all_actions = []

    for policy in RETENTION_POLICY:
        ptype = policy["type"]
        if ptype == "files":
            all_actions.extend(
                _prune_old_files(root, policy["dir"], policy["pattern"], policy["max_days"], policy["label"], dry_run)
            )
        elif ptype == "dirs":
            all_actions.extend(
                _prune_old_dirs(root, policy["dir"], policy["max_days"], policy["label"], dry_run)
            )
        elif ptype == "truncate-jsonl":
            all_actions.extend(
                _truncate_jsonl(root, policy["file"], policy["max_days"], policy["label"], dry_run)
            )
        elif ptype == "legacy":
            all_actions.extend(
                _prune_legacy_logs(root, policy["dir"], policy["patterns"], policy["label"], dry_run)
            )
        elif ptype == "flag-ttl":
            all_actions.extend(
                _prune_flag_by_ttl(root, policy["file"], policy["max_hours"], policy["label"], dry_run)
            )

    return all_actions


# ---------------------------------------------------------------------------
# Symlink / file removal helpers
# ---------------------------------------------------------------------------

SYMLINKS_TO_REMOVE = [
    ".claude/agents",
    ".claude/tools",
    ".claude/hooks",
    ".claude/commands",
    ".claude/templates",
    ".claude/config",
    ".claude/CHANGELOG.md",
    ".claude/README.en.md",
    ".claude/README.md",
]


def _remove_claude_md(root: Path, dry_run: bool) -> dict:
    path = root / "CLAUDE.md"
    if not path.exists():
        return {"found": False}
    if not dry_run:
        path.unlink()
    return {"found": True, "removed": not dry_run, "dry_run": dry_run}


def _remove_settings_json(root: Path, dry_run: bool) -> dict:
    path = root / ".claude" / "settings.json"
    if not path.exists():
        return {"found": False}
    if not dry_run:
        path.unlink()
    return {"found": True, "removed": not dry_run, "dry_run": dry_run}


def _remove_symlinks(root: Path, dry_run: bool) -> dict:
    removed = []
    skipped = []

    targets = list(SYMLINKS_TO_REMOVE)
    targets.append("AGENTS.md")  # project root symlink

    # Also scan for broken symlinks in .claude/
    claude_dir = root / ".claude"
    if claude_dir.exists():
        for entry in claude_dir.iterdir():
            if entry.is_symlink():
                try:
                    entry.resolve(strict=True)
                except OSError:
                    rel = str(entry.relative_to(root))
                    if rel not in targets:
                        targets.append(rel)

    for rel_path in targets:
        full_path = root / rel_path
        try:
            stat = full_path.lstat()
        except OSError:
            skipped.append(rel_path)
            continue

        if stat.st_mode & 0o170000 in (0o120000, 0o100000):  # symlink or file
            removed.append(rel_path)
            if not dry_run:
                try:
                    full_path.unlink()
                except OSError:
                    pass

    return {"removed": removed, "skipped": skipped, "dry_run": dry_run}


# ---------------------------------------------------------------------------
# Plugin interface
# ---------------------------------------------------------------------------

def register(subparsers):
    """Register the 'cleanup' subcommand."""
    p = subparsers.add_parser(
        "cleanup",
        help="Remove CLAUDE.md, settings.json, symlinks and apply data retention policy",
        description=(
            "Cleanup gaia installation files and apply data retention policy.\n"
            "\n"
            "Default mode: removes CLAUDE.md, settings.json, symlinks, then runs retention.\n"
            "--prune / --retain: run data retention only (no file/symlink removal).\n"
            "--dry-run: print what would change without modifying anything.\n"
        ),
    )
    p.add_argument(
        "--prune",
        action="store_true",
        default=False,
        help="Apply data retention policy only (no symlink/settings removal)",
    )
    p.add_argument(
        "--retain",
        action="store_true",
        default=False,
        help="Alias for --prune",
    )
    p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=False,
        help="Print what would be pruned/removed without modifying files",
    )
    p.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output results as JSON",
    )
    return p


def cmd_cleanup(args) -> int:
    """Execute the cleanup subcommand."""
    root = _find_project_root()
    prune_only = getattr(args, "prune", False) or getattr(args, "retain", False)
    dry_run = getattr(args, "dry_run", False)
    as_json = getattr(args, "json", False)

    result = {
        "root": str(root),
        "dry_run": dry_run,
        "prune_only": prune_only,
    }

    # Retention policy display header (mirrors JS)
    retention_policy_info = {
        "audit_logs_days": 30,
        "hook_logs_days": 14,
        "monthly_metrics_days": 90,
        "response_contracts_days": 7,
        "episodic_episodes_days": 90,
        "workflow_metrics_days": 90,
        "anomalies_days": 90,
        "legacy_logs": "all removed",
        "anomaly_flag_hours": 1,
    }

    if prune_only:
        if not as_json:
            print("\ngaia-ops data retention")
            print("\nRetention policy:")
            print("  Audit logs:          30 days")
            print("  Hook logs:           14 days")
            print("  Monthly metrics:     90 days")
            print("  Response contracts:   7 days")
            print("  Episodic episodes:   90 days")
            print("  Workflow metrics:    90 days")
            print("  Anomalies:           90 days")
            print("  Legacy logs:         all removed")
            print("  Anomaly flag:         1 hour TTL")
            if dry_run:
                print("  (dry-run mode -- no files will be modified)\n")
            else:
                print()

        retention_actions = _apply_retention_policy(root, dry_run)
        result["retention_actions"] = retention_actions
        result["retention_policy"] = retention_policy_info

        if as_json:
            print(json.dumps(result, indent=2))
        else:
            if retention_actions:
                for action in retention_actions:
                    verb = "Would prune" if dry_run else "Pruned"
                    print(f"  {verb}: {action['path']} ({action['label']})")
                status = "Data retention preview complete" if dry_run else "Data retention completed"
            else:
                status = "All data within retention limits"
            print(f"\n{status}\n")

        return 0

    # Full cleanup mode
    if not as_json:
        print("\ngaia-ops cleanup")
        if dry_run:
            print("  (dry-run mode -- no files will be modified)\n")
        else:
            print()

    claude_md = _remove_claude_md(root, dry_run)
    settings = _remove_settings_json(root, dry_run)
    symlinks = _remove_symlinks(root, dry_run)
    retention_actions = _apply_retention_policy(root, dry_run)

    result["claude_md"] = claude_md
    result["settings_json"] = settings
    result["symlinks"] = symlinks
    result["retention_actions"] = retention_actions
    result["retention_policy"] = retention_policy_info

    if as_json:
        print(json.dumps(result, indent=2))
    else:
        # Report what happened
        anything_done = (
            claude_md.get("found")
            or settings.get("found")
            or symlinks.get("removed")
            or retention_actions
        )

        if claude_md.get("found"):
            verb = "Would remove" if dry_run else "Removed"
            print(f"  {verb}: CLAUDE.md")
        if settings.get("found"):
            verb = "Would remove" if dry_run else "Removed"
            print(f"  {verb}: .claude/settings.json")
        for rel in symlinks.get("removed", []):
            verb = "Would remove symlink" if dry_run else "Removed symlink"
            print(f"  {verb}: {rel}")
        for action in retention_actions:
            verb = "Would prune" if dry_run else "Pruned"
            print(f"  {verb}: {action['path']} ({action['label']})")

        if anything_done:
            status = "Cleanup preview complete" if dry_run else "Cleanup completed"
            print(f"\n{status}")
            print("\nPreserved data:")
            print("  .claude/logs/")
            print("  .claude/tests/")
            print("  .claude/project-context/")
            print("  .claude/session/")
            print("  .claude/metrics/")
        else:
            print("  Nothing to clean up")
        print()

    return 0
