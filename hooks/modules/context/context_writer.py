"""
ContextWriter: validates and persists update_contracts clauses from agent output.

Parses the ``update_contracts`` array from the agent_contract_handoff envelope,
enforces write permissions via agent_contract_permissions in ~/.gaia/gaia.db,
and upserts to project_context_contracts.

update_contracts format (array inside agent_contract_handoff)::

    "update_contracts": [
      { "contract": "<contract_name>", "payload": { ... } },
      ...
    ]

Public API:
    - validate_permission(update, agent_name, cloud_scope, db_path) -> (allowed, message)
    - apply_update(update, agent_name, workspace, db_path) -> dict
    - process_update_contracts(contract_dict, task_info) -> dict
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
# 1. validate_permission
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
        f"update_contracts rejected: agent '{agent_name}' has no write permission "
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
# 2. apply_update
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
    """Upsert a validated update_contracts entry into project_context_contracts.

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
# 3. process_update_contracts (envelope path)
# ============================================================================

def process_update_contracts(
    contract_dict: dict,
    task_info: dict,
) -> dict:
    """Process the ``update_contracts`` array from a parsed contract dict.

    This is the agent_contract_handoff envelope path. It supports N atomic
    updates per turn, each entry being a ``{contract, payload}`` object.

    Each entry in ``update_contracts`` must be ``{contract, payload}``.
    Permission is checked per entry via ``validate_permission``.  Entries
    that fail permission are collected in ``rejected``; successful entries
    are applied immediately.

    Args:
        contract_dict: Parsed JSON contract dict (from parse_contract()).
        task_info: Task metadata (agent, db_path, cloud_scope, workspace).

    Returns:
        dict with keys:
            updated (bool)         -- True when at least one entry was applied
            contracts (list[str])  -- Names of successfully applied contracts
            rejected (list[str])   -- Names of rejected contracts
            errors (list[str])     -- Error messages for failed entries
    """
    result = {
        "updated": False,
        "contracts": [],
        "rejected": [],
        "errors": [],
    }

    if not isinstance(contract_dict, dict):
        return result

    try:
        from ..agents.contract_validator import parse_update_contracts
        entries = parse_update_contracts(contract_dict)
    except Exception as exc:
        logger.debug("parse_update_contracts failed (non-fatal): %s", exc)
        return result

    if not entries:
        return result

    agent_name = task_info.get("agent", task_info.get("agent_type", "unknown"))
    db_path = task_info.get("db_path")
    cloud_scope = task_info.get("cloud_scope")
    workspace = task_info.get("workspace")

    evidence_entries = []
    context_entries = []
    for entry in entries:
        if entry.get("contract") == "evidence":
            evidence_entries.append(entry)
        else:
            context_entries.append(entry)

    # Process project_context entries (existing path -- permission-gated)
    for entry in context_entries:
        allowed, rejection_msg = validate_permission(
            entry, agent_name, cloud_scope, db_path
        )
        if not allowed:
            contract_name = entry.get("contract", "")
            result["rejected"].append(contract_name)
            result["errors"].append(rejection_msg)
            logger.warning(rejection_msg)
            continue

        audit = apply_update(entry, agent_name, workspace=workspace, db_path=db_path)
        if audit["success"]:
            result["contracts"].append(audit["contract"])
            result["updated"] = True
            logger.info(
                "update_contracts applied by %s: workspace=%s contract=%s",
                agent_name,
                audit.get("workspace"),
                audit["contract"],
            )
        else:
            result["errors"].append(audit.get("error", "unknown error"))
            logger.error(
                "update_contracts failed for %s entry '%s': %s",
                agent_name,
                entry.get("contract", ""),
                audit.get("error"),
            )

    # Process evidence entries (fail-together per D8 -- hook-trusted path)
    if evidence_entries:
        _apply_evidence_entries(evidence_entries, workspace, db_path, agent_name, result)

    return result


def _validate_evidence_payload(payload: dict) -> list:
    """Validate an evidence clause payload at write time.

    Returns a list of error strings (empty = valid).  Mirrors the parse-time
    check in contract_validator.validate_evidence_update_contract_payload but
    lives in the writer layer so write-time rejections are reported consistently.
    """
    errors = []
    if not isinstance(payload, dict):
        errors.append("evidence payload must be an object/dict")
        return errors

    raw_brief_id = payload.get("brief_id")
    if raw_brief_id is None:
        errors.append("evidence payload missing required field: brief_id")
    else:
        try:
            int(raw_brief_id)
        except (TypeError, ValueError):
            errors.append(
                f"evidence payload brief_id must be an integer, got {type(raw_brief_id).__name__!r}"
            )

    ac_id = payload.get("ac_id")
    if not ac_id or not str(ac_id).strip():
        errors.append("evidence payload missing or empty required field: ac_id")

    _valid_types = frozenset({"text", "file", "command_output", "url", "screenshot"})
    ev_type = payload.get("type")
    if not ev_type:
        errors.append(
            f"evidence payload missing required field: type (must be one of {sorted(_valid_types)})"
        )
    elif ev_type not in _valid_types:
        errors.append(
            f"evidence payload type {ev_type!r} is invalid; must be one of {sorted(_valid_types)}"
        )

    has_text = payload.get("text") is not None
    has_artifact = payload.get("artifact_path") is not None
    if has_text and has_artifact:
        errors.append(
            "evidence payload fields 'text' and 'artifact_path' are mutually exclusive"
        )
    elif not has_text and not has_artifact:
        errors.append("evidence payload requires exactly one of 'text' or 'artifact_path'")

    return errors


def _apply_evidence_entries(
    entries: list,
    workspace,
    db_path,
    agent_name: str,
    result: dict,
) -> None:
    """Insert evidence rows from update_contracts evidence clauses.

    Implements fail-together semantics (D8): if ANY entry has a validation
    error, NO rows are inserted for the batch.  Other contract types in the
    same update_contracts array are unaffected.

    Successful inserts are appended to result["contracts"] as "evidence:<id>"
    strings.  Failures are appended to result["rejected"] and result["errors"].
    """
    # Phase 1: validate ALL entries before touching the DB (fail-together)
    all_errors = []
    for i, entry in enumerate(entries):
        payload = entry.get("payload", {})
        errs = _validate_evidence_payload(payload)
        for err in errs:
            all_errors.append(f"evidence[{i}]: {err}")

    if all_errors:
        for entry in entries:
            result["rejected"].append(entry.get("contract", "evidence"))
        result["errors"].extend(all_errors)
        logger.warning(
            "_apply_evidence_entries: rejecting %d evidence entries due to validation errors: %s",
            len(entries),
            "; ".join(all_errors),
        )
        return

    # Phase 2: all valid -- insert each entry
    import sys as _sys
    import pathlib as _pl
    _repo_root = _pl.Path(__file__).resolve().parent.parent.parent.parent
    if str(_repo_root) not in _sys.path:
        _sys.path.insert(0, str(_repo_root))
    try:
        from gaia.evidence.store import insert_evidence
    except ImportError as exc:
        err = f"gaia.evidence.store unavailable: {exc}"
        result["errors"].append(err)
        logger.error("_apply_evidence_entries: %s", err)
        return

    ws = workspace or _derive_workspace()

    for i, entry in enumerate(entries):
        payload = entry.get("payload", {})
        try:
            row = insert_evidence(
                ws,
                int(payload["brief_id"]),
                str(payload["ac_id"]),
                type=payload["type"],
                text=payload.get("text"),
                artifact_path=payload.get("artifact_path"),
                size_bytes=payload.get("size_bytes"),
                task_id=payload.get("task_id"),
                created_by_agent=payload.get("created_by_agent") or agent_name,
                db_path=db_path,
                bypass_dispatch_guard=True,
            )
            ev_ref = f"evidence:{row['id']}"
            result["contracts"].append(ev_ref)
            result["updated"] = True
            logger.info(
                "_apply_evidence_entries: inserted %s by %s (brief_id=%s ac_id=%s)",
                ev_ref, agent_name, payload["brief_id"], payload["ac_id"],
            )
        except Exception as exc:
            err = f"evidence[{i}] insert failed: {exc}"
            result["errors"].append(err)
            logger.error("_apply_evidence_entries: %s", err)
