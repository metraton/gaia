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


def _extract_projects_from_identity(payload: dict, workspace: str,
                                    path_lookup: dict) -> list[tuple[str, str]]:
    """Pull (name, path) pairs out of one ``project_identity`` payload.

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
    """
    out: list[tuple[str, str]] = []
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
            out.append((name, path))
        return out

    repos = payload.get("workspace_repos")
    if isinstance(repos, list) and repos:
        for r in repos:
            if not isinstance(r, dict):
                continue
            name = r.get("name") or ""
            if not name:
                continue
            out.append((name, _resolve(name)))
        return out

    name = payload.get("name") or workspace
    out.append((name, _resolve(name)))
    return out


def build_projects_context_block(max_chars: int = 1400) -> str:
    """Render the active-context project index for the SessionStart manifest.

    This is NOT an index of every git repo on disk. The source is the set of
    projects that have **active project context** -- a ``project_identity`` row
    in ``project_context_contracts``. That filter is the point: it includes
    AOS (which lives only in the ``me`` workspace's hand-authored contract,
    with absolute ``local_path``) and naturally excludes the dozens of cloned
    reference repos under ``me`` that were scanned into the raw ``projects``
    table but never given a project context. No path-prefix filtering is used.

    Each entry is ``- <name>: <path>`` -- the path being the value the
    orchestrator wants (where the project lives on disk). Workspace is not a
    grouping axis here; it is only used internally to resolve relative/missing
    paths against the ``projects`` table.

    Framed as ``## Project Context — Projects`` so it reads as part of the
    project-context setup the orchestrator receives at SessionStart (it is
    emitted immediately after ``## Environment``), not as an orphan section.

    Budget: bounded to ``max_chars`` (default 1400). The real active-context
    set is small (~17 entries, ~1.2 KB today) and is meant to land in full;
    the bound is a guard rail, not a target. On overflow we drop entries from
    the tail and append a recoverable footer. Fail-safe: any error returns "".
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

    entries: list[tuple[str, str]] = []
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
        for name, path in _extract_projects_from_identity(payload, ws, path_lookup):
            key = (name, path)
            if key in seen:
                continue
            seen.add(key)
            entries.append((name, path))

    if not entries:
        return ""

    total_available = len(entries)
    header = "## Project Context — Projects"

    def _render(items: list[tuple[str, str]]) -> str:
        lines = [header, ""]
        for name, path in items:
            lines.append(f"- {name}: {path}" if path else f"- {name}")
        return "\n".join(lines)

    block = _render(entries)
    # Budget: drop from the tail until it fits, then add a recoverable footer.
    if len(block) > max_chars:
        kept = list(entries)
        while kept and len(_render(kept)) > max_chars:
            kept.pop()
        dropped = total_available - len(kept)
        block = _render(kept)
        if dropped > 0:
            footer = f"\n... ({dropped} more, use 'gaia context get')"
            if len(block) + len(footer) <= max_chars:
                block = block + footer

    return block


def build_pending_approvals_block() -> str:
    """Build the [ACTIONABLE] pending-approvals block, if any exist.

    DB-only since Task E of the approval redesign: all pending types
    (T3 commands, COMMAND_SET batches, and SCOPE_FILE_PATH file-write
    blocks) are now written exclusively to gaia.db via
    gaia.approvals.store.insert_requested().  The filesystem supplement
    that was kept in Tasks C-D is removed: scan_pending_db() is the sole
    read source.

    Scoping: DB query uses all_sessions=True (no session filter).  The
    session_id stored in approval rows is the main session while
    $CLAUDE_SESSION_ID inside a subagent is the subagent id -- filtering by
    session would silently drop all subagent pendings.  The DB is
    per-machine so all rows are from the same user.

    Returns "" when no pendings are surfaced. Never raises.
    """
    try:
        from .pending_scanner import (
            format_pending_summary,
            scan_pending_db,
        )

        pendings = scan_pending_db()

        if not pendings:
            return ""

        # Sort oldest-first so the orchestrator sees the most urgent
        # (longest-waiting) pending first.
        pendings.sort(key=lambda x: x["timestamp"])

        summary = format_pending_summary(pendings)
        logger.info(
            "SessionStart: %d pending approval(s) surfaced (DB-only)",
            len(pendings),
        )
        return (
            "[ACTIONABLE] Pending approvals require your attention before "
            "routing the next request.\n\n" + summary
        )
    except Exception as exc:
        logger.debug("build_pending_approvals_block failed (non-fatal): %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Per-turn VERIFIED pending approvals (UserPromptSubmit)
# ---------------------------------------------------------------------------
#
# Why a SEPARATE builder from build_pending_approvals_block():
#
# The SessionStart block above is a one-shot, human-readable *summary*
# (format_pending_summary) deliberately moved OUT of per-turn UserPromptSubmit
# because re-emitting a summary on every prompt added noise without changing
# the answer (see this module's header, "What does NOT move").
#
# The per-turn injection here solves a DIFFERENT problem: it lets the
# orchestrator PRESENT an approval for informed consent directly from injected
# context, WITHOUT dispatching a subagent to derive/verify it. That dispatch's
# SubagentStop is what caused a pending-revocation bug. To present without a
# dispatch the orchestrator needs the full sealed payload (verbatim
# exact_content, the command_set list, scope/risk/rationale/rollback) AND a
# trustworthy VERIFIED marker -- none of which the summary carries.
#
# Noise control (the reason the summary was moved to SessionStart): this
# builder emits "" when there are no currently-pending VERIFIED approvals, so a
# turn with nothing pending injects nothing. It only speaks when there is
# actionable, verified state to present.
#
# Scoping: identical to scan_pending_db() / build_pending_approvals_block() --
# all_sessions=True (no session filter). The DB is per-machine so every row is
# the same user, and pendings are written under the MAIN session while a
# subagent's $CLAUDE_SESSION_ID differs; a session filter would silently drop
# subagent-originated pendings.

def build_verified_pending_approvals() -> list:
    """Return the currently-pending approvals whose fingerprint VERIFIES.

    Reads pending rows via the same all_sessions=True DB scope as
    scan_pending_db(), then gates each row through
    gaia.approvals.chain.verify_fingerprint(): a row is included ONLY when its
    stored payload re-canonicalizes to the fingerprint recorded in its
    REQUESTED event. A row that fails verification (tampered, or with no
    REQUESTED baseline) is skipped and never returned as presentable.

    Each returned dict carries everything the orchestrator needs to present the
    approval for informed consent without any further dispatch::

        {
            "approval_id": "P-...",            # full id; verbatim Approve label uses [P-<nonce8>]
            "nonce_short": "<8 hex>",          # display nonce for the [P-<nonce8>] label
            "verified": True,                  # always True for returned rows
            "operation": "...",                # sealed payload field
            "exact_content": "...",            # verbatim command/content
            "scope": "...",
            "risk_level": "low|medium|high",
            "rationale": "...",
            "rollback_hint": "..." | None,
            "command_set": [ {"command": "...", "rationale": "..."}, ... ],  # [] if singular
            "age_human": "5 min",              # freshness indicator
            "age_seconds": 300.0,
            "session_id": "<originating session>",
        }

    Returns [] on any error (never raises) so the caller's fail-safe applies.
    """
    try:
        try:
            from gaia.approvals.store import list_pending
            from gaia.approvals.chain import verify_fingerprint, ChainTamperError
            from gaia.store.writer import _connect as _connect_db
        except ImportError:
            import pathlib as _pl
            import sys as _sys
            _repo = _pl.Path(__file__).resolve().parents[4]
            _sys.path.insert(0, str(_repo))
            from gaia.approvals.store import list_pending
            from gaia.approvals.chain import verify_fingerprint, ChainTamperError
            from gaia.store.writer import _connect as _connect_db

        from .pending_scanner import _format_age

        rows = list_pending(all_sessions=True)
    except Exception as exc:
        logger.debug("build_verified_pending_approvals: read failed (non-fatal): %s", exc)
        return []

    if not rows:
        return []

    verified: list = []
    con = None
    try:
        con = _connect_db()
        for row in rows:
            try:
                approval_id = row.get("id")
                if not approval_id:
                    continue
                payload_json = row.get("payload_json") or "{}"

                # VERIFIED gate: only rows whose stored payload matches the
                # fingerprint in their REQUESTED event are presentable. A
                # tamper (ChainTamperError) or a missing REQUESTED baseline
                # (ValueError) means the row is NOT safe to present -- skip it.
                try:
                    ok = verify_fingerprint(approval_id, payload_json, con)
                except (ChainTamperError, ValueError) as verr:
                    logger.debug(
                        "build_verified_pending_approvals: %s fails verification, "
                        "skipping (non-fatal): %s", approval_id, verr,
                    )
                    continue
                if not ok:
                    continue

                try:
                    payload = json.loads(payload_json)
                except (json.JSONDecodeError, TypeError):
                    payload = {}

                nonce_short = (
                    approval_id[2:10] if approval_id.startswith("P-")
                    else approval_id[:8]
                )

                command_set = payload.get("command_set") or []
                # Normalize command_set items to {command, rationale}.
                norm_command_set = []
                if isinstance(command_set, list):
                    for it in command_set:
                        if isinstance(it, dict) and it.get("command"):
                            norm_command_set.append({
                                "command": it.get("command"),
                                "rationale": it.get("rationale", ""),
                            })

                age_seconds = row.get("age_seconds", 0.0) or 0.0

                verified.append({
                    "approval_id": approval_id,
                    "nonce_short": nonce_short,
                    "verified": True,
                    "operation": payload.get("operation", ""),
                    "exact_content": payload.get("exact_content", ""),
                    "scope": payload.get("scope", ""),
                    "risk_level": payload.get("risk_level", "medium"),
                    "rationale": payload.get("rationale", ""),
                    "rollback_hint": payload.get("rollback_hint"),
                    "command_set": norm_command_set,
                    "age_human": _format_age(age_seconds),
                    "age_seconds": age_seconds,
                    "session_id": row.get("session_id", "unknown"),
                })
            except Exception as exc:
                logger.debug(
                    "build_verified_pending_approvals: skipping row %s: %s",
                    row.get("id"), exc,
                )
                continue
    except Exception as exc:
        logger.debug("build_verified_pending_approvals: failed (non-fatal): %s", exc)
        return []
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass

    # Oldest-first so the longest-waiting (most urgent) approval is presented first.
    verified.sort(key=lambda x: x.get("age_seconds", 0.0), reverse=True)
    return verified


def build_per_turn_pending_approvals_block() -> str:
    """Render the per-turn VERIFIED pending-approvals block for UserPromptSubmit.

    Emits "" when there are no currently-pending VERIFIED approvals -- a turn
    with nothing pending injects nothing (the noise concern that motivated
    moving the SessionStart summary out of per-turn injection).

    When verified pendings exist, renders a concise structured block carrying,
    per approval, the full sealed payload the orchestrator needs to present for
    informed consent WITHOUT dispatching a subagent. The block is explicitly
    marked VERIFIED so the orchestrator knows every entry passed
    verify_fingerprint and is safe to present verbatim.

    Returns "" on any error (never raises).
    """
    try:
        pendings = build_verified_pending_approvals()
        if not pendings:
            return ""

        lines = [
            "[PENDING-APPROVALS-VERIFIED] "
            f"{len(pendings)} pending approval(s) verified and presentable "
            "WITHOUT dispatch. Present any of these for consent directly from "
            "this block; do NOT dispatch a subagent to derive or verify them. "
            "The Approve label is [P-<nonce8>].",
            "",
        ]
        for i, p in enumerate(pendings, 1):
            lines.append(
                f"### #{i} [P-{p['nonce_short']}] (approval_id: {p['approval_id']})"
            )
            lines.append(f"- verified: true")
            lines.append(f"- operation: {p['operation']}")
            lines.append(f"- risk_level: {p['risk_level']}")
            lines.append(f"- scope: {p['scope']}")
            lines.append(f"- age: {p['age_human']}")
            if p.get("rationale"):
                lines.append(f"- rationale: {p['rationale']}")
            if p.get("rollback_hint"):
                lines.append(f"- rollback_hint: {p['rollback_hint']}")
            if p.get("command_set"):
                lines.append(
                    f"- command_set ({len(p['command_set'])} commands, "
                    f"single approval_id {p['approval_id']}):"
                )
                for c in p["command_set"]:
                    rat = f"  # {c['rationale']}" if c.get("rationale") else ""
                    lines.append(f"    - {c['command']}{rat}")
            else:
                lines.append(f"- exact_content: {p['exact_content']}")
            lines.append("")

        logger.info(
            "UserPromptSubmit: %d verified pending approval(s) injected per-turn",
            len(pendings),
        )
        return "\n".join(lines).rstrip()
    except Exception as exc:
        logger.debug(
            "build_per_turn_pending_approvals_block failed (non-fatal): %s", exc
        )
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
            # Project Context — Projects: the index of projects that have active
            # project context (a project_identity contract), each as name +
            # on-disk path. Emitted immediately after Environment so it reads as
            # part of the project-context setup the orchestrator receives -- it
            # lets a bare mention in memory (e.g. "AOS", "nfi") resolve to a
            # path the orchestrator already holds, without spending a subagent.
            build_projects_context_block(),
            build_agentic_loop_block(),
            build_pending_approvals_block(),
            # Workspace Memory is injected last so the orchestrator sees the
            # operational state (environment, projects, loop, pendings) before
            # the curated knowledge it should anchor against.
            build_workspace_memory_block(),
        ]
        non_empty = [b for b in blocks if b]
        if not non_empty:
            return ""
        return "\n\n".join(non_empty)
    except Exception as exc:
        logger.debug("build_session_context failed (non-fatal): %s", exc)
        return ""
