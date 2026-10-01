"""The session birth block: what every session knows the moment it opens.

``build_session_context`` assembles it with no knowledge of the host; a host
adapter only delivers the string (Claude Code through its SessionStart hook,
OpenCode attached to the main session's first message) and ``gaia session
preview`` prints it. Four sections, in this order:

- Projects -- each project's name, one line about it and its live-pending
  count, counted by canonical project key across every workspace. A large
  group is one line with its size. Project memory itself (anchors, threads)
  never loads here; it arrives when that project is worked on.
- Environment -- where the session stands: machine, installation (version,
  channel, root), folder, the `gaia` CLI path, the real data home and its
  database, the tools on PATH (one line), and one line of pending recurring
  work when there is any.
- The user / User preferences -- the user's standing rows, whole (see
  ``modules.context.user_sections``, shared with the dispatch kernel).

Alarms that condition every write (a database upgrade, a schema mismatch)
come first, outside the four sections. The whole block is sized under
``BIRTH_BUDGET`` because the host replaces a longer string with a short
preview; the user's rows never shrink to fit, the project roster does.

Every builder is fail-safe: it returns "" on any error and never raises, so a
session always starts. Building the block writes nothing unless the caller
asks for the user rows' injection to be recorded.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import sys
from pathlib import Path
from typing import Optional, Sequence

from ..context.user_sections import PREFERENCES_HEADER, USER_HEADER

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


def _scan_live_gaia_installation() -> Optional[dict]:
    """In-process re-run of the install detector, for THIS machine, right now.

    Delegates to ``tools.scan.store_populator._scan_gaia_installations`` --
    the same read-only heuristic ``gaia scan`` persists into the
    ``gaia_installations`` table -- but calls it directly against the current
    workspace root instead of reading that table back. The table only
    refreshes when someone runs `gaia scan`, and it can age silently: a row
    written before a same-day `gaia dev` rebuild keeps reporting the old
    version until the next scan. Returns the one dict for this hostname, or
    None when no install marker (npm/dev/plugin) is found. Never raises.
    """
    try:
        from ..core.paths import find_claude_dir
        workspace_root = find_claude_dir().parent

        _pkg_root = str(Path(__file__).resolve().parents[3])
        if _pkg_root not in sys.path:
            sys.path.insert(0, _pkg_root)
        from tools.scan.store_populator import _scan_gaia_installations

        installations = _scan_gaia_installations(workspace_root)
        return installations[0] if installations else None
    except Exception as exc:
        logger.debug("_scan_live_gaia_installation failed (non-fatal): %s", exc)
        return None


def _own_package_root() -> Path:
    """Root of the package this module ships in, whatever the install layout."""
    return Path(__file__).resolve().parents[3]


def _plugin_root() -> Path:
    """The plugin's real root: the one Claude Code declares, else our own package."""
    declared = os.environ.get("CLAUDE_PLUGIN_ROOT", "").strip()
    return Path(declared) if declared else _own_package_root()


def _declared_cli_path(package_root: Path) -> Optional[str]:
    """``package_root`` joined with its manifest's ``bin.gaia``, or None."""
    manifest_path = package_root / "package.json"
    if not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bin_field = manifest.get("bin") if isinstance(manifest, dict) else None
    bin_rel = bin_field.get("gaia") if isinstance(bin_field, dict) else None
    if not isinstance(bin_rel, str) or not bin_rel.strip():
        return None
    return str(package_root / bin_rel)


def _resolve_gaia_cli_path() -> Optional[str]:
    """Absolute path to the `gaia` CLI the orchestrator should invoke.

    The first guard-verified candidate wins, and PATH is never consulted:

    1. The workspace alias ``node_modules/@jaguilar87/gaia`` -- published as
       the symlink path, not its realpath, because `gaia dev`/pnpm repoint it
       at a fresh store entry on every rebuild and prune the old one while the
       file stays executable, so a resolved snapshot goes stale silently.
    2. The package this hook ships in -- the only candidate a Claude Code
       plugin install has (no node_modules, no `gaia` on PATH), and the same
       package ``is_trusted_gaia_binary`` anchors its trust to.

    None when no candidate passes the guard.
    """
    try:
        from ..security.gaia_cli_only_guard import is_trusted_gaia_binary
    except Exception as exc:
        logger.debug("_resolve_gaia_cli_path: trust guard unavailable: %s", exc)
        return None

    candidates = []
    try:
        from ..core.paths import find_claude_dir
        workspace_root = find_claude_dir().parent
        candidates.append(workspace_root / "node_modules" / "@jaguilar87" / "gaia")
    except Exception as exc:
        logger.debug("_resolve_gaia_cli_path: no workspace alias: %s", exc)
    candidates.append(_own_package_root())

    for package_root in candidates:
        try:
            cli_path = _declared_cli_path(package_root)
            if cli_path and is_trusted_gaia_binary(cli_path):
                return cli_path
        except Exception as exc:
            logger.debug("_resolve_gaia_cli_path: %s rejected: %s", package_root, exc)
    return None


# The orchestrator's identity refuses a dispatch that needs a tool this machine
# lacks, so the roster must name what IS installed. `acli` has no mention in
# skills/ or agents/ and is listed anyway: it is the case this inventory exists
# for -- installed here, invoked by nothing that names it.
_CANDIDATE_TOOLS: tuple = (
    "git", "go", "npm", "terraform", "node", "gcloud", "flux", "kubectl",
    "gh", "terragrunt", "helm", "aws", "pulumi", "python3", "gws", "jq",
    "pnpm", "curl", "ssh", "eslint", "cargo", "prettier", "vault",
    "playwright", "acli",
)


def _scan_available_tools(candidates: tuple = _CANDIDATE_TOOLS) -> list:
    """Which *candidates* resolve on PATH right now, in declared order.

    ``shutil.which`` is a pure PATH lookup -- no subprocess, no version probe
    -- so this reflects the current install rather than a snapshot. Never
    raises: a lookup failure for one name is skipped, not fatal to the rest.
    """
    import shutil

    found = []
    for name in candidates:
        try:
            if shutil.which(name):
                found.append(name)
        except Exception:
            continue
    return found


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

PROJECTS_HEADER = "## Projects"
ENVIRONMENT_HEADER = "## Environment"
BIRTH_SECTION_HEADERS = (PROJECTS_HEADER, ENVIRONMENT_HEADER, USER_HEADER, PREFERENCES_HEADER)

# Claude Code replaces a hook's additionalContext string over 10,000 chars
# with a 2,000-char preview and a file path, without telling the model; the
# margin keeps the whole block clear of that cap.
BIRTH_BUDGET = 9_500


def _installation_label() -> Optional[str]:
    """``<version>, <channel> channel, at <root>`` for the Gaia this session runs.

    The version comes from a live in-process re-scan (never the
    gaia_installations table, which only refreshes on `gaia scan`), falling
    back to the package.json ancestor walk. A declared CLAUDE_PLUGIN_ROOT
    means the plugin channel; otherwise the scan's install mode names it.
    """
    installation = _scan_live_gaia_installation() or {}
    version = installation.get("version") or _read_gaia_version()
    if not version:
        return None
    if os.environ.get("CLAUDE_PLUGIN_ROOT", "").strip():
        channel = "plugin"
    else:
        channel = installation.get("install_mode")
    parts = [_describe_gaia_version(version)]
    if channel and channel != "unknown":
        parts.append(f"{channel} channel")
    parts.append(f"at {_plugin_root()}")
    return ", ".join(parts)


def _recurring_work_line(workspace: Optional[str]) -> str:
    """One line naming the recurring work that needs the user, or "" when nothing does.

    Each unread report and each due reminder or routine is named, the latter with
    its pointer so the session can act on it; the detail lives in
    `gaia notifications list`. Reading evaluates due times against now and
    writes nothing.
    """
    from gaia import notifications_time
    from gaia.store.reader import list_unread_notifications

    items: list[str] = []
    verbs: list[str] = []
    due = list_unread_notifications(workspace=workspace)
    reports = [row for row in due if row["kind"] == "report"]
    if reports:
        items.append(f"{len(reports)} unread task report(s)")
        verbs.append("`gaia notifications list --unread`")
    reminders = [row for row in due if row["kind"] != "report"]
    if reminders:
        items.extend(f"{notifications_time.summary(row)} is due" for row in reminders)
        verbs.append("`gaia notifications ack|snooze|cancel <id>`")
    if not items:
        return ""
    return f"- Recurring work pending: {', '.join(items)} — {', '.join(verbs)}"


def build_environment_section() -> str:
    """Render the Environment section: where this session stands, or "" on failure."""
    try:
        from gaia.paths import data_dir, db_path

        workspace = _read_workspace_identity()
        folder = str(Path.cwd())
        lines = [ENVIRONMENT_HEADER, f"- Machine: {_machine_label()}"]
        installation = _installation_label()
        if installation:
            lines.append(f"- Gaia: {installation}")
        lines.append(f"- Folder: {folder} (workspace {workspace})" if workspace
                     else f"- Folder: {folder}")
        # The orchestrator invokes `gaia` by this absolute path: the trust
        # guard rejects the bare token.
        cli_path = _resolve_gaia_cli_path()
        if cli_path:
            lines.append(f"- gaia CLI: {cli_path}")
        lines.append(f"- Data home: {data_dir()} (database {db_path()})")
        tools = _scan_available_tools()
        if tools:
            lines.append(f"- Tools on PATH: {', '.join(tools)}")
        try:
            recurring = _recurring_work_line(workspace)
        except Exception as exc:
            logger.debug("recurring work line failed (non-fatal): %s", exc)
            recurring = ""
        if recurring:
            lines.append(recurring)
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("build_environment_section failed (non-fatal): %s", exc)
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


def _collect_projects() -> Optional[dict]:
    """The projects with active project context, grouped by owning workspace.

    Returns ``{"live", "unresolved", "group_order", "roots"}``, or None when
    there is nothing to index or the database cannot be read.

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

    The name shown per project is the ON-DISK BASENAME when it differs from the
    contract's stored name, not the stored name itself (see ``_display_name``).
    A legacy hand-authored slot (e.g. the ``aaxis`` workspace's map names a repo
    ``bildwiz-2``) can diverge from the directory the repo actually lives in
    (``bildwiz-iac``) -- the basename is what the user calls the project and
    what ``gaia context project`` resolves against as its second-priority match,
    so showing it is what makes the index actually useful for lookup.

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
      stays one ``gaia context project`` away for the rare turn that asks about a
      removed project. It is not worth a line of every session's context.
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
        logger.debug("project collection import failed: %s", exc)
        return None

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
        logger.debug("project collection query failed: %s", exc)
        return None

    if not identity_rows:
        return None

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
        return None

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

    return {"live": live, "unresolved": unresolved, "group_order": group_order, "roots": roots}


# A workspace holding more projects than this is one line in the roster: past
# a screenful of names nobody scans the list, and the group's size is the
# signal worth its line.
_LARGE_GROUP = 12
_PROJECT_LINE_MAX = 120


def _display_name(e: dict) -> str:
    """The identifier shown in the roster -- the on-disk basename, else the stored name.

    See ``_collect_projects`` for why the basename wins when the two differ.
    """
    if e["path"]:
        base = os.path.basename(e["path"])
        if base:
            return base
    return e["name"]


def _one_line(text: str) -> str:
    """``text`` flattened to one line of at most ``_PROJECT_LINE_MAX`` chars."""
    flat = " ".join(text.split())
    return flat if len(flat) <= _PROJECT_LINE_MAX else flat[: _PROJECT_LINE_MAX - 1] + "…"


def build_projects_section(max_chars: int) -> str:
    """Render the Projects section within ``max_chars``, or "" when there is nothing to index.

    Each project is its name, its live-pending count when it has one, and one
    line about it. The count is taken by canonical project key over every
    workspace (``gaia.store.reader.count_pending_by_initiative``), so it
    equals what ``gaia memory get-relevant --initiative <name>`` returns.
    When the roster does not fit it degrades in steps -- names without their
    lines, then every group as one line, then a single count -- and the
    pointer to a project's ficha always lands.
    """
    try:
        collected = _collect_projects()
        if not collected:
            return ""
        from gaia.store.reader import count_pending_by_initiative
        from gaia.store.writer import normalize_initiative

        live = collected["live"]
        group_order = collected["group_order"]
        roots = collected["roots"]
        unresolved = collected["unresolved"]
        keys = {normalize_initiative(_display_name(e)) for e in live} - {None}
        counts = count_pending_by_initiative(sorted(keys))
        # The first command a newborn orchestrator tries: never the bare
        # `gaia` token the trust guard rejects.
        cli = _resolve_gaia_cli_path() or "gaia"
        pointer = f"Ficha de un proyecto: {cli} context project <nombre>"

        def label(e: dict) -> str:
            name = _display_name(e)
            count = counts.get(normalize_initiative(name), 0)
            return f"{name} ({count})" if count else name

        def title(ws: str) -> str:
            root = roots.get(ws)
            return f"### {ws} — {root}" if root else f"### {ws}"

        def group(ws: str, members: list[dict], detail: str) -> str:
            if detail == "groups" or len(members) > _LARGE_GROUP:
                pending = [label(e) for e in members if label(e) != _display_name(e)]
                line = f"{title(ws)}: {len(members)} projects"
                return line + (f"; pending: {', '.join(pending)}" if pending else "")
            if detail == "names":
                return f"{title(ws)}\n{', '.join(label(e) for e in members)}"
            return "\n".join([title(ws)] + [
                f"- {label(e)}: {_one_line(e['desc'])}" if e["desc"] else f"- {label(e)}"
                for e in members
            ])

        def render(detail: str) -> str:
            parts = [PROJECTS_HEADER]
            for ws in group_order:
                members = [e for e in live if e["ws"] == ws]
                if members:
                    parts.append(group(ws, members, detail))
            if unresolved and detail != "groups":
                parts.append(
                    f"unresolved ({len(unresolved)}): {', '.join(unresolved)} "
                    f"— no path on disk; 'gaia context get'"
                )
            parts.append(pointer)
            return "\n\n".join(parts)

        for detail in ("lines", "names", "groups"):
            text = render(detail)
            if len(text) <= max_chars:
                return text
        return "\n\n".join([
            PROJECTS_HEADER,
            f"{len(live)} projects in {len(group_order)} workspaces; list them with "
            f"`{cli} context get`",
            pointer,
        ])
    except Exception as exc:
        logger.debug("build_projects_section failed (non-fatal): %s", exc)
        return ""


def build_schema_direction_block() -> str:
    """Name the fix when the database and this code disagree on schema version.

    Returns "" when they agree, when either version is unknown, or on any
    error. Reads the database read-only and never creates it.
    """
    try:
        from gaia.paths import db_path as _db_path
        from gaia.store.writer import (
            schema_ahead_message,
            schema_compatible_notice,
            schema_versions,
            writes_refused,
        )

        db_file = _db_path()
        if not db_file.exists():
            return ""
        live, expected, minimum = schema_versions(db_file)
        if live is None or expected is None or live == expected:
            return ""
        if live < expected:
            fix = (
                f"gaia.db at {db_file} is at schema v{live}; this Gaia expects "
                f"v{expected}. Writes that need the newer structure will fail "
                f"until it is migrated. Run `gaia migrate plan` to see the chain, "
                f"then `gaia migrate apply`."
            )
        elif writes_refused(live, expected, minimum):
            fix = schema_ahead_message(live, expected, db_file, minimum)
        else:
            fix = schema_compatible_notice(live, expected, minimum, db_file)
        return "## Database schema\n" + fix
    except Exception as exc:
        logger.debug("build_schema_direction_block failed (non-fatal): %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Assembler
# ---------------------------------------------------------------------------

def _record_user_rows_injected(rows: list[dict]) -> None:
    """Count one injection on each user row the block carried; best-effort."""
    try:
        from gaia.store.writer import record_memory_access
    except ImportError:
        return
    for row in rows:
        try:
            record_memory_access(row["workspace"], row["name"], "injection")
        except Exception:
            logger.debug("user row injection telemetry failed (non-fatal)", exc_info=True)


def build_session_context(
    *, alarms: Sequence[str] = (), record_injection: bool = False,
) -> str:
    """Assemble the session birth block; "" when every part is empty. Never raises.

    ``alarms`` are notices the host adapter produced before the block (a
    database upgrade it just ran); they lead, with the schema-mismatch notice,
    ahead of the four sections, and still ship alone if assembling the rest
    fails. The project roster gets whatever
    ``BIRTH_BUDGET`` leaves after everything else, because the user's rows
    never shrink. Nothing is written unless ``record_injection`` is set, which
    the session-start hook does and ``gaia session preview`` does not.
    """
    try:
        from gaia.store.reader import user_anchor_rows

        from ..context.user_sections import render_user_sections

        user_rows = [r for r in user_anchor_rows() if (r.get("body") or "").strip()]
        prefix = [a for a in (*alarms, build_schema_direction_block()) if a]
        environment = build_environment_section()
        user = render_user_sections(user_rows)
        fixed = [b for b in (*prefix, environment, user) if b]
        roster_budget = BIRTH_BUDGET - sum(len(b) + 2 for b in fixed)
        projects = build_projects_section(max(0, roster_budget))
        text = "\n\n".join(b for b in (*prefix, projects, environment, user) if b)
        if record_injection:
            _record_user_rows_injected(user_rows)
        return text
    except Exception as exc:
        logger.debug("build_session_context failed (non-fatal): %s", exc)
        return "\n\n".join(a for a in alarms if a)
