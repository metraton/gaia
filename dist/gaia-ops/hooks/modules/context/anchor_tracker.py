"""
Context anchor hit tracking for project context effectiveness measurement.

Extracts "anchors" (paths, names, IDs) from injected project context and checks
whether the agent's early tool calls reference them. This measures whether agents
use injected context as search anchors versus discovering on their own.

Provides:
    - extract_anchors(): Extract searchable anchors from a context payload
    - save_anchors(): Persist anchors to a session-scoped temp file
    - load_anchors(): Load persisted anchors for a session
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


def save_anchors(session_id: str, agent_type: str, anchors: Set[str]) -> Optional[Path]:
    """Persist anchors to a session+agent-scoped temp file.

    Args:
        session_id: Current session identifier.
        agent_type: Agent name (e.g. "terraform-architect").
        anchors: Set of anchor strings to save.

    Returns:
        Path to the saved file, or None on failure.
    """
    if not anchors:
        return None

    try:
        anchor_dir = _anchors_dir()
        anchor_dir.mkdir(parents=True, exist_ok=True)

        safe_session = re.sub(r"[^a-zA-Z0-9_-]", "_", session_id or "unknown")[:32]
        safe_agent = re.sub(r"[^a-zA-Z0-9_-]", "_", agent_type or "unknown")[:32]
        anchor_file = anchor_dir / f"{safe_session}-{safe_agent}.json"

        anchor_file.write_text(json.dumps(sorted(anchors)))
        logger.debug(
            "Saved %d anchors for %s/%s -> %s",
            len(anchors), session_id, agent_type, anchor_file,
        )
        return anchor_file
    except Exception as e:
        logger.debug("Failed to save anchors: %s", e)
        return None


def load_anchors(session_id: str, agent_type: str) -> Set[str]:
    """Load persisted anchors for a session+agent.

    Args:
        session_id: Current session identifier.
        agent_type: Agent name.

    Returns:
        Set of anchor strings, or empty set if not found.
    """
    try:
        safe_session = re.sub(r"[^a-zA-Z0-9_-]", "_", session_id or "unknown")[:32]
        safe_agent = re.sub(r"[^a-zA-Z0-9_-]", "_", agent_type or "unknown")[:32]
        anchor_file = _anchors_dir() / f"{safe_session}-{safe_agent}.json"

        if not anchor_file.exists():
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


def cleanup_anchors(session_id: str, agent_type: str) -> None:
    """Remove the anchor temp file after use.

    Args:
        session_id: Current session identifier.
        agent_type: Agent name.
    """
    try:
        safe_session = re.sub(r"[^a-zA-Z0-9_-]", "_", session_id or "unknown")[:32]
        safe_agent = re.sub(r"[^a-zA-Z0-9_-]", "_", agent_type or "unknown")[:32]
        anchor_file = _anchors_dir() / f"{safe_session}-{safe_agent}.json"
        if anchor_file.exists():
            anchor_file.unlink()
            logger.debug("Cleaned up anchor file: %s", anchor_file)
    except Exception as e:
        logger.debug("Failed to cleanup anchors: %s", e)
