"""
Context anchor hit tracking for project context effectiveness measurement.

Extracts "anchors" (paths, names, IDs) from injected project context and checks
whether the agent's early tool calls reference them. This measures whether agents
use injected context as search anchors versus discovering on their own.

Anchor files are keyed by (session_id, agent_type, agent_id) -- the host-assigned
per-dispatch subagent instance id, NOT just (session_id, agent_type). Two
subagents of the SAME type dispatched in parallel within the SAME session share
session_id and agent_type, so a two-part key collides and one dispatch's
measurement silently overwrites the other's. agent_id is only available once
the host has spawned the subagent (SubagentStart/SubagentStop), never at the
PreToolUse:Task dispatch that precedes it -- callers must save at SubagentStart,
not at dispatch time.

Provides:
    - extract_anchors(): Extract searchable anchors from a context payload
    - save_anchors(): Persist anchors to a session+agent+instance-scoped temp file
    - load_anchors(): Load persisted anchors for a session+agent+instance
    - extract_tool_calls_from_transcript(): Parse early tool calls from JSONL transcript
    - compute_anchor_hits(): Compare tool call args against anchors
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# How many early tool calls to check
MAX_TOOL_CALLS_TO_CHECK = 5

# Tool types that have inspectable path/keyword arguments
TRACKABLE_TOOLS = {"Glob", "Grep", "Read", "Bash"}

# Minimum anchor length to avoid false-positive matches on short strings
MIN_ANCHOR_LENGTH = 4


def _anchors_dir() -> Path:
    """Return the directory for anchor temp files."""
    return Path("/tmp/gaia-context-anchors")


def extract_anchors(context_payload: Dict[str, Any]) -> Set[str]:
    """Extract searchable anchor strings from a context payload.

    Walks the project knowledge sections and collects values from fields that
    are likely to appear in agent tool calls: paths, names, IDs, clusters,
    regions, namespaces, service accounts.

    Args:
        context_payload: The full context JSON payload injected into agent prompt.

    Returns:
        Set of anchor strings (paths, names, identifiers).
    """
    anchors: Set[str] = set()
    contract = context_payload.get("project_knowledge", {})

    # Anchor-worthy field name patterns
    anchor_fields = re.compile(
        r"(path|name|cluster|project|region|namespace|service|image|"
        r"base_path|config_path|module_path|repository|bucket|sa$|"
        r"service_account|pod_name|terragrunt_path)",
        re.IGNORECASE,
    )

    def _walk(obj: Any, depth: int = 0) -> None:
        if depth > 10:
            return
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, str) and value and anchor_fields.search(key):
                    # Normalize: strip leading ./ for path matching
                    clean = value.lstrip("./")
                    if len(clean) >= MIN_ANCHOR_LENGTH:
                        anchors.add(clean)
                elif isinstance(value, (dict, list)):
                    _walk(value, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item, depth + 1)

    _walk(contract)

    # Also extract from top-level metadata
    metadata = context_payload.get("metadata", {})
    for key in ("project_id", "cluster_name", "region"):
        val = metadata.get(key)
        if isinstance(val, str) and len(val) >= MIN_ANCHOR_LENGTH:
            anchors.add(val)

    return anchors


def _sanitize_key_part(value: str, default: str = "unknown") -> str:
    """Sanitize and truncate one key component, symmetrically for save/load."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", value or default)[:32]


def _anchor_file_path(session_id: str, agent_type: str, agent_id: str) -> Optional[Path]:
    """Build the anchor file path for (session_id, agent_type, agent_id).

    Returns None when agent_id is missing: the host-assigned per-dispatch
    instance id is the discriminator this key exists for, and a file saved or
    looked up without it would either collide across parallel same-type
    dispatches (the bug this key fixes) or never be found by a symmetric
    caller. Both save and load route through this one builder so the
    sanitization and truncation can never drift between the two sides.
    """
    if not agent_id:
        return None
    safe_session = _sanitize_key_part(session_id)
    safe_agent = _sanitize_key_part(agent_type)
    safe_agent_id = _sanitize_key_part(agent_id, default="")
    if not safe_agent_id:
        return None
    return _anchors_dir() / f"{safe_session}-{safe_agent}-{safe_agent_id}.json"


def save_anchors(
    session_id: str, agent_type: str, agent_id: str, anchors: Set[str],
) -> Optional[Path]:
    """Persist anchors to a session+agent+instance-scoped temp file.

    Args:
        session_id: Current session identifier.
        agent_type: Agent name (e.g. "platform-architect").
        agent_id: Host-assigned per-dispatch subagent instance id. Available
            at SubagentStart/SubagentStop, never at the PreToolUse:Task
            dispatch that precedes them -- callers must save here, not there.
        anchors: Set of anchor strings to save.

    Returns:
        Path to the saved file, or None on failure or a missing agent_id.
    """
    if not anchors:
        return None

    anchor_file = _anchor_file_path(session_id, agent_type, agent_id)
    if anchor_file is None:
        logger.debug(
            "Skipping anchor save for %s/%s: no agent_id available",
            session_id, agent_type,
        )
        return None

    try:
        anchor_file.parent.mkdir(parents=True, exist_ok=True)
        anchor_file.write_text(json.dumps(sorted(anchors)))
        logger.debug(
            "Saved %d anchors for %s/%s/%s -> %s",
            len(anchors), session_id, agent_type, agent_id, anchor_file,
        )
        return anchor_file
    except Exception as e:
        logger.debug("Failed to save anchors: %s", e)
        return None


def load_anchors(session_id: str, agent_type: str, agent_id: str) -> Set[str]:
    """Load persisted anchors for a session+agent+instance.

    Args:
        session_id: Current session identifier.
        agent_type: Agent name.
        agent_id: Host-assigned per-dispatch subagent instance id -- the SAME
            value SubagentStop receives, the same one SubagentStart used
            to save. A missing agent_id degrades to an empty set (never a
            crash, never a fabricated match) so the caller's hit-rate
            computation stays NULL rather than reporting a false 0.0.

    Returns:
        Set of anchor strings, or empty set if not found.
    """
    try:
        anchor_file = _anchor_file_path(session_id, agent_type, agent_id)
        if anchor_file is None or not anchor_file.exists():
            return set()

        data = json.loads(anchor_file.read_text())
        return set(data) if isinstance(data, list) else set()
    except Exception as e:
        logger.debug("Failed to load anchors: %s", e)
        return set()


def extract_tool_calls_from_transcript(
    transcript_path: str,
    max_calls: int = MAX_TOOL_CALLS_TO_CHECK,
) -> List[Dict[str, Any]]:
    """Extract the first N trackable tool calls from a Claude Code transcript JSONL.

    Claude Code transcripts contain tool_use entries in the assistant messages
    (content blocks with type "tool_use").

    Args:
        transcript_path: Path to the transcript JSONL file.
        max_calls: Maximum number of tool calls to extract.

    Returns:
        List of dicts with keys: tool_name, arguments (dict), call_index (1-based).
    """
    if not transcript_path:
        return []

    try:
        path = Path(transcript_path).expanduser()
        if not path.exists():
            return []

        tool_calls: List[Dict[str, Any]] = []
        call_index = 0

        for line in path.read_text().strip().splitlines():
            if not line.strip():
                continue
            if call_index >= max_calls:
                break

            try:
                entry = json.loads(line)
                msg = entry.get("message", entry)

                if msg.get("role") != "assistant":
                    continue

                content = msg.get("content", [])
                if not isinstance(content, list):
                    continue

                for block in content:
                    if call_index >= max_calls:
                        break
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") != "tool_use":
                        continue

                    tool_name = block.get("name", "")
                    if tool_name not in TRACKABLE_TOOLS:
                        continue

                    call_index += 1
                    tool_calls.append({
                        "tool_name": tool_name,
                        "arguments": block.get("input", {}),
                        "call_index": call_index,
                    })

            except (json.JSONDecodeError, TypeError):
                continue

        return tool_calls

    except Exception as e:
        logger.debug("Failed to extract tool calls from transcript: %s", e)
        return []


def _extract_searchable_text(tool_name: str, arguments: Dict[str, Any]) -> str:
    """Extract the searchable text from a tool call's arguments.

    Returns a single string containing all path/keyword-relevant arguments
    concatenated for substring matching.
    """
    parts: List[str] = []

    if tool_name == "Glob":
        parts.append(arguments.get("pattern", ""))
        parts.append(arguments.get("path", ""))
    elif tool_name == "Grep":
        parts.append(arguments.get("pattern", ""))
        parts.append(arguments.get("path", ""))
        parts.append(arguments.get("glob", ""))
    elif tool_name == "Read":
        parts.append(arguments.get("file_path", ""))
    elif tool_name == "Bash":
        parts.append(arguments.get("command", ""))

    return " ".join(p for p in parts if p)


def compute_anchor_hits(
    tool_calls: List[Dict[str, Any]],
    anchors: Set[str],
) -> Dict[str, Any]:
    """Compare tool call arguments against known anchors.

    For each tool call, checks if any anchor appears as a substring in the
    tool's searchable arguments. This is a lightweight prefix/substring match.

    Args:
        tool_calls: List from extract_tool_calls_from_transcript().
        anchors: Set of anchor strings from extract_anchors().

    Returns:
        Dict with hit tracking data.
    """
    if not tool_calls or not anchors:
        return {
            "total_checked": len(tool_calls),
            "hits": 0,
            "hit_rate": 0.0,
            "details": [],
        }

    details: List[Dict[str, Any]] = []
    hits = 0

    for call in tool_calls:
        searchable = _extract_searchable_text(call["tool_name"], call["arguments"])
        matched_anchor: Optional[str] = None

        if searchable:
            for anchor in anchors:
                if anchor in searchable:
                    matched_anchor = anchor
                    break

        is_hit = matched_anchor is not None
        if is_hit:
            hits += 1

        details.append({
            "call_index": call["call_index"],
            "tool": call["tool_name"],
            "anchor": matched_anchor,
            "hit": is_hit,
        })

    total = len(tool_calls)
    return {
        "total_checked": total,
        "hits": hits,
        "hit_rate": round(hits / total, 2) if total > 0 else 0.0,
        "details": details,
    }


def cleanup_anchors(session_id: str, agent_type: str, agent_id: str) -> None:
    """Remove the anchor temp file for one (session, agent_type, agent_id)
    after use.

    A missing agent_id is a no-op: nothing could have been saved under an
    incomplete key, so there is nothing to remove.

    Args:
        session_id: Current session identifier.
        agent_type: Agent name.
        agent_id: Host-assigned per-dispatch subagent instance id.
    """
    try:
        anchor_file = _anchor_file_path(session_id, agent_type, agent_id)
        if anchor_file is not None and anchor_file.exists():
            anchor_file.unlink()
            logger.debug("Cleaned up anchor file: %s", anchor_file)
    except Exception as e:
        logger.debug("Failed to cleanup anchors: %s", e)
