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

from ..context.user_sections import CONTEXT_BUDGET, USER_HEADER, tool_lines

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

PROJECTS_HEADER = "## Workspaces I manage"
ENVIRONMENT_HEADER = "## Environment"
DUE_HEADER = "## Due"
RESUME_HEADER = "## Resume point"
BIRTH_SECTION_HEADERS = (USER_HEADER, DUE_HEADER, ENVIRONMENT_HEADER, PROJECTS_HEADER)
BIRTH_BUDGET = CONTEXT_BUDGET


def _due_item_line(row: dict, now) -> str:
    """One due reminder or routine as values: its title, when, how late, its ref and its id."""
    from gaia import notifications_time

    parts = [f"'{row['headline']}'"]
    due_at = notifications_time.from_iso(row["due_at"]) if row.get("due_at") else None
    if row.get("recurrence"):
        parts.append(f"repeat='{row['recurrence']}'")
        if due_at:
            parts.append(f"missed={due_at.strftime('%Y-%m-%dT%H:%MZ')}")
    elif due_at:
        parts.append(f"due={due_at.strftime('%Y-%m-%dT%H:%MZ')}")
        late = (now - due_at).days
        if late > 0:
            parts.append(f"late={late}d")
    if row.get("pointer_kind") and row.get("pointer_ref"):
        scope = f"{row['pointer_workspace']}/" if row.get("pointer_workspace") else ""
        parts.append(f"ref={row['pointer_kind']}:{scope}{row['pointer_ref']}")
    parts.append(f"id={row['id']}")
    return "  ".join(parts)


def build_due_section(workspace: Optional[str]) -> str:
    """Render ``## Due``: the reminders and routines due now, one value line each, or "" when none is."""
    try:
        from datetime import datetime, timezone

        from gaia.store.reader import list_unread_notifications

        due = [row for row in list_unread_notifications(workspace=workspace) if row["kind"] != "report"]
        if not due:
            return ""
        now = datetime.now(timezone.utc)
        return "\n".join([DUE_HEADER, f"overdue={len(due)}", *(_due_item_line(row, now) for row in due)])
    except Exception as exc:
        logger.debug("build_due_section failed (non-fatal): %s", exc)
        return ""


def _installation_values() -> list[str]:
    """``gaia=<version> channel=<channel>`` as values: a live re-scan of the declared install, else the package.json walk; CLAUDE_PLUGIN_ROOT means the plugin channel."""
    installation = _scan_live_gaia_installation() or {}
    version = installation.get("version") or _read_gaia_version()
    if not version:
        return []
    channel = "plugin" if _on_plugin_channel() else installation.get("install_mode")
    values = [f"gaia={_describe_gaia_version(version)}"]
    if channel and channel != "unknown":
        values.append(f"channel={channel}")
    return values


def build_environment_section(session_id: str = "") -> str:
    """Render ``## Environment`` as values only -- machine, Gaia, CLI, cwd, session, zone, data, tools -- or "" on failure.

    ``session_id`` is the host's id for the session, the one
    `gaia session resume-point set --session-id` writes under and the
    compaction snapshot reads. What each value means lives in the
    orchestrator identity, not here.
    """
    try:
        from gaia import notifications_time
        from gaia.paths import data_dir, db_path

        release = platform.release().lower()
        wsl = "yes" if "microsoft" in release or "wsl" in release else "no"
        lines = [
            ENVIRONMENT_HEADER,
            f"machine={platform.node() or 'unknown'} os={platform.system() or 'unknown'} "
            f"arch={platform.machine() or 'unknown'} wsl={wsl}",
        ]
        installation = " ".join(_installation_values())
        if installation:
            lines.append(installation)
        cli_path = _resolve_gaia_cli_path()
        if cli_path:
            lines.append(f"cli={cli_path}")
        lines.append(f"cwd={Path.cwd()}")
        if session_id:
            lines.append(f"session={session_id}")
        lines.append(f"zone={notifications_time.zone_name() or 'unnamed'}")
        lines.append(f"data={data_dir()} db={db_path()}")
        tools = _scan_available_tools()
        if tools:
            lines.append(f"tools={','.join(tools)}")
        from gaia.store.reader import user_tool_rows

        lines.extend(tool_lines(user_tool_rows()))
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("build_environment_section failed (non-fatal): %s", exc)
        return ""


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


_DISPATCH_WINDOW_DAYS = 30
_FULL_GROUP_CAP = 5
_NARROW_GROUP_CAP = 3


def _home_relative(path: str) -> str:
    """*path* with the user's home directory shown as ``~``."""
    home = str(Path.home())
    if path == home:
        return "~"
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def _display_name(row: dict) -> str:
    """The name the user calls a project: its directory's basename, else the stored name."""
    return os.path.basename(row["path"] or "") or row["name"]


def _read_roster() -> list[dict]:
    """The managed workspaces, each with its root and its active projects; ``[]`` on any error.

    A workspace is managed while it holds a root or a project and has not been
    retired. A project is its ``projects`` row unless it vanished from disk
    or is a second clone another project's contract entry marks ``copy_of``.
    """
    from gaia.identity_shape import COPY_MARK_KEY, classify_identity_shape, is_reserved_slug
    from gaia.store.project_context import workspace_sections, workspaces_with_section
    from gaia.store.writer import _connect

    con = _connect()
    try:
        workspaces = con.execute(
            "SELECT name, root_path FROM workspaces WHERE status != 'retired' ORDER BY name"
        ).fetchall()
        projects = con.execute(
            "SELECT workspace, name, path, group_name FROM projects WHERE status = 'active'"
        ).fetchall()
        copies = set()
        for ws in workspaces_with_section(con, "project_identity"):
            payload = workspace_sections(con, ws)["project_identity"]["payload"]
            if isinstance(payload, dict) and classify_identity_shape(payload) == "map":
                copies.update(
                    os.path.normpath(entry["local_path"]) for slug, entry in payload.items()
                    if not is_reserved_slug(slug) and isinstance(entry, dict)
                    and entry.get(COPY_MARK_KEY) and entry.get("local_path")
                )
    finally:
        con.close()

    members: dict = {}
    for row in projects:
        project = {"name": row["name"], "path": row["path"], "group": row["group_name"] or "",
                   "pending": 0, "dispatches": 0}
        if not (project["path"] and os.path.normpath(project["path"]) in copies):
            members.setdefault(row["workspace"], []).append(project)
    roster = []
    for ws in workspaces:
        held = members.get(ws["name"], [])
        if held:
            root = ws["root_path"] or _workspace_root([p["path"] for p in held if p["path"]])
            roster.append({"name": ws["name"], "path": _home_relative(root) if root else "",
                           "projects": held})
    return roster


def _count_dispatches(roster: list[dict]) -> None:
    """Add to each project the turns dispatched to it in the window, matching by path, else by a unique name."""
    from gaia.store.reader import dispatch_counts_by_project

    every = [p for ws in roster for p in ws["projects"]]
    by_path = {os.path.normpath(p["path"]): p for p in every if p["path"]}
    by_name: dict = {}
    for p in every:
        by_name.setdefault(_display_name(p).lower(), []).append(p)
    for display, turns in dispatch_counts_by_project(_DISPATCH_WINDOW_DAYS).items():
        name, _, rest = display.partition(" (")
        target = by_path.get(os.path.normpath(rest[:-1])) if rest.endswith(")") else None
        named = by_name.get(name.lower().rstrip("."), [])
        if target is None and len(named) == 1:
            target = named[0]
        if target is not None:
            target["dispatches"] += turns


def build_projects_section(max_chars: int) -> str:
    """Render the Workspaces I manage section within ``max_chars``, or "" when there are none.

    Every workspace with its path, counts and groups; each group lists its
    projects busiest first (turns dispatched in the window, then live-pending
    count, then name). When the full section does not fit it degrades in
    order: three projects per group, only the projects with pending work,
    group names with counts, one line per workspace. The two pointer lines
    land whatever ``max_chars`` is.
    """
    return projects_renderer()(max_chars)


def projects_renderer():
    """``build_projects_section`` as a function of ``max_chars`` over one read of the database."""
    try:
        roster = _read_roster()
        if not roster:
            return lambda max_chars: ""
        from gaia.store.reader import count_pending_by_initiative
        from gaia.store.writer import normalize_initiative

        _count_dispatches(roster)
        every = [p for ws in roster for p in ws["projects"]]
        pending = count_pending_by_initiative(
            sorted({normalize_initiative(_display_name(p)) for p in every} - {None})
        )
        for p in every:
            p["pending"] = pending.get(normalize_initiative(_display_name(p)), 0)

        def busiest_first(members: list[dict]) -> list[dict]:
            return sorted(members, key=lambda p: (-p["dispatches"], -p["pending"], _display_name(p)))

        def listing(members: list[dict], cap: int) -> str:
            ordered = busiest_first(members)
            shown = [p for i, p in enumerate(ordered) if i < cap or p["pending"]]
            items = [f"{_display_name(p)}({p['pending']})" if p["pending"] else _display_name(p)
                     for p in shown]
            if len(ordered) > len(shown):
                items.append(f"+{len(ordered) - len(shown)}")
            return "[" + ", ".join(items) + "]"

        def groups_of(ws: dict) -> list[tuple[str, list[dict]]]:
            named: dict = {}
            for p in ws["projects"]:
                named.setdefault(p["group"], []).append(p)
            return [(g, named[g]) for g in sorted(named) if g] + (
                [("", named[""])] if "" in named else [])

        group_total = sum(1 for ws in roster for g, _ in groups_of(ws) if g)
        total = (f"Total: {len(roster)} workspaces · {group_total} groups · "
                 f"{len(every)} projects")

        def workspace_line(ws: dict) -> str:
            groups = sum(1 for g, _ in groups_of(ws) if g)
            location = f" path={ws['path']}" if ws["path"] else ""
            return f"workspace={ws['name']}{location} groups={groups} projects={len(ws['projects'])}"

        def lines_of(ws: dict, level: str, cap: int) -> list[str]:
            if level == "workspaces":
                return []
            groups = groups_of(ws)
            if level == "counts":
                named = [f"{g}={len(members)}" for g, members in groups if g]
                return [f"  {', '.join(named)}"] if named else []
            return [f"  {g}={listing(members, cap)}" if g else f"  {listing(members, cap)}"
                    for g, members in groups]

        def render(level: str, cap: int = 0) -> str:
            body = [line for ws in roster for line in (workspace_line(ws), *lines_of(ws, level, cap))]
            return "\n".join([PROJECTS_HEADER, total, *body])

        levels = [render("projects", _FULL_GROUP_CAP), render("projects", _NARROW_GROUP_CAP),
                  render("projects"), render("counts"), render("workspaces")]

        def fit(max_chars: int) -> str:
            return next((text for text in levels if len(text) <= max_chars), levels[-1])
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
    """The user rows the orchestrator's context indexes: every live fact and preference of the user."""
    from gaia.store.reader import user_memory_rows

    return user_memory_rows()


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

        from gaia.project import current

        rows = orchestrator_user_rows()
        heading = f"# Gaia session context (source: {source})"
        prefix = [heading, *(a for a in (*alarms, build_schema_direction_block()) if a)]
        environment = build_environment_section(session_id)
        due = build_due_section(_read_workspace_identity() or current())
        snapshot = _snapshot(session_id)
        resume = render_resume_section(snapshot) if snapshot else ""
        user, kept = render_user_sections(rows), rows
        roster = projects_renderer()

        if len(_joined(*prefix, *notices, user, due, resume, environment, roster(sys.maxsize))) > BIRTH_BUDGET:
            notices = ()
        floor = roster(0)
        if snapshot and len(_joined(*prefix, user, due, resume, environment, floor)) > BIRTH_BUDGET:
            resume = render_resume_section(snapshot, counts_only=True)
        head = _joined(*prefix, *notices, user, due, resume, environment)
        projects = roster(BIRTH_BUDGET - len(head) - 2)
        if len(_joined(*prefix, user, due, resume, environment, floor)) > BIRTH_BUDGET:
            room = BIRTH_BUDGET - len(_joined(*prefix, due, resume, environment, floor)) - 2
            user, kept = fit_user_sections(rows, room)
            head = _joined(*prefix, user, due, resume, environment)
            projects = roster(BIRTH_BUDGET - len(head) - 2)
        text = _joined(head, projects)
        if text == heading:
            return ""
        if record_injection:
            record_user_rows_injected(kept)
        return text
    except Exception as exc:
        logger.debug("build_session_context failed (non-fatal): %s", exc)
        return "\n\n".join(a for a in alarms if a)
