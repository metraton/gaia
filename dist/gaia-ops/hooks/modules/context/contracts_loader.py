"""Load context contracts and agent permissions from gaia.db.

Subsystem 2 of the pre_tool_use Task/Agent path.

Queries agent_contract_permissions and project_context_contracts in
~/.gaia/gaia.db to find writable contracts and check emptiness.
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _get_db_path() -> Optional[Path]:
    try:
        from gaia.paths import db_path
        return db_path()
    except Exception:
        return Path.home() / ".gaia" / "gaia.db"


def _load_writable_contracts_from_db(
    agent_name: str,
    db_path: Optional[Path] = None,
) -> list:
    """Return the list of contract names the agent may write, from gaia.db.

    Rows with cloud_scope IS NULL match all providers. Non-NULL cloud_scope rows
    are included without additional filtering at this layer (the broader grant
    includes them). Returns empty list when the DB is unavailable.
    """
    resolved = db_path or _get_db_path()
    if resolved is None or not resolved.exists():
        logger.debug("gaia.db not found; no write grants available for '%s'", agent_name)
        return []

    try:
        con = sqlite3.connect(str(resolved))
        rows = con.execute(
            """
            SELECT DISTINCT contract_name
            FROM agent_contract_permissions
            WHERE agent_name = ? AND can_write = 1
            ORDER BY contract_name
            """,
            (agent_name,),
        ).fetchall()
        con.close()
        return [row[0] for row in rows]
    except sqlite3.Error as exc:
        logger.warning(
            "DB error loading write permissions for '%s': %s", agent_name, exc
        )
        return []


def _load_context_sections_from_db(
    workspace: str,
    db_path: Optional[Path] = None,
) -> dict:
    """Return a dict of {contract_name: payload} for a workspace from gaia.db.

    Returns empty dict when the workspace has no rows or the DB is unavailable.
    """
    resolved = db_path or _get_db_path()
    if resolved is None or not resolved.exists():
        logger.debug("gaia.db not found; no context sections available for '%s'", workspace)
        return {}

    try:
        con = sqlite3.connect(str(resolved))
        rows = con.execute(
            "SELECT contract_name, payload FROM project_context_contracts WHERE workspace = ?",
            (workspace,),
        ).fetchall()
        con.close()
    except sqlite3.Error as exc:
        logger.warning("DB error loading context sections for '%s': %s", workspace, exc)
        return {}

    sections = {}
    for contract_name, payload_str in rows:
        try:
            sections[contract_name] = json.loads(payload_str) if payload_str else {}
        except (json.JSONDecodeError, TypeError):
            sections[contract_name] = {}
    return sections


def _derive_workspace() -> str:
    try:
        from gaia.project import current as _project_current
        identity = _project_current()
        return identity if identity else "global"
    except Exception:
        return "global"


def build_context_update_reminder(
    subagent_type: str,
    project_agents: list,
    hooks_dir: Path = None,
    db_path: Optional[Path] = None,
) -> str:
    """Check which writable contracts are empty and build a reminder.

    Queries agent_contract_permissions for writable contracts, then checks
    project_context_contracts to see which payloads are empty.

    Args:
        subagent_type: The agent type string (e.g. developer).
        project_agents: List of valid project agent names.
        hooks_dir: Unused -- kept for API compatibility with callers.
        db_path: Optional explicit path to gaia.db (used in tests).

    Returns:
        Reminder string or empty string if no empty contracts.
    """
    if subagent_type not in project_agents:
        return ""

    writable = _load_writable_contracts_from_db(subagent_type, db_path=db_path)
    if not writable:
        return ""

    workspace = _derive_workspace()
    sections = _load_context_sections_from_db(workspace, db_path=db_path)

    empty = [name for name in writable if not sections.get(name)]

    if not empty:
        return ""

    empty_list = ", ".join(f"`{s}`" for s in empty)
    return (
        f"\n**CONTEXT UPDATE REQUIRED:** Your writable contracts {empty_list} "
        f"are currently EMPTY. After completing your task, you MUST include an "
        f"`update_contracts` clause in your agent_contract_handoff with any data "
        f"you discovered. See the agent-contract-handoff skill for the format.\n\n"
    )
