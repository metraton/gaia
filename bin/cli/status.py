"""
gaia status -- Quick snapshot of the Gaia-Ops system state.

Mirrors the output of gaia-status.js:
- Last agent session (name, time, status)
- Pending context updates count
- Active anomaly signals count
- project-context.json freshness
- Episodic memory stats
- Contract validation stats
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path


def _find_project_root() -> Path:
    """Walk up from cwd until .claude/ is found, or fall back to cwd."""
    init_cwd = os.environ.get("INIT_CWD")
    if init_cwd and (Path(init_cwd) / ".claude").is_dir():
        return Path(init_cwd)

    current = Path.cwd()
    root = Path(current.anchor)
    while current != root:
        if (current / ".claude").is_dir():
            return current
        current = current.parent

    return Path(init_cwd) if init_cwd else Path.cwd()


def _read_json(path: Path):
    """Read and parse a JSON file, returning None on any error."""
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _read_episodic_index(project_root: Path):
    """Read episodic memory index. Returns dict with episodes, last_agent, source."""
    index_path = project_root / ".claude" / "project-context" / "episodic-memory" / "index.json"
    data = _read_json(index_path)

    if data and isinstance(data.get("episodes"), list):
        episodes = data["episodes"]
        with_agent = [e for e in episodes if e.get("agent")]
        last_agent = with_agent[-1] if with_agent else None
        return {"episodes": episodes, "last_agent": last_agent, "source": "episodic-memory"}

    # Fallback: legacy metrics.jsonl
    metrics_path = (
        project_root / ".claude" / "project-context" / "workflow-episodic-memory" / "metrics.jsonl"
    )
    last_agent = None
    if metrics_path.is_file():
        try:
            lines = [ln.strip() for ln in metrics_path.read_text().splitlines() if ln.strip()]
            if lines:
                last_agent = json.loads(lines[-1])
        except Exception:
            pass

    return {"episodes": [], "last_agent": last_agent, "source": "legacy"}


def _get_pending_count(project_root: Path) -> int:
    """Count pending context updates."""
    path = project_root / ".claude" / "project-context" / "pending-updates" / "pending-index.json"
    data = _read_json(path)
    if data:
        return data.get("pending_count", 0)
    return 0


def _get_anomaly_count(project_root: Path) -> int:
    """Count active signal .flag files."""
    signals_dir = project_root / ".claude" / "project-context" / "workflow-episodic-memory" / "signals"
    if not signals_dir.is_dir():
        return 0
    try:
        return len([f for f in signals_dir.iterdir() if f.suffix == ".flag"])
    except OSError:
        return 0


def _get_context_last_updated(project_root: Path):
    """Get last_updated from project-context.json metadata."""
    path = project_root / ".claude" / "project-context" / "project-context.json"
    data = _read_json(path)
    if data:
        return (data.get("metadata") or {}).get("last_updated")
    return None


def _get_contract_stats(project_root: Path):
    """Get response contract validation stats from session directories."""
    contract_dir = project_root / ".claude" / "session" / "active" / "response-contract"
    if not contract_dir.is_dir():
        return None

    valid = 0
    total = 0

    try:
        for entry in contract_dir.iterdir():
            if not entry.is_dir() or not entry.name.startswith("session-"):
                continue
            result_path = entry / "last-result.json"
            data = _read_json(result_path)
            if data and "validation" in data:
                total += 1
                if data["validation"].get("valid"):
                    valid += 1
    except OSError:
        return None

    return {"valid": valid, "total": total} if total > 0 else None


def _format_time(iso_str):
    """Format ISO timestamp to short local time string."""
    if not iso_str:
        return "unknown"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        today = datetime.now().strftime("%Y-%m-%d")
        day_str = dt.strftime("%Y-%m-%d")
        if day_str == today:
            return dt.strftime("%H:%M")
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return str(iso_str)[:16].replace("T", " ")


def _collect_status(project_root: Path) -> dict:
    """Collect all status data into a dict."""
    episodic = _read_episodic_index(project_root)
    pending_count = _get_pending_count(project_root)
    anomaly_count = _get_anomaly_count(project_root)
    context_updated = _get_context_last_updated(project_root)
    contract_stats = _get_contract_stats(project_root)

    last_agent = episodic["last_agent"]
    episodes = episodic["episodes"]
    episode_count = len(episodes)
    agent_session_count = len([e for e in episodes if e.get("agent")])

    return {
        "last_agent": last_agent,
        "episode_count": episode_count,
        "agent_session_count": agent_session_count,
        "pending_count": pending_count,
        "anomaly_count": anomaly_count,
        "context_updated": context_updated,
        "contract_stats": contract_stats,
    }


def _print_human(status: dict) -> None:
    """Print human-readable status output."""
    sep = "-" * 50

    print("\n  Gaia System Status")
    print(f"  {sep}")

    # Last agent
    last = status["last_agent"]
    if last:
        time_str = _format_time(last.get("timestamp"))
        plan_status = last.get("plan_status") or ("ok" if last.get("exit_code") == 0 else "failed")
        agent_name = last.get("agent", "(unknown)")
        print(f"  Last agent:   {agent_name:<22} {time_str} -- {plan_status}")
    else:
        print("  Last agent:   no agent sessions recorded yet")

    # Pending
    pc = status["pending_count"]
    suffix = "" if pc == 1 else "s"
    note = "  run: gaia approvals" if pc > 0 else ""
    print(f"  Pending:      {pc} context update{suffix} to review{note}")

    # Anomalies
    ac = status["anomaly_count"]
    suffix = "" if ac == 1 else "s"
    note = "  check workflow-episodic-memory/signals/" if ac > 0 else ""
    print(f"  Anomalies:    {ac} active signal{suffix}{note}")

    # Context
    ctx = status["context_updated"]
    if ctx:
        print(f"  Context:      project-context.json -- updated {_format_time(ctx)}")
    else:
        print("  Context:      project-context.json missing -- run gaia-scan")

    # Memory
    ep = status["episode_count"]
    ag = status["agent_session_count"]
    ep_str = f"{ep} episodes" if ep else "no episodic-memory"
    ag_str = f"{ag} agent sessions" if ag > 0 else "no agent sessions"
    print(f"  Memory:       {ep_str}  |  {ag_str}")

    # Contracts
    cs = status["contract_stats"]
    if cs:
        pct = round((cs["valid"] / cs["total"]) * 100) if cs["total"] else 0
        print(f"  Contracts:    {cs['valid']} valid / {cs['total']} total ({pct}% success rate)")

    print(f"  {sep}\n")


def register(subparsers):
    """Register the status subcommand."""
    sub = subparsers.add_parser("status", help="Show Gaia system status")
    sub.add_argument("--json", action="store_true", default=False, help="Output as JSON")


def cmd_status(args) -> int:
    """Handler for `gaia status`."""
    project_root = _find_project_root()
    claude_dir = project_root / ".claude"

    if not claude_dir.is_dir():
        msg = "gaia-ops not installed in this directory. Run: gaia-scan"
        if getattr(args, "json", False):
            print(json.dumps({"error": msg}))
        else:
            print(f"\n  {msg}\n")
        return 1

    status = _collect_status(project_root)

    if getattr(args, "json", False):
        print(json.dumps(status, indent=2, default=str))
    else:
        _print_human(status)

    return 0
