"""
gaia context -- Display and refresh project context.

Two DIFFERENT things are both called "a section" in this file, and confusing
them is the single most common mistake made with this command group:

  * The WORKSPACE SHAPE -- the fixed keys `get_context()` returns:
    identity/stack/environment/git/workspace{apps,services,features,...}.
    `show` and `get` resolve `--section` against THIS shape.
  * A PROJECT-CONTEXT CONTRACT -- a row keyed (workspace, contract_name) in
    the `project_context_contracts` table (project_identity, stack,
    infrastructure, git, environment, ...). These are the exact names that
    appear in an agent's `can_read`/`can_write` kernel menu. `get-contract`
    resolves `--section` against THIS table -- `show`/`get` never reach it.

  Note the trap: the workspace shape ALSO has a key named `stack`, but it is
  always `{}` (a scanner placeholder) -- the real `stack` PAYLOAD lives only
  in the project-context contract of the same name, reachable through
  `get-contract`, never through `get`/`show`.

Subcommands:
  gaia context show [--section SECTION] [--json]   Display context from SQLite substrate (tabular)
                                                     -- resolves --section against the WORKSPACE SHAPE
  gaia context scan [--dry-run] [--json]            Run project scanner (legacy)
  gaia context get  [--workspace W] [--section S]   Emit canonical workspace shape from substrate
                    [--json] [--text]                (--include-missing also emits soft-deleted rows)
                    [--include-missing]              -- resolves --section against the WORKSPACE SHAPE,
                                                        NOT project-context contract names
  gaia context get-contract [--workspace W]         Read ONE project-context contract by name (read-only) --
                    --section S [--json] [--text]     resolves --section against project_context_contracts.
                                                        contract_name, the SAME names as an agent's
                                                        can_read/can_write kernel menu
  gaia context dump [--workspace W]                 (deprecated) alias for `gaia context get`
  gaia context project NAME [--workspace W]         Read-only ficha for ONE project: row, facets,
                    [--json]                          project_identity contract entry, curated-memory
                                                        index -- resolves by exact name or basename of path
  gaia context query "<SQL>"                        Run a read-only SELECT against the substrate
  gaia context wipe  --workspace W [--yes]          (DESTRUCTIVE) Delete all rows for a workspace (CASCADE)
  gaia context prune-workspaces [--dry-run] [--yes] Delete PHANTOM workspaces (0 projects, 0 curated
                    [--json]                        collateral); backs up the DB before any delete
"""

from __future__ import annotations

import argparse
import json
import os
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

    --section here resolves against the WORKSPACE SHAPE, exactly like `get`
    (see the module docstring). For a project-context contract by its
    `can_read`/`can_write` name, use `gaia context get-contract`.
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
            msg = (
                f"Section '{section}' not found in the workspace shape. "
                f"Available: {', '.join(sorted(all_keys))}. If '{section}' is "
                f"a project-context contract name (e.g. from your can_read/"
                f"can_write kernel menu), use "
                f"`gaia context get-contract --section {section}` instead."
            )
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
    """Handle `gaia context get [--workspace W] [--section S] [--json] [--text]
    [--include-missing]`.

    Emits the canonical workspace shape from the SQLite substrate.
    Defaults to JSON output. Use --text for the same tabular renderer as `show`.
    Fix #5: exits 1 with message when workspace does not exist in the DB.

    --include-missing surfaces the soft-deleted rows (status='missing') that the
    default active view hides. The soft-delete itself is intentional: `gaia scan`
    demotes a project that vanished from disk instead of dropping it, so the
    "existed but no longer on disk" record stays consultable. Without the flag
    that record was written but unreadable through the CLI.

    --section here resolves against the WORKSPACE SHAPE (apps/services/stack/
    git/environment/...), NOT a project-context contract name. To read a
    contract by its `can_read`/`can_write` name (project_identity, stack,
    infrastructure, ...), use `gaia context get-contract --section <name>`.
    """
    try:
        from gaia.store.provider import get_context
        from gaia.project import current as _project_current
    except Exception as exc:  # pragma: no cover -- import wiring failure
        print(f"gaia context get: failed to import store: {exc}", file=sys.stderr)
        return 1

    workspace = getattr(args, "workspace", None) or _project_current()
    include_missing = getattr(args, "include_missing", False)
    try:
        ctx = get_context(workspace, include_missing=include_missing)
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
                f"gaia context get: section '{section}' not found in the "
                f"workspace shape. Available: {', '.join(sorted(all_keys))}. "
                f"If '{section}' is a project-context contract name (e.g. from "
                f"your can_read/can_write kernel menu), use "
                f"`gaia context get-contract --section {section}` instead -- "
                f"`get` never resolves against contract names.",
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


def _cmd_get_contract(args) -> int:
    """Handle `gaia context get-contract --section S [--workspace W] [--json]
    [--text]`.

    Read-only. Resolves --section against `project_context_contracts.
    contract_name` -- the SAME names an agent's kernel names in its
    `can_read`/`can_write` menu (project_identity, stack, infrastructure,
    git, ...). This is a DIFFERENT namespace from `get`/`show`'s --section,
    which resolves against the fixed workspace shape (apps/services/stack/
    git/environment/...); see the module docstring for the distinction that
    trips this up in practice. Never mutates project_context_contracts --
    the only write path for that table is `move-contracts` (re-keying).

    Workspace defaults to `gaia.project.current()` (the caller's cwd); pass
    --workspace explicitly to name it, so it is always clear which
    workspace/project the returned contract belongs to.
    """
    section = getattr(args, "section", None)
    if not section:
        print("gaia context get-contract: --section is required", file=sys.stderr)
        return 2

    try:
        from gaia.store.writer import _connect as _store_connect
        from gaia.project import current as _project_current
    except Exception as exc:  # pragma: no cover -- import wiring failure
        print(f"gaia context get-contract: failed to import store: {exc}", file=sys.stderr)
        return 1

    workspace = getattr(args, "workspace", None) or _project_current()
    use_text = getattr(args, "text", False)

    con = _store_connect()
    try:
        row = con.execute(
            "SELECT contract_name, payload, metadata, updated_at "
            "FROM project_context_contracts WHERE workspace = ? AND contract_name = ?",
            (workspace, section),
        ).fetchone()
        available = [
            r[0]
            for r in con.execute(
                "SELECT DISTINCT contract_name FROM project_context_contracts "
                "WHERE workspace = ? ORDER BY contract_name",
                (workspace,),
            ).fetchall()
        ]
    finally:
        con.close()

    if row is None:
        avail_txt = ", ".join(available) if available else "(none for this workspace)"
        print(
            f"gaia context get-contract: no contract named '{section}' for "
            f"workspace '{workspace}'. This resolves project_context_contracts."
            f"contract_name (the same names as your can_read/can_write kernel "
            f"menu) -- it is a DIFFERENT namespace from the workspace-shape "
            f"sections `gaia context get`/`show` understand (apps/services/"
            f"stack/git/...). Available contract names for '{workspace}': "
            f"{avail_txt}",
            file=sys.stderr,
        )
        return 1

    contract_name, payload_str, metadata_str, updated_at = row
    try:
        payload = json.loads(payload_str) if payload_str else {}
    except (json.JSONDecodeError, TypeError):
        payload = {}
    try:
        metadata = json.loads(metadata_str) if metadata_str else None
    except (json.JSONDecodeError, TypeError):
        metadata = metadata_str

    if use_text:
        print(f"workspace     : {workspace}")
        print(f"contract_name : {contract_name}")
        print(f"updated_at    : {updated_at or '(unknown)'}")
        print()
        print(json.dumps(payload, indent=2, default=str))
        return 0

    result = {
        "workspace": workspace,
        "contract_name": contract_name,
        "payload": payload,
        "updated_at": updated_at,
    }
    if metadata is not None:
        result["metadata"] = metadata
    print(json.dumps(result, indent=2, default=str))
    return 0


_PROJECT_PENDING_FOOTER_MAX = 200

# The memory-index section ANNOUNCES the corpus; it does not dump it. A
# project whose curated memory runs into the hundreds (measured: 419 rows for
# `gaia` itself, in the workspace where this ficha is most needed) produced an
# unusable, context-heavy ficha for its own primary reader -- the orchestrator,
# reading in conversation. Capped to the N most-recently-updated rows; the rest
# are named by count with the exact sweep command, never silently dropped.
_PROJECT_MEMORY_INDEX_TOP_N = 10


def _project_derive_initiative(project_identity: str | None) -> str | None:
    """The canonical `memory.initiative` key for a resolved project's identity.

    Delegates to `gaia.store.writer.initiative_from_project_ref` -- the SAME
    derivation the write side (`gaia memory add --project`) uses -- so the
    memory index below matches by the identical key rather than a second,
    possibly-diverging guess.
    """
    try:
        from gaia.store.writer import initiative_from_project_ref
    except Exception:
        return None
    return initiative_from_project_ref(project_identity)


def _project_match_identity_entry(
    payload: dict, path: str | None, remote_url: str | None,
) -> tuple[str | None, dict | None]:
    """Find the `project_identity` contract entry for a resolved project row.

    The contract payload is a map keyed by an opaque slug (see
    `tools/scan/promote.py`'s module docstring) -- there is no way to look an
    entry up by the `projects.name` this command resolved. Two passes, path
    first then normalized remote, mirror `tools/scan/promote._match_slug`'s
    own matching order (the strongest on-disk signal first); this command
    does not import that private helper (it also threads a per-run `claimed`
    set this single lookup has no use for), so the match is reimplemented
    narrowly here instead. Returns (slug, entry) or (None, None).
    """
    if not isinstance(payload, dict):
        return None, None

    norm_path = os.path.normpath(path) if path else None
    for slug, entry in payload.items():
        if not isinstance(entry, dict):
            continue
        e_path = entry.get("local_path")
        if norm_path and e_path and os.path.normpath(e_path) == norm_path:
            return slug, entry

    try:
        from gaia.project import _normalize_remote
    except Exception:
        _normalize_remote = None  # type: ignore[assignment]
    norm_remote = _normalize_remote(remote_url) if (_normalize_remote and remote_url) else None
    if norm_remote:
        for slug, entry in payload.items():
            if not isinstance(entry, dict):
                continue
            e_remote = entry.get("remote_url")
            if not e_remote:
                continue
            try:
                if _normalize_remote(e_remote) == norm_remote:
                    return slug, entry
            except Exception:
                continue

    return None, None


def _project_pending_footer(con, workspace: str, initiative: str | None) -> str | None:
    """The P2-style computed footer line (decision_gaia_el_cli_ensena_en_el_

    instante_del_verbo): fires ONLY when the resolved project's initiative
    has other live-pending threads, naming the count and the exact sweep
    command -- mirroring `bin/cli/memory.py`'s `_show_pointer_line2` for
    `memory show`. Same predicate as `gaia memory get-relevant --initiative`
    (class='thread', status IN carry_forward/open, superseded rows excluded)
    so the count printed here is the SAME count that command would return. A
    project with no initiative, or an initiative with zero live-pending rows,
    returns None -- a condition that always fires is not a condition. Never
    raises: any DB error is read as "nothing to say", matching the
    fail-fast-empty convention `_fetch_pending_vivo` already uses.
    """
    if not initiative:
        return None
    try:
        rows = con.execute(
            "SELECT COUNT(*) AS n FROM memory "
            "WHERE workspace = ? AND deleted_at IS NULL AND class = 'thread' "
            "  AND status IN ('carry_forward', 'open') AND initiative = ? "
            "  AND name NOT IN ("
            "    SELECT dst_name FROM memory_links "
            "    WHERE workspace = ? AND kind = 'supersedes'"
            "  )",
            (workspace, initiative, workspace),
        ).fetchone()
    except Exception:
        return None
    n = rows["n"] if rows else 0
    if not n:
        return None
    line = (
        f"> initiative '{initiative}': {n} more live-pending -- sweep with "
        f"`gaia memory get-relevant --initiative {initiative}` before writing."
    )
    if len(line) <= _PROJECT_PENDING_FOOTER_MAX:
        return line
    # Hard cap: drop the descriptive prefix but keep the sweep command whole
    # and runnable -- a truncated command cannot be run, and the line exists
    # so there is one to run.
    short = (
        f"> {n} pendientes vivos -- "
        f"`gaia memory get-relevant --initiative {initiative}`"
    )
    return short if len(short) <= _PROJECT_PENDING_FOOTER_MAX else None


def _project_close_candidates(con, name: str, workspace: str | None) -> list:
    """Fuzzy fallback candidates when NAME resolves to nothing at all.

    Pools both `projects.name` and the basename of `projects.path`, scoped to
    --workspace when given, else every workspace -- the same two identifiers
    `_resolve_project` itself matches against, so a near-miss on either
    surfaces here instead of a bare "not found".
    """
    import difflib

    if workspace:
        rows = con.execute(
            "SELECT workspace, name, path FROM projects WHERE workspace = ?",
            (workspace,),
        ).fetchall()
    else:
        rows = con.execute("SELECT workspace, name, path FROM projects").fetchall()

    labels: list = []
    lookup: list = []
    for r in rows:
        labels.append(r["name"])
        lookup.append((r["workspace"], r["name"]))
        if r["path"]:
            base = Path(r["path"]).name
            if base != r["name"]:
                labels.append(base)
                lookup.append((r["workspace"], r["name"]))

    close = difflib.get_close_matches(name, labels, n=5, cutoff=0.4)
    seen: set = set()
    candidates: list = []
    for label in close:
        idx = labels.index(label)
        ws, nm = lookup[idx]
        if (ws, nm) in seen:
            continue
        seen.add((ws, nm))
        candidates.append({"workspace": ws, "name": nm})
    return candidates


def _project_memory_sweep_command(initiative: str | None, name: str) -> str:
    """The exact command that shows the FULL memory index, for the overflow
    line below the capped top-N. Prefers the initiative sweep (the same
    corpus this section drew from); falls back to a name search only in the
    edge case where the index matched by `project_ref` alone with no
    derivable initiative -- in practice this cannot happen for a non-empty
    index (a `project_ref` match requires a resolvable `project_identity`,
    which always derives a non-None initiative), but the fallback keeps the
    function total rather than assuming that invariant here too.
    """
    if initiative:
        return f"gaia memory get-relevant --initiative {initiative}"
    return f"gaia memory search {name}"


def _resolve_project(con, name: str, workspace: str | None) -> dict:
    """Resolve NAME (and optional --workspace) to exactly one `projects` row.

    Two match passes, in order -- exact `projects.name` first, basename of
    `projects.path` second (the fix for the legacy opaque-slot rows: a
    project scanned as `bildwiz-5` whose real repo is `control-tower-livekit`
    is found when the user names the repo they actually know).

    Returns one of:
      {"status": "resolved", "row": {...}, "resolved_via": str}
      {"status": "ambiguous", "candidates": [{"workspace", "name", "path"}, ...]}
      {"status": "not_found", "candidates": [...], "hint": str | None}
    """
    if workspace:
        exact = con.execute(
            "SELECT * FROM projects WHERE workspace = ? AND name = ?",
            (workspace, name),
        ).fetchall()
    else:
        exact = con.execute(
            "SELECT * FROM projects WHERE name = ?", (name,),
        ).fetchall()

    if len(exact) == 1:
        return {"status": "resolved", "row": dict(exact[0]), "resolved_via": "exact name match"}
    if len(exact) > 1:
        return {
            "status": "ambiguous",
            "candidates": [
                {"workspace": r["workspace"], "name": r["name"], "path": r["path"]}
                for r in exact
            ],
        }

    if workspace:
        rows = con.execute(
            "SELECT * FROM projects WHERE workspace = ? AND path IS NOT NULL",
            (workspace,),
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM projects WHERE path IS NOT NULL",
        ).fetchall()

    basename_matches = [r for r in rows if Path(r["path"]).name == name]
    if len(basename_matches) == 1:
        r = basename_matches[0]
        via = (
            f"basename of path (stored name differs: {r['name']!r})"
            if r["name"] != name
            else "basename of path"
        )
        return {"status": "resolved", "row": dict(r), "resolved_via": via}
    if len(basename_matches) > 1:
        return {
            "status": "ambiguous",
            "candidates": [
                {"workspace": r["workspace"], "name": r["name"], "path": r["path"]}
                for r in basename_matches
            ],
        }

    hint = None
    if workspace:
        other = con.execute(
            "SELECT DISTINCT workspace FROM projects WHERE name = ?", (name,),
        ).fetchall()
        if other:
            hint = ", ".join(repr(r["workspace"]) for r in other)

    return {
        "status": "not_found",
        "candidates": _project_close_candidates(con, name, workspace),
        "hint": hint,
    }


def _cmd_project(args) -> int:
    """Handle `gaia context project NAME [--workspace W] [--json]`.

    NEVER writes -- not `projects`, not `project_facets`, not
    `project_context_contracts`, not `memory` (no telemetry bump either) --
    so it is safe to point at any name, resolved or not. Reads the one-project
    ficha end to end: the `projects` row, its `project_facets`, its
    `project_identity` project-context contract entry (if any), and an INDEX
    (slug + description only, never a body) of the curated `memory` rows
    anchored to it by `project_ref` or `initiative`. The index ANNOUNCES the
    corpus rather than dumping it: capped to `_PROJECT_MEMORY_INDEX_TOP_N`
    (10) most-recently-updated rows -- a NULL `updated_at` sorts last, never
    as freshest -- with a counted overflow line naming the total and the
    exact sweep command when there are more. `--json` applies the identical
    cap and never claims completeness by omission: `memory_index_total` and
    `memory_index_truncated` are always present, and
    `memory_index_sweep_command` is added when truncated.

    Resolution tries an EXACT `projects.name` match first, then the basename
    of `projects.path` -- the fix for a legacy row scanned under an opaque
    slot name (e.g. `bildwiz-5`) whose real repo is the basename the user
    actually names (e.g. `control-tower-livekit`); the response says so
    explicitly rather than resolving silently. Failure semantics: an EMPTY
    field and an ABSENT one are never the same response --
      * resolved (exactly one row, by either pass): exit 0, full ficha.
      * ambiguous (the SAME name/basename matches rows in more than one
        workspace, or more than one row in the scope given): exit 1, listing
        every candidate with its workspace -- the error enumerates rather
        than guessing.
      * not found: exit 1, with the closest names/basenames found in scope
        (and, when --workspace narrowed the search, whether the exact name
        exists in another workspace instead).

    Composes with `gaia context get-contract --section project_identity` for
    the WHOLE workspace contract (this command surfaces only the one entry
    that matches), `gaia memory show <slug>` for a memory row's full body
    (this command's memory section is an index, never a body), and
    `gaia context scan` to refresh the underlying `projects`/`project_facets`
    rows before reading them again.
    """
    name = getattr(args, "name", None)
    if not name:
        print("gaia context project: NAME is required", file=sys.stderr)
        return 2

    workspace = getattr(args, "workspace", None)
    as_json = getattr(args, "json", False)

    try:
        from gaia.store.writer import _connect as _store_connect
    except Exception as exc:  # pragma: no cover -- import wiring failure
        print(f"gaia context project: failed to import store: {exc}", file=sys.stderr)
        return 1

    con = _store_connect()
    try:
        resolution = _resolve_project(con, name, workspace)

        if resolution["status"] == "ambiguous":
            candidates = resolution["candidates"]
            if as_json:
                print(json.dumps(
                    {"error": "ambiguous", "name": name, "candidates": candidates},
                    indent=2, default=str,
                ))
            else:
                print(
                    f"gaia context project: '{name}' is ambiguous -- matches "
                    f"more than one project. Candidates:",
                    file=sys.stderr,
                )
                for c in candidates:
                    print(f"  {c['workspace']}/{c['name']}  ({c['path']})", file=sys.stderr)
                print(
                    "Use --workspace=<workspace> to disambiguate.",
                    file=sys.stderr,
                )
            return 1

        if resolution["status"] == "not_found":
            candidates = resolution["candidates"]
            hint = resolution.get("hint")
            if as_json:
                out = {"error": "not_found", "name": name, "candidates": candidates}
                if hint:
                    out["hint"] = (
                        f"'{name}' not found in workspace {workspace!r}, but "
                        f"exists in: {hint}"
                    )
                print(json.dumps(out, indent=2, default=str))
            else:
                if hint:
                    print(
                        f"gaia context project: '{name}' not found in workspace "
                        f"{workspace!r}, but exists in: {hint} -- use "
                        f"--workspace=<workspace> to resolve it there.",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"gaia context project: '{name}' not found"
                        + (f" in workspace {workspace!r}" if workspace else "")
                        + ".",
                        file=sys.stderr,
                    )
                if candidates:
                    print("Closest names:", file=sys.stderr)
                    for c in candidates:
                        print(f"  {c['workspace']}/{c['name']}", file=sys.stderr)
            return 1

        # resolved
        row = resolution["row"]
        resolved_via = resolution["resolved_via"]
        r_workspace = row["workspace"]
        r_name = row["name"]

        facets = [
            dict(f) for f in con.execute(
                "SELECT scope, key, value FROM project_facets "
                "WHERE workspace = ? AND project = ? ORDER BY scope, key",
                (r_workspace, r_name),
            ).fetchall()
        ]

        contract_slug = None
        contract_entry = None
        contract_row = con.execute(
            "SELECT payload FROM project_context_contracts "
            "WHERE workspace = ? AND contract_name = 'project_identity'",
            (r_workspace,),
        ).fetchone()
        if contract_row is not None:
            try:
                payload = json.loads(contract_row["payload"]) if contract_row["payload"] else {}
            except (json.JSONDecodeError, TypeError):
                payload = {}
            contract_slug, contract_entry = _project_match_identity_entry(
                payload, row.get("path"), row.get("remote_url"),
            )

        initiative = _project_derive_initiative(row.get("project_identity"))
        project_ref = row.get("project_identity")
        memory_rows = [
            dict(m) for m in con.execute(
                "SELECT name, type, description FROM memory "
                "WHERE workspace = ? AND deleted_at IS NULL "
                "  AND (project_ref = ? OR (initiative IS NOT NULL AND initiative = ?)) "
                "ORDER BY COALESCE(updated_at, '') DESC",
                (r_workspace, project_ref, initiative),
            ).fetchall()
        ]

        footer = _project_pending_footer(con, r_workspace, initiative)

        memory_total = len(memory_rows)
        memory_shown = memory_rows[:_PROJECT_MEMORY_INDEX_TOP_N]
        memory_truncated = memory_total > len(memory_shown)

        if as_json:
            out = {
                "project": row,
                "resolved_via": resolved_via,
                "facets": facets,
                "project_identity_contract": (
                    {"slug": contract_slug, "entry": contract_entry}
                    if contract_slug is not None else None
                ),
                # Capped identically to the human view -- the SAME context
                # cost applies to a JSON payload read in conversation. Never
                # silent about it: `memory_index_truncated` is always present
                # (never inferred from array length alone), and `_total` is
                # the real count regardless of how many are shown.
                "memory_index": memory_shown,
                "memory_index_total": memory_total,
                "memory_index_truncated": memory_truncated,
            }
            if memory_truncated:
                out["memory_index_sweep_command"] = _project_memory_sweep_command(
                    initiative, r_name,
                )
            if footer:
                out["pending_footer"] = footer
            print(json.dumps(out, indent=2, default=str))
            return 0

        print(f"workspace        : {r_workspace}")
        print(f"name             : {r_name}")
        print(f"resolved_via     : {resolved_via}")
        print(f"group            : {row.get('group_name') or '(none)'}")
        print(f"path             : {row.get('path') or '(unknown)'}")
        print(f"remote_url       : {row.get('remote_url') or '(none)'}")
        print(f"platform         : {row.get('platform') or '(unknown)'}")
        print(f"role             : {row.get('role') or '(unknown)'}")
        print(f"primary_language : {row.get('primary_language') or '(unknown)'}")
        print(f"status           : {row.get('status')}")
        if row.get("missing_since"):
            print(f"missing_since    : {row['missing_since']}")

        print()
        if facets:
            print(f"project_facets ({len(facets)}):")
            for f in facets:
                label = f"{f['scope']}.{f['key']}"
                print(f"  {label:<28}  {f['value'] if f['value'] is not None else '(none)'}")
        else:
            print("project_facets    : (none)")

        print()
        if contract_slug is not None:
            print(f"project_identity contract (slug: {contract_slug}):")
            for k, v in contract_entry.items():
                print(f"  {k:<16}  {v}")
        else:
            print("project_identity contract : (no entry found)")

        print()
        if memory_rows:
            label = (
                f"curated memory ({memory_total}, showing {len(memory_shown)} "
                f"most recent)" if memory_truncated
                else f"curated memory ({memory_total})"
            )
            print(f"{label} -- index only, use `gaia memory show <slug>` "
                  f"for the full body:")
            for m in memory_shown:
                desc = m.get("description") or ""
                print(f"  - {m['name']}: {desc}" if desc else f"  - {m['name']}")
            if memory_truncated:
                sweep = _project_memory_sweep_command(initiative, r_name)
                print(f"  ... and {memory_total - len(memory_shown)} more -- "
                      f"full index: `{sweep}`")
        else:
            print("curated memory    : (none)")

        if footer:
            print()
            print(footer)

        return 0
    finally:
        con.close()


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
    """Handle `gaia context wipe --workspace W [--yes] [--purge-memory]`.

    Deletes the scannable rows for the workspace (CASCADE removes children).
    scan-v2 SV3: curated memory is PRESERVED across the wipe by default; pass
    --purge-memory to also destroy it (explicit human curation). Requires
    interactive confirmation unless --yes is passed.
    """
    workspace = getattr(args, "workspace", None)
    if not workspace:
        print("gaia context wipe: --workspace is required", file=sys.stderr)
        return 2

    purge_memory = getattr(args, "purge_memory", False)
    if not getattr(args, "yes", False):
        mem_note = (
            "curated memory WILL ALSO BE DESTROYED"
            if purge_memory
            else "curated memory will be PRESERVED"
        )
        try:
            ans = input(
                f"gaia context wipe: about to delete workspace {workspace!r} "
                f"rows ({mem_note}).\n"
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

    purge_memory = getattr(args, "purge_memory", False)
    try:
        # scan-v2 SV3 (Vector 4): preserve curated memory across the wipe by
        # default. --purge-memory is the explicit-human-curation escape hatch
        # that restores the original full CASCADE (memory destroyed).
        wipe_workspace(workspace, preserve_memory=not purge_memory)
    except Exception as exc:
        print(f"gaia context wipe: error: {exc}", file=sys.stderr)
        return 1

    if purge_memory:
        print(f"Wiped workspace (memory PURGED): {workspace}")
    else:
        print(f"Wiped workspace (memory preserved): {workspace}")
    return 0


def _backup_db(db: Path) -> Path:
    """Copy the live gaia.db to a timestamped backup next to it and return the
    backup path. Called before any prune delete so the operation is reversible.
    """
    import shutil
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = db.with_name(f"{db.name}.{ts}.prune.bak")
    shutil.copy2(str(db), str(backup))
    return backup


def _render_prune(plan: dict) -> None:
    """Human render of a prune plan/result dict."""
    verb = "pruned" if plan["mode"] == "apply" else "would prune"
    print(f"scanned workspaces : {plan['scanned']}")
    print(f"{verb} (phantom, 0 projects, 0 curated collateral): {len(plan['pruned'])}")
    for ws in plan["pruned"]:
        print(f"  - {ws}")
    if plan["held"]:
        print(f"held (0 projects but HOLD curated collateral -- NOT deleted): "
              f"{len(plan['held'])}")
        for h in plan["held"]:
            print(f"  ! {h['workspace']}: memory={h['memory']} pcc={h['pcc']} "
                  f"briefs={h['briefs']} -- {h['reason']}")


def _cmd_prune_workspaces(args) -> int:
    """Handle ``gaia context prune-workspaces [--dry-run] [--yes] [--json]``.

    Deletes PHANTOM workspaces (0 projects AND 0 curated collateral -- no live
    memory, no PCC, no briefs). A zero-project workspace that DOES hold curated
    collateral is HELD (reported, never deleted). Backs up the DB before any
    delete. ``--dry-run`` shows the plan without mutating.
    """
    dry_run = getattr(args, "dry_run", False)
    as_json = getattr(args, "json", False)

    try:
        from gaia.store.writer import prune_empty_workspaces
        from gaia.paths import db_path as _db_path
    except Exception as exc:
        print(f"gaia context prune-workspaces: failed to import store: {exc}",
              file=sys.stderr)
        return 1

    # Always compute the plan read-only first (no mutation).
    plan = prune_empty_workspaces(apply=False)

    if dry_run:
        if as_json:
            print(json.dumps(plan, indent=2, default=str))
        else:
            _render_prune(plan)
        return 0

    if not plan["pruned"]:
        if as_json:
            print(json.dumps(plan, indent=2, default=str))
        else:
            print("Nothing to prune (no phantom workspaces without curated "
                  "collateral).")
            _render_prune(plan)
        return 0

    # Confirmation before a real delete (unless --yes).
    if not getattr(args, "yes", False):
        try:
            ans = input(
                f"gaia context prune-workspaces: about to DELETE "
                f"{len(plan['pruned'])} phantom workspace(s): "
                f"{', '.join(plan['pruned'])}.\n"
                f"The DB will be backed up first. Type 'yes' to confirm: "
            )
        except EOFError:
            ans = ""
        if ans.strip().lower() != "yes":
            print("Aborted (no confirmation).")
            return 1

    # Back up the DB before mutating -- reversibility guarantee.
    backup = _backup_db(_db_path())

    result = prune_empty_workspaces(apply=True)
    result["backup"] = str(backup)

    if as_json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _render_prune(result)
        print(f"DB backup written to: {backup}")
    return 0


def _cmd_move_project(args) -> int:
    """Handle ``gaia context move-project --decision D --from-workspace W1
    --from-name N1 --to-workspace W2 --to-name N2 [--dry-run] [--yes] [--json]``.

    EXECUTES a human-adjudicated scan-v2 move_candidate. `gaia scan` only
    DETECTS and REPORTS a move (as `move_candidates`); this is the write path
    that resolves one:

      --decision movido    -> re-key / supersede the OLD projects row to link it
                              to the successor (writes `superseded_by`, never
                              hard-deletes). On re-key, every scanner-owned
                              child row (project_facets, apps, services, ...)
                              is migrated to the new (workspace, name) in the
                              same transaction -- see `children_migrated` in
                              the JSON result. Curated memory / contracts are
                              only PROPOSED for relocation, not auto-moved --
                              use `move-memory` / `move-contracts` for those.
      --decision duplicado -> structural no-op: both rows are legitimately
      --decision worktree     independent and are left exactly as they are.

    Named with a leading `move` token so it hyphen-splits to a MUTATIVE_VERB and
    gates as T3 without any security-layer change (same convention as
    `move-contracts` / `move-memory`); `--dry-run` downgrades to a preview.
    """
    decision = getattr(args, "decision", None)
    from_ws = getattr(args, "from_workspace", None)
    from_name = getattr(args, "from_name", None)
    to_ws = getattr(args, "to_workspace", None)
    to_name = getattr(args, "to_name", None)
    dry_run = getattr(args, "dry_run", False)
    as_json = getattr(args, "json", False)

    if decision not in ("movido", "duplicado", "worktree"):
        print(
            "gaia context move-project: --decision must be one of "
            "movido/duplicado/worktree",
            file=sys.stderr,
        )
        return 2
    if not all((from_ws, from_name, to_ws, to_name)):
        print(
            "gaia context move-project: --from-workspace/--from-name/"
            "--to-workspace/--to-name are all required",
            file=sys.stderr,
        )
        return 2

    # duplicado / worktree: legitimate independent rows -> structural no-op.
    if decision in ("duplicado", "worktree"):
        result = {
            "status": "noop",
            "decision": decision,
            "from": {"workspace": from_ws, "name": from_name},
            "to": {"workspace": to_ws, "name": to_name},
            "reason": (
                f"decision {decision!r}: both rows are legitimately independent "
                "-- left untouched (no re-key, no supersede)."
            ),
        }
        if as_json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"[{decision}] no-op: left both project rows untouched "
                  f"({from_ws}/{from_name} and {to_ws}/{to_name}).")
        return 0

    # movido:
    try:
        from gaia.store.writer import resolve_move_candidate
    except Exception as exc:
        print(f"gaia context move-project: failed to import store: {exc}", file=sys.stderr)
        return 1

    try:
        preview = resolve_move_candidate(
            from_ws, from_name, to_ws, to_name, dry_run=True,
        )
    except ValueError as exc:
        print(f"gaia context move-project: {exc}", file=sys.stderr)
        return 1

    if dry_run:
        if as_json:
            print(json.dumps(preview, indent=2, default=str))
        else:
            print(f"[dry-run] move-project (movido) {from_ws}/{from_name} -> "
                  f"{to_ws}/{to_name}:")
            print(f"  action        : {preview['action']}")
            print(f"  superseded_by : {preview['superseded_by']}")
            rel = preview["proposed_relocations"]
            print(f"  proposed relocations (NOT auto-moved): "
                  f"memory={rel['memory']} contracts={rel['contracts']}")
            print("  -> relocate collateral with `gaia context move-memory` / "
                  "`move-contracts` if desired.")
            if preview["action"] == "rekeyed":
                print("  -> child rows (project_facets, apps, services, ...) "
                      "will be migrated to the new key on apply; counts are "
                      "not computed in this preview.")
        return 0

    if not getattr(args, "yes", False):
        print(f"About to resolve move (movido): re-key/supersede "
              f"{from_ws}/{from_name} -> {to_ws}/{to_name} "
              f"(action={preview['action']}, no hard-delete).")
        try:
            ans = input("Type 'yes' to confirm: ")
        except EOFError:
            ans = ""
        if ans.strip().lower() != "yes":
            print("Aborted (no confirmation).")
            return 1

    try:
        result = resolve_move_candidate(from_ws, from_name, to_ws, to_name)
    except ValueError as exc:
        print(f"gaia context move-project: {exc}", file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps(result, indent=2, default=str))
    else:
        rel = result["proposed_relocations"]
        print(f"Resolved move (movido): {from_ws}/{from_name} -> {to_ws}/{to_name} "
              f"(action={result['action']}, superseded_by={result['superseded_by']}).")
        print(f"Proposed (NOT moved): memory={rel['memory']} contracts={rel['contracts']} "
              f"still keyed to {from_ws!r} -- relocate with move-memory/move-contracts.")
        if result["action"] == "rekeyed":
            children = result["children_migrated"]
            if children:
                detail = ", ".join(f"{table}={n}" for table, n in sorted(children.items()))
                print(f"Children migrated: {detail}")
            else:
                print("Children migrated: none (row had no child rows).")
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
        help="Show a specific section of the WORKSPACE SHAPE (apps/services/"
             "stack/git/...) -- NOT a project-context contract name; for "
             "that use `gaia context get-contract`",
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
            help="Filter output to a single WORKSPACE-SHAPE section "
                 "(top-level or nested under workspace.*) -- NOT a "
                 "project-context contract name; for that use "
                 "`gaia context get-contract`",
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
        p.add_argument(
            "--include-missing",
            dest="include_missing",
            action="store_true",
            default=False,
            help="Also emit soft-deleted rows (status='missing') with their "
                 "missing_since; the default active view hides them",
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

    # gaia context get-contract  (read ONE project-context contract by name)
    # Read-only counterpart to `move-contracts`: resolves --section against
    # project_context_contracts.contract_name, the same names an agent's
    # can_read/can_write kernel menu lists. `get`/`show` never reach this
    # table -- see the module docstring for the two-namespaces trap.
    gc_parser = ctx_subparsers.add_parser(
        "get-contract",
        help="Read ONE project-context contract by name (project_identity, "
             "stack, ...) -- the can_read/can_write namespace, not the "
             "workspace-shape `get`/`show` use",
    )
    gc_parser.add_argument(
        "--workspace",
        metavar="W",
        default=None,
        help="Workspace identity owning the contract (default: "
             "gaia.project.current())",
    )
    gc_parser.add_argument(
        "--section",
        metavar="CONTRACT_NAME",
        required=True,
        help="project_context_contracts.contract_name to read -- the exact "
             "name as it appears in can_read/can_write (project_identity, "
             "stack, infrastructure, git, environment, ...)",
    )
    gc_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit JSON (default)",
    )
    gc_parser.add_argument(
        "--text",
        action="store_true",
        default=False,
        help="Emit human-readable presentation (workspace/contract_name/"
             "updated_at header, then the payload)",
    )

    # gaia context project NAME  (read-only, one-project ficha)
    proj_parser = ctx_subparsers.add_parser(
        "project",
        help="Read-only ficha for ONE project: row, facets, project_identity "
             "contract entry, curated-memory index -- resolves by exact name "
             "or basename of path",
        description=(
            "Print the full read-only ficha of ONE project: its `projects` "
            "row, `project_facets`, the matching `project_identity` "
            "project-context contract entry (if any), and an INDEX (slug + "
            "description only, NEVER a body) of curated `memory` rows "
            "anchored to it by project_ref or initiative -- the index "
            "ANNOUNCES the corpus, it does not dump it: capped to the 10 "
            "most-recently-updated rows (a NULL updated_at sorts LAST, never "
            "read as 'freshest'); when there are more, a counted overflow "
            "line names the total and the exact sweep command "
            "(`gaia memory get-relevant --initiative <key>`) instead of "
            "silently truncating. --json applies the SAME cap and never "
            "claims completeness by omission: it always carries "
            "`memory_index_total` and `memory_index_truncated`, plus "
            "`memory_index_sweep_command` when truncated. NEVER writes -- "
            "not the row, not a facet, not the contract, not memory, no "
            "telemetry bump either -- so it is safe to point at any name, "
            "resolved or not. Resolution tries an EXACT `projects.name` "
            "match first, then the BASENAME of `projects.path` second -- the "
            "fix for a legacy row scanned under an opaque slot name (e.g. "
            "`bildwiz-5`) whose real repo is the basename a user actually "
            "names (e.g. `control-tower-livekit`); a basename resolution "
            "says so explicitly rather than resolving silently. Failure "
            "semantics: found (exactly one row, either pass) exits 0 with "
            "the full ficha; AMBIGUOUS (the same name/basename matches rows "
            "in more than one workspace, or more than one row in the given "
            "scope) exits 1 listing every candidate with its workspace; NOT "
            "FOUND exits 1 with the closest names/basenames in scope (and, "
            "when --workspace narrowed the search, whether the exact name "
            "exists in another workspace instead) -- an empty section and an "
            "absent one are never the same response. Composes with `gaia "
            "context get-contract --section project_identity` for the WHOLE "
            "workspace contract (this prints only the one matching entry), "
            "`gaia memory show <slug>` for a memory row's full body (this "
            "section is an index, never a body), `gaia memory get-relevant "
            "--initiative <key>` for the FULL memory corpus once the index "
            "is capped, and `gaia context scan` to refresh the "
            "projects/facets rows before reading them again."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  gaia context project gaia\n"
               "  gaia context project control-tower-livekit --workspace aaxis\n"
               "  gaia context project gaia --json\n",
    )
    proj_parser.add_argument(
        "name",
        metavar="NAME",
        help="Project name to resolve -- tried as an exact `projects.name` "
             "match first, then as the basename of `projects.path`",
    )
    proj_parser.add_argument(
        "--workspace",
        metavar="W",
        default=None,
        help="Scope resolution to one workspace (default: search every "
             "workspace)",
    )
    proj_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit JSON",
    )

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
    wipe_parser.add_argument(
        "--purge-memory",
        action="store_true",
        default=False,
        help="Also destroy curated memory (default: memory is preserved across "
             "the wipe). Explicit human curation only.",
    )

    # gaia context prune-workspaces [--dry-run] [--yes] [--json]
    prune_parser = ctx_subparsers.add_parser(
        "prune-workspaces",
        help="Delete PHANTOM workspaces (0 projects, 0 curated collateral); "
             "backs up the DB before any delete",
    )
    prune_parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Show the prune plan (phantoms + held) without deleting anything",
    )
    prune_parser.add_argument(
        "--yes", action="store_true", default=False,
        help="Skip interactive confirmation",
    )
    prune_parser.add_argument(
        "--json", action="store_true", default=False,
        help="Emit the plan/result as JSON",
    )

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

    # gaia context move-project --decision D --from-workspace/--from-name
    #                           --to-workspace/--to-name
    # (T3: the verb token 'move-project' hyphen-splits to 'move', which is in
    # MUTATIVE_VERBS -- gated as T3 without touching the security layer. This is
    # the write path that EXECUTES a scan-v2 move_candidate a human adjudicated.)
    mp_parser = ctx_subparsers.add_parser(
        "move-project",
        help="Resolve an adjudicated scan-v2 move_candidate (re-key a projects "
             "row, migrating its child rows, or supersede it)",
    )
    mp_parser.add_argument("--decision", required=True,
                           choices=("movido", "duplicado", "worktree"),
                           help="Human adjudication of the move_candidate")
    mp_parser.add_argument("--from-workspace", dest="from_workspace", metavar="W1", required=True,
                           help="Old row workspace (move_candidate from.workspace)")
    mp_parser.add_argument("--from-name", dest="from_name", metavar="N1", required=True,
                           help="Old row name (move_candidate from.project)")
    mp_parser.add_argument("--to-workspace", dest="to_workspace", metavar="W2", required=True,
                           help="Successor workspace (move_candidate to.workspace)")
    mp_parser.add_argument("--to-name", dest="to_name", metavar="N2", required=True,
                           help="Successor name (move_candidate to.project)")
    mp_parser.add_argument("--dry-run", action="store_true", default=False,
                           help="Preview the resolution without mutating")
    mp_parser.add_argument("--json", action="store_true", default=False,
                           help="Emit JSON")
    mp_parser.add_argument("--yes", action="store_true", default=False,
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
    if context_cmd == "get-contract":
        return _cmd_get_contract(args)
    if context_cmd == "project":
        return _cmd_project(args)
    if context_cmd == "query":
        return _cmd_query(args)
    if context_cmd == "wipe":
        return _cmd_wipe(args)
    if context_cmd == "prune-workspaces":
        return _cmd_prune_workspaces(args)
    if context_cmd == "move-contracts":
        return _cmd_move_contracts(args)
    if context_cmd == "move-memory":
        return _cmd_move_memory(args)
    if context_cmd == "move-project":
        return _cmd_move_project(args)

    # No sub-action: print help for the context subcommand
    import argparse

    tmp_parser = argparse.ArgumentParser(prog="gaia context")
    tmp_sub = tmp_parser.add_subparsers(dest="context_cmd", metavar="<action>")
    show_p = tmp_sub.add_parser("show", help="Display workspace SHAPE (tabular, from substrate)")
    show_p.add_argument("--section", metavar="SECTION")
    tmp_sub.add_parser("scan", help="Run project scanner").add_argument("--dry-run", action="store_true")
    get_p = tmp_sub.add_parser("get", help="Emit canonical workspace SHAPE as JSON (from substrate)")
    get_p.add_argument("--workspace", metavar="W")
    get_p.add_argument("--section", metavar="SECTION")
    get_p.add_argument("--json", action="store_true")
    get_p.add_argument("--text", action="store_true")
    get_p.add_argument("--include-missing", dest="include_missing", action="store_true")
    tmp_sub.add_parser("dump", help="(deprecated) alias for `get`").add_argument("--workspace", metavar="W")
    gc_p = tmp_sub.add_parser(
        "get-contract",
        help="Read ONE project-context CONTRACT by name -- the can_read/"
             "can_write namespace, not the workspace shape `get`/`show` use",
    )
    gc_p.add_argument("--workspace", metavar="W")
    gc_p.add_argument("--section", metavar="CONTRACT_NAME", required=True)
    gc_p.add_argument("--json", action="store_true")
    gc_p.add_argument("--text", action="store_true")
    proj_p = tmp_sub.add_parser(
        "project",
        help="Read-only ficha for ONE project (row, facets, contract entry, "
             "memory index) -- resolves by exact name or basename of path",
    )
    proj_p.add_argument("name", metavar="NAME")
    proj_p.add_argument("--workspace", metavar="W")
    proj_p.add_argument("--json", action="store_true")
    tmp_sub.add_parser("query", help="Read-only SELECT").add_argument("sql", metavar="SQL")
    wipe_p = tmp_sub.add_parser("wipe", help="(DESTRUCTIVE) Delete all rows for a workspace (CASCADE)")
    wipe_p.add_argument("--workspace", metavar="W", required=True)
    wipe_p.add_argument("--yes", action="store_true")
    tmp_parser.print_help()
    return 0
