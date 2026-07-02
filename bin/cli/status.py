"""
gaia status -- Quick installation snapshot: version, mode, DB path,
registered workspace, last scan.

Reports:
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
    """Read episodic memory from gaia.db episodes table.

    T6 migration: primary source is now the episodes table in gaia.db.
    The legacy episodic-memory/index.json and workflow-episodic-memory/metrics.jsonl
    files are no longer read here.

    Returns dict with episodes, last_agent, source.
    """
    import sys as _sys
    _PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
    if str(_PLUGIN_ROOT) not in _sys.path:
        _sys.path.insert(0, str(_PLUGIN_ROOT))

    try:
        from gaia.store.writer import _connect as _store_connect
        from gaia.project import current as _project_current
    except ImportError:
        return {"episodes": [], "last_agent": None, "source": "db-unavailable"}

    try:
        ws = _project_current(cwd=project_root)
    except Exception:
        ws = None

    try:
        con = _store_connect()
        try:
            if ws:
                rows = con.execute(
                    "SELECT episode_id, workspace, timestamp, agent, plan_status, "
                    "outcome, exit_code, output_tokens_approx "
                    "FROM episodes WHERE workspace = ? ORDER BY timestamp ASC",
                    (ws,),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT episode_id, workspace, timestamp, agent, plan_status, "
                    "outcome, exit_code, output_tokens_approx "
                    "FROM episodes ORDER BY timestamp ASC"
                ).fetchall()
        finally:
            con.close()
    except Exception:
        return {"episodes": [], "last_agent": None, "source": "db-error"}

    episodes = [dict(r) for r in rows]
    with_agent = [e for e in episodes if e.get("agent")]
    last_agent = with_agent[-1] if with_agent else None
    return {"episodes": episodes, "last_agent": last_agent, "source": "gaia.db"}


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
    """Get last_scan_at from the DB workspaces row (T1.3: DB-backed read).

    Falls back to None when the workspace row or column is absent.
    """
    try:
        import sys as _sys
        _PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
        if str(_PLUGIN_ROOT) not in _sys.path:
            _sys.path.insert(0, str(_PLUGIN_ROOT))
        from gaia.project import current as _project_current
        from gaia.store.writer import _connect as _store_connect
        ws = _project_current(cwd=project_root)
        con = _store_connect()
        try:
            row = con.execute(
                "SELECT last_scan_at FROM workspaces WHERE name = ?", (ws,)
            ).fetchone()
            if row:
                return row[0]
        finally:
            con.close()
    except Exception:
        pass
    return None


def _get_memory_v2_stats(project_root: Path) -> dict:
    """Get Memory v2 stats: indexed count and avg score from gaia.db.

    T6 migration: reads episodes_fts count and episode timestamps from gaia.db.
    No longer reads from episodic-memory/index.json.
    Returns {"indexed": 0, "avg_score": None} on any failure.
    """
    import sys as _sys
    _PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
    if str(_PLUGIN_ROOT) not in _sys.path:
        _sys.path.insert(0, str(_PLUGIN_ROOT))

    base = {"indexed": 0, "avg_score": None}

    try:
        import tools.memory.scoring as scoring  # noqa: PLC0415
    except ImportError:
        scoring = None

    try:
        from gaia.store.writer import _connect as _store_connect
        from gaia.project import current as _project_current
    except ImportError:
        return base

    try:
        ws = _project_current(cwd=project_root)
    except Exception:
        ws = None

    try:
        con = _store_connect()
        try:
            # FTS5 indexed count from episodes_fts
            row = con.execute("SELECT COUNT(*) FROM episodes_fts").fetchone()
            indexed = row[0] if row else 0

            # avg_score from a sample of episodes timestamps
            avg_score = None
            if scoring is not None:
                if ws:
                    ep_rows = con.execute(
                        "SELECT timestamp FROM episodes WHERE workspace = ? "
                        "ORDER BY timestamp DESC LIMIT 50",
                        (ws,),
                    ).fetchall()
                else:
                    ep_rows = con.execute(
                        "SELECT timestamp FROM episodes ORDER BY timestamp DESC LIMIT 50"
                    ).fetchall()
                if ep_rows:
                    now = __import__("datetime").datetime.now(
                        __import__("datetime").timezone.utc
                    )
                    scores = []
                    for ep_row in ep_rows:
                        ts = ep_row[0] if ep_row else None
                        if ts:
                            try:
                                dt = __import__("datetime").datetime.fromisoformat(
                                    ts.replace("Z", "+00:00")
                                )
                                days_old = max(0.0, (now - dt).total_seconds() / 86400)
                            except Exception:
                                days_old = 0.0
                        else:
                            days_old = 0.0
                        try:
                            scores.append(scoring.score_memory(days_old, 0))
                        except Exception:
                            pass
                    if scores:
                        avg_score = sum(scores) / len(scores)
        finally:
            con.close()
    except Exception:
        return base

    return {"indexed": indexed, "avg_score": avg_score}


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
    memory_v2 = _get_memory_v2_stats(project_root)

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
        "indexed": memory_v2["indexed"],
        "avg_score": memory_v2["avg_score"],
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
        print(f"  Context:      last scan {_format_time(ctx)}")
    else:
        print("  Context:      never scanned -- run `gaia scan`")

    # Memory
    ep = status["episode_count"]
    ag = status["agent_session_count"]
    ep_str = f"{ep} episodes" if ep else "no episodic-memory"
    ag_str = f"{ag} agent sessions" if ag > 0 else "no agent sessions"
    indexed = status.get("indexed", 0)
    avg_score = status.get("avg_score")
    if avg_score is not None:
        print(f"  Memory:       {ep_str}  |  {ag_str}  |  {indexed} indexed  |  avg score {avg_score:.2f}")
    else:
        print(f"  Memory:       {ep_str}  |  {ag_str}  |  {indexed} indexed")

    # Contracts
    cs = status["contract_stats"]
    if cs:
        pct = round((cs["valid"] / cs["total"]) * 100) if cs["total"] else 0
        print(f"  Contracts:    {cs['valid']} valid / {cs['total']} total ({pct}% success rate)")

    print(f"  {sep}\n")


def register(subparsers):
    """Register the status subcommand."""
    sub = subparsers.add_parser(
        "status",
        help="Show Gaia system status",
        description="Print workspace status: agents, hooks, contracts.",
    )
    sub.add_argument("--json", action="store_true", default=False,
                     help="Emit JSON. bool.")


def cmd_status(args) -> int:
    """Handler for `gaia status`."""
    project_root = _find_project_root()
    claude_dir = project_root / ".claude"

    if not claude_dir.is_dir():
        msg = "gaia not installed in this directory. Run: gaia scan"
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
