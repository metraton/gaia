"""
Transcript reading and parsing for Claude Code agent transcripts.

Provides:
    - read_transcript(): Read assistant messages from transcript JSONL
    - read_first_user_content_from_transcript(): Read first user message content
    - extract_task_description_from_transcript(): Extract task description
    - extract_injected_context_payload_from_transcript(): Extract auto-injected JSON
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def read_transcript(transcript_path: str) -> str:
    """Read agent transcript from file path provided by Claude Code.

    Claude Code provides ``agent_transcript_path`` pointing to a JSONL file.
    Each line has the structure:
        {"type": "assistant", "message": {"role": "assistant", "content": [...]}, ...}
    The role/content are nested inside the ``message`` field.

    Falls back to empty string on any error so the hook never crashes.
    """
    try:
        # Expand ~ to home directory (Claude Code may use ~ in paths)
        path = Path(transcript_path).expanduser()
        logger.debug("Reading transcript from: %s", path)

        if not path.exists():
            logger.warning("Transcript file not found: %s", path)
            return ""

        lines = path.read_text().strip().splitlines()

        text_parts: List[str] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)

                # Claude Code transcript format: content is inside entry["message"]
                msg = entry.get("message", entry)  # fallback to entry itself for simple format
                role = msg.get("role", "")
                if role != "assistant":
                    continue

                content = msg.get("content", "")
                if isinstance(content, str):
                    text_parts.append(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif isinstance(block, str):
                            text_parts.append(block)
            except (json.JSONDecodeError, TypeError):
                continue

        result = "\n".join(text_parts)
        logger.debug("Extracted %d text parts, total length: %d chars", len(text_parts), len(result))
        return result

    except Exception as e:
        logger.debug("Failed to read transcript from %s: %s", transcript_path, e)
        return ""


def read_first_user_content_from_transcript(transcript_path: str) -> Optional[str]:
    """Read the raw content string of the first user message from a transcript JSONL.

    Handles: empty path guard, path expansion, existence check, JSONL iteration,
    JSON parse, role=="user" check, content normalization (str vs list).
    Returns the raw content string or None.
    """
    if not transcript_path:
        return None
    try:
        path = Path(transcript_path).expanduser()
        if not path.exists():
            return None
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    msg = entry.get("message", entry)
                    if msg.get("role") != "user":
                        continue
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        return content
                    elif isinstance(content, list):
                        return " ".join(
                            b.get("text", "") for b in content
                            if isinstance(b, dict) and b.get("type") == "text"
                        )
                    return None
                except (json.JSONDecodeError, TypeError):
                    continue
    except Exception as e:
        logger.debug("Failed to read first user content from transcript: %s", e)
    return None


def extract_task_description_from_transcript(transcript_path: str) -> str:
    """Read the first user message from the subagent transcript JSONL.

    Claude Code's agent_transcript_path contains the full subagent conversation.
    The first ``role: "user"`` entry is the task prompt sent by the orchestrator --
    which is the most meaningful description of what the agent was asked to do.

    Returns empty string on any error so the hook never crashes.
    """
    content = read_first_user_content_from_transcript(transcript_path)
    if not content:
        return ""

    text = content.strip()
    # Pattern 2: pre_tool_use injected project context before the real prompt.
    # The injected block ends with "\n\n---\n\n# User Task\n\n" followed by
    # the actual task description sent by the orchestrator.
    if text.startswith("# Project Context (Auto-Injected)"):
        sep_full = "\n\n---\n\n# User Task\n\n"
        sep_bare = "\n\n---\n\n"
        pos = text.find(sep_full)
        if pos != -1:
            text = text[pos + len(sep_full):].strip()
        else:
            pos = text.find(sep_bare)
            if pos != -1:
                text = text[pos + len(sep_bare):].strip()
            else:
                text = ""  # Cannot extract real prompt

    if text:
        # Truncate to 500 chars -- enough context, not too much
        return text[:500]
    return ""


def extract_injected_context_payload_from_transcript(
    transcript_path: str,
) -> Dict[str, Any]:
    """Extract the auto-injected JSON context payload from the first user message."""
    content = read_first_user_content_from_transcript(transcript_path)
    if not content:
        return {}
    if not content.startswith("# Project Context (Auto-Injected)"):
        return {}
    try:
        start = content.find("{")
        end = content.find("\n\n---\n\n")
        if start == -1 or end == -1 or end <= start:
            return {}
        return json.loads(content[start:end].strip())
    except Exception:
        return {}
