"""
gaia uninstall -- disconnect Gaia from the current workspace.

Wraps the workspace-level cleanup performed by `gaia cleanup` and adds:
  * preuninstall mode (invoked by npm before package removal)
  * optional --purge to delete ~/.gaia/gaia.db (DESTRUCTIVE, off by default)
  * dry-run reporting

Default behaviour is CONSERVATIVE: the user database in ~/.gaia/gaia.db is
preserved unless --purge is passed explicitly. Memory, episodes, and any
persisted state survive an accidental `npm uninstall`.

Modes:
  --preuninstall      Tone output for npm preuninstall hook (still exits 0)
  --purge             Also delete ~/.gaia/gaia.db (DESTRUCTIVE)
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
import gzip
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Reuse the heavy lifting already implemented in cleanup.py rather than
# duplicating retention policy, symlink lists, or root detection.
from cli.cleanup import (  # type: ignore  # noqa: E402
    _apply_retention_policy,
    _find_project_root,
    _remove_claude_md,
    _remove_settings_json,
    _remove_symlinks,
)

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = Path.home() / ".gaia" / "gaia.db"
DEFAULT_SNAPSHOT_DIR = Path.home() / ".gaia" / "snapshots"


# ---------------------------------------------------------------------------
# DB snapshot helper (always runs before --purge to guarantee a backup)
# ---------------------------------------------------------------------------

def _snapshot_db(db_path: Path, snapshot_dir: Path, dry_run: bool) -> dict:
    """Create a gzip snapshot of the DB before destructive purge.

    Returns a result dict with shape:
      {"requested": True,
       "source":  "<db path>",
       "path":    "<snapshot path>",
       "created": True/False,
       "dry_run": True/False,
       "error":   "<message>" (only on failure)}

    Failure to create the snapshot is fatal -- the caller MUST abort the
    purge so the user does not lose their DB without a backup.
    """
    result: dict = {
        "requested": True,
        "source": str(db_path),
        "path": None,
        "created": False,
        "dry_run": dry_run,
    }

    if not db_path.exists():
        # Nothing to snapshot -- not an error, just a no-op.
        result["details"] = "DB does not exist; nothing to snapshot"
        return result

    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    snapshot_path = snapshot_dir / f"uninstall-{timestamp}.db.gz"
    result["path"] = str(snapshot_path)

    if dry_run:
        result["details"] = "would create snapshot"
        return result

    try:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        with open(db_path, "rb") as src, gzip.open(snapshot_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
    except OSError as exc:
        result["error"] = str(exc)
        return result

    result["created"] = True
    result["details"] = f"snapshot {snapshot_path.stat().st_size} bytes"
    return result


# ---------------------------------------------------------------------------
# DB purge helper (only runs when --purge is passed)
# ---------------------------------------------------------------------------

def _purge_db(db_path: Path, dry_run: bool) -> dict:
    """Delete the user DB if it exists. Returns a result dict."""
    result = {
        "path": str(db_path),
        "found": db_path.exists(),
        "removed": False,
        "dry_run": dry_run,
    }
    if not result["found"]:
        return result
    if dry_run:
        return result
    try:
        db_path.unlink()
        result["removed"] = True
    except OSError as exc:
        result["error"] = str(exc)
    return result


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
        help="Disconnect Gaia from this workspace (cleanup + optional DB purge)",
        description=(
            "Disconnect Gaia from the current machine.\n"
            "\n"
            "By default removes CLAUDE.md, .claude/ symlinks, settings.json,\n"
            "and applies the retention policy. The user DB at ~/.gaia/gaia.db\n"
            "is PRESERVED unless --purge is passed.\n"
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
        "--purge",
        action="store_true",
        default=False,
        help="Also delete ~/.gaia/gaia.db (DESTRUCTIVE, off by default)",
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
        "--snapshot-dir",
        dest="snapshot_dir",
        type=str,
        default=None,
        help=(
            f"Directory for pre-purge DB snapshots "
            f"(default: {DEFAULT_SNAPSHOT_DIR}). Only used with --purge."
        ),
    )
    p.add_argument(
        "--no-snapshot",
        dest="no_snapshot",
        action="store_true",
        default=False,
        help="Skip the pre-purge snapshot (DANGEROUS; loses backup safety net)",
    )
    return p


def cmd_uninstall(args: argparse.Namespace) -> int:
    """Execute the uninstall subcommand. Always returns 0 from the cleanup path."""
    dry_run = bool(getattr(args, "dry_run", False))
    purge = bool(getattr(args, "purge", False))
    preuninstall = bool(getattr(args, "preuninstall", False))
    quiet = bool(getattr(args, "quiet", False))
    as_json = bool(getattr(args, "json", False))
    db_override = getattr(args, "db_path", None)

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    db_path = Path(db_override).expanduser() if db_override else DEFAULT_DB_PATH

    snapshot_override = getattr(args, "snapshot_dir", None)
    no_snapshot = bool(getattr(args, "no_snapshot", False))
    snapshot_dir = (
        Path(snapshot_override).expanduser() if snapshot_override else DEFAULT_SNAPSHOT_DIR
    )

    result: dict = {
        "mode": "preuninstall" if preuninstall else "manual",
        "workspace": str(workspace),
        "db_path": str(db_path),
        "dry_run": dry_run,
        "purge_requested": purge,
    }

    # --- Workspace cleanup (delegated to cleanup.py helpers) -------------
    try:
        result["claude_md"] = _remove_claude_md(workspace, dry_run)
        result["settings_json"] = _remove_settings_json(workspace, dry_run)
        result["symlinks"] = _remove_symlinks(workspace, dry_run)
        result["retention_actions"] = _apply_retention_policy(workspace, dry_run)
    except Exception as exc:  # noqa: BLE001
        result["cleanup_error"] = str(exc)

    # --- DB handling ------------------------------------------------------
    if purge:
        # Safety net: snapshot before purge unless explicitly disabled.
        # If snapshot creation fails (and not --no-snapshot), abort the
        # purge so the user does not lose their DB without a backup.
        if no_snapshot:
            result["snapshot"] = {
                "requested": False,
                "details": "snapshot skipped via --no-snapshot",
            }
        else:
            snapshot_result = _snapshot_db(db_path, snapshot_dir, dry_run)
            result["snapshot"] = snapshot_result

            snapshot_failed = (
                db_path.exists()
                and not dry_run
                and not snapshot_result.get("created")
                and "error" in snapshot_result
            )
            if snapshot_failed:
                result["db"] = {
                    "path": str(db_path),
                    "found": True,
                    "preserved": True,
                    "note": "Purge aborted: snapshot failed -- DB preserved.",
                    "error": snapshot_result.get("error"),
                }
                if not quiet and not as_json:
                    print(
                        f"\n  ERROR: snapshot failed ({snapshot_result.get('error')}); "
                        f"--purge aborted to protect your DB.\n",
                        file=sys.stderr,
                    )
                if as_json:
                    print(json.dumps(result, indent=2))
                elif not quiet:
                    _print_human(
                        result, preuninstall=preuninstall, purge=purge, dry_run=dry_run,
                    )
                return 0

            # Print snapshot path right after creation so the user has it
            # before the destructive operation appears.
            if (
                not quiet
                and not as_json
                and snapshot_result.get("created")
            ):
                print(f"  Snapshot created: {snapshot_result['path']}")
            elif (
                not quiet
                and not as_json
                and dry_run
                and db_path.exists()
            ):
                print(f"  Would create snapshot: {snapshot_result['path']}")

        result["db"] = _purge_db(db_path, dry_run)
    else:
        result["db"] = {
            "path": str(db_path),
            "found": db_path.exists(),
            "preserved": True,
            "note": "Pass --purge to delete (DESTRUCTIVE).",
        }

    # --- Reporting --------------------------------------------------------
    if as_json:
        print(json.dumps(result, indent=2))
    elif not quiet:
        _print_human(result, preuninstall=preuninstall, purge=purge, dry_run=dry_run)

    # Always exit 0 on the cleanup path so npm uninstall continues even on
    # partial failures. Argparse errors still produce non-zero via parse_args.
    return 0


def _print_human(result: dict, *, preuninstall: bool, purge: bool, dry_run: bool) -> None:
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
    symlinks = result.get("symlinks") or {}
    retention = result.get("retention_actions") or []
    db = result.get("db") or {}

    if claude_md.get("found"):
        verb = "Would remove" if dry_run else "Removed"
        print(f"  {verb}: CLAUDE.md")
    if settings.get("found"):
        verb = "Would remove" if dry_run else "Removed"
        print(f"  {verb}: .claude/settings.json")
    for rel in symlinks.get("removed", []):
        verb = "Would remove symlink" if dry_run else "Removed symlink"
        print(f"  {verb}: {rel}")
    for action in retention:
        verb = "Would prune" if dry_run else "Pruned"
        path = action.get("path", "?")
        label = action.get("label", "")
        print(f"  {verb}: {path} ({label})")

    print()
    if purge:
        if db.get("found"):
            verb = "Would delete" if dry_run else "Deleted"
            print(f"  {verb} DB: {db.get('path')}")
        else:
            print(f"  DB not found: {db.get('path')}")
    else:
        if db.get("found"):
            print(f"  DB preserved: {db.get('path')}")
            print("    (pass --purge to delete -- DESTRUCTIVE)")
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
