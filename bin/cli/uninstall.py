"""
gaia uninstall -- disconnect Gaia from the current workspace.

Wraps the workspace-level cleanup performed by `gaia cleanup` and adds:
  * preuninstall mode (invoked by npm before package removal)
  * a gzip snapshot of ~/.gaia/gaia.db, taken by DEFAULT before cleanup
  * dry-run reporting

Default behaviour is CONSERVATIVE: the user database in ~/.gaia/gaia.db is
ALWAYS preserved -- there is no flag, combination of flags, or code path in
this module that deletes it. Memory, episodes, and any persisted state
survive `npm uninstall` unconditionally. The default snapshot is purely
ADDITIVE: it only ever writes a new gzip file next to the live DB; it never
removes or modifies the source. Pass --no-backup to skip it.

Modes:
  --preuninstall      Tone output for npm preuninstall hook (still exits 0)
  --no-backup         Skip the default gzip snapshot of ~/.gaia/gaia.db
                      (the DB is still never deleted either way)
  --snapshot-dir DIR  Directory for the snapshot (default: the shared
                      gaia.paths.snapshot_dir())
  --workspace PATH    Restrict cleanup to PATH instead of auto-detected root
  --dry-run           Print what would happen without modifying anything
  --quiet             Suppress non-error output
  --json              Machine-readable output

Exit code is always 0 on the cleanup path so that `npm uninstall` continues
even if cleanup misses a file. Argparse errors and unexpected exceptions
still surface a non-zero exit.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Reuse the heavy lifting already implemented in cleanup.py rather than
# duplicating retention policy, symlink lists, or root detection.
from cli.cleanup import (  # type: ignore  # noqa: E402
    _apply_retention_policy,
    _clean_settings_local_json,
    _find_project_root,
    _remove_claude_md,
    _remove_plugin_initialized,
    _remove_plugin_registry_entry,
    _remove_settings_json,
    _remove_symlinks,
)

# cli.cleanup (imported above) already inserts the repo root into sys.path,
# so gaia.paths is importable here. Single source of truth for BOTH the
# snapshot directory (AC-4: gaia/paths/resolver.py snapshot_dir() and this
# module MUST resolve to the same plural `snapshots` directory) AND the
# snapshot+retention implementation itself (AC-7: shared with the
# SessionStart auto-backup in hooks/modules/session/db_backup.py -- ONE
# "create gzip snapshot + keep last N" implementation, two call sites).
from gaia.paths import create_snapshot  # noqa: E402
from gaia.paths import snapshot_dir as _resolver_snapshot_dir  # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = Path.home() / ".gaia" / "gaia.db"
DEFAULT_SNAPSHOT_DIR = _resolver_snapshot_dir()
DEFAULT_RETAIN = 5


# ---------------------------------------------------------------------------
# DB snapshot helper (on by default; --no-backup skips it; DB is preserved
# either way). Thin wrapper over the shared gaia.paths.create_snapshot so
# uninstall and the SessionStart auto-backup (AC-7) share ONE
# create-snapshot-and-rotate implementation.
# ---------------------------------------------------------------------------

def _snapshot_db(db_path: Path, snapshot_dir: Path, dry_run: bool) -> dict:
    """Create a gzip snapshot of the DB and enforce the shared retention
    policy (keep the newest ``DEFAULT_RETAIN`` snapshots).

    Returns a result dict with shape:
      {"requested": True,
       "source":  "<db path>",
       "path":    "<snapshot path>",
       "created": True/False,
       "dry_run": True/False,
       "pruned":  ["<path>", ...],
       "error":   "<message>" (only on failure)}

    This is purely additive to the source DB -- it never deletes or
    modifies it. A failure here only means no backup was written; the DB
    is untouched. See gaia.paths.snapshot.create_snapshot for the
    copy-based safety guarantee.
    """
    return create_snapshot(
        db_path, snapshot_dir, dry_run=dry_run, retain=DEFAULT_RETAIN,
        prefix="uninstall",
    )


# ---------------------------------------------------------------------------
# Workspace resolution
# ---------------------------------------------------------------------------

def _resolve_workspace(arg_workspace: str | None) -> Path:
    """Return the workspace root to clean. --workspace overrides auto-detect."""
    if arg_workspace:
        return Path(arg_workspace).expanduser().resolve()
    return _find_project_root()


# ---------------------------------------------------------------------------
# Plugin interface
# ---------------------------------------------------------------------------

def register(subparsers):
    """Register the 'uninstall' subcommand."""
    p = subparsers.add_parser(
        "uninstall",
        help="Disconnect Gaia from this workspace (cleanup; DB is never deleted)",
        description=(
            "Disconnect Gaia from the current machine.\n"
            "\n"
            "By default removes CLAUDE.md, .claude/ symlinks, settings.json,\n"
            "and applies the retention policy. The user DB at ~/.gaia/gaia.db\n"
            "is NEVER deleted by this command -- there is no flag that removes\n"
            "it. A gzip snapshot of it is written by default before cleanup;\n"
            "pass --no-backup to skip that snapshot.\n"
            "\n"
            "Intended to be invoked from npm preuninstall via:\n"
            "    python3 bin/gaia uninstall --preuninstall\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--preuninstall",
        action="store_true",
        default=False,
        help="Adapt output for npm preuninstall hook (still exits 0)",
    )
    p.add_argument(
        "--workspace",
        type=str,
        default=None,
        help="Workspace path to clean (default: auto-detect via .claude/)",
    )
    p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=False,
        help="Print actions without modifying anything",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress non-error output",
    )
    p.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output results as JSON",
    )
    p.add_argument(
        "--db-path",
        type=str,
        default=None,
        help=f"Override DB path (default: {DEFAULT_DB_PATH})",
    )
    p.add_argument(
        "--no-backup",
        dest="no_backup",
        action="store_true",
        default=False,
        help=(
            "Skip the gzip snapshot of ~/.gaia/gaia.db that uninstall "
            "writes by default (the DB is preserved in place either way)"
        ),
    )
    p.add_argument(
        "--snapshot-dir",
        dest="snapshot_dir",
        type=str,
        default=None,
        help=(
            f"Directory for the default snapshot "
            f"(default: {DEFAULT_SNAPSHOT_DIR}). Ignored with --no-backup."
        ),
    )
    return p


def cmd_uninstall(args: argparse.Namespace) -> int:
    """Execute the uninstall subcommand. Always returns 0 from the cleanup path.

    The DB at ``db_path`` is NEVER deleted by this function -- no flag,
    combination of flags, or branch below removes it. The default gzip
    snapshot (skippable via --no-backup) only ever writes an additional
    file alongside the live DB.
    """
    dry_run = bool(getattr(args, "dry_run", False))
    backup = not bool(getattr(args, "no_backup", False))
    preuninstall = bool(getattr(args, "preuninstall", False))
    quiet = bool(getattr(args, "quiet", False))
    as_json = bool(getattr(args, "json", False))
    db_override = getattr(args, "db_path", None)

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    db_path = Path(db_override).expanduser() if db_override else DEFAULT_DB_PATH

    snapshot_override = getattr(args, "snapshot_dir", None)
    snapshot_dir = (
        Path(snapshot_override).expanduser() if snapshot_override else DEFAULT_SNAPSHOT_DIR
    )

    result: dict = {
        "mode": "preuninstall" if preuninstall else "manual",
        "workspace": str(workspace),
        "db_path": str(db_path),
        "dry_run": dry_run,
        "backup_requested": backup,
    }

    # --- Workspace cleanup (delegated to cleanup.py helpers) -------------
    try:
        result["claude_md"] = _remove_claude_md(workspace, dry_run)
        result["settings_json"] = _remove_settings_json(workspace, dry_run)
        result["settings_local_json"] = _clean_settings_local_json(workspace, dry_run)
        result["plugin_initialized"] = _remove_plugin_initialized(workspace, dry_run)
        result["plugin_registry"] = _remove_plugin_registry_entry(workspace, dry_run)
        result["symlinks"] = _remove_symlinks(workspace, dry_run)
        result["retention_actions"] = _apply_retention_policy(workspace, dry_run)
    except Exception as exc:  # noqa: BLE001
        result["cleanup_error"] = str(exc)

    # --- DB handling --------------------------------------------------------
    # The DB is ALWAYS preserved -- uninstall has no delete path for it.
    # A gzip snapshot is taken by DEFAULT (skippable via --no-backup): it is
    # purely additive -- writes a new snapshot and rotates old ones, never
    # touches the source DB, so a failed backup never blocks anything.
    if backup:
        snapshot_result = _snapshot_db(db_path, snapshot_dir, dry_run)
        result["snapshot"] = snapshot_result

        if not quiet and not as_json:
            if snapshot_result.get("created"):
                print(f"  Backup created: {snapshot_result['path']}")
            elif dry_run and db_path.exists():
                print(f"  Would create backup: {snapshot_result['path']}")
            elif "error" in snapshot_result:
                print(
                    f"  WARNING: backup failed ({snapshot_result['error']}); "
                    f"DB was NOT touched.",
                    file=sys.stderr,
                )

    result["db"] = {
        "path": str(db_path),
        "found": db_path.exists(),
        "preserved": True,
        "note": "gaia uninstall never deletes the DB. It is snapshotted by default (use --no-backup to skip).",
    }

    # --- Reporting --------------------------------------------------------
    if as_json:
        print(json.dumps(result, indent=2))
    elif not quiet:
        _print_human(result, preuninstall=preuninstall, dry_run=dry_run)

    # Always exit 0 on the cleanup path so npm uninstall continues even on
    # partial failures. Argparse errors still produce non-zero via parse_args.
    return 0


def _print_human(result: dict, *, preuninstall: bool, dry_run: bool) -> None:
    """Print a human-friendly summary."""
    header = "gaia uninstall (preuninstall)" if preuninstall else "gaia uninstall"
    print(f"\n{header}")
    if dry_run:
        print("  (dry-run -- no files will be modified)")
    print(f"  workspace: {result['workspace']}")
    print(f"  db:        {result['db_path']}")
    print()

    claude_md = result.get("claude_md") or {}
    settings = result.get("settings_json") or {}
    settings_local = result.get("settings_local_json") or {}
    plugin_initialized = result.get("plugin_initialized") or {}
    plugin_registry = result.get("plugin_registry") or {}
    symlinks = result.get("symlinks") or {}
    retention = result.get("retention_actions") or []
    db = result.get("db") or {}

    if claude_md.get("found"):
        verb = "Would remove" if dry_run else "Removed"
        print(f"  {verb}: CLAUDE.md")
    if settings.get("found"):
        verb = "Would remove" if dry_run else "Removed"
        print(f"  {verb}: .claude/settings.json")
    if settings_local.get("found"):
        verb = "Would clean" if dry_run else "Cleaned"
        fields = ", ".join(settings_local.get("removed_fields", []))
        print(f"  {verb}: .claude/settings.local.json ({fields})")
    if plugin_initialized.get("found"):
        verb = "Would remove" if dry_run else "Removed"
        print(f"  {verb}: .claude/.plugin-initialized")
    if plugin_registry.get("found"):
        verb = "Would remove" if dry_run else "Removed"
        entries = ", ".join(plugin_registry.get("removed_entries", []))
        print(f"  {verb}: plugin-registry.json entry ({entries})")
    for rel in symlinks.get("removed", []):
        verb = "Would remove symlink" if dry_run else "Removed symlink"
        print(f"  {verb}: {rel}")
    if retention:
        # These are routine log-hygiene prunes applied on the way out, NOT
        # part of the uninstall teardown -- the header keeps that distinct.
        print("\n  Retention policy (routine log hygiene, not part of uninstall):")
        for action in retention:
            verb = "Would prune" if dry_run else "Pruned"
            path = action.get("path", "?")
            label = action.get("label", "")
            print(f"    {verb}: {path} ({label})")

    print()
    snapshot = result.get("snapshot") or {}
    if snapshot:
        if snapshot.get("created"):
            print(f"  Backup written: {snapshot.get('path')}")
        elif "error" in snapshot:
            print(f"  Backup failed: {snapshot.get('error')}")
    if db.get("found"):
        print(f"  DB preserved: {db.get('path')}")
        if not result.get("backup_requested"):
            print("    (--no-backup: snapshot skipped; uninstall never deletes the DB)")
    else:
        print(f"  DB not present: {db.get('path')}")

    print()
    if "cleanup_error" in result:
        print(f"  Warning: cleanup error: {result['cleanup_error']}")
        print()
    if preuninstall:
        print("  Continuing with npm uninstall...")
    else:
        print("  Done.")
    print()
