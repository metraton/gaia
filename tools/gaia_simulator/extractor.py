"""
Log parser for gaia hook replay testing.

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
    """A hook invocation recovered from a log, with the outcome the log recorded.

    expected_decision is ALLOW, DENY or BLOCK for logged pre-tool decisions and
    PASS for stop_hook and audit events; expected_tier is "" when the log has none.
    """

    timestamp: str
    hook_name: str
    tool_name: str
    stdin_payload: dict
    expected_decision: str
    expected_exit_code: int
    expected_tier: str
    source_file: str
    expected_metadata: dict[str, Any] = field(default_factory=dict)
    compare_tier: bool = False
    limitations: tuple[str, ...] = field(default_factory=tuple)


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

    # The log line cuts params off; the longest prefix ending in "}" that parses wins.
    for i in range(len(raw) - 1, 0, -1):
        if raw[i] == "}":
            try:
                return json.loads(raw[: i + 1])
            except json.JSONDecodeError:
                continue
    return None


class LogExtractor:
    """Extracts ReplayEvents from gaia hook logs.

    Parses two log formats:
    - hooks-YYYY-MM-DD.log: Human-readable hook execution logs
    - audit-YYYY-MM-DD.jsonl: Structured JSON audit trail

    Each format produces ReplayEvent instances that can be replayed
    against current hooks to detect regressions.
    """

    def extract_from_hooks_log(self, path: Path) -> list[ReplayEvent]:
        """Parse hooks-YYYY-MM-DD.log and extract hook invocation events.

        Each "Hook invoked" line is paired with the next unused ALLOWED/BLOCKED
        line in file order; each "Stop:" line becomes a stop_hook event.

        Returns:
            List of ReplayEvent instances, ordered by timestamp.
        """
        if not path.exists():
            return []

        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        source = path.name
        events: list[ReplayEvent] = []

        pending_invocations: list[dict] = []
        outcomes: list[dict] = []

        for line in lines:
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

            m = _RE_BLOCKED.match(line)
            if m:
                reason = m.group("reason")
                # One "BLOCKED:" line covers exit-2 blocks and exit-0 structured
                # denies; the reason prefix is the only thing that tells them apart.
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

        # Pairing is positional: it assumes every "Hook invoked" line has exactly
        # one outcome line, in the same order.
        outcome_idx = 0
        for inv in pending_invocations:
            if outcome_idx >= len(outcomes):
                break

            outcome = outcomes[outcome_idx]
            outcome_idx += 1

            tool = inv["tool"]
            params = inv["params"]

            # A params line cut off before its JSON closed has no full payload to replay.
            if params is None:
                continue

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

        Reads timestamp, session_id, tool_name, parameters, duration_ms, exit_code
        and tier from each record.

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

            # The audit log keeps no tool output, so replay restores only the
            # tool metadata, exit code and duration.
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

        pattern = f"hooks-{date_filter}.log" if date_filter else "hooks-*.log"
        for log_path in sorted(logs_dir.glob(pattern)):
            events.extend(self.extract_from_hooks_log(log_path))

        pattern = f"audit-{date_filter}.jsonl" if date_filter else "audit-*.jsonl"
        for jsonl_path in sorted(logs_dir.glob(pattern)):
            events.extend(self.extract_from_audit_jsonl(jsonl_path))

        if hook_filter:
            events = [e for e in events if e.hook_name == hook_filter]

        # Same timestamp, tool and decision count as one event; a hooks-log copy
        # replaces whatever was seen first.
        seen: dict[tuple, ReplayEvent] = {}
        for ev in events:
            key = (ev.timestamp, ev.tool_name, ev.expected_decision)
            if key not in seen or ev.source_file.startswith("hooks-"):
                seen[key] = ev

        result = sorted(seen.values(), key=lambda e: e.timestamp)
        return result
