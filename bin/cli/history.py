"""
gaia history -- mirror of gaia-history.js

Shows recent agent sessions with task descriptions, outcomes, and token usage.

Reads from:
  .claude/project-context/episodic-memory/index.json  (primary)
  .claude/project-context/workflow-episodic-memory/metrics.jsonl  (fallback)

Flags:
  --today / -t         Show only today's sessions
  --blocked / -b       Show only BLOCKED or NEEDS_INPUT sessions
  --agent / -a NAME    Filter by agent name
  --limit / -n N       Max sessions to show (default 20)
  --json               Machine-readable output
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Project root detection
# ---------------------------------------------------------------------------

def _find_project_root() -> Path:
    start = Path(os.environ.get("INIT_CWD", "")) if os.environ.get("INIT_CWD") else None
    if start and (start / ".claude").exists():
        return start

    current = Path.cwd()
    while True:
        if (current / ".claude").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent

    return Path(os.environ.get("INIT_CWD", str(Path.cwd())))


# ---------------------------------------------------------------------------
# Data readers
# ---------------------------------------------------------------------------

def _read_workflow_metrics(root: Path) -> list:
    """
    Read agent session history.
    Primary source: episodic-memory/index.json (episodes array with agent field).
    Fallback: workflow-episodic-memory/metrics.jsonl.
    """
    # Primary
    index_path = root / ".claude" / "project-context" / "episodic-memory" / "index.json"
    if index_path.exists():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            episodes = [e for e in (data.get("episodes") or []) if e.get("agent")]
            if episodes:
                return episodes
        except (json.JSONDecodeError, OSError):
            pass

    # Fallback
    metrics_path = root / ".claude" / "project-context" / "workflow-episodic-memory" / "metrics.jsonl"
    if not metrics_path.exists():
        return []

    entries = []
    try:
        for line in metrics_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("agent"):
                    entries.append(entry)
            except json.JSONDecodeError:
                pass
    except OSError:
        pass

    return entries


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------

_GENERIC_PROMPT_RE_PREFIX = "subagentStop for "


def _format_time(iso: str) -> str:
    """Format timestamp as HH:MM for today, or MM-DD HH:MM otherwise."""
    if not iso:
        return "?"
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        today = datetime.now(timezone.utc).date().isoformat()
        day_str = d.date().isoformat()
        time_str = d.strftime("%H:%M")
        if day_str == today:
            return time_str
        return f"{day_str[5:]} {time_str}"
    except (ValueError, AttributeError):
        return iso[11:16] if len(iso) >= 16 else iso


def _truncate(text: str, max_len: int) -> str:
    """Truncate string to max_len, collapsing whitespace first."""
    if not text:
        return ""
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _format_tokens(n) -> str:
    """Format token count as human-readable string."""
    if n is None:
        return "  n/a"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".rjust(6)
    if n >= 1_000:
        return f"{n / 1_000:.1f}k".rjust(6)
    return str(n).rjust(6)


def _status_label(status: str) -> str:
    """Return padded status string (no ANSI colours in stdlib version)."""
    if not status:
        return "n/a     "
    return status.upper().ljust(8)


# ---------------------------------------------------------------------------
# Plugin interface
# ---------------------------------------------------------------------------

def register(subparsers):
    """Register the 'history' subcommand."""
    p = subparsers.add_parser(
        "history",
        help="Show recent agent session history",
        description=(
            "Display recent agent sessions with task descriptions, statuses, and token usage.\n"
            "\n"
            "Data sources (in priority order):\n"
            "  .claude/project-context/episodic-memory/index.json\n"
            "  .claude/project-context/workflow-episodic-memory/metrics.jsonl\n"
        ),
    )
    p.add_argument(
        "--today", "-t",
        action="store_true",
        default=False,
        help="Show today's sessions only",
    )
    p.add_argument(
        "--blocked", "-b",
        action="store_true",
        default=False,
        help="Show BLOCKED or NEEDS_INPUT sessions only",
    )
    p.add_argument(
        "--agent", "-a",
        metavar="NAME",
        default=None,
        help="Filter by agent name (substring match)",
    )
    p.add_argument(
        "--limit", "-n",
        type=int,
        default=20,
        metavar="N",
        help="Maximum number of sessions to show (default: 20)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output results as JSON",
    )
    return p


def cmd_history(args) -> int:
    """Execute the history subcommand."""
    root = _find_project_root()
    claude_dir = root / ".claude"
    as_json = getattr(args, "json", False)

    if not claude_dir.exists():
        if as_json:
            print(json.dumps({"error": "gaia-ops not installed in this directory"}))
        else:
            print("\n  gaia-ops not installed in this directory")
            print("  Run: npx gaia-scan\n")
        return 1

    entries = _read_workflow_metrics(root)

    if not entries:
        if as_json:
            print(json.dumps([]))
        else:
            print("\n  No agent session history found yet")
            print("  History is recorded after each agent completes\n")
        return 0

    # Apply filters
    today_str = datetime.now(timezone.utc).date().isoformat()

    if getattr(args, "today", False):
        entries = [e for e in entries if (e.get("timestamp") or "").startswith(today_str)]

    if getattr(args, "blocked", False):
        entries = [e for e in entries
                   if (e.get("plan_status") or "").upper() in ("BLOCKED", "NEEDS_INPUT")]

    agent_filter = getattr(args, "agent", None)
    if agent_filter:
        needle = agent_filter.lower()
        entries = [e for e in entries if needle in (e.get("agent") or "").lower()]

    # Sort newest-first, apply limit
    limit = getattr(args, "limit", 20)
    entries = sorted(entries, key=lambda e: e.get("timestamp") or "", reverse=True)[:limit]

    if not entries:
        if as_json:
            print(json.dumps([]))
        else:
            print("\n  No sessions match the current filters\n")
        return 0

    if as_json:
        print(json.dumps(entries, indent=2))
        return 0

    # Table output (mirrors JS column layout)
    TIME_W = 11
    AGENT_W = 22
    TASK_W = 38
    STATUS_W = 12
    SEP = "-" * (TIME_W + AGENT_W + TASK_W + STATUS_W + 16)

    print("\n  Agent Session History")
    print("  " + SEP)
    print(
        "  "
        + "Time".ljust(TIME_W) + " "
        + "Agent".ljust(AGENT_W) + " "
        + "Task".ljust(TASK_W) + " "
        + "Status".ljust(STATUS_W) + " "
        + "~Tokens"
    )
    print("  " + SEP)

    total_tokens = 0
    agent_set = set()

    for entry in entries:
        time_str = _format_time(entry.get("timestamp") or "").ljust(TIME_W)
        agent_str = (entry.get("agent") or "unknown").ljust(AGENT_W)

        raw_prompt = entry.get("prompt") or ""
        is_generic = raw_prompt.lower().startswith(_GENERIC_PROMPT_RE_PREFIX) or not raw_prompt
        if is_generic:
            task_str = "(no description)".ljust(TASK_W)
        else:
            task_str = _truncate(raw_prompt, TASK_W).ljust(TASK_W)

        status_str = _status_label(entry.get("plan_status") or "").ljust(STATUS_W)
        tokens_n = entry.get("output_tokens_approx")
        tokens_str = _format_tokens(tokens_n)

        print(f"  {time_str} {agent_str} {task_str} {status_str} {tokens_str}")

        if isinstance(tokens_n, (int, float)):
            total_tokens += tokens_n
        agent_set.add(entry.get("agent") or "unknown")

    print("  " + SEP)

    # Footer
    agent_count = len(agent_set)
    agent_plural = "s" if agent_count != 1 else ""
    footer = f"  Total: {len(entries)} sessions | {agent_count} agent{agent_plural}"
    if total_tokens > 0:
        tok_fmt = f"{total_tokens / 1000:.1f}k" if total_tokens >= 1000 else str(total_tokens)
        footer += f" | ~{tok_fmt} tokens approx"
    print(footer)
    print()

    # Tip (only when no active filters)
    if not getattr(args, "today", False) and not getattr(args, "blocked", False) and not agent_filter:
        print("  Flags: --today | --blocked | --agent <name> | --limit <n>\n")

    return 0
