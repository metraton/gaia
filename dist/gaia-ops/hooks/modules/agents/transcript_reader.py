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
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from adapters.host_transcript import iter_transcript_entries

logger = logging.getLogger(__name__)


def read_transcript(transcript_path: str) -> str:
    """Read assistant messages from the host transcript provided by the CLI.

    The host CLI advertises ``agent_transcript_path``; the on-disk format
    (JSONL, ``message``-nesting) is owned by ``adapters/host_transcript.py``.
    This reader iterates normalized ``(role, content)`` entries from that
    adapter and joins the text of every ``assistant`` message -- it makes no
    assumption about how the host serializes the transcript.

    Falls back to empty string on any error so the hook never crashes.
    """
    try:
        text_parts: List[str] = []
        for role, content in iter_transcript_entries(transcript_path):
            if role != "assistant":
                continue
            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        text_parts.append(block)

        result = "\n".join(text_parts)
        logger.debug("Extracted %d text parts, total length: %d chars", len(text_parts), len(result))
        return result

    except Exception as e:
        logger.debug("Failed to read transcript from %s: %s", transcript_path, e)
        return ""


def read_first_user_content_from_transcript(transcript_path: str) -> Optional[str]:
    """Read the raw content of the first user message from the host transcript.

    Iterates normalized ``(role, content)`` entries from the adapter (which
    owns the host transcript format) and returns the content of the first
    ``user`` message, normalized to a string. Returns None when there is no
    user message (or the path is empty/missing).
    """
    for role, content in iter_transcript_entries(transcript_path):
        if role != "user":
            continue
        if isinstance(content, str):
            return content
        elif isinstance(content, list):
            return " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        return None
    return None


def extract_task_description_from_transcript(transcript_path: str) -> str:
    """Read the first user message from the subagent transcript JSONL.

    Claude Code's agent_transcript_path contains the full subagent conversation.
    The first ``role: "user"`` entry is the task prompt sent by the orchestrator --
    which is the most meaningful description of what the agent was asked to do.

    Context is delivered via additionalContext (not prompt mutation), so the
    first user message IS the original prompt without any wrapping.

    Returns empty string on any error so the hook never crashes.
    """
    content = read_first_user_content_from_transcript(transcript_path)
    if not content:
        return ""

    return content.strip()[:500]


def extract_injected_context_payload_from_transcript(
    transcript_path: str,
) -> Dict[str, Any]:
    """Extract the auto-injected context payload from disk cache.

    Context is delivered via additionalContext and the payload is persisted to
    disk by context_injector. Prompts do not contain embedded payloads.
    """
    # Empty/None path guard. Without it, Path("").stem == "" and the substring
    # match below (``candidate.stem in "" or "" in candidate.stem``) is ALWAYS
    # True because ``"" in any_string`` is True -- so an empty path would match
    # (and return) the FIRST payload sitting in gaia-context-payloads/, making
    # the result depend on whatever happens to be in that directory. Mirror the
    # guard in read_first_user_content_from_transcript: no path, no match.
    if not transcript_path:
        return {}

    try:
        payload_dir = Path(os.environ.get("TMPDIR", "/tmp")) / "gaia-context-payloads"
        if payload_dir.exists():
            agent_file = Path(transcript_path).stem  # e.g. "agent-ae190a4da68d626d4"
            # A stem that came out empty (e.g. path was "/" or "."): nothing to
            # match against, so the substring test would again degrade to the
            # always-true ``"" in candidate.stem``. Bail rather than grab an
            # arbitrary payload.
            if not agent_file:
                return {}
            # Match by agent ID substring
            for candidate in payload_dir.glob("*.json"):
                if candidate.stem in agent_file or agent_file in candidate.stem:
                    return json.loads(candidate.read_text())
    except Exception:
        pass
    return {}
