"""
ContextWriter: validates and persists CONTEXT_UPDATE blocks from agent output.

Parses CONTEXT_UPDATE blocks, enforces write permissions via
agent_contract_permissions in ~/.gaia/gaia.db, and upserts to
project_context_contracts.

CONTEXT_UPDATE format::

    CONTEXT_UPDATE:
    {
      "contract": "<contract_name>",
      "payload": { ... }
    }

Public API:
    - parse_context_update(agent_output) -> Optional[dict]
    - validate_permission(update, agent_name, cloud_scope, db_path) -> (allowed, message)
    - apply_update(update, agent_name, workspace, db_path) -> dict
    - process_agent_output(agent_output, task_info) -> dict
    - process_context_updates(agent_output, task_info, find_claude_dir_fn=None) -> Optional[dict]
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level permissions cache (cleared between tests via .clear())
# Keyed by (agent_name, cloud_scope, db_path_str)
# ---------------------------------------------------------------------------
_permissions_cache: dict = {}


# ============================================================================
# 1. parse_context_update
# ============================================================================

def parse_context_update(agent_output: str) -> Optional[dict]:
    """Extract and parse a CONTEXT_UPDATE block from agent output.

    Accepts the contract/payload schema only::

        CONTEXT_UPDATE:
        { "contract": "application_services", "payload": { ... } }

    Returns None when:
    - No marker is found
    - The JSON is malformed
    - The parsed value is not a dict
    - Required keys ``contract`` and ``payload`` are absent
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

    remaining = _strip_code_fence(remaining)
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

    if "contract" not in parsed or "payload" not in parsed:
        logger.warning(
            "CONTEXT_UPDATE block missing required keys 'contract' and 'payload'. "
            "Got keys: %s",
            list(parsed.keys()),
        )
        return None

    return parsed


def _strip_code_fence(text: str) -> str:
    """Remove leading/trailing markdown code fences (``` or ```json)."""
    if not text.startswith("```"):
        return text
    fence_lines = text.split("\n")
    fence_lines.pop(0)  # opening ```[lang]
    for i in range(len(fence_lines) - 1, -1, -1):
        if fence_lines[i].strip() == "```":
            fence_lines.pop(i)
            break
    return "\n".join(fence_lines).strip()


# ============================================================================
# 2. validate_permission
# ============================================================================

def _get_db_path() -> Optional[Path]:
    try:
        from gaia.paths import db_path
        return db_path()
    except Exception:
        return None


def _load_writable_contracts(
    agent_name: str,
    cloud_scope: Optional[str],
    db_path: Optional[Path],
) -> set:
    """Return the contracts agent_name may write under the given cloud scope.

    Query::

        SELECT contract_name FROM agent_contract_permissions
        WHERE agent_name = ? AND can_write = 1
          AND (cloud_scope IS NULL OR cloud_scope = ?);

    A NULL ``cloud_scope`` on the permission row means "all providers". A
    non-NULL value only matches when the caller's cloud_scope equals it.
    """
    cache_key = (agent_name, cloud_scope, str(db_path) if db_path else None)
    if cache_key in _permissions_cache:
        return _permissions_cache[cache_key]

    resolved = db_path or _get_db_path()
    if resolved is None or not resolved.exists():
        logger.debug("gaia.db not found; no write grants for '%s'", agent_name)
        _permissions_cache[cache_key] = set()
        return set()

    try:
        con = sqlite3.connect(str(resolved))
        rows = con.execute(
            """
            SELECT contract_name FROM agent_contract_permissions
            WHERE agent_name = ? AND can_write = 1
              AND (cloud_scope IS NULL OR cloud_scope = ?)
            """,
            (agent_name, cloud_scope),
        ).fetchall()
        con.close()
        contracts = {row[0] for row in rows}
        _permissions_cache[cache_key] = contracts
        return contracts
    except sqlite3.Error as exc:
        logger.warning(
            "Error loading agent_contract_permissions for '%s': %s",
            agent_name, exc,
        )
        _permissions_cache[cache_key] = set()
        return set()


def _format_rejection_message(
    agent_name: str,
    contract_name: str,
    writable: set,
) -> str:
    allowed_list = ", ".join(sorted(writable)) if writable else "(none)"
    return (
        f"CONTEXT_UPDATE rejected: agent '{agent_name}' has no write permission "
        f"for contract '{contract_name}'. "
        f"Writable contracts for this agent: {allowed_list}."
    )


def validate_permission(
    update: dict,
    agent_name: str,
    cloud_scope: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Check whether agent_name may write the contract in update.

    Returns:
        (True, "")           -- allowed
        (False, message)     -- blocked; message is deterministic and names
                                agent_name, contract_name, and writable contracts
    """
    contract_name = update.get("contract", "")
    writable = _load_writable_contracts(agent_name, cloud_scope, db_path)

    if contract_name in writable:
        return True, ""

    return False, _format_rejection_message(agent_name, contract_name, writable)


# ============================================================================
# 3. apply_update
# ============================================================================

def _derive_workspace() -> str:
    try:
        from gaia.project import current as _project_current
        identity = _project_current()
        return identity if identity else "global"
    except Exception:
        return "global"


def apply_update(
    update: dict,
    agent_name: str,
    workspace: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> dict:
    """Upsert a validated CONTEXT_UPDATE into project_context_contracts.

    ``workspace`` defaults to gaia.project.current() when not provided.

    Returns an audit dict: ``{timestamp, agent, contract, workspace, success, error}``.
    """
    contract_name = update["contract"]
    payload = update["payload"]
    ws = workspace or _derive_workspace()
    now = datetime.now(timezone.utc).isoformat()

    audit = {
        "timestamp": now,
        "agent": agent_name,
        "contract": contract_name,
        "workspace": ws,
        "success": False,
        "error": None,
    }

    resolved = db_path or _get_db_path()
    if resolved is None or not resolved.exists():
        audit["error"] = f"gaia.db not found (path={resolved})"
        logger.error("apply_update: gaia.db unavailable: %s", resolved)
        return audit

    try:
        payload_json = json.dumps(payload)
    except (TypeError, ValueError) as exc:
        audit["error"] = f"payload is not JSON-serializable: {exc}"
        return audit

    try:
        con = sqlite3.connect(str(resolved))
        con.execute("PRAGMA foreign_keys = ON")

        # Ensure workspace row exists so the FK on project_context_contracts holds.
        con.execute(
            "INSERT OR IGNORE INTO workspaces (name, identity, created_at) VALUES (?, ?, ?)",
            (ws, ws, now),
        )

        con.execute(
            """
            INSERT INTO project_context_contracts
                (workspace, contract_name, payload, metadata, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(workspace, contract_name) DO UPDATE SET
                payload    = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (ws, contract_name, payload_json, None, now),
        )
        con.commit()
        con.close()

        audit["success"] = True
        logger.info(
            "Context updated by %s: workspace=%s contract=%s",
            agent_name, ws, contract_name,
        )
        return audit

    except sqlite3.Error as exc:
        audit["error"] = str(exc)
        logger.error(
            "Failed to upsert contract '%s' for agent '%s': %s",
            contract_name, agent_name, exc,
        )
        return audit


# ============================================================================
# 4. process_agent_output
# ============================================================================

def process_agent_output(agent_output: str, task_info: dict) -> dict:
    """Orchestrate parse -> validate -> apply for one agent output string.

    Parameters
    ----------
    agent_output : str
        Full output text from the agent.
    task_info : dict
        Required: ``agent_type`` (str).
        Optional: ``db_path`` (Path), ``cloud_scope`` (str), ``workspace`` (str).

    Returns
    -------
    dict
        ``{updated, contract, rejected, error}``
    """
    result = {
        "updated": False,
        "contract": None,
        "rejected": [],
        "error": None,
    }

    update = parse_context_update(agent_output)
    if update is None:
        return result

    agent_name = task_info.get("agent_type", "unknown")
    db_path = task_info.get("db_path")
    cloud_scope = task_info.get("cloud_scope")
    workspace = task_info.get("workspace")

    allowed, rejection_msg = validate_permission(update, agent_name, cloud_scope, db_path)
    if not allowed:
        result["rejected"] = [update.get("contract", "")]
        result["error"] = rejection_msg
        logger.warning(rejection_msg)
        return result

    audit = apply_update(update, agent_name, workspace=workspace, db_path=db_path)

    if audit["success"]:
        result["updated"] = True
        result["contract"] = audit["contract"]
    else:
        result["error"] = audit["error"]

    return result


# ============================================================================
# 5. process_context_updates (thin adapter for subagent_stop integration)
# ============================================================================

def process_context_updates(
    agent_output: str,
    task_info: dict,
    find_claude_dir_fn=None,
) -> Optional[dict]:
    """Process CONTEXT_UPDATE blocks from agent output.

    Validates write permission via agent_contract_permissions and writes to
    project_context_contracts in ~/.gaia/gaia.db.

    All errors are caught and logged; returns None on unexpected failure so
    the hook flow is never interrupted.

    Args:
        agent_output: Complete output from agent execution.
        task_info: Task metadata (agent, description, task_id).
        find_claude_dir_fn: Unused -- kept for API compatibility.
    """
    try:
        agent_name = task_info.get("agent", "unknown")
        writer_task_info = {
            "agent_type": agent_name,
            "db_path": task_info.get("db_path"),
            "cloud_scope": task_info.get("cloud_scope"),
            "workspace": task_info.get("workspace"),
        }

        result = process_agent_output(agent_output, writer_task_info)

        if result.get("updated"):
            logger.info(
                "Context updated by %s: contract=%s",
                agent_name,
                result.get("contract"),
            )
        if result.get("rejected"):
            logger.warning(
                "Context write rejected for %s: %s",
                agent_name,
                result.get("error", ""),
            )

        return result

    except Exception as exc:
        logger.debug("Context update processing failed (non-fatal): %s", exc)
        return None
