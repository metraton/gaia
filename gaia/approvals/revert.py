"""gaia.approvals.revert -- Inverse-command derivation for approval revert.

Per D14 (plan design decision), revert works by querying EXECUTED events
for an approval, deriving candidate inverse commands using a hardcoded
best-effort mapping, and presenting them for user confirmation.

Public API:
    InverseCommand     -- dataclass for a candidate inverse operation
    derive_inverse(event) -> InverseCommand | None
    derive_inverses_for_approval(approval_id, con) -> list[InverseCommand]

The InverseCommand dataclass carries:
    event_id: int          -- the original EXECUTED event id
    original_command: str  -- the original command (from payload or metadata)
    inverse_command: str | None  -- the derived inverse, or None if NOT REVERSIBLE
    reversible: bool       -- False when no inverse can be derived
    notes: str             -- human-readable explanation
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class InverseCommand:
    """Candidate inverse operation for an EXECUTED approval event."""

    event_id: int
    original_command: str
    inverse_command: Optional[str]
    reversible: bool
    notes: str


# ---------------------------------------------------------------------------
# Hardcoded verb -> inverse mapping (D14)
# ---------------------------------------------------------------------------

# Pattern-based inverse rules. Each entry is (pattern_re, inverse_template_fn).
# The pattern is matched against the original command. The template function
# receives the re.Match object and returns the inverse command string.
_INVERSE_RULES: list[tuple[re.Pattern, Any]] = []


def _rule(pattern: str):
    """Decorator to register an inverse rule."""
    def decorator(fn):
        _INVERSE_RULES.append((re.compile(pattern), fn))
        return fn
    return decorator


@_rule(r"^gaia\s+brief\s+set-status\s+(\S+)\s+done\s*$")
def _brief_done_to_pending(m):
    brief_id = m.group(1)
    return f"gaia brief set-status {brief_id} pending"


@_rule(r"^gaia\s+brief\s+set-status\s+(\S+)\s+active\s*$")
def _brief_active_to_draft(m):
    brief_id = m.group(1)
    return f"gaia brief set-status {brief_id} draft"


@_rule(r"^gaia\s+brief\s+set-status\s+(\S+)\s+pending\s*$")
def _brief_pending_to_draft(m):
    brief_id = m.group(1)
    return f"gaia brief set-status {brief_id} draft"


@_rule(r"^git\s+branch\s+(\S+)\s*$")
def _git_branch_create_to_delete(m):
    branch = m.group(1)
    if branch.startswith("-"):
        # Already a delete flag -- not a create
        return None
    return f"git branch -D {branch}"


@_rule(r"^git\s+branch\s+-[bB]\s+(\S+)\s*$")
def _git_branch_b_to_delete(m):
    branch = m.group(1)
    return f"git branch -D {branch}"


@_rule(r"^rm\s+(.+)\s*$")
def _rm_not_reversible(_m):
    # rm has no generic inverse; caller sees NOT REVERSIBLE
    return None


def _derive_from_command_string(command: str) -> InverseCommand | None:
    """Apply hardcoded rules to a single command string.

    Returns an InverseCommand on the first match, or None when no rule matches.
    """
    cmd = command.strip()
    for pattern, fn in _INVERSE_RULES:
        m = pattern.match(cmd)
        if m:
            inverse = fn(m)
            if inverse is not None:
                return InverseCommand(
                    event_id=0,
                    original_command=cmd,
                    inverse_command=inverse,
                    reversible=True,
                    notes=f"Derived from pattern: {pattern.pattern}",
                )
            else:
                return InverseCommand(
                    event_id=0,
                    original_command=cmd,
                    inverse_command=None,
                    reversible=False,
                    notes="NOT REVERSIBLE -- matched pattern has no safe inverse",
                )
    return None


def _is_file_create(payload: dict, original_cmd: str) -> bool:
    """Heuristic: detect if the payload represents a file creation."""
    commands = payload.get("commands") or []
    scope = payload.get("scope") or ""
    # A Write tool on a new path is stored with 'write' or 'create' in operation.
    operation = (payload.get("operation") or "").lower()
    if "write" in operation or "create" in operation:
        return True
    # If the scope looks like a file path and command is a write-like verb
    if scope and ("write" in original_cmd.lower() or "create" in original_cmd.lower()):
        return True
    return False


def derive_inverse(event: Dict[str, Any]) -> InverseCommand:
    """Derive a candidate inverse command for a single EXECUTED event.

    Best-effort approach per D14:
    - Tries hardcoded verb patterns first.
    - Detects file create -> suggests rm <path>.
    - Falls through to "NOT REVERSIBLE" with original command for reference.

    Args:
        event: A dict from store.replay_for_approval() representing an
            EXECUTED event row. Must have 'id', 'payload_json', and
            optionally 'metadata_json'.

    Returns:
        InverseCommand with reversible=True if an inverse was derived, or
        reversible=False with inverse_command=None and a "NOT REVERSIBLE"
        note.
    """
    event_id = event.get("id", 0)
    payload_json = event.get("payload_json") or ""
    metadata_json = event.get("metadata_json") or ""

    payload: dict = {}
    if payload_json:
        try:
            payload = json.loads(payload_json)
        except (json.JSONDecodeError, TypeError):
            pass

    metadata: dict = {}
    if metadata_json:
        try:
            metadata = json.loads(metadata_json)
        except (json.JSONDecodeError, TypeError):
            pass

    # Collect commands to try inverting.
    commands = payload.get("commands") or []
    exact_content = payload.get("exact_content") or ""

    # Build list of individual command strings to invert.
    cmd_strings: list[str] = []
    if commands:
        cmd_strings = [str(c).strip() for c in commands if c]
    elif exact_content:
        # Split newline-separated commands per D13.
        cmd_strings = [l.strip() for l in exact_content.splitlines() if l.strip()]

    if not cmd_strings:
        # No commands found -- check operation field.
        operation = payload.get("operation") or ""
        if operation:
            cmd_strings = [operation]

    if not cmd_strings:
        return InverseCommand(
            event_id=event_id,
            original_command="(no command recorded)",
            inverse_command=None,
            reversible=False,
            notes="NOT REVERSIBLE -- no command data found in event payload",
        )

    # For multi-command events, try to invert each command.
    # If ALL have inverses, return a compound inverse. If any fails, NOT REVERSIBLE.
    inverses = []
    original_summary = "; ".join(cmd_strings)

    for cmd in cmd_strings:
        result = _derive_from_command_string(cmd)
        if result is None:
            # No rule matched -- check for file create heuristic.
            scope = payload.get("scope") or ""
            if scope and _is_file_create(payload, cmd):
                # Suggest rm <scope> as the inverse.
                scope_path = scope.strip()
                inverses.append(
                    InverseCommand(
                        event_id=event_id,
                        original_command=cmd,
                        inverse_command=f"rm {scope_path}",
                        reversible=True,
                        notes=f"File create detected -- inverse is rm {scope_path} (requires confirm)",
                    )
                )
            else:
                return InverseCommand(
                    event_id=event_id,
                    original_command=original_summary,
                    inverse_command=None,
                    reversible=False,
                    notes=f"NOT REVERSIBLE -- no inverse rule matches: {cmd!r}",
                )
        else:
            result.event_id = event_id
            inverses.append(result)

    if len(inverses) == 1:
        return inverses[0]

    # Multiple inverses -- combine into a compound inverse.
    combined_inverse = " && ".join(ic.inverse_command for ic in inverses if ic.inverse_command)
    combined_notes = "; ".join(ic.notes for ic in inverses)
    return InverseCommand(
        event_id=event_id,
        original_command=original_summary,
        inverse_command=combined_inverse if combined_inverse else None,
        reversible=bool(combined_inverse),
        notes=combined_notes,
    )


def derive_inverses_for_approval(
    approval_id: str,
    con: sqlite3.Connection,
) -> List[InverseCommand]:
    """Return a list of InverseCommand for all EXECUTED events of an approval.

    Args:
        approval_id: The P-{uuid4} approval identifier.
        con: An open sqlite3.Connection.

    Returns:
        List of InverseCommand, one per EXECUTED event, in insertion order.
        Empty list if no EXECUTED events exist.
    """
    cur = con.execute(
        "SELECT id, payload_json, metadata_json FROM approval_events "
        "WHERE approval_id = ? AND event_type = 'EXECUTED' "
        "ORDER BY id ASC",
        (approval_id,),
    )
    rows = cur.fetchall()
    result = []
    for row in rows:
        event = {
            "id": row[0],
            "payload_json": row[1],
            "metadata_json": row[2],
        }
        result.append(derive_inverse(event))
    return result
