"""
Transcript analysis and compliance scoring for Claude Code agent transcripts.

Provides:
    - ToolCall, DuplicateCall, TranscriptAnalysis: Data structures for analysis results
    - analyze(): Single-pass JSONL transcript parser
    - ComplianceScore: Compliance scoring data structure
    - compute_compliance_score(): Score agent behavior against compliance factors
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# T004 — Dataclasses
# ============================================================================


@dataclass(frozen=True)
class ToolCall:
    """A single tool invocation extracted from the transcript."""

    index: int  # 1-based position in the tool sequence
    tool_name: str
    arguments: Dict[str, Any]


@dataclass(frozen=True)
class DuplicateCall:
    """A group of identical tool calls detected via argument hashing."""

    tool_name: str
    arguments_hash: str
    indices: List[int]


@dataclass
class TranscriptAnalysis:
    """Aggregated metrics from a single-pass JSONL transcript parse."""

    input_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    duration_ms: Optional[int] = None
    first_timestamp: Optional[str] = None
    last_timestamp: Optional[str] = None
    model: str = ""
    stop_reasons: List[str] = field(default_factory=list)
    api_call_count: int = 0
    tool_sequence: List[ToolCall] = field(default_factory=list)
    tool_call_count: int = 0
    bash_commands: List[str] = field(default_factory=list)
    skills_injected: List[str] = field(default_factory=list)
    duplicate_tool_calls: List[DuplicateCall] = field(default_factory=list)
    pipe_commands: List[str] = field(default_factory=list)
    first_tool_name: Optional[str] = None


# ============================================================================
# T005 — analyze() function
# ============================================================================

# Matches <command-name>...</command-name> tags in user messages
_COMMAND_NAME_RE = re.compile(r"<command-name>\s*(.+?)\s*</command-name>")


def _has_unquoted_pipe(command: str) -> bool:
    """Detect unquoted pipe characters in a bash command string.

    Uses a character-walk approach to track quote state, which is more
    reliable than regex for nested quotes.
    """
    in_single = False
    in_double = False
    escaped = False

    for ch in command:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if ch == "|" and not in_single and not in_double:
            return True

    return False


def _hash_tool_call(tool_name: str, arguments: Dict[str, Any]) -> str:
    """Produce a stable hash for a (tool_name, arguments) pair."""
    canonical = json.dumps(
        {"tool_name": tool_name, "arguments": arguments},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _parse_timestamp(ts_str: str) -> Optional[datetime]:
    """Best-effort ISO timestamp parse."""
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    return None


def _extract_tool_calls_from_content(
    content: Any,
    result: TranscriptAnalysis,
    tool_index_counter: List[int],
    hash_map: Dict[str, Dict[str, Any]],
) -> None:
    """Extract tool_use blocks from a content list and update result in place."""
    if not isinstance(content, list):
        return

    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_use":
            continue

        tool_name = block.get("name", "")
        arguments = block.get("input", {})
        if not isinstance(arguments, dict):
            arguments = {}

        idx = tool_index_counter[0]
        tool_index_counter[0] += 1

        tc = ToolCall(index=idx, tool_name=tool_name, arguments=arguments)
        result.tool_sequence.append(tc)
        result.tool_call_count += 1

        if result.first_tool_name is None:
            result.first_tool_name = tool_name

        # Bash command extraction
        if tool_name in ("Bash", "bash"):
            cmd = arguments.get("command", "")
            if isinstance(cmd, str) and cmd:
                result.bash_commands.append(cmd)
                if _has_unquoted_pipe(cmd):
                    result.pipe_commands.append(cmd)

        # Duplicate detection
        h = _hash_tool_call(tool_name, arguments)
        hash_map.setdefault(h, {"tool_name": tool_name, "indices": []})
        hash_map[h]["indices"].append(idx)


def _extract_skills_from_content(content: Any, result: TranscriptAnalysis) -> None:
    """Search content for <command-name> tags and populate skills_injected."""
    if isinstance(content, str):
        for m in _COMMAND_NAME_RE.finditer(content):
            skill = m.group(1).strip()
            if skill and skill not in result.skills_injected:
                result.skills_injected.append(skill)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, str):
                for m in _COMMAND_NAME_RE.finditer(block):
                    skill = m.group(1).strip()
                    if skill and skill not in result.skills_injected:
                        result.skills_injected.append(skill)
            elif isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                for m in _COMMAND_NAME_RE.finditer(text):
                    skill = m.group(1).strip()
                    if skill and skill not in result.skills_injected:
                        result.skills_injected.append(skill)


def analyze(transcript_path: str) -> TranscriptAnalysis:
    """Single-pass JSONL parser for Claude Code agent transcripts.

    Reads the transcript file line by line and accumulates:
    - Token usage (input, cache_creation, cache_read, output)
    - Model name (from first assistant turn)
    - Stop reasons
    - API call count (assistant turns)
    - Tool sequence with ToolCall entries
    - Bash commands and pipe violations
    - Skills injected (from <command-name> tags in user messages)
    - Timestamps and duration
    - Duplicate tool call detection

    Args:
        transcript_path: Path to the JSONL transcript file.

    Returns:
        TranscriptAnalysis with all accumulated metrics.
        Returns default TranscriptAnalysis() for empty or missing files.
    """
    result = TranscriptAnalysis()

    if not transcript_path:
        return result

    path = Path(transcript_path).expanduser()
    if not path.exists():
        logger.debug("Transcript file not found: %s", path)
        return result

    try:
        text = path.read_text()
    except Exception as e:
        logger.debug("Failed to read transcript: %s", e)
        return result

    lines = text.strip().splitlines()
    if not lines:
        return result

    # Mutable counter for tool indexing (1-based)
    tool_index_counter = [1]
    # Hash map for duplicate detection: hash -> {tool_name, indices}
    hash_map: Dict[str, Dict[str, Any]] = {}

    first_ts_dt: Optional[datetime] = None
    last_ts_dt: Optional[datetime] = None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            logger.debug("Skipping malformed JSON line: %.80s", line)
            continue

        if not isinstance(entry, dict):
            continue

        # --- Timestamp tracking ---
        timestamp = entry.get("timestamp", "")
        if isinstance(timestamp, str) and timestamp:
            parsed_ts = _parse_timestamp(timestamp)
            if parsed_ts is not None:
                if result.first_timestamp is None:
                    result.first_timestamp = timestamp
                    first_ts_dt = parsed_ts
                result.last_timestamp = timestamp
                last_ts_dt = parsed_ts

        msg = entry.get("message", entry)
        if not isinstance(msg, dict):
            continue

        role = msg.get("role", "")
        content = msg.get("content", "")

        # --- Assistant turns ---
        if role == "assistant":
            # Usage accumulation — check both top-level and nested in message
            # Claude Code transcripts store usage/model/stop_reason inside
            # message object, but some formats keep them at entry level.
            usage = entry.get("usage") or msg.get("usage") or {}
            if isinstance(usage, dict):
                result.input_tokens += int(usage.get("input_tokens", 0))
                result.cache_creation_tokens += int(
                    usage.get("cache_creation_input_tokens", 0)
                )
                result.cache_read_tokens += int(
                    usage.get("cache_read_input_tokens", 0)
                )
                result.output_tokens += int(usage.get("output_tokens", 0))

            # Model from first assistant turn — check both locations
            model = entry.get("model") or msg.get("model") or ""
            if isinstance(model, str) and model and not result.model:
                result.model = model

            # Stop reason — check both locations
            stop_reason = entry.get("stop_reason") or msg.get("stop_reason") or ""
            if isinstance(stop_reason, str) and stop_reason:
                result.stop_reasons.append(stop_reason)

            # API call count
            result.api_call_count += 1

        # --- Tool calls from all content lists ---
        _extract_tool_calls_from_content(
            content, result, tool_index_counter, hash_map
        )

        # --- User messages: skill injection detection ---
        if role == "user":
            _extract_skills_from_content(content, result)

    # --- Duration computation ---
    if first_ts_dt is not None and last_ts_dt is not None:
        delta = last_ts_dt - first_ts_dt
        result.duration_ms = int(delta.total_seconds() * 1000)

    # --- Duplicate detection finalization ---
    for h, info in hash_map.items():
        if len(info["indices"]) > 1:
            result.duplicate_tool_calls.append(
                DuplicateCall(
                    tool_name=info["tool_name"],
                    arguments_hash=h,
                    indices=info["indices"],
                )
            )

    return result


# ============================================================================
# T006 — ComplianceScore
# ============================================================================


@dataclass(frozen=True)
class ComplianceScore:
    """Compliance score computed from transcript analysis and external signals."""

    total: int
    grade: str
    factors: Dict[str, int]
    deductions: List[str]


# Tools considered disciplined as first-tool choices
_DISCIPLINED_FIRST_TOOLS = {"Read", "Glob", "Grep"}


def _grade_from_total(total: int) -> str:
    """Map a numeric score to a letter grade."""
    if total >= 90:
        return "A"
    if total >= 75:
        return "B"
    if total >= 50:
        return "C"
    return "F"


def compute_compliance_score(
    analysis: TranscriptAnalysis,
    contract_valid: bool,
    has_scope_escalation: bool,
    anchor_hit_rate: float,
) -> ComplianceScore:
    """Compute a compliance score from transcript analysis and external signals.

    Factors (100 points total):
        - contract_valid: 25 pts (binary)
        - investigation_discipline: 20 pts (first tool is Read/Glob/Grep/None)
        - context_utilization: 15 pts (proportional to anchor_hit_rate)
        - no_pipe_violations: 15 pts (minus 3 per pipe command, floor 0)
        - no_duplicate_calls: 10 pts (minus 2 per duplicate group, floor 0)
        - no_scope_escalation: 15 pts (binary)

    Args:
        analysis: TranscriptAnalysis from analyze().
        contract_valid: Whether the agent's response contract passed validation.
        has_scope_escalation: Whether the agent escalated beyond its scope.
        anchor_hit_rate: Float 0.0-1.0 representing how many context anchors
            the agent referenced in its evidence.

    Returns:
        ComplianceScore with total, grade, factors breakdown, and deductions.
    """
    factors: Dict[str, int] = {}
    deductions: List[str] = []

    # 1. contract_valid (25 pts, binary)
    if contract_valid:
        factors["contract_valid"] = 25
    else:
        factors["contract_valid"] = 0
        deductions.append("contract_valid: invalid contract (-25)")

    # 2. investigation_discipline (20 pts)
    first_tool = analysis.first_tool_name
    if first_tool is None or first_tool in _DISCIPLINED_FIRST_TOOLS:
        factors["investigation_discipline"] = 20
    else:
        factors["investigation_discipline"] = 0
        deductions.append(
            f"investigation_discipline: first tool was {first_tool}, "
            f"expected Read/Glob/Grep/None (-20)"
        )

    # 3. context_utilization (15 pts, proportional)
    clamped_rate = max(0.0, min(1.0, anchor_hit_rate))
    ctx_points = round(15 * clamped_rate)
    factors["context_utilization"] = ctx_points
    if ctx_points < 15:
        deductions.append(
            f"context_utilization: anchor_hit_rate={clamped_rate:.2f} "
            f"(-{15 - ctx_points})"
        )

    # 4. no_pipe_violations (15 pts, -3 per pipe, floor 0)
    pipe_count = len(analysis.pipe_commands)
    pipe_points = max(0, 15 - 3 * pipe_count)
    factors["no_pipe_violations"] = pipe_points
    if pipe_count > 0:
        deductions.append(
            f"no_pipe_violations: {pipe_count} pipe command(s) (-{15 - pipe_points})"
        )

    # 5. no_duplicate_calls (10 pts, -2 per duplicate group, floor 0)
    dup_count = len(analysis.duplicate_tool_calls)
    dup_points = max(0, 10 - 2 * dup_count)
    factors["no_duplicate_calls"] = dup_points
    if dup_count > 0:
        deductions.append(
            f"no_duplicate_calls: {dup_count} duplicate group(s) (-{10 - dup_points})"
        )

    # 6. no_scope_escalation (15 pts, binary)
    if not has_scope_escalation:
        factors["no_scope_escalation"] = 15
    else:
        factors["no_scope_escalation"] = 0
        deductions.append("no_scope_escalation: scope escalation detected (-15)")

    total = sum(factors.values())
    grade = _grade_from_total(total)

    return ComplianceScore(
        total=total,
        grade=grade,
        factors=factors,
        deductions=deductions,
    )
