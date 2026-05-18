"""
ContextWriter module for gaia-ops progressive context enrichment.

Parses CONTEXT_UPDATE blocks from agent output, validates write permissions
against agent_permissions in ~/.gaia/gaia.db (B3 M5), and applies updates
to the SQLite substrate via the gaia.store library.

New CONTEXT_UPDATE schema (B3 M5.b):
    CONTEXT_UPDATE:
    {
      "table": "<table_name>",
      "rows": [{...}, ...]
    }

The workspace is derived automatically from gaia.project.current() — agents
do NOT pass it in the CONTEXT_UPDATE block.

topic_key is accepted as an optional field within each row dict when the
target table supports it.

Public API:
    - parse_context_update(agent_output) -> Optional[dict]
    - validate_permissions(update, agent_type) -> (dict, list)
    - apply_update(update, agent_type) -> dict
    - process_agent_output(agent_output, task_info) -> dict
    - process_context_updates(agent_output, task_info, find_claude_dir_fn=None) -> Optional[dict]
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level cache (cleared between tests via monkeypatch / reload)
# ---------------------------------------------------------------------------
_permissions_cache: Dict[str, set] = {}


# ============================================================================
# 1. parse_context_update
# ============================================================================

def parse_context_update(agent_output: str) -> Optional[dict]:
    """Extract and parse a CONTEXT_UPDATE block from agent output.

    Accepts both the new table/rows schema and the legacy section-dict schema
    for backward compatibility during migration. New schema is preferred:

    New schema::
        CONTEXT_UPDATE:
        { "table": "apps", "rows": [{...}] }

    Legacy schema (deprecated, still accepted for backward compat)::
        CONTEXT_UPDATE:
        { "section_name": {...} }

    Returns None when:
    - No marker is found
    - The JSON is malformed
    - The parsed value is not a dict
    """
    marker = "CONTEXT_UPDATE:"
    lines = agent_output.split("\n")

    marker_idx = None
    for i, line in enumerate(lines):
        if line.strip() == marker:
            marker_idx = i
            break

    if marker_idx is None:
        return None

    remaining = "\n".join(lines[marker_idx + 1:]).strip()

    if not remaining:
        return None

    # Strip markdown code fences
    if remaining.startswith("```"):
        fence_lines = remaining.split("\n")
        fence_lines.pop(0)
        for i in range(len(fence_lines) - 1, -1, -1):
            if fence_lines[i].strip() == "```":
                fence_lines.pop(i)
                break
        remaining = "\n".join(fence_lines).strip()

    if not remaining:
        return None

    decoder = json.JSONDecoder()
    try:
        parsed, _ = decoder.raw_decode(remaining)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Malformed JSON in CONTEXT_UPDATE block: %s", exc)
        return None

    if not isinstance(parsed, dict):
        return None

    return parsed


# ============================================================================
# 2. validate_permissions -- queries agent_permissions in ~/.gaia/gaia.db
# ============================================================================

def _get_db_path() -> Optional[Path]:
    """Resolve gaia.db path. Returns None on import error."""
    try:
        from gaia.paths import db_path
        return db_path()
    except Exception:
        return None


def _load_agent_tables(agent_type: str, db_path: Optional[Path] = None) -> set:
    """Return the set of tables the agent is allowed to write.

    Queries agent_permissions WHERE agent_name=? AND allow_write=1.
    Returns empty set if DB unavailable or agent has no permissions.
    """
    cache_key = agent_type
    if cache_key in _permissions_cache:
        return _permissions_cache[cache_key]

    resolved = db_path or _get_db_path()
    if resolved is None or not resolved.exists():
        logger.debug("gaia.db not found for permissions check of '%s'", agent_type)
        _permissions_cache[cache_key] = set()
        return set()

    try:
        con = sqlite3.connect(str(resolved))
        rows = con.execute(
            "SELECT table_name FROM agent_permissions WHERE agent_name = ? AND allow_write = 1",
            (agent_type,),
        ).fetchall()
        con.close()
        tables = {row[0] for row in rows}
        _permissions_cache[cache_key] = tables
        return tables
    except sqlite3.Error as exc:
        logger.warning("Error loading agent_permissions for '%s': %s", agent_type, exc)
        _permissions_cache[cache_key] = set()
        return set()


def validate_permissions(
    update: dict,
    agent_type: str,
    _db_path: Optional[Path] = None,
) -> tuple:
    """Validate which tables the agent is allowed to write.

    Supports both new schema (update has 'table' key) and legacy section-dict schema.

    Returns:
        (allowed_update, rejected_items) where:
        - allowed_update: the original update dict if table is allowed, else {}
        - rejected_items: list of rejected table names or section names
    """
    allowed_tables = _load_agent_tables(agent_type, _db_path)

    # New schema: { "table": "apps", "rows": [...] }
    if "table" in update and "rows" in update:
        table = update["table"]
        if table in allowed_tables:
            return update, []
        else:
            return {}, [table]

    # Legacy schema: { "section_name": {...}, ... } — kept for backward compat
    # In legacy mode, we cannot enforce per-table permissions (no DB mapping to
    # legacy section names). Accept everything to avoid blocking existing flows.
    # This path will be removed once all agents migrate to the new schema.
    logger.debug(
        "Agent '%s' emitted legacy CONTEXT_UPDATE schema. "
        "Migrate to {table, rows} schema for per-table enforcement.",
        agent_type,
    )
    return update, []


# ============================================================================
# 3. apply_update -- dispatches to gaia.store via bulk_upsert
# ============================================================================

def _derive_workspace() -> str:
    """Derive workspace identity via gaia.project.current().

    Falls back to 'global' if the project module is unavailable.
    """
    try:
        from gaia.project import current as _project_current
        identity = _project_current()
        return identity if identity else "global"
    except Exception:
        return "global"


def apply_update(
    update: dict,
    agent_type: str,
    *,
    db_path: Optional[Path] = None,
) -> dict:
    """Apply a validated CONTEXT_UPDATE to the SQLite substrate via gaia.store.

    New schema: dispatches to bulk_upsert(table, workspace, rows, agent).
    Workspace is derived from gaia.project.current() — never from the update.

    Returns an audit entry dict with success/error status.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    audit_entry = {
        "timestamp": timestamp,
        "agent": agent_type,
        "success": False,
        "error": None,
    }

    # New schema: { "table": "apps", "rows": [...] }
    if "table" in update and "rows" in update:
        table = update["table"]
        rows = update.get("rows", [])
        workspace = _derive_workspace()

        try:
            from gaia.store import bulk_upsert

            kwargs = {}
            if db_path is not None:
                kwargs["db_path"] = db_path

            result = bulk_upsert(
                table=table,
                workspace=workspace,
                rows=rows,
                agent=agent_type,
                **kwargs,
            )
            applied = result.get("applied", 0)
            rejected = result.get("rejected", 0)
            audit_entry["success"] = True
            audit_entry["table"] = table
            audit_entry["rows_applied"] = applied
            audit_entry["rows_rejected"] = rejected
            if applied == 0 and rejected > 0:
                audit_entry["success"] = False
                audit_entry["error"] = f"All {rejected} rows rejected (permission denied for table '{table}')"
            logger.info(
                "Context updated by %s: table=%s applied=%d rejected=%d",
                agent_type, table, applied, rejected,
            )
            return audit_entry

        except ImportError as exc:
            audit_entry["error"] = f"gaia.store not available: {exc}"
            logger.error("gaia.store import failed: %s", exc)
            return audit_entry
        except Exception as exc:
            audit_entry["error"] = str(exc)
            logger.error("Failed to apply context update via store: %s", exc)
            return audit_entry

    # Legacy schema fallback: log and skip (cannot route to store without table name)
    logger.debug(
        "Legacy CONTEXT_UPDATE from '%s' — skipping store write (no table/rows keys). "
        "Migrate to {\"table\": \"...\", \"rows\": [...]} schema.",
        agent_type,
    )
    audit_entry["success"] = False
    audit_entry["error"] = "Legacy schema not persisted: missing 'table' and 'rows' keys"
    return audit_entry


# ============================================================================
# 4. process_agent_output
# ============================================================================

def process_agent_output(agent_output: str, task_info: dict) -> dict:
    """Orchestrate the full context-update flow.

    Steps: parse -> validate -> apply.

    Parameters
    ----------
    agent_output : str
        Full agent output string.
    task_info : dict
        Must contain: ``agent_type``.
        Optional: ``db_path`` (Path, for tests).

    Returns
    -------
    dict
        ``{updated, table, rows_applied, rejected, error}``
    """
    result = {
        "updated": False,
        "table": None,
        "rows_applied": 0,
        "rejected": [],
        "error": None,
    }

    # 1. Parse CONTEXT_UPDATE
    update = parse_context_update(agent_output)
    if update is None:
        return result

    agent_type = task_info.get("agent_type", "unknown")
    db_path = task_info.get("db_path")  # Optional[Path], for tests

    # 2. Validate permissions
    allowed, rejected = validate_permissions(update, agent_type, db_path)
    result["rejected"] = rejected

    if not allowed:
        return result

    # 3. Apply update
    audit = apply_update(allowed, agent_type, db_path=db_path)

    if audit.get("success"):
        result["updated"] = True
        result["table"] = audit.get("table")
        result["rows_applied"] = audit.get("rows_applied", 0)
    else:
        result["error"] = audit.get("error")

    return result


# ============================================================================
# 5. process_context_updates (thin wrapper for subagent_stop integration)
# ============================================================================

def process_context_updates(
    agent_output: str,
    task_info: dict,
    find_claude_dir_fn=None,
) -> Optional[dict]:
    """
    Process CONTEXT_UPDATE blocks from agent output via context_writer.

    Validates permissions against agent_permissions in ~/.gaia/gaia.db and
    writes to the SQLite substrate via gaia.store.bulk_upsert.

    This function MUST NOT break the existing hook flow -- all errors are caught
    and logged, returning None on failure.

    Args:
        agent_output: Complete output from agent execution
        task_info: Task metadata (agent, description, task_id)
        find_claude_dir_fn: Unused (kept for API compatibility). Workspace is
            derived from gaia.project.current(), not from .claude directory.

    Returns:
        Result dict from process_agent_output, or None on error
    """
    try:
        agent_type = task_info.get("agent", "unknown")
        task_info_for_writer = {
            "agent_type": agent_type,
        }

        result = process_agent_output(agent_output, task_info_for_writer)

        if result and result.get("updated"):
            logger.info(
                "Context updated by %s: table=%s rows_applied=%d",
                agent_type,
                result.get("table"),
                result.get("rows_applied", 0),
            )
        if result and result.get("rejected"):
            logger.debug(
                "Context tables rejected for %s: %s",
                agent_type, result.get("rejected", []),
            )

        return result

    except Exception as e:
        logger.debug("Context update processing failed (non-fatal): %s", e)
        return None
