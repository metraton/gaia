"""Session manifest builders for SessionStart injection.

Phase 4 of the context-injection redesign moves what was previously emitted
on every UserPromptSubmit to a one-shot SessionStart manifest. The blocks
that move:

- Environment manifest (NEW) -- workspace identity, machine, gaia version,
  mode, cwd, plugin data dir. Stable for the lifetime of the session.
- Agentic-loop resume -- if there is an active loop in cwd, surface it once
  at SessionStart instead of re-scanning every prompt.

Pending approvals are NOT surfaced here. Cross-session surfacing of pendings
(the former [ACTIONABLE] block) has been removed entirely: the DB remains the
canonical pending store, TTL hygiene (approval_cleanup) keeps it free of
orphans, session-agnostic matching (check_db_semantic_grant) still authorizes
retried commands, and the user inspects/acts on pendings on demand through
`gaia approvals`.

What does NOT move (stays in UserPromptSubmit):

- First-run welcome (one-shot, but tied to first user prompt of the install).
- Surface Routing Recommendation (depends on the prompt of the turn).

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
        from gaia.project import current as _project_current
        from gaia.paths import db_path as _db_path

        workspace = _project_current()
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
            lines.append(f"- Workspace: {workspace}")
        lines.append(f"- Machine: {machine}")
        if version:
            lines.append(f"- Gaia: {version}")
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


def build_agentic_loop_block() -> str:
    """Surface an active agentic-loop's resume context at SessionStart.

    Thin wrapper around ``agentic_loop_detector.build_resume_context`` --
    the detection logic is owned there because PreCompact also uses it.
    Wrapped fail-safe in case the detector raises for any reason.
    """
    try:
        from ..context.agentic_loop_detector import build_resume_context
        return build_resume_context() or ""
    except Exception as exc:
        logger.debug("build_agentic_loop_block failed (non-fatal): %s", exc)
        return ""


def build_workspace_memory_block(
    workspace: Optional[str] = None,
    sections: Optional[list[str]] = None,
) -> str:
    """Top relevant curated memory for the workspace, bounded.

    Calls ``gaia memory get-relevant --workspace <X> --max-chars 800`` and
    captures stdout. Returns markdown to inject in SessionStart
    additionalContext, or "" when there are no curated rows for the
    workspace, when the workspace cannot be inferred, or when the
    subprocess fails for any reason.

    ``sections`` (optional): a subset of ``carry_forward``/``anchor``/
    ``thread_open`` to render. When omitted (the orchestrator's SessionStart
    call), all three sections are emitted -- the orchestrator sees "For this
    session", "About you / What I know", and "Open threads" unchanged. The
    subagent-dispatch path passes ``["anchor"]`` so a dispatched subagent
    receives only the durable "About you / What I know" anchors, not the
    session-scoped carry_forward or open-thread state. When set, it is
    forwarded verbatim as ``--sections`` to the CLI.

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
            "--max-chars", "800",
        ]
        if sections:
            cmd += ["--sections", ",".join(sections)]

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
) -> list[tuple[str, str, str, str]]:
    """Pull ``(name, path, type, description)`` tuples out of one payload.

    The contract stores two distinct shapes, and this normalizes both:

    * **Map shape** (hand-authored, e.g. the ``me`` workspace's AOS entry):
      a dict keyed by project slug, each value a dict with ``name`` and an
      absolute ``local_path``. Detected by the absence of a top-level ``name``
      and all values being dicts.
    * **Scanner shape** (e.g. bildwiz/nfi/qxo/rnd): a top-level ``name`` plus an
      optional ``workspace_repos`` list whose entries carry only a *relative*
      ``path``. The absolute path is not in the contract, so it is resolved
      from the ``projects`` table via ``path_lookup``.

    ``path_lookup`` maps ``(workspace, name)`` -> absolute path, with a
    per-workspace single-path fallback so a name mismatch (contract says
    ``nfi`` but the project row is ``nfi-oro-com``) still resolves when the
    workspace holds exactly one project. Entries that cannot resolve a path
    are still returned (name only) -- the name alone is partial signal.

    ``type`` and ``description`` are carried alongside name and path when the
    payload holds them (both shapes expose these fields), so the rendered
    Projects block can label each entry (e.g. "aos-iac (terraform) — Terraform
    IaC for AOS GCP infra"). Either may be an empty string when absent.
    """
    out: list[tuple[str, str, str, str]] = []
    by_name: dict = path_lookup.get("by_name", {})
    by_ws: dict = path_lookup.get("by_ws", {})

    def _resolve(name: str) -> str:
        p = by_name.get((workspace, name))
        if p:
            return p
        # Single-project workspace: the one path we have is unambiguous.
        ws_paths = by_ws.get(workspace) or []
        if len(ws_paths) == 1:
            return ws_paths[0]
        return ""

    is_map_shape = (
        bool(payload)
        and "name" not in payload
        and all(isinstance(v, dict) for v in payload.values())
        and any(("local_path" in v or "name" in v)
                for v in payload.values() if isinstance(v, dict))
    )

    if is_map_shape:
        for slug, v in payload.items():
            if not isinstance(v, dict):
                continue
            name = v.get("name") or slug
            path = v.get("local_path") or _resolve(slug) or _resolve(name)
            ptype = (v.get("type") or "").strip()
            desc = (v.get("description") or "").strip()
            out.append((name, path, ptype, desc))
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
            out.append((name, _resolve(name), ptype, desc))
        return out

    name = payload.get("name") or workspace
    ptype = (payload.get("type") or "").strip()
    desc = (payload.get("description") or "").strip()
    out.append((name, _resolve(name), ptype, desc))
    return out


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
    excluded here if it was never scanned, failed the gate, or its workspace's
    existing contract is a flat (non-map) shape with more than one promotable
    project deferred for human review. No path-prefix filtering is used.

    Each entry is ``- <name>: <path>`` -- the path being the value the
    orchestrator wants (where the project lives on disk). Workspace is not a
    grouping axis here; it is only used internally to resolve relative/missing
    paths against the ``projects`` table.

    Framed as ``## Project Context — Projects`` so it reads as part of the
    project-context setup the orchestrator receives at SessionStart (it is
    emitted immediately after ``## Environment``), not as an orphan section.

    Budget: bounded to ``max_chars`` (default 8000). The real active-context
    set is small (~17 entries) but each entry now carries ``(type)`` and a
    ``— description`` tail, so the block runs ~2-3 KB; the cap is sized with
    generous headroom so the full index lands and the routing surface is never
    silently truncated. On overflow we drop entries from the tail and ALWAYS
    append a recoverable footer stating the dropped count (footer space is
    reserved before trimming). Fail-safe: any error returns "".
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
    for r in proj_rows:
        d = dict(r)
        p = d.get("path")
        if not p:
            continue
        by_name[(d["workspace"], d["name"])] = p
        by_ws.setdefault(d["workspace"], []).append(p)
    path_lookup = {"by_name": by_name, "by_ws": by_ws}

    entries: list[tuple[str, str, str, str]] = []
    seen: set = set()
    for r in identity_rows:
        d = dict(r)
        ws = d.get("workspace") or ""
        try:
            payload = json.loads(d.get("payload") or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        for name, path, ptype, desc in _extract_projects_from_identity(
            payload, ws, path_lookup
        ):
            key = (name, path)
            if key in seen:
                continue
            seen.add(key)
            entries.append((name, path, ptype, desc))

    if not entries:
        return ""

    total_available = len(entries)
    header = "## Project Context — Projects"

    def _render(items: list[tuple[str, str, str, str]]) -> str:
        lines = [header, ""]
        for name, path, ptype, desc in items:
            # Name + optional "(type)", then " — description" when present.
            # Kept on one line per project so the block stays scannable and
            # bounded; description is not truncated here (the char budget below
            # trims whole entries from the tail if the block overflows).
            label = f"{name} ({ptype})" if ptype else name
            line = f"- {label}: {path}" if path else f"- {label}"
            if desc:
                line += f" — {desc}"
            lines.append(line)
        return "\n".join(lines)

    block = _render(entries)
    # Budget: drop from the tail until the block PLUS its footer fits. The
    # footer must never be lost -- a silent tail-drop with no footer turns the
    # projects index (a routing surface) into a lie about how many projects
    # exist. So we reserve the footer's worst-case width up front and trim
    # against ``max_chars - footer_budget``, guaranteeing the footer always
    # lands when anything was dropped. See FIX (a)/(b).
    if len(block) > max_chars:
        # Footer width is bounded by the digit count of ``total_available``;
        # size the reservation against that count, not against a live ``dropped``
        # value we do not yet have.
        def _footer(n: int) -> str:
            return f"\n... ({n} more, use 'gaia context get')"

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
            return f"\n... ({n} more, see config/surface-routing.json)"

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
            build_agentic_loop_block(),
            # Pending approvals are no longer surfaced here. Cross-session
            # surfacing of pendings (the [ACTIONABLE] block) was removed: the
            # DB remains the pending store, TTL hygiene keeps it clean, and the
            # user inspects/acts on pendings on demand via `gaia approvals`.
            # Workspace Memory is injected last so the orchestrator sees the
            # operational state (environment, projects, loop) before the curated
            # knowledge it should anchor against.
            build_workspace_memory_block(),
        ]
        non_empty = [b for b in blocks if b]
        if not non_empty:
            return ""
        return "\n\n".join(non_empty)
    except Exception as exc:
        logger.debug("build_session_context failed (non-fatal): %s", exc)
        return ""
