"""
gaia scan -- Deterministically classify repos into (workspace, project) rows.

Scan and install are separate flows. This command NEVER installs: it does not
create ``package.json``, run ``npm``, build ``.claude/``, or install git hooks.
Installation is owned by ``gaia install`` (bin/cli/install.py).

Classification is DETERMINISTIC and driven by one REQUIRED parameter,
``--workspace <name>``. There is no inference: for each git repo found by
walking down from ``root``, the workspace is the ancestor path segment that
matches ``<name>``, and the project is the segment immediately before the repo
(or the repo name itself when the workspace is the repo's direct parent). See
``tools/scan/classify.py`` for the full ruleset (R1-R6).

Arg surface (locked):
  gaia scan --workspace <name> [root]
      --workspace <name>   REQUIRED. Workspace name matched against each repo's
                           ancestor path segments.
      root                 Optional positional. Directory to walk for repos.
                           Defaults to the current working directory.
      --dry-run            Classify only; report what would change, no DB write.
      --json               Emit the structured report as JSON.

Exit codes:
  0  Success (every discovered repo either matched or was reported as data)
  1  Error (no root, no git repos under root, unexpected failure)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# bin/cli/scan.py -> bin/cli/ -> bin/ -> repo root
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _emit_error(args: argparse.Namespace, msg: str) -> int:
    """Emit an error in the active output format and return exit code 1."""
    if getattr(args, "json", False):
        print(json.dumps({"status": "error", "error": msg}))
    else:
        print(f"Error: {msg}", file=sys.stderr)
    return 1


def _render_human(report, *, dry_run: bool) -> None:
    """Render a ScanReport for a human reader (non-JSON path)."""
    if report.error:
        print(f"Error: {report.error}", file=sys.stderr)
        return

    prefix = "[dry-run] " if dry_run else ""
    ws = report.resolved_workspace or "(none matched)"
    print(f"{prefix}resolved_workspace : {ws}")
    print(f"{prefix}repos_found        : {len(report.repos_found)}")

    if report.projects:
        print(f"{prefix}projects:")
        for p in report.projects:
            applied = "applied" if p["applied"] else ("would-apply" if dry_run else "not-applied")
            print(
                f"  - repo={p['repo']} project={p['project']} "
                f"workspace={p['workspace']} [{applied}]"
            )

    if report.ambiguities:
        print(f"{prefix}ambiguities (deeper-than-3 nesting, returned as data):")
        for a in report.ambiguities:
            print(f"  - repo={a['repo']} extra_levels={a['extra_levels']}")

    if report.errors:
        print(f"{prefix}errors (no workspace match):")
        for e in report.errors:
            print(f"  - repo={e['repo']} --workspace={e['W']}: {e['suggestion']}")

    if not dry_run:
        print(f"{prefix}marked_missing     : {report.marked_missing}")


# ---------------------------------------------------------------------------
# Plugin registration (discovered by bin/gaia)
# ---------------------------------------------------------------------------

def register(subparsers) -> argparse.ArgumentParser:
    """Register the `scan` subcommand with the root parser."""
    p = subparsers.add_parser(
        "scan",
        help="Classify repos into (workspace, project) rows (scan only -- never installs)",
        description=(
            "Walk a directory for git repos and classify each into a "
            "(workspace, project) row, keyed on --workspace. Deterministic: no "
            "inference. This command never installs."
        ),
    )
    p.add_argument(
        "--workspace",
        required=True,
        metavar="NAME",
        help=(
            "REQUIRED workspace name. Matched against each repo's ancestor path "
            "segments to resolve the workspace; the project is the segment "
            "immediately before the repo."
        ),
    )
    p.add_argument(
        "root",
        nargs="?",
        default=None,
        help="Directory to walk for repos (default: current working directory)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        dest="dry_run",
        help="Classify only; report what would change without writing",
    )
    p.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit the structured report as JSON on stdout",
    )
    return p


def cmd_scan(args: argparse.Namespace) -> int:
    """Dispatch handler for `gaia scan`."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [gaia scan] %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )

    workspace = args.workspace
    if not workspace or not workspace.strip():
        return _emit_error(args, "--workspace <name> is required and cannot be empty")

    root = Path(args.root).resolve() if args.root else Path.cwd().resolve()
    if not root.is_dir():
        return _emit_error(args, f"root not found: {root}")

    try:
        from tools.scan.classify import scan as classify_scan
    except Exception as exc:
        return _emit_error(args, f"failed to import tools.scan.classify: {exc}")

    dry_run = getattr(args, "dry_run", False)
    try:
        report = classify_scan(root, workspace, apply=not dry_run)
    except Exception as exc:
        logging.exception("gaia scan failed")
        return _emit_error(args, str(exc))

    if getattr(args, "json", False):
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _render_human(report, dry_run=dry_run)

    return 1 if report.error else 0
