"""Session manifest builders for SessionStart injection.

Phase 4 of the context-injection redesign moves what was previously emitted
on every UserPromptSubmit to a one-shot SessionStart manifest. The blocks
that move:

- Environment manifest (NEW) -- workspace identity, machine, gaia version,
  mode, cwd, plugin data dir. Stable for the lifetime of the session.

Pending approvals are NOT surfaced here. Cross-session surfacing of pendings
(the former [ACTIONABLE] block) has been removed entirely: the DB remains the
canonical pending store, TTL hygiene (approval_cleanup) keeps it free of
orphans, session-agnostic matching (check_db_semantic_grant) still authorizes
retried commands, and the user inspects/acts on pendings on demand through
`gaia approvals`.

UserPromptSubmit retains only sparse turn-time notices such as the first-run
welcome and unread-notification counter. Surface classification remains a
DB-backed diagnostic capability but is no longer injected into every turn.

Design constraints:

- Every builder is fail-safe: returns "" on any error, logs at debug.
- Builders never raise. SessionStart must succeed even if the manifest is empty.
- Security mode short-circuits to "" -- security plugin has no orchestrator
  routing layer to consume the manifest.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_gaia_version() -> Optional[str]:
    """Best-effort read of the installed gaia version.

    Walks up from this file until a package.json with a ``version`` field is
    found. Returns the version string or None if no readable package.json is
    on the ancestor chain. Never raises.
    """
    try:
        here = Path(__file__).resolve()
        for ancestor in here.parents:
            pkg = ancestor / "package.json"
            if pkg.is_file():
                try:
                    data = json.loads(pkg.read_text(encoding="utf-8"))
                except Exception:
                    return None
                version = data.get("version")
                if isinstance(version, str) and version:
                    return version
                return None
    except Exception:
        pass
    return None


def _describe_gaia_version(version: str) -> str:
    """Annotate *version* with the machine's local dev-build count, if any.

    A `gaia dev` build ships the same semver as the release it was packed from,
    so the bare version cannot distinguish the pristine release from the Nth
    local iteration of it; `gaia.dev_builds` keeps that count in a sidecar
    (never in the five version sources the release gate cross-checks).

    Returns *version* unchanged whenever the counter is absent, corrupt, or
    unreadable -- the same fail-to-silence discipline as the memory block, and
    the reason SessionStart cannot be broken by this annotation.
    """
    try:
        from gaia.dev_builds import describe_version
        return describe_version(version) or version
    except Exception as exc:
        logger.debug("dev-build label unavailable (non-fatal): %s", exc)
        return version


def _read_workspace_identity() -> Optional[str]:
    """Read the workspace name from the project_context_contracts table.

    Resolves the current workspace via ``gaia.project.current()`` then queries
    ``project_context_contracts`` for the ``project_identity`` contract's
    ``$.name`` payload field. Falls back to the matching ``workspaces.name``
    row when the payload lacks a name. Returns None when neither yields a
    usable identity. Never raises.
    """
    import sqlite3

    try:
        from gaia.project import containing_workspace
        from gaia.paths import db_path as _db_path

        workspace = containing_workspace()
        if not workspace:
            return None

        db_file = _db_path()
        if not db_file or not db_file.exists():
            return None

        con = sqlite3.connect(str(db_file))
        try:
            row = con.execute(
                """
                SELECT json_extract(payload, '$.name')
                FROM project_context_contracts
                WHERE workspace = ? AND contract_name = 'project_identity'
                """,
                (workspace,),
            ).fetchone()
            if row and row[0]:
                return row[0]

            row = con.execute(
                "SELECT name FROM workspaces WHERE name = ?",
                (workspace,),
            ).fetchone()
            if row and row[0]:
                return row[0]
        finally:
            con.close()
    except Exception as exc:
        logger.debug("workspace identity read failed (non-fatal): %s", exc)
    return None


def _machine_label() -> str:
    """Return a short machine label like ``hostname (Linux/x86_64)``.

    platform calls return "" rather than raise on unsupported OSes; we just
    glue the parts we have. Always returns a non-empty string -- worst case
    it's only the hostname or only the OS.
    """
    try:
        host = platform.node() or ""
        system = platform.system() or ""
        machine = platform.machine() or ""
        os_part = "/".join(p for p in (system, machine) if p)
        if host and os_part:
            return f"{host} ({os_part})"
        return host or os_part or "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def build_environment_block() -> str:
    """Render the Environment section: workspace, machine, gaia, paths.

    Returns "" if every subcomponent fails -- the block is purely informational
    and a half-filled block is worse than nothing. In practice cwd and
    machine_label always succeed, so this rarely happens.
    """
    try:
        workspace = _read_workspace_identity()
        machine = _machine_label()
        version = _read_gaia_version()
        cwd = str(Path.cwd())

        # Data dir resolution can fail under headless tests with no .claude/
        # tree; treat as soft-missing.
        try:
            from ..core.paths import find_claude_dir, get_plugin_data_dir
            plugin_root = str(find_claude_dir())
            data_dir = str(get_plugin_data_dir())
        except Exception:
            plugin_root = None
            data_dir = None

        lines = ["## Environment"]
        if workspace:
            # Not "Workspace": the bare word reads as "where we are working",
            # and this value is where GAIA is installed -- the orchestrator is
            # born in the installation's workspace (me) even when the session's
            # subject lives in another one. The value stays because memory and
            # the database are scoped by it; only the label was lying.
            lines.append(f"- Gaia workspace (memory/db scope): {workspace}")
        lines.append(f"- Machine: {machine}")
        if version:
            lines.append(f"- Gaia: {_describe_gaia_version(version)}")
        lines.append(f"- cwd: {cwd}")
        if plugin_root:
            lines.append(f"- Plugin root: {plugin_root}")
        if data_dir and data_dir != plugin_root:
            lines.append(f"- Data dir: {data_dir}")

        # Drop the block entirely if it would only be a header -- pure
        # decoration adds noise to the orchestrator prompt without value.
        if len(lines) <= 1:
            return ""
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("build_environment_block failed (non-fatal): %s", exc)
        return ""


def build_workspace_memory_block(
    workspace: Optional[str] = None,
    sections: Optional[list[str]] = None,
) -> str:
    """Top relevant curated memory for the workspace, bounded.

    Calls ``gaia memory get-relevant --workspace <X> --max-chars 1500`` and
    captures stdout. Returns markdown to inject in SessionStart
    additionalContext, or "" when there are no curated rows for the
    workspace, when the workspace cannot be inferred, or when the
    subprocess fails for any reason.

    v32 (cwd-INDEPENDENT). When ``sections`` is omitted the CLI emits the
    TRANSVERSAL INITIATIVE DIGEST: a cross-project worklist of live-pending
    threads (``class='thread'``, ``status`` in ``carry_forward``/``open``)
    grouped by ``memory.initiative``, ordered by recency, top-K initiatives
    with global + per-initiative overflow. It no longer anchors to the launch
    directory -- the digest is identical whether the session starts at a
    workspace root or inside one project.

    The orchestrator's SessionStart assembler (``build_session_context``)
    calls this builder TWICE: once with no ``sections`` for the digest above,
    and once more with ``sections=["anchor"]`` for the durable "About you /
    What I know" anchors (``class='anchor'``) -- see the ``sections``
    paragraph below. The two calls query DISJOINT DB classes (``thread`` vs
    ``anchor``), so nothing is duplicated between the two injected blocks.

    Budget: ``--max-chars`` is raised 800 -> 1500. The old 800 cap, combined
    with the retired cwd anchoring, truncated the block to a SINGLE project as
    soon as that project carried several pending threads (the monopoly the
    digest is designed to prevent). With one short line per initiative
    (~90-110 chars) plus header and pointer, ~10 initiatives need ~1500 chars;
    the CLI still self-trims the least-fresh initiatives into the overflow line
    if the budget is exceeded, so the cap stays hard.

    ``sections`` (optional): a subset of ``carry_forward``/``anchor``/
    ``thread_open``. When set, the CLI uses the class/status section renderer
    instead of the digest. The orchestrator's SessionStart assembler passes
    ``["anchor"]`` so its caller receives only the durable "About you / What
    I know" anchors -- never the session-scoped
    ``carry_forward``/``thread_open`` state, which is instead carried by the
    no-``sections`` digest call. (Dispatched subagents get their anchors from
    the kernel's ``build_memory_block``, not from this builder.) When set, it
    is forwarded verbatim as ``--sections`` to the CLI.

    Fail-safe: any error (subprocess timeout, non-zero exit, missing CLI,
    empty output) returns "". SessionStart must not block on memory.
    """
    import subprocess

    try:
        ws = workspace or _read_workspace_identity()
        if not ws:
            # Without a workspace we cannot scope the query; skip the block.
            return ""

        # Resolve the CLI: prefer the in-repo bin/gaia when present so the
        # hook works from any cwd, fall back to PATH lookup otherwise.
        cli_args: list[str]
        try:
            from ..core.paths import find_claude_dir
            claude_dir = find_claude_dir()
            # In-repo / symlinked layout: .claude/tools/gaia or PATH.
            cli_args = ["gaia"]
            _ = claude_dir  # documented dependency, future-proofing
        except Exception:
            cli_args = ["gaia"]

        cmd = cli_args + [
            "memory", "get-relevant",
            "--workspace", ws,
            "--max-chars", "1500",
        ]
        if sections:
            # This helper is called TWICE by the SessionStart assembler: once
            # with no sections (the digest, which carries the recoverable-
            # pointer footer), once with sections=["anchor"] for the durable
            # "About you / What I know" block. --no-pointer suppresses the
            # CLI's footer on this second call only, so the guide is emitted
            # once per manifest instead of twice verbatim -- and so it never
            # sits under a section its write/curate verbs (close a thread,
            # graduate, reclassify) don't apply to. A direct/agent invocation
            # of `gaia memory get-relevant --sections ...` outside SessionStart
            # never passes this flag and keeps the footer.
            cmd += ["--sections", ",".join(sections), "--no-pointer"]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            logger.debug(
                "build_workspace_memory_block: CLI exit=%d stderr=%s",
                result.returncode, (result.stderr or "")[:200],
            )
            return ""
        block = (result.stdout or "").strip()
        return block
    except Exception as exc:
        logger.debug(
            "build_workspace_memory_block failed (non-fatal): %s", exc
        )
        return ""


def _extract_projects_from_identity(
    payload: dict, workspace: str, path_lookup: dict
) -> list[tuple[str, str, str, str, str]]:
    """Pull ``(name, path, type, description, missing_since)`` tuples out of one payload.

    The contract stores two distinct shapes, and this normalizes both:

    * **Map shape** (hand-authored, e.g. the ``me`` workspace's AOS entry):
      a dict keyed by project slug, each value a dict with ``name`` and an
      absolute ``local_path``. Detected by the absence of a top-level ``name``
      and all values being dicts.
    * **Scanner shape** (e.g. bildwiz/nfi/qxo/rnd): a top-level ``name`` plus an
      optional ``workspace_repos`` list whose entries carry only a *relative*
      ``path``. The absolute path is not in the contract, so it is resolved
      from the ``projects`` table via ``path_lookup``.

    ``path_lookup`` resolves a name to an absolute path through three indexes,
    tried in descending strictness:

    1. ``by_name`` -- ``(workspace, name)`` -> path, an exact hit.
    2. ``by_basename`` -- the last path component -> path, and ONLY when that
       basename is unique across the whole ``projects`` table. This is what
       reunites a legacy contract (which names the repo by its DIRECTORY, e.g.
       ``bildwiz-iac``) with the current scan-promoted row (which names the same
       repo by its uniquified SLUG, e.g. ``bildwiz_2``). Ambiguous basenames are
       left unresolved rather than guessed.
    3. ``by_ws`` -- a per-workspace single-path fallback, so a name mismatch
       (contract says ``nfi`` but the project row is ``nfi-oro-com``) still
       resolves when the workspace holds exactly one project.

    Entries that cannot resolve a path are still returned (name only) -- the
    caller decides how to present them.

    ``type`` and ``description`` are carried alongside name and path when the
    payload holds them (both shapes expose these fields), so the rendered
    Projects block can label each entry (e.g. "aos-iac (terraform) — Terraform
    IaC for AOS GCP infra"). Either may be an empty string when absent.

    ``missing_since`` carries the vanished mark that promotion stamps on an
    entry whose repo left the disk (``tools/scan/promote.py``). The entry is
    still returned -- a repo that vanished is signal, so the block SHOWS the
    mark rather than filtering the entry out. Empty string when absent.
    """
    out: list[tuple[str, str, str, str, str]] = []
    by_name: dict = path_lookup.get("by_name", {})
    by_ws: dict = path_lookup.get("by_ws", {})
    by_basename: dict = path_lookup.get("by_basename", {})

    def _resolve(name: str) -> str:
        p = by_name.get((workspace, name))
        if p:
            return p
        # Directory-name match, unique across all workspaces (see docstring).
        p = by_basename.get(name)
        if p:
            return p
        # Single-project workspace: the one path we have is unambiguous.
        ws_paths = by_ws.get(workspace) or []
        if len(ws_paths) == 1:
            return ws_paths[0]
        return ""

    from gaia.identity_shape import (
        MISSING_MARK_KEY,
        classify_identity_shape,
        is_reserved_slug,
    )

    if classify_identity_shape(payload) == "map":
        for slug, v in payload.items():
            if is_reserved_slug(slug) or not isinstance(v, dict):
                continue
            name = v.get("name") or slug
            path = v.get("local_path") or _resolve(slug) or _resolve(name)
            ptype = (v.get("type") or "").strip()
            desc = (v.get("description") or "").strip()
            gone = (v.get(MISSING_MARK_KEY) or "").strip()
            out.append((name, path, ptype, desc, gone))
        return out

    repos = payload.get("workspace_repos")
    if isinstance(repos, list) and repos:
        for r in repos:
            if not isinstance(r, dict):
                continue
            name = r.get("name") or ""
            if not name:
                continue
            ptype = (r.get("type") or "").strip()
            desc = (r.get("description") or "").strip()
            gone = (r.get(MISSING_MARK_KEY) or "").strip()
            out.append((name, _resolve(name), ptype, desc, gone))
        return out

    name = payload.get("name") or workspace
    ptype = (payload.get("type") or "").strip()
    desc = (payload.get("description") or "").strip()
    gone = (payload.get(MISSING_MARK_KEY) or "").strip()
    out.append((name, _resolve(name), ptype, desc, gone))
    return out


def _workspace_root(paths: list[str]) -> str:
    """Longest common directory of *paths*, or "" when it cannot be derived.

    When the common prefix IS one of the project paths (a single-project group,
    or a group where one project nests inside another) the prefix is that
    project's own directory, which would make its relative path empty; step up
    one level so every member still renders as a non-empty relative path.
    """
    if not paths:
        return ""
    try:
        root = os.path.commonpath(paths)
    except (ValueError, TypeError):
        return ""
    if root in set(paths):
        root = os.path.dirname(root)
    return root


def _relative_to_root(path: str, root: str) -> str:
    """Path shown for a project inside its workspace group.

    Returns the portion of *path* below *root*, or *path* unchanged when it does
    not sit under *root* (so an out-of-tree entry is never silently rewritten
    into a misleading relative path).
    """
    if not path:
        return ""
    if not root:
        return path
    try:
        rel = os.path.relpath(path, root)
    except (ValueError, TypeError):
        return path
    if rel.startswith(".."):
        return path
    return rel


def build_projects_context_block(max_chars: int = 8000) -> str:
    """Render the active-context project index for the SessionStart manifest.

    This is NOT an index of every git repo on disk. The source is the set of
    projects that have **active project context** -- a ``project_identity`` row
    in ``project_context_contracts``. That filter is the point: it includes
    AOS (which lives only in the ``me`` workspace's hand-authored contract,
    with absolute ``local_path``) and, since the scan-promotion stage
    (``tools/scan/promote.py::promote_workspace``), also includes any scanned
    repo under ``me`` that passed the promotion gate (resolvable
    ``project_identity``, absolute path, ``status='active'``) and was merged
    into the contract as a scan-owned entry -- a cloned reference repo is only
    excluded here if it was never scanned or failed the gate. A flat or scanner
    (non-map) contract with more than one promotable project is auto-converted
    to a map (its old top-level metadata preserved under a reserved key), so
    those projects are included rather than held back. No path-prefix filtering
    is used.

    The block is HIERARCHICAL: a ``### <workspace> — <root>`` group per scanned
    workspace, with that workspace's projects underneath as
    ``- <name> (<type>): <path relative to root> — <description>``. The relative
    path is dropped entirely when it equals the project's name, which is the
    common case; the group root plus the name reconstruct it exactly, so nothing
    is lost and the repeated absolute prefix is not paid ~40 times.

    A project's group is the workspace that owns its ``projects`` row (the
    scan-verified physical truth), falling back to the contract's own workspace
    key for a hand-authored entry with no row.

    Two defects of the older flat list are fixed at the source rather than in
    the render:

    * **Duplicates.** Two generations of contract rows coexist -- the current
      scan-promoted map (keyed by uniquified slug, e.g. ``bildwiz_2``) and legacy
      per-directory scanner/flat rows (keyed by the repo's directory name, e.g.
      ``bildwiz-iac``, with no ``projects`` row under that workspace at all). The
      old ``(name, path)`` dedup key could not see the collision because BOTH
      components differed: one side had the slug and a path, the other the
      directory name and no path. Dedup is now keyed on the RESOLVED ABSOLUTE
      PATH -- a project's actual identity -- and the basename index in
      ``_extract_projects_from_identity`` is what lets the legacy side resolve
      to that path in the first place. Colliding entries are MERGED field by
      field, so metadata carried by only one of the two survives.
    * **Vanished repos.** An entry marked ``missing_since`` is not rendered at
      all. Nothing is deleted -- not from the ``projects`` table, not from the
      contract -- so the full record (path, type, description, exact timestamp)
      stays one ``gaia context get`` away for the rare turn that asks about a
      removed project. It is not worth a line of every session's context.

    Budget: bounded to ``max_chars`` (default 8000). On overflow, live projects
    are dropped from the tail and a recoverable footer stating the dropped count
    ALWAYS lands (footer space is reserved before trimming). Under budget
    pressure the explanatory note is dropped before any data. Fail-safe: any
    error returns "".
    """
    # Ensure the package root (which holds the `gaia/` package) is importable.
    # At real SessionStart, session_start.py already inserts it; this self-heal
    # makes the builder robust when called from other entry points or tests.
    try:
        _pkg_root = str(Path(__file__).resolve().parents[3])
        if _pkg_root not in sys.path:
            sys.path.insert(0, _pkg_root)
    except Exception:
        pass

    try:
        from gaia.store.writer import _connect
    except Exception as exc:
        logger.debug("build_projects_context_block import failed: %s", exc)
        return ""

    try:
        con = _connect()
        try:
            identity_rows = con.execute(
                "SELECT workspace, payload FROM project_context_contracts "
                "WHERE contract_name = 'project_identity' ORDER BY workspace"
            ).fetchall()
            # Path resolution sources: include missing rows -- the on-disk path
            # may still be valid even if the scanner marked the repo missing.
            proj_rows = con.execute(
                "SELECT workspace, name, path FROM projects WHERE path IS NOT NULL"
            ).fetchall()
        finally:
            con.close()
    except Exception as exc:
        logger.debug("build_projects_context_block query failed: %s", exc)
        return ""

    if not identity_rows:
        return ""

    by_name: dict = {}
    by_ws: dict = {}
    ws_of_path: dict = {}
    basename_hits: dict = {}
    for r in proj_rows:
        d = dict(r)
        p = d.get("path")
        if not p:
            continue
        by_name[(d["workspace"], d["name"])] = p
        by_ws.setdefault(d["workspace"], []).append(p)
        ws_of_path.setdefault(p, d["workspace"])
        basename_hits.setdefault(os.path.basename(p), set()).add(p)
    # A basename is only a usable key while it identifies exactly ONE path;
    # two repos sharing a directory name are left unresolved, never guessed.
    by_basename = {
        base: next(iter(paths))
        for base, paths in basename_hits.items()
        if len(paths) == 1
    }
    path_lookup = {
        "by_name": by_name,
        "by_ws": by_ws,
        "by_basename": by_basename,
    }

    # Dedup on the RESOLVED PATH -- a project's real identity. Two contract
    # generations name the same repo differently (promoted slug vs. directory
    # name), so a name-based key cannot see the collision. Path-less entries
    # fall back to a name key; they cannot collide with a path-keyed entry.
    merged: dict = {}
    order: list = []
    for r in identity_rows:
        d = dict(r)
        contract_ws = d.get("workspace") or ""
        try:
            payload = json.loads(d.get("payload") or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        for name, path, ptype, desc, gone in _extract_projects_from_identity(
            payload, contract_ws, path_lookup
        ):
            key = path or f"name:{name.lower()}"
            # The owning workspace is the one holding this path's projects row;
            # a hand-authored entry with no row keeps its contract's workspace.
            group = ws_of_path.get(path) or contract_ws
            prev = merged.get(key)
            if prev is None:
                merged[key] = {
                    "name": name, "path": path, "type": ptype,
                    "desc": desc, "gone": gone, "ws": group,
                    "is_ws_identity": not path and name.lower() == contract_ws.lower(),
                }
                order.append(key)
                continue
            # Same project reached twice: keep every field either side carries.
            for field, value in (
                ("type", ptype), ("desc", desc), ("gone", gone),
            ):
                if not prev[field] and value:
                    prev[field] = value

    if not merged:
        return ""

    # Split live from vanished, and pin the per-workspace group order. Roots are
    # computed from the LIVE paths only: a vanished repo's path should not widen
    # (or, as the sole member, define) the root every sibling renders against.
    live: list[dict] = []
    unresolved: list[str] = []
    group_order: list[str] = []
    for key in order:
        e = merged[key]
        ws = e["ws"]
        if e["is_ws_identity"]:
            # A flat contract whose name IS its own workspace key, with no path
            # resolvable anywhere in the projects table, is the workspace-identity
            # form of the contract (see gaia.identity_shape), not a project. It is
            # skipped as a misclassification rather than reported as a project
            # whose path was lost -- the contract row itself is untouched.
            continue
        if e["gone"]:
            # A vanished repo is not injected at all. It is asked for, on the
            # rare occasion someone wants one: `gaia context get` still has the
            # whole record, and nothing here deletes it. Injecting the names
            # every session spent budget on a question almost nobody asks.
            continue
        if ws and ws not in group_order:
            group_order.append(ws)
        if not e["path"]:
            unresolved.append(e["name"])
        else:
            live.append(e)

    roots: dict = {}
    for ws in group_order:
        roots[ws] = _workspace_root([e["path"] for e in live if e["ws"] == ws])

    total_available = len(live)
    header = "## Project Context — Projects"
    note = (
        "Paths are relative to each workspace root, and omitted when the "
        "directory matches the project name."
    )

    def _render(items: list[dict], with_note: bool = True) -> str:
        parts = [header]
        if with_note:
            parts.append(note)
        for ws in group_order:
            group = [e for e in items if e["ws"] == ws]
            if not group:
                continue
            root = roots.get(ws) or ""
            lines = [f"### {ws} — {root}" if root else f"### {ws}"]
            for e in group:
                label = f"{e['name']} ({e['type']})" if e["type"] else e["name"]
                shown = _relative_to_root(e["path"], root)
                # A relative path equal to the name is fully implied by the
                # group root; printing it would only repeat the name.
                line = (
                    f"- {label}: {shown}"
                    if shown and shown != e["name"]
                    else f"- {label}"
                )
                if e["desc"]:
                    line += f" — {e['desc']}"
                lines.append(line)
            parts.append("\n".join(lines))
        if unresolved:
            parts.append(
                f"unresolved ({len(unresolved)}): {', '.join(unresolved)} "
                f"— no path on disk; 'gaia context get'"
            )
        return "\n\n".join(parts)

    block = _render(live)
    # Budget: drop live projects from the tail until the block PLUS its footer
    # fits. The footer must never be lost -- a silent tail-drop with no footer
    # turns the projects index (a routing surface) into a lie about how many
    # projects exist. So we reserve the footer's worst-case width up front and
    # trim against ``max_chars - footer_budget``. Under pressure the note goes
    # first: it explains the data, so it is the cheapest thing to lose.
    if len(block) > max_chars:
        def _footer(n: int) -> str:
            return f"\n... ({n} more, use 'gaia context get')"

        footer_budget = len(_footer(total_available))
        trim_target = max(0, max_chars - footer_budget)

        kept = list(live)
        while kept and len(_render(kept, with_note=False)) > trim_target:
            kept.pop()
        dropped = total_available - len(kept)
        block = _render(kept, with_note=False)
        if dropped > 0:
            block = block + _footer(dropped)

    return block


def _load_surface_routing() -> dict:
    """Best-effort load of the surface routing config. Never raises.

    Routing moved from ``config/surface-routing.json`` (retired, git-rm'd) to
    the ``surface_routing`` table in gaia.db, seeded from each agent's
    ``routing:`` frontmatter block by ``tools/scan/seed_surface_routing.py``.
    This delegates to ``tools.context.surface_router.load_surface_routing_config``
    -- the same DB-backed loader ``surface_router.classify_surfaces`` uses --
    so this builder and the matcher never drift on where routing data comes
    from.

    Returns the same in-memory shape the retired JSON produced:
    ``{version, reconnaissance_agent, surfaces: {name: {primary_agent,
    contract_sections, ...}}}``. Returns ``{}`` on any import/query failure --
    callers treat an empty dict (or a degraded ``surfaces: {}``) as "no
    routing config" and emit no block.
    """
    try:
        pkg_root = Path(__file__).resolve().parents[3]
        tools_dir = pkg_root / "tools" / "context"
        if str(tools_dir) not in sys.path:
            sys.path.insert(0, str(tools_dir))
        from surface_router import load_surface_routing_config
        return load_surface_routing_config()
    except Exception:
        return {}


def build_contracts_index_block(max_chars: int = 2000) -> str:
    """Render a compact ``surface -> contract_sections`` index for SessionStart.

    DB-backed: reads the ``surface_routing`` table via ``_load_surface_routing``
    (which delegates to ``load_surface_routing_config``), not the retired
    ``config/surface-routing.json``. It tells the orchestrator which
    project-context sections each specialist surface will receive when
    dispatched -- section NAMES only, never their contents. This lets the
    orchestrator reason about what a target surface can see before spending a
    subagent, without duplicating the (potentially large) section bodies here.

    Format, one line per surface::

        - iac (platform-architect) → project_identity, stack, git, ...

    The ``primary_agent`` is included in parentheses when present because it is
    the concrete handle the orchestrator dispatches to; it is cheap (one token)
    and makes the surface actionable. Surfaces with no ``contract_sections`` are
    skipped -- an empty section list carries no signal.

    Budget: bounded to ``max_chars`` (default 2000). The full 7-surface index is
    ~1.25 KB today and is meant to land complete; the bound is a guard rail, not
    a target. On overflow, whole surface lines are dropped from the tail with a
    recoverable footer. Fail-safe: any error, a missing file, or an absent
    ``surfaces`` map returns "".
    """
    try:
        data = _load_surface_routing()
    except Exception as exc:
        logger.debug("build_contracts_index_block load failed: %s", exc)
        return ""

    surfaces = data.get("surfaces") if isinstance(data, dict) else None
    if not isinstance(surfaces, dict) or not surfaces:
        return ""

    entries: list[tuple[str, str, list[str]]] = []
    for name, cfg in surfaces.items():
        if not isinstance(cfg, dict):
            continue
        sections = cfg.get("contract_sections")
        if not isinstance(sections, list) or not sections:
            continue
        section_names = [str(s) for s in sections if isinstance(s, str) and s]
        if not section_names:
            continue
        agent = cfg.get("primary_agent")
        agent = str(agent) if isinstance(agent, str) and agent else ""
        entries.append((str(name), agent, section_names))

    if not entries:
        return ""

    total_available = len(entries)
    header = "## Project Context — Contract Index (per surface)"

    def _render(items: list[tuple[str, str, list[str]]]) -> str:
        lines = [header, ""]
        for name, agent, sections in items:
            label = f"{name} ({agent})" if agent else name
            lines.append(f"- {label} → {', '.join(sections)}")
        return "\n".join(lines)

    block = _render(entries)
    # Reserve the footer's worst-case width before trimming so a tail-drop can
    # never happen silently -- the footer that states how many surfaces were
    # omitted always lands. See FIX (b).
    if len(block) > max_chars:
        def _footer(n: int) -> str:
            return f"\n... ({n} more, inspect the DB-backed surface_routing registry)"

        footer_budget = len(_footer(total_available))
        trim_target = max(0, max_chars - footer_budget)

        kept = list(entries)
        while kept and len(_render(kept)) > trim_target:
            kept.pop()
        dropped = total_available - len(kept)
        block = _render(kept)
        if dropped > 0:
            block = block + _footer(dropped)

    return block


def build_task_notifications_block(
    workspace: Optional[str] = None,
    limit: int = 10,
) -> str:
    """Render a compact list of UNREAD headless-task notifications, one per line.

    A headless scheduled task (see the scheduled-task skill) leaves a report row
    via `gaia notifications add` when it finishes; it cannot ask the user
    anything, so this SessionStart block is how those reports surface. Each line
    carries task_name + headline + time + the resumable session_id, so the user
    can `claude --resume <session_id>` to grant any pending T3s. Read via
    `gaia notifications show <id>` for the full body; clear with
    `gaia notifications ack`.

    Scoped to the current workspace when one can be inferred, else all
    workspaces. Emits "" when there are no unread rows (zero-noise, like the
    per-prompt counter). Fail-safe: any error returns "".
    """
    try:
        _pkg_root = str(Path(__file__).resolve().parents[3])
        if _pkg_root not in sys.path:
            sys.path.insert(0, _pkg_root)
    except Exception:
        pass

    try:
        from gaia.store.reader import list_unread_notifications
    except Exception as exc:
        logger.debug("task_notifications import failed (non-fatal): %s", exc)
        return ""

    try:
        ws = workspace or _read_workspace_identity()
        rows = list_unread_notifications(workspace=ws, limit=limit)
        if not rows:
            return ""

        lines = ["## Task Notifications (unread)"]
        for r in rows:
            sid = r.get("session_id") or "-"
            when = r.get("created_at") or "?"
            lines.append(
                f"- [{r['id']}] {r['task_name']} — {r['headline']} "
                f"({when}) · resume: {sid}"
            )
        lines.append(
            "Read one: `gaia notifications show <id>` · "
            "resume: `claude --resume <session_id>` · clear: `gaia notifications ack <id>`"
        )
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("build_task_notifications_block failed (non-fatal): %s", exc)
        return ""


def _resume_hint(scope: Optional[str], task_name: Optional[str]) -> str:
    """The exact `gaia schedule resume` invocation that clears THIS suspension.

    Scope-specific, not offered as one interchangeable "<name>|--all" form: a
    task-scope suspension clears only by NAME, a global (workspace-wide) one
    only by `--all`. `resume_scheduled_tasks` (gaia.store.writer) looks the
    row up by `task_id`, and a global suspension's `task_id` is NULL --
    `resume <name>` finds no row to delete for it and returns
    `{"status": "not_suspended"}`, leaving the notice standing. This SessionStart
    block is the one channel a lapse cannot self-clear from, so the hint it
    prints must work verbatim -- a wrong one trains the user to ignore it.
    """
    if scope == "task" and task_name:
        return f"gaia schedule resume {task_name}"
    return "gaia schedule resume --all"


def build_schedule_suspension_block(
    workspace: Optional[str] = None,
) -> str:
    """Announce scheduled-task suspensions -- LIVE ones, and LAPSED ones louder.

    Two things must never happen quietly, and this block is where both are made
    audible at the one moment the user is guaranteed to be looking:

      * A task stays switched off because everyone forgot. So every LIVE
        suspension is announced with how long it has left.
      * A task starts running again without anyone noticing. So a LAPSED
        suspension -- deadline passed, tasks active again -- is announced FIRST
        and marked, because it is the entry that changed what runs.

    DETECT-ONLY, exactly like build_schedule_reconciliation_block. Reading a
    suspension is what expires it (a comparison against now, no daemon), and
    that read reactivates DESIRED state only: this hook does not install, does
    not reactivate the machine scheduler, and does not sync. A lapse therefore
    leaves the crontab exactly as the last consented `gaia schedule sync` left
    it -- if that sync had removed the entry, the drift block will say so and
    the user decides. A SessionStart hook cannot obtain T3 consent, so it may
    only report.

    The lapse notice is NOT self-clearing: it stands until an explicit `gaia
    schedule resume` acknowledges it, the same contract task_notifications has
    with `gaia notifications ack`. Emits "" when nothing is suspended
    (zero-noise). Fail-safe: returns "" on any error.
    """
    try:
        _pkg_root = str(Path(__file__).resolve().parents[3])
        if _pkg_root not in sys.path:
            sys.path.insert(0, _pkg_root)
    except Exception:
        pass

    try:
        from gaia.store.reader import list_schedule_suspensions
    except Exception as exc:
        logger.debug("schedule suspension import failed (non-fatal): %s", exc)
        return ""

    try:
        ws = workspace or _read_workspace_identity()
        rows = list_schedule_suspensions(workspace=ws)
        if not rows:
            return ""

        lapsed = [s for s in rows if s.get("expired")]
        live = [s for s in rows if not s.get("expired")]
        lines: list[str] = []

        if lapsed:
            lines.append("## Scheduled Tasks — SUSPENSION LAPSED (running again)")
            for s in lapsed:
                who = s.get("task_name") or "all tasks"
                resumed = ", ".join(s.get("resumed_names") or [])
                what = (f"active again: {resumed}" if resumed else
                        "nothing came back (still disabled, or held by another suspension)")
                hint = _resume_hint(s.get("scope"), s.get("task_name"))
                lines.append(
                    f"- ! {who} — suspension expired {s.get('lapsed_ago')} ago "
                    f"(deadline {s.get('until')}) — {what} — acknowledge: "
                    f"`{hint}` (T0)"
                )
            lines.append(
                "Nothing was reactivated by session start: the deadline simply "
                "stopped applying. Verify the machine: `gaia schedule status`"
            )

        if live:
            if lapsed:
                lines.append("")
            lines.append("## Scheduled Tasks (suspended)")
            for s in live:
                who = s.get("task_name") or "all tasks"
                window = ("suspended indefinitely (no deadline)"
                          if s.get("indefinite")
                          else f"suspended {s.get('remaining')} more "
                               f"(until {s.get('until')})")
                reason = f" — {s['reason']}" if s.get("reason") else ""
                hint = _resume_hint(s.get("scope"), s.get("task_name"))
                lines.append(
                    f"- {who} — {window}{reason} — lift early: `{hint}` (T0)"
                )
            lines.append("Inspect: `gaia schedule status`")

        return "\n".join(lines)
    except Exception as exc:
        logger.debug("build_schedule_suspension_block failed (non-fatal): %s", exc)
        return ""


def build_schedule_reconciliation_block(
    workspace: Optional[str] = None,
) -> str:
    """DETECT-ONLY drift between desired scheduled tasks (DB) and this machine.

    The consent boundary made visible: this block is READ-ONLY (T0) and
    zero-noise. It compares the desired state in gaia.db against the LOCAL
    scheduler for the current machine and, when they diverge, surfaces a compact
    "N tasks not installed here -> run `gaia schedule sync`" line. It NEVER writes
    the scheduler -- installing is `gaia schedule sync` (T3), which the user runs
    after seeing this. A SessionStart hook cannot obtain T3 consent, so it must
    only detect and advise, never materialize silently.

    Emits "" when fully reconciled (and the daemon looks healthy), matching the
    zero-noise contract of the notifications blocks. Fail-safe: returns "" on any
    error so it can never block session start.
    """
    try:
        _pkg_root = str(Path(__file__).resolve().parents[3])
        if _pkg_root not in sys.path:
            sys.path.insert(0, _pkg_root)
    except Exception:
        pass

    try:
        from gaia.schedulers import compute_plan
    except Exception as exc:
        logger.debug("schedule reconciliation import failed (non-fatal): %s", exc)
        return ""

    try:
        ws = workspace or _read_workspace_identity()
        plan = compute_plan(workspace=ws)
        if not plan.available:
            return ""  # no backend on this platform -> nothing to say

        daemon_down = plan.daemon is not None and plan.daemon.running is False
        if plan.in_sync and not daemon_down and not plan.invalid:
            return ""  # zero-noise: everything reconciled

        lines = [f"## Scheduled Tasks (drift on {plan.machine})"]
        if plan.missing:
            names = ", ".join(m["name"] for m in plan.missing)
            lines.append(f"- {len(plan.missing)} not installed here: {names}")
        if plan.drift:
            names = ", ".join(d["name"] for d in plan.drift)
            lines.append(f"- {len(plan.drift)} schedule drifted: {names}")
        if plan.orphans:
            lines.append(f"- {len(plan.orphans)} orphan entr(ies): {', '.join(plan.orphans)}")
        if plan.disabled_present:
            lines.append(
                f"- {len(plan.disabled_present)} disabled but still installed: "
                f"{', '.join(plan.disabled_present)}"
            )
        for iv in plan.invalid:
            lines.append(f"- INVALID {iv['name']}: {iv['error']}")
        if daemon_down:
            lines.append(f"- scheduler daemon: {plan.daemon.detail}")
        lines.append(
            "Reconcile with `gaia schedule sync` (T3) · inspect: `gaia schedule status`"
        )
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("build_schedule_reconciliation_block failed (non-fatal): %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Assembler
# ---------------------------------------------------------------------------

def build_session_context() -> str:
    """Top-level assembler. Concatenate non-empty blocks with blank lines.

    Returns "" when every block is empty. Never raises.
    """
    try:
        blocks = [
            build_environment_block(),
            # Project Context — Projects: the index of projects that have active
            # project context (a project_identity contract), each as name +
            # on-disk path. Emitted immediately after Environment so it reads as
            # part of the project-context setup the orchestrator receives -- it
            # lets a bare mention in memory (e.g. "AOS", "nfi") resolve to a
            # path the orchestrator already holds, without spending a subagent.
            build_projects_context_block(),
            # Project Context — Contract Index: which project-context sections
            # each specialist surface receives when dispatched (surface ->
            # contract_sections, DB-backed via surface_routing). Grouped right
            # after Projects because both are static "project-context setup"
            # blocks the orchestrator reads before routing/dispatch decisions --
            # this was implemented and tested (42a6231) but never wired into
            # the assembler until this fix.
            build_contracts_index_block(),
            # Unread headless-task notifications: a compact list of reports left
            # by scheduled tasks (task_name + headline + time + resumable
            # session_id). Emitted after the static project-context setup so the
            # user sees what ran unattended and can `claude --resume` to grant
            # pending T3s. Zero-noise: emits nothing when there are no unread rows.
            build_task_notifications_block(),
            # Scheduled-task drift: DETECT-ONLY (T0). When the desired state in
            # gaia.db diverges from this machine's local scheduler, surface a
            # compact "N not installed here -> gaia schedule sync" line. Zero-
            # noise when reconciled. The hook never writes the scheduler -- that
            # is the T3 `gaia schedule sync` the user runs after seeing this.
            build_schedule_reconciliation_block(),
            # Scheduled-task suspensions: DETECT-ONLY (T0). A LIVE suspension is
            # announced with the time it has left, so nothing stays switched off
            # by being forgotten; a LAPSED one is announced first and marked,
            # because it means tasks are running again. Placed after the drift
            # block so the two read together: the lapse says WHAT changed in
            # desired state, the drift block says whether this machine still
            # matches it. Zero-noise when nothing is suspended. Like the drift
            # block it never writes the scheduler -- reading is what expires a
            # suspension, and expiry restores desired state only.
            build_schedule_suspension_block(),
            # Pending approvals are no longer surfaced here. Cross-session
            # surfacing of pendings (the [ACTIONABLE] block) was removed: the
            # DB remains the pending store, TTL hygiene keeps it clean, and the
            # user inspects/acts on pendings on demand via `gaia approvals`.
            # Workspace Memory is injected last so the orchestrator sees the
            # operational state (environment, projects, schedule) before the
            # curated knowledge it should anchor against. Two calls, DISJOINT classes
            # so neither duplicates the other's tokens:
            #   1. No `sections` -> the transversal initiative digest, the
            #      live-pending worklist (class='thread', status in
            #      carry_forward/open).
            #   2. `sections=["anchor"]` -> the durable "About you / What I
            #      know" anchors (class='anchor'). This second call is the
            #      Bug-2 fix: d2fba1c (15 jul) moved the orchestrator's default
            #      call from the three-section renderer to the digest-only
            #      call above, dropping the anchors with no replacement. This
            #      restores them via the disjoint class so the orchestrator's
            #      durable "about you" facts are never silently lost again.
            build_workspace_memory_block(),
            build_workspace_memory_block(sections=["anchor"]),
        ]
        non_empty = [b for b in blocks if b]
        if not non_empty:
            return ""
        return "\n\n".join(non_empty)
    except Exception as exc:
        logger.debug("build_session_context failed (non-fatal): %s", exc)
        return ""
