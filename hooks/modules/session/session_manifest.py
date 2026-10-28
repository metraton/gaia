"""The session birth block: what every session knows when it opens, and again after each compaction.

``build_session_context`` assembles it with no knowledge of the host, for
every start source; its sections, their order and the order in which the
block shrinks under ``BIRTH_BUDGET`` are described in hooks/README.md.

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

from ..context.user_sections import CONTEXT_BUDGET, PREFERENCES_HEADER, USER_HEADER

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
    """Name of the declared workspace holding the cwd, or None outside every one.

    The name is the ``project_identity`` contract's ``$.name`` when it carries
    one, else the workspace's own name. Never raises.
    """
    import sqlite3

    try:
        from gaia.project import declared_workspace

        workspace = declared_workspace()
    except Exception as exc:
        logger.debug("declared workspace unresolved (non-fatal): %s", exc)
        return None
    if not workspace:
        return None

    try:
        from gaia.paths import db_path
        from gaia.store.project_context import workspace_sections

        con = sqlite3.connect(str(db_path()))
        try:
            section = workspace_sections(con, workspace).get("project_identity")
        finally:
            con.close()
    except sqlite3.Error as exc:
        logger.debug("workspace identity read failed (non-fatal): %s", exc)
        return workspace
    payload = section["payload"] if section else None
    name = payload.get("name") if isinstance(payload, dict) else None
    return name if isinstance(name, str) and name else workspace


def _on_plugin_channel() -> bool:
    """Whether Claude Code runs this session from a plugin install."""
    return bool(os.environ.get("CLAUDE_PLUGIN_ROOT", "").strip())


def _declared_install_root() -> Optional[Path]:
    """Root of the declared workspace holding the cwd, where an npm install keeps
    ``node_modules``; None outside every declared workspace."""
    from gaia.install_root import owning_root, registered_roots

    return owning_root(Path.cwd().resolve(), registered_roots())


def _scan_live_gaia_installation() -> Optional[dict]:
    """In-process re-run of the install detector, for THIS machine, right now.

    Delegates to ``tools.scan.store_populator._scan_gaia_installations`` --
    the same read-only heuristic ``gaia scan`` persists into the
    ``gaia_installations`` table -- but calls it directly against the declared
    install root instead of reading that table back. The table only
    refreshes when someone runs `gaia scan`, and it can age silently: a row
    written before a same-day `gaia dev` rebuild keeps reporting the old
    version until the next scan. Returns the one dict for this hostname, or
    None under the plugin channel, outside every declared workspace, or when
    no install marker is found. Never raises.
    """
    if _on_plugin_channel():
        return None
    try:
        install_root = _declared_install_root()
        if install_root is None:
            return None

        _pkg_root = str(Path(__file__).resolve().parents[3])
        if _pkg_root not in sys.path:
            sys.path.insert(0, _pkg_root)
        from tools.scan.store_populator import _scan_gaia_installations

        installations = _scan_gaia_installations(install_root)
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

    The first guard-verified candidate wins, and PATH is never consulted.
    The guard accepts every genuine copy of the package, so the candidate
    order alone decides which install is published:

    1. Under the plugin channel, the plugin root Claude Code declares.
    2. Otherwise, the declared workspace's alias
       ``node_modules/@jaguilar87/gaia`` -- never one in a nested folder --
       published as the symlink path, not its realpath, because `gaia
       dev`/pnpm repoint it at a fresh store entry on every rebuild and prune
       the old one while the file stays executable.
    3. The package this hook ships in.

    None when no candidate passes the guard.
    """
    try:
        from ..security.gaia_cli_only_guard import is_trusted_gaia_binary
    except Exception as exc:
        logger.debug("_resolve_gaia_cli_path: trust guard unavailable: %s", exc)
        return None

    candidates = []
    if _on_plugin_channel():
        candidates.append(_plugin_root())
    else:
        try:
            install_root = _declared_install_root()
        except Exception as exc:
            logger.debug("_resolve_gaia_cli_path: declared root unresolved: %s", exc)
            install_root = None
        if install_root is not None:
            candidates.append(install_root / "node_modules" / "@jaguilar87" / "gaia")
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


USER_EXECUTABLES_MAX = 40

_PACKAGE_TREES = frozenset({
    "node_modules", ".venv", "venv", ".cargo", ".rustup", "go", ".nvm", ".pyenv",
    ".rbenv", ".asdf", ".npm", ".npm-global", ".bun", ".volta", ".gem", "gem", ".sdkman",
})


_CONSOLE_SCRIPT_HEAD_BYTES = 1024
_CONSOLE_SCRIPT_MARKER = rb"-script\.pyw"


def _is_package_console_script(path: str) -> bool:
    """Whether *path* is the python entry-point wrapper pip and uv generate for a package."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(_CONSOLE_SCRIPT_HEAD_BYTES)
    except OSError:
        return False
    first_line = head.split(b"\n", 1)[0]
    return first_line.startswith(b"#!") and b"python" in first_line and _CONSOLE_SCRIPT_MARKER in head


def _scan_user_executables(exclude=(), limit: int = USER_EXECUTABLES_MAX) -> tuple:
    """The user's executables on PATH as (authored, package console scripts), together within *limit*."""
    home = Path.home()
    seen = set(exclude)
    names: list = []
    packages: list = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        directory = Path(entry)
        if not directory.is_absolute() or not directory.is_relative_to(home):
            continue
        if _PACKAGE_TREES.intersection(directory.relative_to(home).parts):
            continue
        try:
            files = sorted((e.name, e.path) for e in os.scandir(directory)
                           if not e.name.startswith(".") and e.is_file() and os.access(e.path, os.X_OK))
        except OSError:
            continue
        for name, path in files:
            if name not in seen:
                seen.add(name)
                (packages if _is_package_console_script(path) else names).append(name)
    names = names[:limit]
    return names, packages[:limit - len(names)]


def _scan_available_tools(candidates: tuple = _CANDIDATE_TOOLS) -> list:
    """The user's own tools, then which *candidates* resolve on PATH right now, then package console scripts.

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
    authored, packages = _scan_user_executables(exclude=candidates)
    return authored + found + packages


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


ENVIRONMENT_LINE_MAX = 400


def _os_label() -> str:
    system = platform.system() or "unknown"
    release = platform.release().lower()
    if "wsl2" in release:
        return f"{system}/WSL2"
    if "microsoft" in release:
        return f"{system}/WSL"
    return system


def build_environment_line(*, limit: int = ENVIRONMENT_LINE_MAX) -> str:
    """The OS, gaia CLI path and tools on PATH as one line within ``limit`` characters."""
    try:
        parts = [_os_label()]
        cli_path = _resolve_gaia_cli_path()
        if cli_path:
            parts.append(f"gaia CLI {cli_path}")
        line = "; ".join(parts)
        if len(line) > limit:
            return line[:limit - 1] + "…"
        label = "; tools on PATH: "
        kept = ""
        for name in _scan_available_tools():
            candidate = f"{kept}, {name}" if kept else name
            if len(line + label + candidate) > limit:
                break
            kept = candidate
        return line + label + kept if kept else line
    except Exception as exc:
        logger.debug("build_environment_line failed (non-fatal): %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

PROJECTS_HEADER = "## Projects"
ENVIRONMENT_HEADER = "## Environment"
RESUME_HEADER = "## Resume point"
BIRTH_SECTION_HEADERS = (USER_HEADER, PREFERENCES_HEADER, ENVIRONMENT_HEADER, PROJECTS_HEADER)
BIRTH_BUDGET = CONTEXT_BUDGET


def _installation_label() -> Optional[str]:
    """``<version>, <channel> channel, at <root>`` for the Gaia this session runs.

    The version comes from a live in-process re-scan of the declared install
    (never the gaia_installations table, which only refreshes on `gaia
    scan`), falling back to the package.json ancestor walk, which is the only
    source under the plugin channel. A declared CLAUDE_PLUGIN_ROOT means the
    plugin channel; otherwise the scan's install mode names it.
    """
    installation = _scan_live_gaia_installation() or {}
    version = installation.get("version") or _read_gaia_version()
    if not version:
        return None
    if _on_plugin_channel():
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


MAP_POINTER = "- Live state, habits and which capability serves an intent: `gaia map`"


def _local_zone_line() -> str:
    """The zone only: a time in the birth block goes stale within one long turn."""
    from gaia import notifications_time

    zone = notifications_time.zone_name() or "unnamed (the offset is in `gaia now`)"
    return f"- Local zone: {zone}; read the current time with `gaia now`"


def build_environment_section(session_id: str = "") -> str:
    """Render the Environment section: where this session stands, or "" on failure.

    ``session_id`` is the host's id for the session, the one
    `gaia session resume-point set --session-id` writes under and the
    compaction snapshot reads; the block publishes it at birth and after every
    compaction, so the orchestrator can name its own session on any host.
    """
    try:
        from gaia.paths import data_dir, db_path
        from gaia.project import current

        workspace = _read_workspace_identity()
        folder = str(Path.cwd())
        lines = [ENVIRONMENT_HEADER, f"- Machine: {_machine_label()}"]
        installation = _installation_label()
        if installation:
            lines.append(f"- Gaia: {installation}")
        lines.append(f"- Folder: {folder} (workspace {workspace})" if workspace
                     else f"- Folder: {folder} (not inside a declared workspace)")
        if session_id:
            lines.append(f"- Session id: {session_id}")
        lines.append(_local_zone_line())
        lines.append(MAP_POINTER)
        # The orchestrator invokes `gaia` by this absolute path: the trust
        # guard rejects the bare token.
        cli_path = _resolve_gaia_cli_path()
        if cli_path:
            lines.append(f"- gaia CLI: {cli_path}")
        lines.append(f"- Data home: {data_dir()} (database {db_path()})")
        tools = _scan_available_tools()
        if tools:
            lines.append(f"- Tools on PATH: {', '.join(tools)}")
        # Reminders set outside every workspace are filed under current()'s fallback.
        notification_scope = workspace or current()
        try:
            recurring = _recurring_work_line(notification_scope)
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
    """The projects with active project context, keyed by owning workspace and group.

    Returns ``{"live", "unresolved", "workspace_order", "roots"}``, or None when
    there is nothing to index or the database cannot be read.

    This is NOT an index of every git repo on disk. The source is the set of
    projects that have **active project context** -- a ``project_identity``
    section in a workspace's view (``workspace_sections``). That filter is the point: it includes
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

    A project's workspace is the one that owns its ``projects`` row (the
    scan-verified physical truth), falling back to the contract's own workspace
    key for a hand-authored entry with no row; its group is that row's
    ``group_name``, and an entry with no row has none.

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
        from gaia.store.project_context import workspace_sections, workspaces_with_section
        from gaia.store.writer import _connect
    except Exception as exc:
        logger.debug("project collection import failed: %s", exc)
        return None

    try:
        con = _connect()
        try:
            identity_rows = [
                {"workspace": ws,
                 "payload": json.dumps(workspace_sections(con, ws)["project_identity"]["payload"])}
                for ws in workspaces_with_section(con, "project_identity")
            ]
            # Path resolution sources: include missing rows -- the on-disk path
            # may still be valid even if the scanner marked the repo missing.
            proj_rows = con.execute(
                "SELECT workspace, name, path, group_name FROM projects WHERE path IS NOT NULL"
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
    group_of_path: dict = {}
    basename_hits: dict = {}
    for r in proj_rows:
        d = dict(r)
        p = d.get("path")
        if not p:
            continue
        by_name[(d["workspace"], d["name"])] = p
        by_ws.setdefault(d["workspace"], []).append(p)
        ws_of_path.setdefault(p, d["workspace"])
        group_of_path.setdefault(p, d.get("group_name") or "")
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
        for name, path, _type, _desc, gone in _extract_projects_from_identity(
            payload, contract_ws, path_lookup
        ):
            key = path or f"name:{name.lower()}"
            prev = merged.get(key)
            if prev is None:
                merged[key] = {
                    "name": name, "path": path, "gone": gone,
                    "ws": ws_of_path.get(path) or contract_ws,
                    "group": group_of_path.get(path, ""),
                    "is_ws_identity": not path and name.lower() == contract_ws.lower(),
                }
                order.append(key)
            elif gone and not prev["gone"]:
                prev["gone"] = gone

    if not merged:
        return None

    # Roots are computed from the LIVE paths only: a vanished repo's path should
    # not widen (or, as the sole member, define) the root every sibling renders against.
    live: list[dict] = []
    unresolved: list[str] = []
    workspace_order: list[str] = []
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
        if ws and ws not in workspace_order:
            workspace_order.append(ws)
        if not e["path"]:
            unresolved.append(e["name"])
        else:
            live.append(e)

    roots: dict = {}
    for ws in workspace_order:
        roots[ws] = _workspace_root([e["path"] for e in live if e["ws"] == ws])

    return {"live": live, "unresolved": unresolved, "workspace_order": workspace_order, "roots": roots}


def _display_name(e: dict) -> str:
    """The identifier shown in the roster -- the on-disk basename, else the stored name.

    See ``_collect_projects`` for why the basename wins when the two differ.
    """
    if e["path"]:
        base = os.path.basename(e["path"])
        if base:
            return base
    return e["name"]


def _groups_of(members: list[dict]) -> list[tuple[str, list[dict]]]:
    """``members`` split by group, named groups alphabetically and the ungrouped ("") last."""
    by_group: dict[str, list[dict]] = {}
    for e in members:
        by_group.setdefault(e["group"], []).append(e)
    named = sorted(g for g in by_group if g)
    return [(g, by_group[g]) for g in named] + ([("", by_group[""])] if "" in by_group else [])


def build_projects_section(max_chars: int) -> str:
    """Render the Projects section within ``max_chars``, or "" when there is nothing to index.

    The index carries names only: each workspace as its groups, each group as
    its projects, a project with its live-pending count when it has one. The
    count is taken by canonical project key over every workspace
    (``gaia.store.reader.count_pending_by_initiative``), so it equals what
    ``gaia memory get-relevant --initiative <name>`` returns. When the index
    does not fit it degrades in steps -- each workspace as one line naming its
    groups, then one line naming every group, then a line that only counts --
    and the pointer always lands, whatever ``max_chars`` is.
    """
    return projects_renderer()(max_chars)


def projects_renderer():
    """``build_projects_section`` as a function of ``max_chars`` over one read of the projects."""
    try:
        collected = _collect_projects()
        if not collected:
            return lambda max_chars: ""
        from gaia.store.reader import count_pending_by_initiative
        from gaia.store.writer import normalize_initiative

        live = collected["live"]
        workspace_order = collected["workspace_order"]
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

        def names_block(ws: str, members: list[dict]) -> str:
            groups = _groups_of(members)
            if groups == [("", members)]:
                return f"{title(ws)}\n{', '.join(label(e) for e in members)}"
            return "\n".join([title(ws)] + [
                f"{g} ({len(projects)}): {', '.join(label(e) for e in projects)}" if g
                else f"ungrouped: {', '.join(label(e) for e in projects)}"
                for g, projects in groups
            ])

        def summary_line(ws: str, members: list[dict]) -> str:
            named = [f"{g} ({len(projects)})" for g, projects in _groups_of(members) if g]
            pending = [label(e) for e in members if label(e) != _display_name(e)]
            line = f"{title(ws)}: {len(members)} projects"
            line += f"; groups: {', '.join(named)}" if named else ""
            return line + (f"; pending: {', '.join(pending)}" if pending else "")

        by_workspace = [(ws, [e for e in live if e["ws"] == ws]) for ws in workspace_order]
        by_workspace = [(ws, members) for ws, members in by_workspace if members]
        group_count = sum(1 for _, members in by_workspace for g, _ in _groups_of(members) if g)
        census = (f"{len(live)} projects in {group_count} groups in "
                  f"{len(workspace_order)} workspaces")

        def render(block) -> str:
            parts = [PROJECTS_HEADER, *(block(ws, members) for ws, members in by_workspace)]
            if unresolved and block is names_block:
                parts.append(
                    f"unresolved ({len(unresolved)}): {', '.join(unresolved)} "
                    f"— no path on disk; 'gaia context get'"
                )
            parts.append(pointer)
            return "\n\n".join(parts)

        every_group = "; ".join(
            f"{ws}: {', '.join(g for g, _ in _groups_of(members) if g)}"
            for ws, members in by_workspace if any(e["group"] for e in members)
        )
        rendered = [render(names_block), render(summary_line)]
        if every_group:
            rendered.append("\n\n".join([PROJECTS_HEADER, f"{census} — {every_group}", pointer]))
        floor = "\n\n".join([
            PROJECTS_HEADER,
            f"{census}; list them with `{cli} context get`",
            pointer,
        ])

        def fit(max_chars: int) -> str:
            return next((text for text in rendered if len(text) <= max_chars), floor)
        return fit
    except Exception as exc:
        logger.debug("build_projects_section failed (non-fatal): %s", exc)
        return lambda max_chars: ""


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

def orchestrator_user_rows() -> list[dict]:
    """The user rows the orchestrator's context renders: every standing user row with a body."""
    from gaia.store.reader import user_anchor_rows

    return [r for r in user_anchor_rows() if (r.get("body") or "").strip()]


def record_user_rows_injected(rows: list[dict]) -> None:
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


def _snapshot(session_id: str) -> Optional[dict]:
    if not session_id:
        return None
    try:
        from gaia.session_snapshot import build_snapshot

        return build_snapshot(session_id)
    except Exception as exc:
        logger.debug("session snapshot failed (non-fatal): %s", exc)
        return None


def _joined(*blocks: str) -> str:
    return "\n\n".join(b for b in blocks if b)


def build_session_context(
    *, source: str = "startup", alarms: Sequence[str] = (), notices: Sequence[str] = (),
    record_injection: bool = False, session_id: str = "",
) -> str:
    """Assemble the session birth block for any start ``source``; "" when every part is empty. Never raises.

    ``alarms`` still ship alone if assembling the rest fails; ``notices`` ship
    only while the block has room. ``record_injection`` counts one injection
    on each user row the block carries.
    """
    try:
        from gaia.session_snapshot import render_resume_section

        from ..context.user_sections import fit_user_sections, render_user_sections

        rows = orchestrator_user_rows()
        heading = f"# Gaia session context (source: {source})"
        prefix = [heading, *(a for a in (*alarms, build_schema_direction_block()) if a)]
        environment = build_environment_section(session_id)
        snapshot = _snapshot(session_id)
        resume = render_resume_section(snapshot) if snapshot else ""
        user, kept = render_user_sections(rows), rows
        roster = projects_renderer()

        if len(_joined(*prefix, *notices, user, resume, environment, roster(sys.maxsize))) > BIRTH_BUDGET:
            notices = ()
        floor = roster(0)
        if snapshot and len(_joined(*prefix, user, resume, environment, floor)) > BIRTH_BUDGET:
            resume = render_resume_section(snapshot, counts_only=True)
        head = _joined(*prefix, *notices, user, resume, environment)
        projects = roster(BIRTH_BUDGET - len(head) - 2)
        if len(_joined(*prefix, user, resume, environment, floor)) > BIRTH_BUDGET:
            room = BIRTH_BUDGET - len(_joined(*prefix, resume, environment, floor)) - 2
            user, kept = fit_user_sections(rows, room)
            head, projects = _joined(*prefix, user, resume, environment), floor
        text = _joined(head, projects)
        if text == heading:
            return ""
        if record_injection:
            record_user_rows_injected(kept)
        return text
    except Exception as exc:
        logger.debug("build_session_context failed (non-fatal): %s", exc)
        return "\n\n".join(a for a in alarms if a)
