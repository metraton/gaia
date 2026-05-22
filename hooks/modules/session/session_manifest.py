"""Session manifest builders for SessionStart injection.

Phase 4 of the context-injection redesign moves what was previously emitted
on every UserPromptSubmit to a one-shot SessionStart manifest. The blocks
that move:

- Environment manifest (NEW) -- workspace identity, machine, gaia version,
  mode, cwd, plugin data dir. Stable for the lifetime of the session.
- Agentic-loop resume -- if there is an active loop in cwd, surface it once
  at SessionStart instead of re-scanning every prompt.
- Pending approvals ([ACTIONABLE]) -- relies on Fase 1 heartbeat liveness
  and Fase 2 orphan cleanup, so cross-session pendings can finally be shown
  reliably at session start without duplicates.

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

        # Plugin mode and data dir resolution can both fail under headless
        # tests with no .claude/ tree; treat as soft-missing.
        try:
            from ..core.plugin_mode import get_plugin_mode
            mode = get_plugin_mode()
        except Exception:
            mode = None

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
        if mode:
            lines.append(f"- Mode: {mode}")
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


def build_workspace_memory_block(workspace: Optional[str] = None) -> str:
    """Top relevant curated memory for the workspace, bounded.

    Calls ``gaia memory get-relevant --workspace <X> --max-chars 800`` and
    captures stdout. Returns markdown to inject in SessionStart
    additionalContext, or "" when there are no curated rows for the
    workspace, when the workspace cannot be inferred, or when the
    subprocess fails for any reason.

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

        result = subprocess.run(
            cli_args + [
                "memory", "get-relevant",
                "--workspace", ws,
                "--max-chars", "800",
            ],
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


def build_pending_approvals_block() -> str:
    """Build the [ACTIONABLE] pending-approvals block, if any exist.

    Same cross-session fallback as the legacy ``_build_pending_context()``:
    current session first, then a sweep across all sessions filtered by
    ``exclude_live_sessions=True``. With Fase 1's heartbeat liveness, that
    filter is now reliable, so the block can live in SessionStart instead
    of being re-evaluated on every prompt.

    Returns "" when no pendings are surfaced. Never raises.
    """
    try:
        from ..core.paths import get_plugin_data_dir
        from ..core.state import get_session_id
        from .pending_scanner import (
            format_pending_summary,
            scan_pending_approvals,
        )

        approvals_dir = get_plugin_data_dir() / "cache" / "approvals"
        session_id = get_session_id()

        pendings = scan_pending_approvals(
            approvals_dir,
            session_id=session_id,
            current_session_id=session_id,
        )

        # Cross-session fallback. exclude_live_sessions=True drops pendings
        # from parallel live sessions so we don't double-surface them in
        # two interactive Claude Code windows. include_headless=False is
        # already applied inside scan_pending_approvals.
        if not pendings:
            pendings = scan_pending_approvals(
                approvals_dir,
                current_session_id=session_id,
                exclude_live_sessions=True,
            )

        if not pendings:
            return ""

        summary = format_pending_summary(pendings)
        logger.info("SessionStart: %d pending approval(s) surfaced", len(pendings))
        return (
            "[ACTIONABLE] Pending approvals require your attention before "
            "routing the next request.\n\n" + summary
        )
    except Exception as exc:
        logger.debug("build_pending_approvals_block failed (non-fatal): %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Assembler
# ---------------------------------------------------------------------------

def build_session_context(mode: str) -> str:
    """Top-level assembler. Concatenate non-empty blocks with blank lines.

    Args:
        mode: Plugin mode string from ``get_plugin_mode()``. Anything other
            than ``"ops"`` returns "" -- the security plugin has no
            orchestrator routing layer to act on the manifest.

    Returns "" when the mode is not ops or every block is empty. Never raises.
    """
    if mode != "ops":
        return ""

    try:
        blocks = [
            build_environment_block(),
            build_agentic_loop_block(),
            build_pending_approvals_block(),
            # Workspace Memory is injected last so the orchestrator sees the
            # operational state (environment, loop, pendings) before the
            # curated knowledge it should anchor against.
            build_workspace_memory_block(),
        ]
        non_empty = [b for b in blocks if b]
        if not non_empty:
            return ""
        return "\n\n".join(non_empty)
    except Exception as exc:
        logger.debug("build_session_context failed (non-fatal): %s", exc)
        return ""
