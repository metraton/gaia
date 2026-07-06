"""
gaia context -- Display and refresh project context.

Subcommands:
  gaia context show [--section SECTION] [--json]   Display context from SQLite substrate (tabular)
  gaia context scan [--dry-run] [--json]            Run project scanner (legacy)
  gaia context get  [--workspace W] [--section S]   Emit canonical workspace shape from substrate
                    [--json] [--text]
  gaia context dump [--workspace W]                 (deprecated) alias for `gaia context get`
  gaia context query "<SQL>"                        Run a read-only SELECT against the substrate
  gaia context wipe  --workspace W [--yes]          (DESTRUCTIVE) Delete all rows for a workspace (CASCADE)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure the gaia package (repo root) is importable regardless of cwd.
# bin/cli/context.py -> bin/cli/ -> bin/ -> repo_root
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Root detection
# ---------------------------------------------------------------------------

def _find_project_root(start: Path) -> Path | None:
    """Locate the project root that owns a .claude/ directory.

    Resolution order:
    1. CLAUDE_PLUGIN_DATA env var (set by Claude Code at runtime) -- its
       parent is the project root.
    2. Walk up from ``start`` looking for .claude/project-context/ directory
       (legacy marker; still accepted for backward compat with installs
       that have the directory even if project-context.json is retired).
    3. Walk up from ``start`` for any .claude/ directory (canonical fallback).

    Note (T1.3): the legacy Pass 1 that looked for project-context.json by
    file existence has been removed. Project context now lives exclusively in
    gaia.db (project_context_contracts table). The .claude/ directory itself
    is the authoritative marker.
    """
    import os
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        candidate = Path(plugin_data)
        if candidate.is_dir():
            return candidate.parent
        return candidate.parent

    current = start.resolve()
    candidates = [current, *current.parents]

    # Pass 1: prefer a root that has the project-context/ directory.
    for parent in candidates:
        if (parent / ".claude" / "project-context").is_dir():
            return parent

    # Pass 2: any .claude/ directory.
    for parent in candidates:
        if (parent / ".claude").is_dir():
            return parent

    return None


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _render_context_tabular(ctx: dict, section: str | None = None) -> None:
    """Render the canonical context shape as human-readable text (tabular)."""
    if section:
        val = ctx.get(section)
        if val is None:
            # Check inside workspace sub-dict
            val = ctx.get("workspace", {}).get(section)
        print(json.dumps(val, indent=2, default=str))
        return

    # Top-level summary
    print(f"workspace        : {ctx.get('identity', '(unknown)')}")
    print()
    workspace = ctx.get("workspace", {})
    top_keys = [k for k in ctx if k not in ("workspace",)]
    for key in top_keys:
        val = ctx[key]
        if isinstance(val, dict) and val:
            print(f"{key}:")
            for k, v in val.items():
                print(f"  {k:<28}  {v}")
        elif val:
            print(f"{key:<30}  {val}")
    print()
    print("workspace entities:")
    for key, rows in workspace.items():
        count = len(rows) if isinstance(rows, list) else "?"
        print(f"  {key:<28}  {count} row(s)")


def _cmd_show(args) -> int:
    """Handle `gaia context show [--section SECTION] [--json]`.

    Reads from the SQLite substrate (single source of truth).
    Presentation: tabular (human-readable). For raw JSON use `gaia context get`.
    """
    try:
        from gaia.store.provider import get_context
        from gaia.project import current as _project_current
    except Exception as exc:  # pragma: no cover
        print(f"gaia context show: failed to import store: {exc}", file=sys.stderr)
        return 1

    workspace = _project_current()
    ctx = get_context(workspace)

    if ctx is None:
        msg = f"workspace '{workspace}' not found in substrate"
        if getattr(args, "json", False):
            print(json.dumps({"error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    section = getattr(args, "section", None)

    if section:
        # Validate section exists
        top_keys = set(ctx.keys())
        workspace_keys = set((ctx.get("workspace") or {}).keys())
        all_keys = top_keys | workspace_keys
        if section not in all_keys:
            msg = f"Section '{section}' not found. Available: {', '.join(sorted(all_keys))}"
            if getattr(args, "json", False):
                print(json.dumps({"error": msg}))
            else:
                print(f"Error: {msg}", file=sys.stderr)
            return 1
        val = ctx.get(section)
        if val is None:
            val = ctx.get("workspace", {}).get(section)
        if getattr(args, "json", False):
            print(json.dumps(val, indent=2, default=str))
        else:
            print(json.dumps(val, indent=2, default=str))
        return 0

    if getattr(args, "json", False):
        print(json.dumps(ctx, indent=2, default=str))
        return 0

    _render_context_tabular(ctx)
    return 0


def _cmd_scan(args) -> int:
    """Handle `gaia context scan [--dry-run] [--json]`.

    Delegates to `bin/cli/scan.py:cmd_scan` in-process. The legacy
    standalone scanner subprocess shell-out has been removed in favour of
    a direct module call -- one process, shared sys.path, no fork overhead.
    """
    project_root = _find_project_root(Path.cwd())
    if project_root is None:
        msg = "gaia context: could not find project root (.claude/ directory)"
        if getattr(args, "json", False):
            print(json.dumps({"error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    dry_run = getattr(args, "dry_run", False)

    if dry_run:
        # Report what would be scanned. Reads last_scan_at from DB (T1.3).
        last_scan = None
        try:
            from gaia.project import current as _project_current
            from gaia.store.writer import _connect as _store_connect
            ws = _project_current(cwd=project_root)
            con = _store_connect()
            try:
                row = con.execute(
                    "SELECT last_scan_at FROM workspaces WHERE name = ?", (ws,)
                ).fetchone()
                if row:
                    last_scan = row[0]
            finally:
                con.close()
        except Exception:
            pass

        result = {
            "dry_run": True,
            "project_root": str(project_root),
            "last_scan": last_scan or "unknown",
            "would_scan": (
                "all scanners (stack, git, infrastructure, environment, "
                "orchestration, architecture)"
            ),
        }

        if getattr(args, "json", False):
            print(json.dumps(result, indent=2))
        else:
            print("[dry-run] Context scan would execute:")
            print(f"  project_root : {result['project_root']}")
            if result.get("last_scan") and result["last_scan"] != "unknown":
                print(f"  last_scan    : {result['last_scan']}")
            print(f"  would_scan   : {result['would_scan']}")
        return 0

    from cli.scan import cmd_scan as _cmd_scan_impl

    scan_args = argparse.Namespace(
        workspace=str(project_root),
        fresh=False,
        dry_run=False,
        json=getattr(args, "json", False),
        scanners=None,
        check_staleness=False,
        no_color=False,
        verbose=False,
    )
    return _cmd_scan_impl(scan_args)


# ---------------------------------------------------------------------------
# B1+ SQLite substrate subcommands: dump / query / wipe
# ---------------------------------------------------------------------------

_SELECT_VERBS = {"select", "with", "explain", "pragma"}


def _cmd_get(args) -> int:
    """Handle `gaia context get [--workspace W] [--section S] [--json] [--text]`.

    Emits the canonical workspace shape from the SQLite substrate.
    Defaults to JSON output. Use --text for the same tabular renderer as `show`.
    Fix #5: exits 1 with message when workspace does not exist in the DB.
    """
    try:
        from gaia.store.provider import get_context
        from gaia.project import current as _project_current
    except Exception as exc:  # pragma: no cover -- import wiring failure
        print(f"gaia context get: failed to import store: {exc}", file=sys.stderr)
        return 1

    workspace = getattr(args, "workspace", None) or _project_current()
    try:
        ctx = get_context(workspace)
    except Exception as exc:
        print(f"gaia context get: error reading store: {exc}", file=sys.stderr)
        return 1

    # Fix #5: workspace not found
    if ctx is None:
        print(f"workspace '{workspace}' not found", file=sys.stderr)
        return 1

    section = getattr(args, "section", None)
    use_text = getattr(args, "text", False)

    if section:
        top_keys = set(ctx.keys())
        workspace_keys = set((ctx.get("workspace") or {}).keys())
        all_keys = top_keys | workspace_keys
        if section not in all_keys:
            print(
                f"gaia context get: section '{section}' not found. "
                f"Available: {', '.join(sorted(all_keys))}",
                file=sys.stderr,
            )
            return 1
        val = ctx.get(section)
        if val is None:
            val = ctx.get("workspace", {}).get(section)
        if use_text:
            print(json.dumps(val, indent=2, default=str))
        else:
            print(json.dumps(val, indent=2, default=str))
        return 0

    if use_text:
        _render_context_tabular(ctx, section=section)
    else:
        print(json.dumps(ctx, indent=2, default=str))
    return 0


def _cmd_dump(args) -> int:
    """Handle `gaia context dump [--workspace W]`.

    Deprecated: use `gaia context get` instead.
    Kept as a backwards-compatible alias; emits a deprecation warning to stderr.
    """
    print(
        "Warning: `gaia context dump` is deprecated; use `gaia context get`",
        file=sys.stderr,
    )
    return _cmd_get(args)


def _cmd_query(args) -> int:
    """Handle `gaia context query "<SQL>"`.

    Executes a read-only SELECT (or EXPLAIN/PRAGMA/WITH) against the substrate.
    Other verbs are rejected with a non-zero exit code.
    """
    sql = (getattr(args, "sql", "") or "").strip()
    if not sql:
        print("gaia context query: SQL string is required", file=sys.stderr)
        return 2

    head = sql.lstrip("(").lstrip().split(None, 1)[0].lower() if sql.lstrip("(").lstrip() else ""
    if head not in _SELECT_VERBS:
        print(
            f"gaia context query: only read-only verbs allowed ({', '.join(sorted(_SELECT_VERBS))}); got {head!r}",
            file=sys.stderr,
        )
        return 2

    try:
        from gaia.store.writer import _connect as _store_connect
    except Exception as exc:
        print(f"gaia context query: failed to import store: {exc}", file=sys.stderr)
        return 1

    con = _store_connect()
    try:
        try:
            cur = con.execute(sql)
        except Exception as exc:
            print(f"gaia context query: SQL error: {exc}", file=sys.stderr)
            return 1
        rows = cur.fetchall()
        # Print as JSON list of dicts for machine-readability
        out = [dict(r) for r in rows]
        print(json.dumps(out, indent=2, default=str))
    finally:
        con.close()
    return 0


def _cmd_wipe(args) -> int:
    """Handle `gaia context wipe --workspace W [--yes]`.

    Deletes all rows for the workspace (CASCADE removes children).
    Requires interactive confirmation unless --yes is passed.
    """
    workspace = getattr(args, "workspace", None)
    if not workspace:
        print("gaia context wipe: --workspace is required", file=sys.stderr)
        return 2

    if not getattr(args, "yes", False):
        try:
            ans = input(
                f"gaia context wipe: about to delete ALL rows for workspace {workspace!r}.\n"
                f"Type 'yes' to confirm: "
            )
        except EOFError:
            ans = ""
        if ans.strip().lower() != "yes":
            print("Aborted (no confirmation).")
            return 1

    try:
        from gaia.store.writer import wipe_workspace
    except Exception as exc:
        print(f"gaia context wipe: failed to import store: {exc}", file=sys.stderr)
        return 1

    try:
        wipe_workspace(workspace)
    except Exception as exc:
        print(f"gaia context wipe: error: {exc}", file=sys.stderr)
        return 1

    print(f"Wiped workspace: {workspace}")
    return 0


def _cmd_delete_projects(args) -> int:
    """Handle ``gaia context delete-projects --workspace W [filters] [--dry-run] [--yes]``.

    Targeted deletion of `projects` rows within a SINGLE workspace, leaving the
    workspaces row and every non-project child (memory, PCC, briefs, episodes)
    intact -- the surgical alternative to `wipe` for a LIVE workspace. At least
    one filter beyond --workspace is required.
    """
    workspace = getattr(args, "workspace", None)
    if not workspace:
        print("gaia context delete-projects: --workspace is required", file=sys.stderr)
        return 2

    group_name = getattr(args, "group", None)
    status = getattr(args, "status", None)
    path = getattr(args, "path", None)
    project_identity = getattr(args, "identity", None)
    names = getattr(args, "name", None) or None
    dry_run = getattr(args, "dry_run", False)
    as_json = getattr(args, "json", False)

    if not any(v is not None for v in (group_name, status, names, path, project_identity)):
        print(
            "gaia context delete-projects: at least one filter beyond --workspace "
            "is required (--group/--status/--name/--path/--identity). To delete an "
            "entire workspace use `gaia context wipe`.",
            file=sys.stderr,
        )
        return 2

    try:
        from gaia.store.writer import delete_projects
    except Exception as exc:
        print(f"gaia context delete-projects: failed to import store: {exc}", file=sys.stderr)
        return 1

    # Preview first (always), so both --dry-run and the confirmation prompt show
    # exactly which rows are in scope.
    preview = delete_projects(
        workspace,
        group_name=group_name,
        status=status,
        names=names,
        path=path,
        project_identity=project_identity,
        dry_run=True,
    )
    matched = preview["matched"]

    if dry_run:
        if as_json:
            print(json.dumps(preview, indent=2, default=str))
        else:
            print(f"[dry-run] delete-projects would remove {len(matched)} row(s) from workspace {workspace!r}:")
            for r in matched:
                print(f"  - {r['name']}  (group={r['group_name']}, status={r['status']}, path={r['path']})")
        return 0

    if not matched:
        print(f"gaia context delete-projects: no matching rows in workspace {workspace!r}")
        return 0

    if not getattr(args, "yes", False):
        print(f"About to delete {len(matched)} project row(s) from workspace {workspace!r}:")
        for r in matched:
            print(f"  - {r['name']}  (group={r['group_name']}, status={r['status']})")
        try:
            ans = input("Type 'yes' to confirm: ")
        except EOFError:
            ans = ""
        if ans.strip().lower() != "yes":
            print("Aborted (no confirmation).")
            return 1

    result = delete_projects(
        workspace,
        group_name=group_name,
        status=status,
        names=names,
        path=path,
        project_identity=project_identity,
    )
    if as_json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Deleted {result['deleted']} project row(s) from workspace {workspace!r}")
    return 0


def _cmd_move_contracts(args) -> int:
    """Handle ``gaia context move-contracts --from W1 --to W2 --contract C ...``.

    Re-keys project_context_contracts rows between workspaces (the only
    correction path for a mis-keyed contract, since `gaia scan` never touches
    that table). At least one --contract is required.
    """
    from_ws = getattr(args, "from_workspace", None)
    to_ws = getattr(args, "to_workspace", None)
    contracts = getattr(args, "contract", None) or []
    on_conflict = getattr(args, "on_conflict", "error")
    dry_run = getattr(args, "dry_run", False)
    as_json = getattr(args, "json", False)

    if not from_ws or not to_ws:
        print("gaia context move-contracts: --from and --to are required", file=sys.stderr)
        return 2
    if not contracts:
        print("gaia context move-contracts: at least one --contract is required", file=sys.stderr)
        return 2

    try:
        from gaia.store.writer import relocate_contracts
    except Exception as exc:
        print(f"gaia context move-contracts: failed to import store: {exc}", file=sys.stderr)
        return 1

    try:
        preview = relocate_contracts(
            from_ws, to_ws, contracts, on_conflict=on_conflict, dry_run=True
        )
    except ValueError as exc:
        print(f"gaia context move-contracts: {exc}", file=sys.stderr)
        return 1

    if dry_run:
        if as_json:
            print(json.dumps(preview, indent=2, default=str))
        else:
            print(f"[dry-run] move-contracts {from_ws!r} -> {to_ws!r}:")
            print(f"  would move : {preview['moved']}")
            print(f"  skipped    : {preview['skipped']}")
            print(f"  missing    : {preview['missing']}")
            print(f"  overwritten: {preview['overwritten']}")
        return 0

    if not getattr(args, "yes", False):
        print(f"About to move {len(preview['moved'])} contract(s) from {from_ws!r} to {to_ws!r}: {preview['moved']}")
        try:
            ans = input("Type 'yes' to confirm: ")
        except EOFError:
            ans = ""
        if ans.strip().lower() != "yes":
            print("Aborted (no confirmation).")
            return 1

    try:
        result = relocate_contracts(from_ws, to_ws, contracts, on_conflict=on_conflict)
    except ValueError as exc:
        print(f"gaia context move-contracts: {exc}", file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Moved contracts {from_ws!r} -> {to_ws!r}: moved={result['moved']} "
              f"skipped={result['skipped']} missing={result['missing']} "
              f"overwritten={result['overwritten']}")
    return 0


def _cmd_move_memory(args) -> int:
    """Handle ``gaia context move-memory --from W1 --to W2 --name N ...``.

    Re-keys curated `memory` rows (and their intra-set memory_links) between
    workspaces -- the only correction path for a mis-keyed memory note, since
    `gaia scan` never touches that table. Subject to the curated-memory write
    guard: run from a human shell or the orchestrator/operator context (a
    non-curator subagent dispatch is refused). At least one --name is required.
    """
    from_ws = getattr(args, "from_workspace", None)
    to_ws = getattr(args, "to_workspace", None)
    names = getattr(args, "name", None) or []
    on_conflict = getattr(args, "on_conflict", "error")
    dry_run = getattr(args, "dry_run", False)
    as_json = getattr(args, "json", False)

    if not from_ws or not to_ws:
        print("gaia context move-memory: --from and --to are required", file=sys.stderr)
        return 2
    if not names:
        print("gaia context move-memory: at least one --name is required", file=sys.stderr)
        return 2

    try:
        from gaia.store.writer import relocate_memory, MemoryWriteForbidden
    except Exception as exc:
        print(f"gaia context move-memory: failed to import store: {exc}", file=sys.stderr)
        return 1

    try:
        preview = relocate_memory(
            from_ws, to_ws, names, on_conflict=on_conflict, dry_run=True
        )
    except MemoryWriteForbidden as exc:
        print(f"gaia context move-memory: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"gaia context move-memory: {exc}", file=sys.stderr)
        return 1

    if dry_run:
        if as_json:
            print(json.dumps(preview, indent=2, default=str))
        else:
            print(f"[dry-run] move-memory {from_ws!r} -> {to_ws!r}:")
            print(f"  would move   : {preview['moved']}")
            print(f"  skipped      : {preview['skipped']}")
            print(f"  missing      : {preview['missing']}")
            print(f"  overwritten  : {preview['overwritten']}")
            print(f"  links_moved  : {preview['links_moved']}")
            print(f"  partial_links: {preview['partial_links']}")
        return 0

    if not getattr(args, "yes", False):
        print(f"About to move {len(preview['moved'])} memory row(s) from {from_ws!r} to {to_ws!r}: {preview['moved']}")
        try:
            ans = input("Type 'yes' to confirm: ")
        except EOFError:
            ans = ""
        if ans.strip().lower() != "yes":
            print("Aborted (no confirmation).")
            return 1

    try:
        result = relocate_memory(from_ws, to_ws, names, on_conflict=on_conflict)
    except (MemoryWriteForbidden, ValueError) as exc:
        print(f"gaia context move-memory: {exc}", file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Moved memory {from_ws!r} -> {to_ws!r}: moved={result['moved']} "
              f"skipped={result['skipped']} missing={result['missing']} "
              f"overwritten={result['overwritten']} links_moved={result['links_moved']} "
              f"partial_links={result['partial_links']}")
    return 0


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(subparsers) -> None:
    """Register the `context` subcommand with the root parser."""
    ctx_parser = subparsers.add_parser(
        "context",
        help="Display and refresh project context",
    )
    ctx_subparsers = ctx_parser.add_subparsers(dest="context_cmd", metavar="<action>")

    # gaia context show  (tabular view from substrate)
    show_parser = ctx_subparsers.add_parser(
        "show", help="Display workspace context from substrate (tabular)"
    )
    show_parser.add_argument(
        "--section",
        metavar="SECTION",
        default=None,
        help="Show a specific section of the workspace context",
    )
    show_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output as JSON",
    )

    # gaia context scan
    scan_parser = ctx_subparsers.add_parser(
        "scan", help="Run project scanner"
    )
    scan_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate context freshness without running scan",
    )
    scan_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output as JSON",
    )

    # gaia context get  (canonical JSON from substrate)
    def _add_get_args(p) -> None:
        p.add_argument(
            "--workspace",
            metavar="W",
            default=None,
            help="Workspace identity (default: gaia.project.current())",
        )
        p.add_argument(
            "--section",
            metavar="SECTION",
            default=None,
            help="Filter output to a single top-level or workspace section",
        )
        p.add_argument(
            "--json",
            action="store_true",
            default=False,
            help="Emit JSON (default when output is redirected)",
        )
        p.add_argument(
            "--text",
            action="store_true",
            default=False,
            help="Emit human-readable tabular presentation",
        )

    get_parser = ctx_subparsers.add_parser(
        "get",
        help="Emit canonical workspace shape from SQLite substrate as JSON",
    )
    _add_get_args(get_parser)

    # gaia context dump  (deprecated alias for get)
    dump_parser = ctx_subparsers.add_parser(
        "dump",
        help="(deprecated) Use `gaia context get` instead",
    )
    _add_get_args(dump_parser)

    # gaia context query "<SQL>"
    query_parser = ctx_subparsers.add_parser(
        "query",
        help="Run a read-only SELECT against the SQLite substrate",
    )
    query_parser.add_argument(
        "sql",
        metavar="SQL",
        help="SELECT/EXPLAIN/PRAGMA/WITH statement to execute",
    )

    # gaia context wipe --workspace W
    wipe_parser = ctx_subparsers.add_parser(
        "wipe",
        help="(DESTRUCTIVE) Delete all rows for a workspace (CASCADE)",
    )
    wipe_parser.add_argument(
        "--workspace",
        metavar="W",
        required=True,
        help="Workspace identity to wipe",
    )
    wipe_parser.add_argument(
        "--yes",
        action="store_true",
        default=False,
        help="Skip interactive confirmation",
    )

    # gaia context delete-projects --workspace W [filters]
    # (T3: the verb token 'delete-projects' hyphen-splits to 'delete', which is
    # in the security hook's MUTATIVE_VERBS -- so the command is gated as T3
    # without any change to the security layer.)
    del_parser = ctx_subparsers.add_parser(
        "delete-projects",
        help="Delete matching `projects` rows within one workspace (leaves workspace + PCC/memory intact)",
    )
    del_parser.add_argument("--workspace", metavar="W", required=True,
                            help="Workspace to operate within (scope guard)")
    del_parser.add_argument("--group", metavar="G", default=None,
                            help="Match projects.group_name")
    del_parser.add_argument("--status", metavar="S", default=None,
                            help="Match projects.status (e.g. 'missing')")
    del_parser.add_argument("--name", metavar="N", action="append", default=None,
                            help="Match projects.name (repeatable)")
    del_parser.add_argument("--path", metavar="P", default=None,
                            help="Match projects.path")
    del_parser.add_argument("--identity", metavar="I", default=None,
                            help="Match projects.project_identity")
    del_parser.add_argument("--dry-run", action="store_true", default=False,
                            help="Preview matched rows without deleting")
    del_parser.add_argument("--json", action="store_true", default=False,
                            help="Emit JSON")
    del_parser.add_argument("--yes", action="store_true", default=False,
                            help="Skip interactive confirmation")

    # gaia context move-contracts --from W1 --to W2 --contract C ...
    # (T3: the verb token 'move-contracts' hyphen-splits to 'move', which is in
    # MUTATIVE_VERBS -- gated as T3 without touching the security layer.)
    mv_parser = ctx_subparsers.add_parser(
        "move-contracts",
        help="Re-key project_context_contracts rows between workspaces",
    )
    mv_parser.add_argument("--from", dest="from_workspace", metavar="W1", required=True,
                           help="Source workspace (current, wrong key)")
    mv_parser.add_argument("--to", dest="to_workspace", metavar="W2", required=True,
                           help="Destination workspace (correct key)")
    mv_parser.add_argument("--contract", metavar="C", action="append", default=None,
                           help="Contract name to move (repeatable)")
    mv_parser.add_argument("--on-conflict", dest="on_conflict", default="error",
                           choices=("error", "skip", "overwrite"),
                           help="Behavior when target already has the contract")
    mv_parser.add_argument("--dry-run", action="store_true", default=False,
                           help="Preview the move without mutating")
    mv_parser.add_argument("--json", action="store_true", default=False,
                           help="Emit JSON")
    mv_parser.add_argument("--yes", action="store_true", default=False,
                           help="Skip interactive confirmation")

    # gaia context move-memory --from W1 --to W2 --name N ...
    # (T3: the verb token 'move-memory' hyphen-splits to 'move', which is in
    # MUTATIVE_VERBS -- gated as T3 without touching the security layer.)
    mm_parser = ctx_subparsers.add_parser(
        "move-memory",
        help="Re-key curated `memory` rows (and intra-set links) between workspaces",
    )
    mm_parser.add_argument("--from", dest="from_workspace", metavar="W1", required=True,
                           help="Source workspace (current, wrong key)")
    mm_parser.add_argument("--to", dest="to_workspace", metavar="W2", required=True,
                           help="Destination workspace (correct key)")
    mm_parser.add_argument("--name", metavar="N", action="append", default=None,
                           help="Memory row name to move (repeatable)")
    mm_parser.add_argument("--on-conflict", dest="on_conflict", default="error",
                           choices=("error", "skip", "overwrite"),
                           help="Behavior when target already has the memory row")
    mm_parser.add_argument("--dry-run", action="store_true", default=False,
                           help="Preview the move without mutating")
    mm_parser.add_argument("--json", action="store_true", default=False,
                           help="Emit JSON")
    mm_parser.add_argument("--yes", action="store_true", default=False,
                           help="Skip interactive confirmation")


def cmd_context(args) -> int:
    """Dispatch handler for `gaia context`."""
    context_cmd = getattr(args, "context_cmd", None)
    if context_cmd == "show":
        return _cmd_show(args)
    if context_cmd == "scan":
        return _cmd_scan(args)
    if context_cmd == "get":
        return _cmd_get(args)
    if context_cmd == "dump":
        return _cmd_dump(args)
    if context_cmd == "query":
        return _cmd_query(args)
    if context_cmd == "wipe":
        return _cmd_wipe(args)
    if context_cmd == "delete-projects":
        return _cmd_delete_projects(args)
    if context_cmd == "move-contracts":
        return _cmd_move_contracts(args)
    if context_cmd == "move-memory":
        return _cmd_move_memory(args)

    # No sub-action: print help for the context subcommand
    import argparse

    tmp_parser = argparse.ArgumentParser(prog="gaia context")
    tmp_sub = tmp_parser.add_subparsers(dest="context_cmd", metavar="<action>")
    show_p = tmp_sub.add_parser("show", help="Display workspace context (tabular, from substrate)")
    show_p.add_argument("--section", metavar="SECTION")
    tmp_sub.add_parser("scan", help="Run project scanner").add_argument("--dry-run", action="store_true")
    get_p = tmp_sub.add_parser("get", help="Emit canonical workspace shape as JSON (from substrate)")
    get_p.add_argument("--workspace", metavar="W")
    get_p.add_argument("--section", metavar="SECTION")
    get_p.add_argument("--json", action="store_true")
    get_p.add_argument("--text", action="store_true")
    tmp_sub.add_parser("dump", help="(deprecated) alias for `get`").add_argument("--workspace", metavar="W")
    tmp_sub.add_parser("query", help="Read-only SELECT").add_argument("sql", metavar="SQL")
    wipe_p = tmp_sub.add_parser("wipe", help="(DESTRUCTIVE) Delete all rows for a workspace (CASCADE)")
    wipe_p.add_argument("--workspace", metavar="W", required=True)
    wipe_p.add_argument("--yes", action="store_true")
    tmp_parser.print_help()
    return 0
