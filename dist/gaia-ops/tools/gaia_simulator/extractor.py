"""
Log parser for gaia-ops hook replay testing.

Extracts ReplayEvent instances from production hook logs and audit JSONL files.
Completely decoupled from hooks -- only understands log formats.

Supported log formats:
    hooks-YYYY-MM-DD.log  - Human-readable hook execution logs
    audit-YYYY-MM-DD.jsonl - Structured JSON audit trail (post_tool_use events)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class ReplayEvent:
    """A single hook invocation extracted from logs."""

    timestamp: str
    hook_name: str  # "pre_tool_use", "post_tool_use", "subagent_stop"
    tool_name: str  # "Bash", "Agent", "Read", etc.
    stdin_payload: dict  # The JSON that was sent to the hook
    expected_decision: str  # "ALLOW", "BLOCK", "DENY"
    expected_exit_code: int  # 0, 1, 2
    expected_tier: str  # "T0", "T1", "T2", "T3", "" for non-bash
    source_file: str  # Which log file this came from
    expected_metadata: dict[str, Any] = field(default_factory=dict)
    compare_tier: bool = False
    limitations: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Internal regex patterns for hooks-*.log parsing
# ---------------------------------------------------------------------------

# Matches: 2026-03-11 00:03:32,080 [pre_tool_use] __main__ - INFO - Hook invoked: tool=Bash, params={...}
_RE_HOOK_INVOKED = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d+)"
    r"\s+\[(?P<hook>[^\]]+)\]\s+"
    r"__main__\s+-\s+INFO\s+-\s+"
    r"Hook invoked:\s+tool=(?P<tool>\w+),\s+params=(?P<params>\{.+)"
)

# Matches: ... - INFO - ALLOWED: <command> - tier=T0
_RE_ALLOWED_BASH = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d+)"
    r"\s+\[(?P<hook>[^\]]+)\]\s+"
    r"__main__\s+-\s+INFO\s+-\s+"
    r"ALLOWED:\s+(?P<cmd>.+?)\s+-\s+tier=(?P<tier>T\d)"
)

# Matches: ... - INFO - ALLOWED Task: <agent-name>
_RE_ALLOWED_TASK = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d+)"
    r"\s+\[(?P<hook>[^\]]+)\]\s+"
    r"__main__\s+-\s+INFO\s+-\s+"
    r"ALLOWED Task:\s+(?P<agent>\S+)"
)

# Matches: ... - WARNING - BLOCKED: <command> - <reason>
_RE_BLOCKED = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d+)"
    r"\s+\[(?P<hook>[^\]]+)\]\s+"
    r"__main__\s+-\s+WARNING\s+-\s+"
    r"BLOCKED:\s+(?P<cmd>.+?)\s+-\s+(?P<reason>.+)"
)

# Matches: ... - WARNING - BLOCKED Task: <agent> - <reason>
_RE_BLOCKED_TASK = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d+)"
    r"\s+\[(?P<hook>[^\]]+)\]\s+"
    r"__main__\s+-\s+WARNING\s+-\s+"
    r"BLOCKED Task:\s+(?P<agent>\S+)\s+-\s+(?P<reason>.+)"
)

# Matches: ... - INFO - Hook event: SubagentStop, agent: <agent-name>
_RE_SUBAGENT_STOP = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d+)"
    r"\s+\[subagent_stop\]\s+"
    r"__main__\s+-\s+INFO\s+-\s+"
    r"Hook event:\s+SubagentStop,\s+agent:\s+(?P<agent>\S+)"
)

# Matches: ... - INFO - Post-hook event: PostToolUse
_RE_POST_TOOL_USE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d+)"
    r"\s+\[post_tool_use\]\s+"
    r"__main__\s+-\s+INFO\s+-\s+"
    r"Post-hook event:\s+PostToolUse"
)

# Matches: ... - INFO - Stop: reason=user_requested, quality_sufficient=True, score=1.00
_RE_STOP_RESULT = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d+)"
    r"\s+\[stop_hook\]\s+"
    r"__main__\s+-\s+INFO\s+-\s+"
    r"Stop:\s+reason=(?P<reason>[^,]+),\s+quality_sufficient=(?P<quality>True|False),\s+score=(?P<score>\d+(?:\.\d+)?)"
)


def _try_parse_params(raw: str) -> Optional[dict]:
    """Try to parse the params JSON from a Hook invoked log line.

    The params value may be truncated in the log, so we attempt parsing
    and return None on failure.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Truncated JSON -- try to find a valid JSON prefix
    # The log line cuts off params, so we try progressively shorter substrings
    # that end with }
    for i in range(len(raw) - 1, 0, -1):
        if raw[i] == "}":
            try:
                return json.loads(raw[: i + 1])
            except json.JSONDecodeError:
                continue
    return None


class LogExtractor:
    """Extracts ReplayEvents from gaia-ops hook logs.

    Parses two log formats:
    - hooks-YYYY-MM-DD.log: Human-readable hook execution logs
    - audit-YYYY-MM-DD.jsonl: Structured JSON audit trail

    Each format produces ReplayEvent instances that can be replayed
    against current hooks to detect regressions.
    """

    def extract_from_hooks_log(self, path: Path) -> list[ReplayEvent]:
        """Parse hooks-YYYY-MM-DD.log and extract hook invocation events.

        Strategy:
        1. Find "Hook invoked" lines to get the tool + params (stdin payload)
        2. Find the corresponding ALLOWED/BLOCKED line to get the decision
        3. Pair them by timestamp proximity to build ReplayEvents

        Returns:
            List of ReplayEvent instances, ordered by timestamp.
        """
        if not path.exists():
            return []

        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        source = path.name
        events: list[ReplayEvent] = []

        # Two-pass approach:
        # Pass 1: collect all "Hook invoked" entries with their params
        # Pass 2: match them to ALLOWED/BLOCKED outcomes

        pending_invocations: list[dict] = []
        outcomes: list[dict] = []

        for line in lines:
            # Hook invoked line
            m = _RE_HOOK_INVOKED.match(line)
            if m:
                params = _try_parse_params(m.group("params"))
                pending_invocations.append({
                    "ts": m.group("ts"),
                    "hook": m.group("hook"),
                    "tool": m.group("tool"),
                    "params": params,
                })
                continue

            # ALLOWED bash command
            m = _RE_ALLOWED_BASH.match(line)
            if m:
                outcomes.append({
                    "ts": m.group("ts"),
                    "hook": m.group("hook"),
                    "decision": "ALLOW",
                    "exit_code": 0,
                    "tier": m.group("tier"),
                    "type": "bash",
                })
                continue

            # ALLOWED task (agent)
            m = _RE_ALLOWED_TASK.match(line)
            if m:
                outcomes.append({
                    "ts": m.group("ts"),
                    "hook": m.group("hook"),
                    "decision": "ALLOW",
                    "exit_code": 0,
                    "tier": "",
                    "type": "task",
                    "agent": m.group("agent"),
                })
                continue

            # BLOCKED bash command
            m = _RE_BLOCKED.match(line)
            if m:
                reason = m.group("reason")
                # The hook logs "BLOCKED:" for both permanent blocks (exit 2,
                # plain string) and structured deny responses (exit 0, JSON
                # with permissionDecision: "deny").  Distinguish them by
                # reason text:
                #
                # Exit 0 DENY (block_response is set):
                #   - "Dangerous ..." -- mutative verb T3 nonce flow
                #   - "Command-execution rule violated ..." -- cloud pipe
                #   - "Failed to persist pending approval ..." -- T3 nonce write error
                #   - Compound wrappers that propagate a component's block_response
                #
                # Exit 2 BLOCK (block_response is None):
                #   - "Command blocked by security policy ..." -- permanent deny list
                #   - "Commit message validation failed ..." -- validation error
                #   - "GitOps policy violation ..." -- GitOps validation
                #   - "Empty command not allowed"
                if (
                    reason.startswith("Dangerous")
                    or reason.startswith("Command-execution rule violated")
                    or reason.startswith("Failed to persist pending approval")
                    or (reason.startswith("Compound command blocked") and "Dangerous" in reason)
                ):
                    decision = "DENY"
                    exit_code = 0
                else:
                    decision = "BLOCK"
                    exit_code = 2
                outcomes.append({
                    "ts": m.group("ts"),
                    "hook": m.group("hook"),
                    "decision": decision,
                    "exit_code": exit_code,
                    "tier": "",
                    "type": "bash",
                    "reason": reason,
                })
                continue

            # BLOCKED task
            m = _RE_BLOCKED_TASK.match(line)
            if m:
                outcomes.append({
                    "ts": m.group("ts"),
                    "hook": m.group("hook"),
                    "decision": "BLOCK",
                    "exit_code": 2,
                    "tier": "",
                    "type": "task",
                    "agent": m.group("agent"),
                    "reason": m.group("reason"),
                })
                continue

            # Stop hook result (minimal replayable payload)
            m = _RE_STOP_RESULT.match(line)
            if m:
                quality_sufficient = m.group("quality") == "True"
                events.append(ReplayEvent(
                    timestamp=m.group("ts"),
                    hook_name="stop_hook",
                    tool_name="Stop",
                    stdin_payload={
                        "hook_event_name": "Stop",
                        "session_id": "replay",
                        "stop_reason": m.group("reason"),
                    },
                    expected_decision="PASS",
                    expected_exit_code=0,
                    expected_tier="",
                    source_file=source,
                    expected_metadata={
                        "quality_sufficient": quality_sufficient,
                        "score": float(m.group("score")),
                    },
                    limitations=(
                        "hooks log captures stop reason and quality summary, but not last_assistant_message",
                    ),
                ))
                continue

        # Match invocations to outcomes by sequential pairing
        # Each Hook invoked is followed by exactly one ALLOWED or BLOCKED
        outcome_idx = 0
        for inv in pending_invocations:
            if outcome_idx >= len(outcomes):
                break

            outcome = outcomes[outcome_idx]
            outcome_idx += 1

            # Build the stdin_payload that the hook expects
            tool = inv["tool"]
            params = inv["params"]

            # Skip events with truncated/unparseable params -- they cannot
            # be replayed meaningfully. The log line was cut off before the
            # JSON closed, so we don't have the full payload.
            if params is None:
                continue

            # Validate minimum payload: Bash needs "command", Agent needs
            # at least "subagent_type" or "description"
            if tool == "Bash" and "command" not in params:
                continue
            if tool == "Agent" and not any(
                k in params for k in ("subagent_type", "description", "prompt")
            ):
                continue

            stdin_payload = {
                "tool_name": tool,
                "tool_input": params,
                "hook_event_name": "PreToolUse",
                "session_id": "replay",
            }

            events.append(ReplayEvent(
                timestamp=inv["ts"],
                hook_name=inv["hook"],
                tool_name=tool,
                stdin_payload=stdin_payload,
                expected_decision=outcome["decision"],
                expected_exit_code=outcome["exit_code"],
                expected_tier=outcome.get("tier", ""),
                source_file=source,
                compare_tier=tool in {"Bash", "Task", "Agent"} and inv["hook"] == "pre_tool_use",
            ))

        return sorted(events, key=lambda event: event.timestamp)

    def extract_from_audit_jsonl(self, path: Path) -> list[ReplayEvent]:
        """Parse audit-YYYY-MM-DD.jsonl for structured event data.

        Audit JSONL files contain post_tool_use records with:
        - timestamp, session_id, tool_name, command, parameters, duration_ms,
          exit_code, tier

        These records are emitted by the post_tool_use hook after a tool ran,
        so they are replayed back into post_tool_use with the best available
        synthetic tool_result payload reconstructed from the audit entry.

        Returns:
            List of ReplayEvent instances, ordered by timestamp.
        """
        if not path.exists():
            return []

        source = path.name
        events: list[ReplayEvent] = []

        for line_text in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line_text = line_text.strip()
            if not line_text:
                continue

            try:
                record = json.loads(line_text)
            except json.JSONDecodeError:
                continue

            tool_name = record.get("tool_name", "")
            if not tool_name:
                continue

            # Build stdin payload matching PostToolUse format. The audit log does
            # not persist tool stdout/stderr, so replay can only restore the
            # tool metadata, exit code, and duration.
            params = record.get("parameters", {})
            tool_exit_code = int(record.get("exit_code", 0) or 0)
            duration_ms = record.get("duration_ms", 0)
            stdin_payload = {
                "tool_name": tool_name,
                "tool_input": params,
                "tool_result": {
                    "output": "",
                    "stdout": "",
                    "exit_code": tool_exit_code,
                    "duration_ms": duration_ms,
                },
                "hook_event_name": "PostToolUse",
                "session_id": record.get("session_id", "replay") or "replay",
            }

            tier = record.get("tier", "")

            events.append(ReplayEvent(
                timestamp=record.get("timestamp", ""),
                hook_name="post_tool_use",
                tool_name=tool_name,
                stdin_payload=stdin_payload,
                expected_decision="PASS",
                expected_exit_code=0,
                expected_tier=tier,
                source_file=source,
                expected_metadata={
                    "tool_exit_code": tool_exit_code,
                },
                compare_tier=bool(tier),
                limitations=(
                    "audit JSONL does not persist tool output, so post_tool_use replay cannot validate output-dependent critical-event detection",
                ),
            ))

        return events

    def extract_all(
        self,
        logs_dir: Path,
        date_filter: Optional[str] = None,
        hook_filter: Optional[str] = None,
    ) -> list[ReplayEvent]:
        """Extract from all log files in a directory, merge by timestamp.

        Args:
            logs_dir: Directory containing hooks-*.log and audit-*.jsonl files.
            date_filter: Optional YYYY-MM-DD string to filter by date.
            hook_filter: Optional hook name to filter (e.g. "pre_tool_use").

        Returns:
            Merged list of ReplayEvent instances, sorted by timestamp.
        """
        events: list[ReplayEvent] = []

        # Process hooks logs
        pattern = f"hooks-{date_filter}.log" if date_filter else "hooks-*.log"
        for log_path in sorted(logs_dir.glob(pattern)):
            events.extend(self.extract_from_hooks_log(log_path))

        # Process audit JSONL
        pattern = f"audit-{date_filter}.jsonl" if date_filter else "audit-*.jsonl"
        for jsonl_path in sorted(logs_dir.glob(pattern)):
            events.extend(self.extract_from_audit_jsonl(jsonl_path))

        # Apply hook filter
        if hook_filter:
            events = [e for e in events if e.hook_name == hook_filter]

        # Deduplicate: same timestamp + same tool_name + same expected_decision
        # Keep the hooks-log version (richer data) over audit version
        seen: dict[tuple, ReplayEvent] = {}
        for ev in events:
            key = (ev.timestamp, ev.tool_name, ev.expected_decision)
            if key not in seen or ev.source_file.startswith("hooks-"):
                seen[key] = ev

        # Sort by timestamp
        result = sorted(seen.values(), key=lambda e: e.timestamp)
        return result
