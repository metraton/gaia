"""
Transcript reading and parsing for Claude Code agent transcripts.

Provides:
    - read_transcript(): Read assistant messages from transcript JSONL
    - read_full_transcript_text(): Read every role's text content from transcript JSONL
    - read_first_user_content_from_transcript(): Read first user message content
    - extract_task_description_from_transcript(): Extract task description
    - extract_minted_agent_id_from_transcript(): Recover the CLI-minted agent id
    - extract_injected_context_payload_from_transcript(): Extract auto-injected JSON
"""

import json
import logging
import os
import re
from functools import lru_cache
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


def read_full_transcript_text(transcript_path: str) -> str:
    """Read every message's text content from the transcript, all roles.

    Unlike read_transcript() (assistant-only), this walks every role --
    which matters because the host records a skill load as a
    ``<command-name>skill-name</command-name>`` tag inside a ``user``-role
    tool-result entry, not inside anything the assistant itself says. A
    fingerprint search scoped to the assistant's own words (or worse, only
    its LAST message, as the SubagentStop caller used to do) never sees
    that tag or the skill body it wraps -- restricting the window there is
    what let skill_injection_verifier miss a skill that genuinely loaded
    earlier in the turn.

    Falls back to empty string on any error so the hook never crashes.
    """
    try:
        text_parts: List[str] = []
        for _role, content in iter_transcript_entries(transcript_path):
            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        text_parts.append(block)

        result = "\n".join(text_parts)
        logger.debug(
            "Extracted %d text parts from full transcript, total length: %d chars",
            len(text_parts), len(result),
        )
        return result

    except Exception as e:
        logger.debug("Failed to read full transcript from %s: %s", transcript_path, e)
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


# A draft id is minted by the CLI as f"{agent_id}.{secrets.token_hex(6)}"
# (gaia.contract.drafts.mint_draft_id), where agent_id is 'a' + >=16 hex
# (gaia.contract.validator.AGENT_ID_PATTERN_TEXT).
#
# Only ONE appearance of that shape proves the id belongs to the turn being
# scanned: the report `gaia contract init` prints when it MINTS the draft
# (bin/cli/contract.py::cmd_init -> _write_if_valid). Every other appearance --
# a `--draft-id` argument, a `gaia contract view` payload, an id quoted in the
# task prompt -- can just as easily carry ANOTHER agent's draft, which a turn
# routinely handles (an operator asked to recover a peer's contract). The two
# patterns below therefore match the init report specifically, in its text and
# --json forms, and nothing else. Both tolerate JSON escaping, because the
# report reaches the transcript re-encoded as a JSON string: its line breaks
# arrive as a literal backslash-n, and its quotes as backslash-quote. Requiring
# the text label to open a line is what keeps it off the JSON spelling
# (``"draft_id": "..."``, where the quote follows the colon) and off any
# incidental ``..._draft_id:`` key.
_INIT_REPORT_TEXT_RE = re.compile(
    r"(?:^|\\n|[\n\r])draft_id:\s*(a[0-9a-f]{16,})\.[0-9a-f]{8,}\b",
    re.MULTILINE,
)
_INIT_REPORT_JSON_RE = re.compile(
    r"\\?\"draft_id\\?\"\s*:\s*\\?\"(a[0-9a-f]{16,})\.[0-9a-f]{8,}\\?\""
    r"(?=.{0,200}?agent_id_minted)",
    re.DOTALL,
)


@lru_cache(maxsize=8)
def extract_minted_agent_id_from_transcript(transcript_path: str) -> Optional[str]:
    """Recover the CLI-MINTED agent id THIS turn built its own draft under.

    Two identifier spaces coexist on a SubagentStop and are NOT interchangeable:
    the harness stamps ``hook_data['agent_id']`` (e.g. ``aac5be534edc91e44``),
    while ``gaia contract init`` mints its own id and keys the on-disk draft by
    ``{minted-agent-id}.{token}``. Both match ``^a[0-9a-f]{16,}$``, so resolving
    a draft under the harness id fails SILENTLY -- no draft matches the glob and
    ``resolve_draft_id`` simply returns None, which is what left the M4
    missing-fence reconstruction and the T9 backstop's draft lookup inoperative.

    The turn's own transcript is where the two spaces meet, but MENTIONING a
    draft id is not OWNING it: a turn that runs ``gaia contract view --draft-id
    <peer>`` after its own ``init`` mentions the peer's id last, and handing that
    id to the reconstruction path would seal ANOTHER agent's envelope as this
    turn's -- a silent misattribution strictly worse than the silent miss it
    replaced, since ``_reconstruct_contract_from_finalized_draft`` checks only
    that a terminal row exists, never who owns it. So this scans for the ``gaia
    contract init`` MINT REPORT alone, which no other agent's id can appear in,
    and fails CLOSED: zero reports, or two reports naming different ids
    (a re-``init``, or a mint report quoted into the prompt), both yield None.
    A None is the pre-existing "no draft found" path -- the caller falls back to
    the harness id, the reconstruction declines, and the turn is rejected as it
    was before. That is the cost of a miss, and it is the one worth paying.

    Read RAW rather than through ``iter_transcript_entries`` on purpose: the id
    lives inside tool_use inputs and tool_result payloads, which the normalized
    text projections (assistant text blocks) do not surface.
    """
    if not transcript_path:
        return None
    try:
        raw = Path(transcript_path).expanduser().read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError as exc:
        logger.debug("Minted-id scan: unreadable transcript %s: %s", transcript_path, exc)
        return None
    minted = set(_INIT_REPORT_TEXT_RE.findall(raw))
    minted.update(_INIT_REPORT_JSON_RE.findall(raw))
    if len(minted) != 1:
        logger.debug(
            "Minted-id scan: %d distinct mint reports in %s -- declining to resolve",
            len(minted), transcript_path,
        )
        return None
    return minted.pop()


def extract_injected_context_payload_from_transcript(
    transcript_path: str,
    agent_type: str = "",
) -> Dict[str, Any]:
    """Extract the auto-injected context payload from disk cache.

    Context is delivered via additionalContext and the payload is persisted to
    disk by context_injector. Prompts do not contain embedded payloads.

    The payload is written by context_injector.build_project_context keyed by
    agent name: ``gaia-context-payloads/{agent_name}.json``. The reliable way to
    find it is therefore ``agent_type`` (the SubagentStop event's ``agent_type``
    / task_info ``agent``), which equals the write side's ``subagent_type``.

    The legacy transcript-stem substring match is retained as a fallback for
    callers that cannot supply ``agent_type`` -- but it never intersects the
    write key (a transcript stem like ``agent-ae190a4da68d626d4`` shares no
    substring with an agent name like ``developer``), so passing ``agent_type``
    is what actually populates the context-snapshot telemetry.
    """
    try:
        payload_dir = Path(os.environ.get("TMPDIR", "/tmp")) / "gaia-context-payloads"
        if not payload_dir.exists():
            return {}

        # Primary lookup: keyed by agent name, matching the write side.
        if agent_type:
            candidate = payload_dir / f"{agent_type}.json"
            if candidate.exists():
                return json.loads(candidate.read_text())

        # Legacy fallback: transcript-stem substring match.
        # Empty/None path guard. Without it, Path("").stem == "" and the
        # substring match below (``candidate.stem in "" or "" in
        # candidate.stem``) is ALWAYS True because ``"" in any_string`` is
        # True -- so an empty path would match (and return) the FIRST payload
        # sitting in gaia-context-payloads/, making the result depend on
        # whatever happens to be in that directory. Mirror the guard in
        # read_first_user_content_from_transcript: no path, no match.
        if not transcript_path:
            return {}
        agent_file = Path(transcript_path).stem  # e.g. "agent-ae190a4da68d626d4"
        # A stem that came out empty (e.g. path was "/" or "."): nothing to
        # match against, so the substring test would again degrade to the
        # always-true ``"" in candidate.stem``. Bail rather than grab an
        # arbitrary payload.
        if not agent_file:
            return {}
        for candidate in payload_dir.glob("*.json"):
            if candidate.stem in agent_file or agent_file in candidate.stem:
                return json.loads(candidate.read_text())
    except Exception:
        pass
    return {}
