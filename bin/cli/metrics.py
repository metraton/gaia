"""
gaia metrics -- Usage analytics: tier classification, agent invocations,
anomaly counters.

Dashboard v2: cmd_metrics() computes one MetricsSnapshot (see the
MetricsSnapshot dataclass below) per invocation and feeds it to both
render_console() and json.dumps() -- the data is calculated exactly once,
under one canonical set of keys, versioned via schema_version.

Displays system metrics dashboard:
  - Security tier usage distribution
  - Command type breakdown
  - Top commands by frequency
  - Agent invocations (Gaia specialists only)
  - Native agent activity (Explore/Plan/claude-code-guide/general-purpose,
    segregated so harness noise doesn't drown out Gaia specialist signal)
  - Agent outcomes
  - Token usage (real, transcript-parsed when available; approx/chars-4
    fallback labeled as such)
  - Anomaly summary (last 30 days, severity-sorted so a rare critical/error
    entry can't be buried behind a high-volume low-severity type)
  - Activity today (UTC calendar day -- audit timestamps are UTC)

With --agent NAME shows a detail view for that agent.

Data sources:
  ~/.gaia/gaia.db  (substrate SQLite)
    - episodes table          -> agent invocations, outcomes, token usage,
                                 runtime skills, context snapshots/updates
                                 (context_metrics JSON column)
    - episode_anomalies table -> anomaly summary
  .claude/logs/audit-*.jsonl  (security tier events -> tier usage, command
                               breakdown, top commands; last ~30d retention,
                               not filtered by workspace)

Flags:
  --agent NAME      Show detail view for a specific agent
  --workspace NAME  Workspace identity override (default: gaia.project.current())
  --json            Machine-readable output (MetricsSnapshot.to_dict(), versioned
                     via schema_version)
"""

import fnmatch
import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional


# Schema version for the JSON contract emitted by MetricsSnapshot.to_dict().
# Bump this whenever a section's shape changes in a way a machine consumer
# (future web/API surface) would need to branch on.
SCHEMA_VERSION = "1"


# ---------------------------------------------------------------------------
# Native agent segregation
# ---------------------------------------------------------------------------

_FALLBACK_NATIVE_AGENTS = frozenset({"Explore", "Plan", "general-purpose", "claude-code-guide"})


def _native_agent_names() -> frozenset:
    """Harness-native Claude Code agent names.

    Reuses the canonical ``NATIVE_AGENTS`` list from
    hooks/modules/tools/task_validator.py -- the same list the Task-tool
    validator uses to recognize non-Gaia dispatch targets -- instead of
    re-declaring it here. Falls back to a mirrored literal if the hooks
    package cannot be imported (e.g. metrics.py invoked standalone).
    """
    _bin_dir = Path(__file__).resolve().parent.parent
    _repo_root = _bin_dir.parent
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))
    try:
        from hooks.modules.tools.task_validator import NATIVE_AGENTS
        return frozenset(NATIVE_AGENTS)
    except Exception:
        return _FALLBACK_NATIVE_AGENTS


NATIVE_AGENT_NAMES = _native_agent_names()


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

def _read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    entries = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    except OSError:
        pass
    return entries


def _read_audit_logs(root: Path) -> list:
    logs_dir = root / ".claude" / "logs"
    if not logs_dir.exists():
        return []
    all_entries = []
    try:
        for f in logs_dir.iterdir():
            if f.name.startswith("audit-") and f.name.endswith(".jsonl"):
                all_entries.extend(_read_jsonl(f))
    except OSError:
        pass
    return all_entries


# ---------------------------------------------------------------------------
# DB-backed readers (T6 episodic-workflow-to-db migration)
#
# T4 migrated the workflow episodic writers from JSONL/JSON files to the
# gaia.db ``episodes`` + ``episode_anomalies`` tables. These readers were the
# missing half of that migration (T6): they now query gaia.db via
# gaia.store.reader instead of the dead .claude/project-context/*.jsonl files.
# The connection setup mirrors bin/cli/history.py's own T6 migration exactly:
# resolve the workspace via gaia.project.current, open the store connection,
# and fall back to [] on any import/connection failure. The three audit-log
# sections (tier usage, command breakdown, top commands) still read
# .claude/logs/audit-*.jsonl and are untouched.
# ---------------------------------------------------------------------------

def _open_store(root: Path, workspace_override: str = None):
    """Resolve (connection, workspace) against gaia.db, or (None, None).

    ``workspace_override`` -- explicit ``--workspace`` value from the CLI.
    When set, it wins over ``gaia.project.current()`` resolution. Mirrors the
    connection setup in bin/cli/history.py._read_workflow_metrics.
    """
    import sys as _sys
    _BIN_DIR = Path(__file__).resolve().parent.parent
    _REPO_ROOT = _BIN_DIR.parent
    for p in (_REPO_ROOT, str(_REPO_ROOT)):
        if str(p) not in _sys.path:
            _sys.path.insert(0, str(p))

    try:
        from gaia.store.reader import _connect
        from gaia.project import current as _project_current
    except ImportError:
        return None, None

    if workspace_override:
        ws = workspace_override
    else:
        try:
            ws = _project_current(cwd=root)
        except Exception:
            ws = None

    try:
        con = _connect()
    except Exception:
        return None, None
    return con, ws


def _extract_real_token_fields(raw_context_metrics) -> dict:
    """Pull real (transcript-parsed) token counts out of an episode's
    ``context_metrics`` JSON blob.

    Mirrors the ``metrics`` dict shape written by
    hooks/modules/audit/workflow_recorder.py: alongside the always-present
    ``output_tokens_approx`` (chars/4 heuristic), a transcript-backed run also
    carries ``output_tokens_real`` / ``input_tokens`` / ``cache_creation_tokens``
    / ``cache_read_tokens``. Returns all four keys, each ``None`` when absent,
    so ``_calculate_token_usage`` can tell "real" from "approx" per entry.
    """
    fields = {
        "output_tokens_real": None,
        "input_tokens": None,
        "cache_creation_tokens": None,
        "cache_read_tokens": None,
    }
    if not raw_context_metrics:
        return fields
    try:
        blob = json.loads(raw_context_metrics)
    except (json.JSONDecodeError, TypeError):
        return fields
    if isinstance(blob, dict) and isinstance(blob.get("metrics"), dict):
        metrics = blob["metrics"]
    else:
        metrics = blob
    if not isinstance(metrics, dict):
        return fields
    for key in fields:
        if key in metrics:
            fields[key] = metrics[key]
    return fields


def _read_workflow_metrics(root: Path, workspace_override: str = None) -> list:
    """Agent-session rows from the gaia.db ``episodes`` table (T6 migration).

    Returns episode dicts carrying agent/timestamp/plan_status/exit_code/
    output_length/output_tokens_approx -- the fields the agent-invocation,
    agent-outcome, and token-usage calculators consume. Replaces the dead
    episodic-memory/index.json + workflow-episodic-memory/metrics.jsonl reads.

    Each row also carries the real-token fields extracted from
    ``context_metrics`` (output_tokens_real / input_tokens /
    cache_creation_tokens / cache_read_tokens, all ``None`` when the episode
    has no transcript-backed metrics) so token-usage reporting can prefer real
    counts over the chars/4 approximation. The raw ``context_metrics`` blob
    itself is not retained on the row -- only its extracted token fields.
    """
    con, ws = _open_store(root, workspace_override)
    if con is None:
        return []
    try:
        try:
            if ws:
                rows = con.execute(
                    "SELECT episode_id, workspace, timestamp, session_id, task_id, "
                    "agent, type, title, plan_status, outcome, exit_code, "
                    "duration_seconds, output_length, output_tokens_approx, tier, "
                    "context_metrics "
                    "FROM episodes WHERE workspace = ? AND agent IS NOT NULL "
                    "ORDER BY timestamp DESC",
                    (ws,),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT episode_id, workspace, timestamp, session_id, task_id, "
                    "agent, type, title, plan_status, outcome, exit_code, "
                    "duration_seconds, output_length, output_tokens_approx, tier, "
                    "context_metrics "
                    "FROM episodes WHERE agent IS NOT NULL "
                    "ORDER BY timestamp DESC"
                ).fetchall()
            result = []
            for r in rows:
                row = dict(r)
                raw_cm = row.pop("context_metrics", None)
                row.update(_extract_real_token_fields(raw_cm))
                result.append(row)
            return result
        finally:
            con.close()
    except Exception:
        return []


def _read_run_snapshots(root: Path, workspace_override: str = None) -> list:
    """Per-episode workflow-metrics blobs from ``episodes.context_metrics``.

    T4 folded the old run-snapshots.jsonl signals (context_snapshot,
    context_updated / *_sections, default_skills_snapshot, model, skills) into
    the ``episodes.context_metrics`` JSON column under the ``metrics`` key.
    This reader parses that blob per episode so the context-snapshot,
    context-update, and runtime-skill calculators keep working. Older migrated
    rows that stored the metrics dict at the top level are handled too.
    """
    con, ws = _open_store(root, workspace_override)
    if con is None:
        return []
    try:
        try:
            if ws:
                rows = con.execute(
                    "SELECT context_metrics FROM episodes "
                    "WHERE workspace = ? AND context_metrics IS NOT NULL "
                    "ORDER BY timestamp DESC",
                    (ws,),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT context_metrics FROM episodes "
                    "WHERE context_metrics IS NOT NULL "
                    "ORDER BY timestamp DESC"
                ).fetchall()
        finally:
            con.close()
    except Exception:
        return []

    snapshots = []
    for r in rows:
        raw = r["context_metrics"]
        if not raw:
            continue
        try:
            blob = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(blob, dict) and isinstance(blob.get("metrics"), dict):
            snap = blob["metrics"]
        else:
            snap = blob
        if isinstance(snap, dict):
            snapshots.append(snap)
    return snapshots


def _read_agent_skill_snapshots(root: Path) -> list:
    """Explicit per-agent skill snapshots.

    The legacy agent-skills.jsonl (explicit snapshots) has no gaia.db
    equivalent -- the runtime-skill summary now derives every profile from
    each episode's ``default_skills_snapshot`` (supplied by
    _read_run_snapshots). Returns [] so _calculate_runtime_skill_summary falls
    back to those run-default profiles.
    """
    return []


def _read_anomaly_entries(root: Path, workspace_override: str = None) -> list:
    """Anomaly entries grouped per episode from the ``episode_anomalies`` table.

    T4 migrated anomalies from workflow-episodic-memory/anomalies.jsonl into
    the ``episode_anomalies`` child table (one row per anomaly). This reader
    regroups them into the per-session shape the anomaly-summary calculator
    expects: ``{timestamp, anomalies: [{type}, ...], metrics: {agent}}``.
    """
    con, ws = _open_store(root, workspace_override)
    if con is None:
        return []
    try:
        try:
            if ws:
                rows = con.execute(
                    "SELECT ea.episode_id AS episode_id, ea.timestamp AS timestamp, "
                    "ea.type AS type, ea.severity AS severity, e.agent AS agent "
                    "FROM episode_anomalies ea "
                    "LEFT JOIN episodes e ON e.episode_id = ea.episode_id "
                    "WHERE ea.workspace = ? "
                    "ORDER BY ea.timestamp DESC",
                    (ws,),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT ea.episode_id AS episode_id, ea.timestamp AS timestamp, "
                    "ea.type AS type, ea.severity AS severity, e.agent AS agent "
                    "FROM episode_anomalies ea "
                    "LEFT JOIN episodes e ON e.episode_id = ea.episode_id "
                    "ORDER BY ea.timestamp DESC"
                ).fetchall()
        finally:
            con.close()
    except Exception:
        return []

    grouped = {}
    order = []
    for r in rows:
        ep = r["episode_id"]
        if ep not in grouped:
            grouped[ep] = {
                "timestamp": r["timestamp"],
                "anomalies": [],
                "metrics": {"agent": r["agent"] or "unknown"},
            }
            order.append(ep)
        grouped[ep]["anomalies"].append({"type": r["type"], "severity": r["severity"]})
    return [grouped[ep] for ep in order]


def _read_agent_definition(root: Path, agent_name: str) -> dict:
    """Extract description and skills from agent .md frontmatter."""
    agent_path = root / ".claude" / "agents" / f"{agent_name}.md"
    if not agent_path.exists():
        return {}
    try:
        content = agent_path.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return {}
        end = content.find("---", 3)
        if end == -1:
            return {}
        fm = content[3:end]
        description = ""
        skills = []
        in_skills = False
        for line in fm.splitlines():
            stripped = line.strip()
            if stripped.startswith("description:"):
                description = stripped[len("description:"):].strip().strip("'\"")
                in_skills = False
            elif stripped == "skills:":
                in_skills = True
            elif in_skills and stripped.startswith("- "):
                skills.append(stripped[2:].strip())
            elif in_skills and stripped and not stripped.startswith("-"):
                in_skills = False
        return {"description": description, "skills": skills}
    except OSError:
        return {}


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _classify_command(command: str) -> str:
    if not command:
        return "general"
    cmd = command.strip().lower()
    if cmd.startswith("terragrunt") or cmd.startswith("terraform"):
        return "terraform"
    if cmd.startswith("kubectl"):
        return "kubernetes"
    if cmd.startswith("helm") or cmd.startswith("flux"):
        return "gitops"
    if cmd.startswith("git") or cmd.startswith("glab"):
        return "git"
    if cmd.startswith("gcloud") or cmd.startswith("gsutil"):
        return "gcp"
    if cmd.startswith("aws"):
        return "aws"
    if cmd.startswith("docker"):
        return "docker"
    if cmd.startswith(("npm", "node", "python", "pip")):
        return "dev"
    return "general"


def _extract_command_label(command: str) -> str:
    """Extract short human-readable label from full command string."""
    if not command:
        return "(unknown)"
    cmd = command.strip()
    # Strip env var assignments
    cmd = re.sub(r'^(?:[A-Z_][A-Z0-9_]*=\S+\s+)+', '', cmd)
    # Strip timeout wrapper
    cmd = re.sub(r'^timeout\s+\S+\s+', '', cmd)
    # Strip cd/pushd navigation
    m = re.match(r'^(?:cd|pushd)\s+\S+\s*(?:&&|;)\s*(.*)', cmd)
    if m:
        cmd = m.group(1).strip()
    # Strip at pipe/semicolon/&&
    cmd = re.split(r'\s*(?:[|;&]|&&|\|\|)\s*', cmd)[0].strip()
    # Strip trailing redirections
    cmd = re.sub(r'\s*\d*>.*$', '', cmd).strip()

    tokens = cmd.split()
    parts = [tokens[0]] if tokens else ["(unknown)"]
    for t in tokens[1:]:
        if len(parts) >= 3:
            break
        if not t.startswith(("-", "/", '"', "'")):
            parts.append(t)
    return " ".join(parts)[:32]


def _format_tokens(n) -> str:
    if n is None:
        return "n/a"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _format_chars(n) -> str:
    if n is None:
        return "n/a"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _make_bar(percentage: float, width: int = 24) -> str:
    """Fixed-width Unicode bar: filled cells (█) + empty cells (░).

    Unlike the old ``#``-only bar (which returned only the filled prefix and
    relied on the caller's ``:<N`` string format for padding), this returns
    the full ``width``-length string so callers can drop straight into a
    fixed-column box row without separate padding.
    """
    filled = max(0, min(width, round((percentage / 100) * width)))
    return "█" * filled + "░" * (width - filled)


def _count_values(values: list) -> dict:
    counts = {}
    for v in values:
        if not v:
            continue
        counts[v] = counts.get(v, 0) + 1
    return counts


def _sorted_counts(counts: dict) -> list:
    return sorted(
        [{"name": k, "count": v} for k, v in counts.items()],
        key=lambda x: (-x["count"], x["name"]),
    )


def _top_counts(values: list, limit: int = 5) -> list:
    return _sorted_counts(_count_values(values))[:limit]


def _format_count_summary(entries: list, empty_label: str = "none") -> str:
    if not entries:
        return empty_label
    return ", ".join(f"{e['name']}({e['count']})" for e in entries)


def _format_skills(skills: list, limit: int = 4) -> str:
    if not skills:
        return "none"
    if len(skills) <= limit:
        return ", ".join(skills)
    return ", ".join(skills[:limit]) + f", +{len(skills) - limit} more"


# ---------------------------------------------------------------------------
# Metric calculators
# ---------------------------------------------------------------------------

def _calculate_tier_usage(audit_logs: list) -> dict:
    tier_entries = [l for l in audit_logs if l.get("tier")]
    counts = {}
    for e in tier_entries:
        t = e.get("tier", "unknown")
        counts[t] = counts.get(t, 0) + 1

    total = len(tier_entries)
    distribution = sorted(
        [{"tier": t, "count": c, "percentage": c / total * 100 if total else 0}
         for t, c in counts.items()],
        key=lambda x: x["tier"],
    )

    today = datetime.now(timezone.utc).date().isoformat()
    today_entries = [l for l in audit_logs if (l.get("timestamp") or "").startswith(today)]
    today_t3 = sum(1 for l in today_entries if l.get("tier") == "T3")

    hour_counts = {}
    for e in today_entries:
        ts = e.get("timestamp")
        if ts and len(ts) >= 13:
            h = ts[11:13]
            hour_counts[h] = hour_counts.get(h, 0) + 1

    peak_hour = None
    peak_count = 0
    for h, c in hour_counts.items():
        if c > peak_count:
            peak_count = c
            peak_hour = h

    return {
        "total": total,
        "distribution": distribution,
        "today_count": len(today_entries),
        "today_t3": today_t3,
        "peak_hour": peak_hour,
        "peak_count": peak_count,
    }


def _calculate_command_type_breakdown(audit_logs: list) -> dict:
    counts = {}
    for e in audit_logs:
        t = _classify_command(e.get("command") or "")
        counts[t] = counts.get(t, 0) + 1

    total = len(audit_logs)
    breakdown = sorted(
        [{"type": t, "count": c, "percentage": c / total * 100 if total else 0}
         for t, c in counts.items()],
        key=lambda x: -x["count"],
    )
    return {"total": total, "breakdown": breakdown}


def _calculate_top_commands(audit_logs: list) -> list:
    tier_order = {"T3": 3, "T2": 2, "T1": 1, "T0": 0, "unknown": -1}
    label_map = {}

    for e in audit_logs:
        if not e.get("command"):
            continue
        label = _extract_command_label(e["command"])
        tier = e.get("tier") or "unknown"

        if label not in label_map:
            label_map[label] = {"count": 0, "tier": tier, "t3count": 0}
        label_map[label]["count"] += 1
        if tier == "T3":
            label_map[label]["t3count"] += 1
        if tier_order.get(tier, -1) > tier_order.get(label_map[label]["tier"], -1):
            label_map[label]["tier"] = tier

    return sorted(
        [{"label": l, **v} for l, v in label_map.items()],
        key=lambda x: -x["count"],
    )[:10]


def _calculate_error_rate(audit_logs: list) -> dict:
    with_code = [l for l in audit_logs if "exit_code" in l]
    errors = [l for l in with_code if l["exit_code"] != 0]
    all_zero = bool(with_code) and len(errors) == 0
    total = len(with_code)
    return {
        "total": total,
        "errors": len(errors),
        "error_rate": len(errors) / total * 100 if total else 0,
        "limited_by_api": all_zero,
    }


def _split_native_agents(workflow_metrics: list) -> tuple:
    """Separate harness-native Claude Code agents from Gaia domain specialists.

    Native agents (Explore, Plan, claude-code-guide, general-purpose -- see
    ``NATIVE_AGENT_NAMES``) are utility subagents built into the harness, not
    Gaia specialists. Explore alone can dwarf every Gaia specialist's
    invocation count, which drowns out the invocation/outcome/token reads
    that exist to gauge Gaia specialist usage. Returns
    ``(gaia_metrics, native_metrics)``.
    """
    gaia, native = [], []
    for r in workflow_metrics:
        target = native if (r.get("agent") or "") in NATIVE_AGENT_NAMES else gaia
        target.append(r)
    return gaia, native


def _calculate_agent_invocations(workflow_metrics: list) -> dict:
    today = datetime.now(timezone.utc).date().isoformat()
    today_count = sum(1 for r in workflow_metrics if (r.get("timestamp") or "").startswith(today))

    agent_map = {}
    for e in workflow_metrics:
        name = e.get("agent") or "unknown"
        if name not in agent_map:
            agent_map[name] = {"count": 0, "total_output": 0, "successes": 0}
        agent_map[name]["count"] += 1
        agent_map[name]["total_output"] += e.get("output_length") or 0
        if e.get("exit_code") == 0:
            agent_map[name]["successes"] += 1

    total = len(workflow_metrics)
    agents = sorted(
        [
            {
                "name": n,
                "count": v["count"],
                "avg_output": round(v["total_output"] / v["count"]) if v["count"] else 0,
                "success_rate": v["successes"] / v["count"] * 100 if v["count"] else 0,
                "percentage": v["count"] / total * 100 if total else 0,
            }
            for n, v in agent_map.items()
        ],
        key=lambda x: -x["count"],
    )
    return {"agents": agents, "total": total, "today_count": today_count}


def _calculate_agent_outcomes(workflow_metrics: list):
    with_status = [r for r in workflow_metrics if r.get("plan_status")]
    if not with_status:
        return None

    counts = {}
    for e in with_status:
        s = e["plan_status"].upper()
        counts[s] = counts.get(s, 0) + 1

    total = len(with_status)
    distribution = sorted(
        [{"status": s, "count": c, "percentage": c / total * 100} for s, c in counts.items()],
        key=lambda x: -x["count"],
    )
    return {"distribution": distribution, "total": total}


def _calculate_token_usage(workflow_metrics: list):
    """Aggregate output-token usage per agent, preferring real counts.

    Each entry may carry ``output_tokens_real`` (transcript-parsed
    usage.output_tokens, set by workflow_recorder.py when a transcript was
    available) alongside the always-present ``output_tokens_approx``
    (chars/4 heuristic). The "effective" total per entry is the real count
    when present, degrading to the approximation otherwise -- both the
    per-entry and the aggregate output are labeled so a caller can tell which
    source backed the number. When any entry carries input/cache token data,
    the totals for those are surfaced too (None when no entry has them, so a
    workspace with no transcript-backed episodes doesn't show a false zero).
    """
    with_tokens = [
        r
        for r in workflow_metrics
        if isinstance(r.get("output_tokens_real"), (int, float))
        or isinstance(r.get("output_tokens_approx"), (int, float))
    ]
    if not with_tokens:
        return None

    agent_map = {}
    grand_total = 0
    real_count = 0
    total_input = 0
    total_cache_creation = 0
    total_cache_read = 0
    has_input_data = False

    for e in with_tokens:
        name = e.get("agent") or "unknown"
        real = e.get("output_tokens_real")
        is_real = isinstance(real, (int, float))
        effective = real if is_real else (e.get("output_tokens_approx") or 0)
        if is_real:
            real_count += 1
        grand_total += effective

        if name not in agent_map:
            agent_map[name] = {"total": 0, "count": 0, "real_count": 0}
        agent_map[name]["total"] += effective
        agent_map[name]["count"] += 1
        if is_real:
            agent_map[name]["real_count"] += 1

        if isinstance(e.get("input_tokens"), (int, float)):
            has_input_data = True
            total_input += e["input_tokens"]
            total_cache_creation += e.get("cache_creation_tokens") or 0
            total_cache_read += e.get("cache_read_tokens") or 0

    agents = sorted(
        [
            {
                "name": n,
                "total": v["total"],
                "avg": round(v["total"] / v["count"]) if v["count"] else 0,
                "count": v["count"],
                "source": "real" if v["real_count"] == v["count"] else ("mixed" if v["real_count"] else "approx"),
            }
            for n, v in agent_map.items()
        ],
        key=lambda x: -x["total"],
    )
    return {
        "agents": agents,
        "grand_total": grand_total,
        "entry_count": len(with_tokens),
        "real_count": real_count,
        "approx_count": len(with_tokens) - real_count,
        "input_tokens": total_input if has_input_data else None,
        "cache_creation_tokens": total_cache_creation if has_input_data else None,
        "cache_read_tokens": total_cache_read if has_input_data else None,
    }


_SEVERITY_RANK = {"critical": 3, "error": 2, "warning": 1, "info": 0, "unknown": -1}


def _calculate_anomaly_summary(anomaly_entries: list):
    """Anomaly summary over the last 30 days, sorted so severity beats volume.

    A single high-volume, low-severity type (e.g. ``pipe_retroactive``, a
    warning fired on every pipe) can otherwise dominate a plain count-sorted
    list and bury a rare but critical entry (e.g.
    ``response_contract_violation``). ``by_type`` sorts by severity rank
    first, count second, so critical/error entries always surface above
    warning/info noise regardless of how often the noisy type fires.
    ``by_severity`` gives the aggregate breakdown for a one-line read.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    entries = [e for e in anomaly_entries if e and (e.get("timestamp") or "") >= cutoff]
    if not entries:
        return None

    type_counts = {}
    type_severity = {}
    severity_counts = {}
    agent_counts = {}
    for e in entries:
        agent = (e.get("metrics") or {}).get("agent", "unknown")
        for anomaly in e.get("anomalies") or []:
            t = anomaly.get("type", "unknown")
            sev = anomaly.get("severity") or "unknown"
            type_counts[t] = type_counts.get(t, 0) + 1
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
            agent_counts[agent] = agent_counts.get(agent, 0) + 1
            if _SEVERITY_RANK.get(sev, -1) > _SEVERITY_RANK.get(type_severity.get(t, "unknown"), -1):
                type_severity[t] = sev

    total = sum(type_counts.values())
    by_type = sorted(
        [
            {
                "type": t,
                "count": c,
                "percentage": c / total * 100 if total else 0,
                "severity": type_severity.get(t, "unknown"),
            }
            for t, c in type_counts.items()
        ],
        key=lambda x: (-_SEVERITY_RANK.get(x["severity"], -1), -x["count"]),
    )
    by_severity = sorted(
        [{"severity": s, "count": c, "percentage": c / total * 100 if total else 0}
         for s, c in severity_counts.items()],
        key=lambda x: -_SEVERITY_RANK.get(x["severity"], -1),
    )
    by_agent = _sorted_counts(agent_counts)[:5]

    return {
        "total": total,
        "session_count": len(entries),
        "by_type": by_type,
        "by_severity": by_severity,
        "by_agent": by_agent,
    }


def _calculate_runtime_skill_summary(skill_snapshots: list, run_snapshots: list) -> dict:
    explicit = [e for e in skill_snapshots if e and e.get("agent")]
    run_defaults = [
        {
            "timestamp": e.get("timestamp", ""),
            "session_id": e.get("session_id", ""),
            "agent": e.get("agent"),
            "model": (e.get("default_skills_snapshot") or {}).get("model", ""),
            "tools": (e.get("default_skills_snapshot") or {}).get("tools", []),
            "skills": (e.get("default_skills_snapshot") or {}).get("skills", []),
            "skills_count": (e.get("default_skills_snapshot") or {}).get("skills_count", 0),
            "source": "run-default",
        }
        for e in run_snapshots
        if e and e.get("agent") and e.get("default_skills_snapshot")
    ]

    latest_by_agent = {}
    for snap in run_defaults + explicit:
        agent = snap.get("agent") or "unknown"
        current = latest_by_agent.get(agent)
        if not current or str(snap.get("timestamp", "")) >= str(current.get("timestamp", "")):
            latest_by_agent[agent] = {
                "agent": agent,
                "timestamp": snap.get("timestamp", ""),
                "model": snap.get("model", ""),
                "tools": snap.get("tools") if isinstance(snap.get("tools"), list) else [],
                "skills": snap.get("skills") if isinstance(snap.get("skills"), list) else [],
                "skills_count": snap.get("skills_count") if isinstance(snap.get("skills_count"), int) else len(snap.get("skills") or []),
                "source": snap.get("source", "explicit"),
            }

    profiles = sorted(latest_by_agent.values(), key=lambda x: x["agent"])
    all_skills = [s for p in profiles for s in p["skills"]]
    top_skills = _top_counts(all_skills, 6)

    return {
        "explicit_count": len(explicit),
        "run_default_count": len(run_defaults),
        "agent_count": len(profiles),
        "latest_profiles": profiles,
        "top_skills": top_skills,
    }


def _calculate_context_snapshot_summary(run_snapshots: list):
    with_ctx = [e for e in run_snapshots if e and e.get("context_snapshot")]
    if not with_ctx:
        return None

    primary_surfaces = []
    contract_sections = []
    writable_sections = []
    multi_surface_count = 0

    for e in with_ctx:
        snap = e["context_snapshot"]
        sr = snap.get("surface_routing") or {}
        if sr.get("primary_surface"):
            primary_surfaces.append(sr["primary_surface"])
        if sr.get("multi_surface"):
            multi_surface_count += 1
        contract_sections.extend(snap.get("contract_sections") or [])
        writable_sections.extend((snap.get("context_update_scope") or {}).get("writable_sections") or [])

    return {
        "total": len(with_ctx),
        "multi_surface_count": multi_surface_count,
        "primary_surfaces": _top_counts(primary_surfaces, 6),
        "contract_sections": _top_counts(contract_sections, 6),
        "writable_sections": _top_counts(writable_sections, 6),
    }


def _calculate_context_update_summary(run_snapshots: list):
    if not run_snapshots:
        return None

    updated = [e for e in run_snapshots if e.get("context_updated")]
    rejected = [e for e in run_snapshots if e.get("context_rejected_sections")]

    updated_sections = [s for e in updated for s in (e.get("context_sections_updated") or [])]
    rejected_sections = [s for e in run_snapshots for s in (e.get("context_rejected_sections") or [])]

    return {
        "total_runs": len(run_snapshots),
        "updated_runs": len(updated),
        "rejected_runs": len(rejected),
        "updated_sections": _top_counts(updated_sections, 6),
        "rejected_sections": _top_counts(rejected_sections, 6),
    }


# ---------------------------------------------------------------------------
# MetricsSnapshot -- unified data model (dashboard v2)
#
# Before this model, cmd_metrics() ran the 11 calculators TWICE -- once for
# --json, once for the console display -- and the two branches carried
# divergent key names for the same data (the JSON branch's "security_tiers"
# vs. the display branch's "tiers"). MetricsSnapshot is computed exactly
# once per invocation and fed to both json.dumps(snapshot.to_dict()) and
# render_console(snapshot), so there is one source of truth for both data
# and canonical key names. schema_version makes the JSON shape a contract a
# future web/API consumer can version against.
# ---------------------------------------------------------------------------

@dataclass
class MetricsSnapshot:
    schema_version: str
    generated_at: str
    workspace: Optional[str]
    audit_entry_count: int
    security_tiers: dict
    cmd_types: dict
    top_cmds: list
    agent_invocations: dict
    native_agent_activity: dict
    error_stats: dict
    agent_outcomes: Optional[dict]
    token_usage: Optional[dict]
    anomaly_summary: Optional[dict]
    runtime_skills: dict
    context_snapshots: Optional[dict]
    context_updates: Optional[dict]
    agent_filter: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def build(
        cls,
        *,
        workspace: Optional[str],
        audit_logs: list,
        workflow_metrics: list,
        run_snapshots: list,
        skill_snapshots: list,
        anomaly_entries: list,
        agent_filter: Optional[str] = None,
    ) -> "MetricsSnapshot":
        gaia_metrics, native_metrics = _split_native_agents(workflow_metrics)
        return cls(
            schema_version=SCHEMA_VERSION,
            generated_at=datetime.now(timezone.utc).isoformat(),
            workspace=workspace,
            audit_entry_count=len(audit_logs),
            security_tiers=_calculate_tier_usage(audit_logs),
            cmd_types=_calculate_command_type_breakdown(audit_logs),
            top_cmds=_calculate_top_commands(audit_logs),
            agent_invocations=_calculate_agent_invocations(gaia_metrics),
            native_agent_activity=_calculate_agent_invocations(native_metrics),
            error_stats=_calculate_error_rate(audit_logs),
            agent_outcomes=_calculate_agent_outcomes(gaia_metrics),
            token_usage=_calculate_token_usage(gaia_metrics),
            anomaly_summary=_calculate_anomaly_summary(anomaly_entries),
            runtime_skills=_calculate_runtime_skill_summary(skill_snapshots, run_snapshots),
            context_snapshots=_calculate_context_snapshot_summary(run_snapshots),
            context_updates=_calculate_context_update_summary(run_snapshots),
            agent_filter=agent_filter,
        )

    @classmethod
    def empty(cls, workspace: Optional[str] = None) -> "MetricsSnapshot":
        empty_inv = {"agents": [], "total": 0, "today_count": 0}
        return cls(
            schema_version=SCHEMA_VERSION,
            generated_at=datetime.now(timezone.utc).isoformat(),
            workspace=workspace,
            audit_entry_count=0,
            security_tiers={"total": 0, "distribution": [], "today_count": 0, "today_t3": 0, "peak_hour": None, "peak_count": 0},
            cmd_types={"total": 0, "breakdown": []},
            top_cmds=[],
            agent_invocations=dict(empty_inv),
            native_agent_activity=dict(empty_inv),
            error_stats={"total": 0, "errors": 0, "error_rate": 0, "limited_by_api": False},
            agent_outcomes=None,
            token_usage=None,
            anomaly_summary=None,
            runtime_skills={"explicit_count": 0, "run_default_count": 0, "agent_count": 0, "latest_profiles": [], "top_skills": []},
            context_snapshots=None,
            context_updates=None,
        )


# ---------------------------------------------------------------------------
# Render -- hand-rolled Unicode box-drawing (zero new dependencies)
# ---------------------------------------------------------------------------

_BOX_W = 74  # interior width, excluding the two border characters

_TIER_LABELS = {"T0": "read-only", "T1": "validation", "T2": "dry-run", "T3": "mutating"}
_SEVERITY_LABELS = {"critical": "CRIT", "error": "ERR ", "warning": "WARN", "info": "INFO", "unknown": "?   "}


def _box_top(title: str, right_label: str = "") -> str:
    left = f"─ {title} "
    right = f" {right_label} ─" if right_label else "─"
    fill = max(0, _BOX_W - len(left) - len(right))
    return "┌" + left + "─" * fill + right + "┐"


def _box_row(text: str = "") -> str:
    content = f" {text}" if text else ""
    if len(content) > _BOX_W:
        content = content[: _BOX_W - 1] + "…"
    return "│" + content.ljust(_BOX_W) + "│"


def _box_divider(label: str = "") -> str:
    left = f"── {label} " if label else ""
    fill = max(0, _BOX_W - len(left))
    return "├" + left + "─" * fill + "┤"


def _box_bottom() -> str:
    return "└" + "─" * _BOX_W + "┘"


def _print_box(title: str, rows: list, legend: list = None, right_label: str = "") -> None:
    print(_box_top(title, right_label))
    for r in rows:
        print(_box_row(r))
    if legend:
        print(_box_divider("legend"))
        for l in legend:
            print(_box_row(l))
    print(_box_bottom())
    print()


def render_console(snapshot: MetricsSnapshot) -> None:
    """Render the dashboard from a single, already-computed MetricsSnapshot.

    Every section is a box: a title bar (with an at-a-glance right-aligned
    total), the data rows, and an embedded legend explaining what the metric
    IS and any non-obvious classification behind it -- not just the values.
    """
    print("\nGaia System Metrics  (dashboard v2)")
    ws_label = snapshot.workspace or "unfiltered (all workspaces)"
    print(f"Generated {snapshot.generated_at}  |  workspace: {ws_label}")
    print()

    # Security Tier Usage
    tiers = snapshot.security_tiers
    rows = []
    if tiers["total"] == 0:
        rows.append("no tier data")
    else:
        for item in tiers["distribution"]:
            tier = item["tier"]
            bar = _make_bar(item["percentage"], 24)
            label = _TIER_LABELS.get(tier, tier)
            warn = " ⚠ " if tier == "T3" else "   "
            rows.append(f"{tier:<3}{warn}{label:<11}{item['count']:>4}  {bar}  {item['percentage']:>5.1f}%")
    legend = [
        "T0 read-only / T1 local validation / T2 dry-run — none require approval",
        "T3 = mutates state — REQUIRES approval before it runs",
        "Surprises: curl is T0 unless -X POST/--data; a bare `python3 script.py`",
        "defaults to T3 when the script body can't be resolved (conservative)",
        "Window: last ~30d (audit-log retention), not filtered by workspace",
    ]
    _print_box("SECURITY TIER USAGE", rows, legend, right_label=f"{tiers['total']} ops")

    # Command Type Breakdown
    ct = snapshot.cmd_types
    rows = []
    if not ct["breakdown"]:
        rows.append("no command data")
    else:
        for item in ct["breakdown"]:
            bar = _make_bar(item["percentage"], 20)
            rows.append(f"{item['type']:<12}{item['count']:>4}  {bar}  {item['percentage']:>5.1f}%")
    legend = ["Classified from Bash tool_name entries in audit-*.jsonl",
              "Window: last ~30d (audit-log retention), not filtered by workspace"]
    _print_box("COMMAND TYPE BREAKDOWN", rows, legend, right_label=f"{snapshot.audit_entry_count} entries")

    # Top Commands
    rows = []
    if not snapshot.top_cmds:
        rows.append("no command data")
    else:
        for item in snapshot.top_cmds:
            warn = " ⚠" if item["t3count"] > 0 else "  "
            rows.append(f"{item['label']:<30}{item['count']:>4}  {item['tier']:<4}{warn}")
    legend = ["⚠ marks a label with at least one T3 (mutating) invocation",
              "Window: last ~30d (audit-log retention), not filtered by workspace"]
    _print_box("TOP COMMANDS", rows, legend)

    # Agent Invocations (Gaia specialists only)
    inv = snapshot.agent_invocations
    rows = []
    if not inv["agents"]:
        rows.append("no invocation data")
    else:
        for item in inv["agents"]:
            bar = _make_bar(item["percentage"], 14)
            rows.append(
                f"{item['name']:<22}{item['count']:>3}  {bar}  "
                f"avg {_format_chars(item['avg_output']):>6} chars  {item['success_rate']:>3.0f}% ok"
            )
        rows.append("")
        rows.append("tip: gaia metrics --agent <name>  for detail view")
    legend = [
        "Gaia domain specialists only -- harness-native agents (Explore, Plan,",
        "claude-code-guide, general-purpose) are in Native Agent Activity below",
        "Header splits today vs all-time; the breakdown rows above are all-time",
    ]
    _print_box(
        "AGENT INVOCATIONS",
        rows,
        legend,
        right_label=f"{inv['today_count']} invocations today · {inv['total']} all-time",
    )

    # Native Agent Activity (segregated -- P1 fix: Explore et al. are harness
    # noise, not Gaia specialist signal)
    native = snapshot.native_agent_activity
    if native["total"] > 0:
        rows = []
        for item in native["agents"]:
            bar = _make_bar(item["percentage"], 14)
            rows.append(f"{item['name']:<22}{item['count']:>3}  {bar}  {item['percentage']:>5.1f}%")
        legend = [
            "Harness-native utility agents, NOT Gaia domain specialists --",
            "excluded from Agent Invocations/Outcomes/Token Usage above so those",
            "reads measure Gaia specialists, not harness plumbing (e.g. Explore)",
        ]
        _print_box(
            "NATIVE AGENT ACTIVITY (not Gaia specialists)",
            rows,
            legend,
            right_label=f"{native['today_count']} today · {native['total']} all-time",
        )

    # Agent Outcomes
    if snapshot.agent_outcomes:
        ao = snapshot.agent_outcomes
        rows = []
        for item in ao["distribution"]:
            bar = _make_bar(item["percentage"], 20)
            rows.append(f"{item['status']:<16}{item['count']:>3}  {bar}  {item['percentage']:>5.1f}%")
        _print_box(
            "AGENT OUTCOMES",
            rows,
            ["Gaia specialists only -- same segregation as Agent Invocations above"],
            right_label=f"{ao['total']} invocations with status",
        )

    # Token Usage
    if snapshot.token_usage:
        tu = snapshot.token_usage
        rows = []
        for item in tu["agents"]:
            rows.append(
                f"{item['name']:<22}{item['count']:>3} inv  total {_format_tokens(item['total']):>6}  "
                f"avg {_format_tokens(item['avg']):>6}  ({item['source']})"
            )
        legend = [
            f"real = transcript-parsed usage.output_tokens ({tu['real_count']} entries); "
            f"approx = chars/4 ({tu['approx_count']} entries)",
        ]
        if tu["input_tokens"] is not None:
            legend.append(
                f"input {_format_tokens(tu['input_tokens'])}  "
                f"cache-write {_format_tokens(tu['cache_creation_tokens'])}  "
                f"cache-read {_format_tokens(tu['cache_read_tokens'])}"
            )
        _print_box("TOKEN USAGE", rows, legend, right_label=f"~{_format_tokens(tu['grand_total'])} total")

    # Runtime Skill Snapshots
    rs = snapshot.runtime_skills
    if rs["agent_count"] > 0:
        rows = []
        for profile in rs["latest_profiles"][:6]:
            model = profile.get("model") or "default"
            rows.append(
                f"{profile['agent']:<22}model {model:<8}skills {profile['skills_count']:>2}  "
                f"tools {len(profile['tools']):>2}  {_format_skills(profile['skills'], 3)}"
            )
        if len(rs["latest_profiles"]) > 6:
            rows.append(f"... {len(rs['latest_profiles']) - 6} more agents with captured snapshots")
        rows.append(f"Common skills: {_format_count_summary(rs['top_skills'])}")
        legend = [
            "Runtime Skill Snapshot = which model/tools/skills the harness actually",
            "loaded for that agent's latest dispatch -- not its .md declaration",
        ]
        _print_box("RUNTIME SKILL SNAPSHOTS", rows, legend, right_label=f"{rs['agent_count']} agents")

    # Context Snapshot Summary
    if snapshot.context_snapshots:
        cs = snapshot.context_snapshots
        rows = [
            f"Primary surfaces:  {_format_count_summary(cs['primary_surfaces'])}",
            f"Multi-surface:     {cs['multi_surface_count']}/{cs['total']} invocations",
            f"Contract sections: {_format_count_summary(cs['contract_sections'])}",
        ]
        if cs["writable_sections"]:
            rows.append(f"Writable scope:    {_format_count_summary(cs['writable_sections'])}")
        legend = [
            "Context Snapshot Summary = which surface/contract sections the",
            "orchestrator injected into agents' project context per invocation",
        ]
        _print_box("CONTEXT SNAPSHOT SUMMARY", rows, legend, right_label=f"{cs['total']} invocations")

    # Context Updates
    if snapshot.context_updates:
        cu = snapshot.context_updates
        rows = [
            f"Updated sections:  {_format_count_summary(cu['updated_sections'])}",
            f"Rejected writes:   {cu['rejected_runs']} invocations",
        ]
        if cu["rejected_sections"]:
            rows.append(f"Rejected sections: {_format_count_summary(cu['rejected_sections'])}")
        _print_box("CONTEXT UPDATES", rows, right_label=f"{cu['updated_runs']}/{cu['total_runs']} updated")

    # Anomaly Summary
    if snapshot.anomaly_summary and snapshot.anomaly_summary["total"] > 0:
        a = snapshot.anomaly_summary
        rows = []
        for item in a["by_type"]:
            sev = _SEVERITY_LABELS.get(item.get("severity", "unknown"), "?   ")
            bar = _make_bar(item["percentage"], 16)
            rows.append(f"[{sev}] {item['type']:<26}{item['count']:>3}  {bar}  {item['percentage']:>5.1f}%")
        if a["by_agent"]:
            rows.append(f"Agents: {_format_count_summary(a['by_agent'])}")
        legend = [
            "CRIT/ERR need attention now; WARN/INFO are informational. Sorted by",
            "severity first, count second -- a high-volume WARN (e.g.",
            "pipe_retroactive) can never bury a rarer CRIT/ERR entry below it",
        ]
        _print_box(
            "ANOMALY SUMMARY (last 30 days)",
            rows,
            legend,
            right_label=f"{a['total']} across {a['session_count']} invocations",
        )

    # Activity Today
    rows = [
        f"Total calls:   {tiers['today_count']}",
        f"T3 operations: {tiers['today_t3']}" + ("  ⚠" if tiers["today_t3"] > 0 else ""),
    ]
    if tiers["peak_hour"] is not None:
        rows.append(f"Peak hour:     {tiers['peak_hour']}:00-{tiers['peak_hour']}:59 UTC  ({tiers['peak_count']} calls)")
    else:
        rows.append("Peak hour:     no data for today")

    err = snapshot.error_stats
    if err["limited_by_api"]:
        rows.append("Error rate:    n/a (hook API limitation -- exit_code always 0)")
    elif err["total"] == 0:
        rows.append("Error rate:    no exit_code data")
    else:
        rows.append(f"Error rate:    {err['errors']}/{err['total']} ({err['error_rate']:.1f}%)")
    legend = [
        "'Today' = UTC calendar day. Audit timestamps are UTC (fixed from a",
        "prior local-time-vs-UTC mismatch that could misplace entries near",
        "midnight); this section was the one affected by that bug",
    ]
    _print_box("ACTIVITY TODAY", rows, legend)

    print(
        f"schema_version={snapshot.schema_version}  |  "
        "source: ~/.gaia/gaia.db (episodes, episode_anomalies)  |  "
        ".claude/logs/audit-*.jsonl\n"
    )


def _display_agent_detail(root: Path, agent_name: str, data: dict):
    SEP = "=" * 52
    wm = data["workflow_metrics"]
    audit_logs = data["audit_logs"]
    run_snapshots = data["run_snapshots"]
    skill_snapshots = data["skill_snapshots"]
    anomaly_entries = data["anomaly_entries"]

    print(f"\nAgent: {agent_name}")
    print(SEP)

    # Profile
    print("\nProfile")
    agent_def = _read_agent_definition(root, agent_name)
    if not agent_def:
        print("  Agent definition not found in .claude/agents/")
    else:
        if agent_def.get("description"):
            print(f"  Description: {agent_def['description']}")
        if agent_def.get("skills"):
            skills_str = ", ".join(agent_def["skills"])
            if len(skills_str) <= 60:
                print(f"  Skills:      {skills_str}")
            else:
                # Wrap skills at ~60 chars
                chunks = []
                current = []
                length = 0
                for s in agent_def["skills"]:
                    if length + len(s) + 2 > 56 and current:
                        chunks.append(", ".join(current))
                        current = [s]
                        length = len(s)
                    else:
                        current.append(s)
                        length += len(s) + 2
                if current:
                    chunks.append(", ".join(current))
                print(f"  Skills:      {chunks[0]}")
                for chunk in chunks[1:]:
                    print(f"               {chunk}")

    # Runtime Snapshot (latest profile for this agent)
    print("\nRuntime Snapshot")
    # Find latest snapshot
    explicit = [e for e in skill_snapshots if e.get("agent") == agent_name]
    run_defaults = [e for e in run_snapshots if e.get("agent") == agent_name and e.get("default_skills_snapshot")]
    all_snaps = sorted(
        [{"ts": e.get("timestamp", ""), "source": "explicit", **e} for e in explicit]
        + [{"ts": e.get("timestamp", ""), "source": "run-default", "model": (e.get("default_skills_snapshot") or {}).get("model", ""), "tools": (e.get("default_skills_snapshot") or {}).get("tools", []), "skills": (e.get("default_skills_snapshot") or {}).get("skills", []), "skills_count": (e.get("default_skills_snapshot") or {}).get("skills_count", 0)} for e in run_defaults],
        key=lambda x: x["ts"],
        reverse=True,
    )
    if not all_snaps:
        print("  no runtime skill snapshot data")
    else:
        latest = all_snaps[0]
        print(f"  Latest model:    {latest.get('model') or 'default'}")
        src_label = "agent-skills.jsonl" if latest.get("source") == "explicit" else "run-snapshots default profile"
        print(f"  Snapshot source: {src_label}")
        print(f"  Snapshots seen:  {len(explicit)} explicit, {len(run_defaults)} run defaults")
        tools = latest.get("tools") or []
        print(f"  Tools:           {', '.join(tools) if tools else 'none'}")
        skills = latest.get("skills") or []
        print(f"  Skills:          {_format_skills(skills, 6)}")

    # Invocation History
    agent_sessions = sorted(
        [r for r in wm if r.get("agent") == agent_name],
        key=lambda r: r.get("timestamp") or "",
    )
    success_count = sum(1 for r in agent_sessions if r.get("exit_code") == 0)
    total_output = sum(r.get("output_length") or 0 for r in agent_sessions)
    avg_output = round(total_output / len(agent_sessions)) if agent_sessions else 0

    print("\nInvocation History  (last 7 days)")
    if not agent_sessions:
        print("  no invocations found in gaia.db episodes")
    else:
        print(
            f"  Total: {len(agent_sessions)} invocations  |  "
            f"Success: {success_count}/{len(agent_sessions)}  |  "
            f"Avg output: {_format_chars(avg_output)} chars"
        )
        print()
        for session in agent_sessions:
            dt = (session.get("timestamp") or "")[:16].replace("T", " ")
            ok = "ok" if session.get("exit_code") == 0 else "!!"
            chars = f"{session.get('output_length') or 0:,}"
            task_short = (session.get("task_id") or "n/a")[:8]
            print(f"  {dt}  {ok}  {chars:>7} chars  task: {task_short}")

    # Context Snapshot Summary
    agent_run_snaps = [e for e in run_snapshots if e.get("agent") == agent_name]
    agent_ctx = _calculate_context_snapshot_summary(agent_run_snaps)
    agent_ctx_updates = _calculate_context_update_summary(agent_run_snaps)

    print("\nContext Snapshot Summary")
    if not agent_ctx:
        print("  no context snapshot data")
    else:
        print(f"  Sessions with context: {agent_ctx['total']}")
        print(f"  Primary surfaces:      {_format_count_summary(agent_ctx['primary_surfaces'])}")
        print(f"  Multi-surface:         {agent_ctx['multi_surface_count']}/{agent_ctx['total']}")
        print(f"  Contract sections:     {_format_count_summary(agent_ctx['contract_sections'])}")
        if agent_ctx["writable_sections"]:
            print(f"  Writable scope:        {_format_count_summary(agent_ctx['writable_sections'])}")

    # Context Updates + Anomalies
    agent_anomalies_entries = [e for e in anomaly_entries if (e.get("metrics") or {}).get("agent") == agent_name]
    agent_anomaly_type_counts = {}
    for e in agent_anomalies_entries:
        for anomaly in e.get("anomalies") or []:
            t = anomaly.get("type", "unknown")
            agent_anomaly_type_counts[t] = agent_anomaly_type_counts.get(t, 0) + 1
    agent_anomaly_total = sum(agent_anomaly_type_counts.values())
    agent_anomaly_by_type = _sorted_counts(agent_anomaly_type_counts)[:6]

    print("\nContext Updates + Anomalies")
    if not agent_ctx_updates and not agent_anomaly_total:
        print("  no context update or anomaly data")
    else:
        if agent_ctx_updates:
            print(f"  Context updated:   {agent_ctx_updates['updated_runs']}/{agent_ctx_updates['total_runs']} invocations")
            print(f"  Updated sections:  {_format_count_summary(agent_ctx_updates['updated_sections'])}")
            if agent_ctx_updates["rejected_sections"]:
                print(f"  Rejected sections: {_format_count_summary(agent_ctx_updates['rejected_sections'])}")
        if agent_anomaly_total:
            print(f"  Anomalies:         {agent_anomaly_total} across {len(agent_anomalies_entries)} invocations")
            print(f"  Types:             {_format_count_summary(agent_anomaly_by_type)}")

    # Top Commands (correlated from audit log -- approximate)
    print("\nTop Commands  (sampled from audit log, approximate time windows)")
    if not agent_sessions or not audit_logs:
        print("  no data to correlate")
    else:
        named_stops = sorted([r for r in wm if r.get("agent")], key=lambda r: r.get("timestamp") or "")
        tier_order = {"T3": 3, "T2": 2, "T1": 1, "T0": 0, "unknown": -1}
        label_map = {}

        for i, session in enumerate(agent_sessions):
            # Find this session's position in named_stops
            stop_idx = next(
                (j for j, r in enumerate(named_stops) if r.get("task_id") == session.get("task_id")),
                -1,
            )
            prev_stop = named_stops[stop_idx - 1] if stop_idx > 0 else None
            window_start = (prev_stop or {}).get("timestamp")
            window_end = session.get("timestamp")

            if not window_end:
                continue

            end_ts = _parse_ts(window_end)
            start_ts = _parse_ts(window_start) if window_start else end_ts - 600

            for e in audit_logs:
                if not e.get("command") or not e.get("timestamp"):
                    continue
                ts = _parse_ts(e["timestamp"])
                if start_ts <= ts <= end_ts:
                    label = _extract_command_label(e["command"])
                    tier = e.get("tier") or "unknown"
                    if label not in label_map:
                        label_map[label] = {"count": 0, "tier": tier, "t3count": 0}
                    label_map[label]["count"] += 1
                    if tier == "T3":
                        label_map[label]["t3count"] += 1
                    if tier_order.get(tier, -1) > tier_order.get(label_map[label]["tier"], -1):
                        label_map[label]["tier"] = tier

        top = sorted(
            [{"label": l, **v} for l, v in label_map.items()],
            key=lambda x: -x["count"],
        )[:10]

        if not top:
            print("  no overlapping commands found in audit window")
        else:
            for item in top:
                warn = "  (!)" if item["t3count"] > 0 else ""
                print(f"  {item['tier']:<3}  {item['label']:<28} {item['count']:>4}{warn}")
        print("\n  Note: command windows are approximated from SubagentStop timestamps")

    print("\n" + SEP + "\n")


def _parse_ts(ts_str: str) -> float:
    """Parse ISO timestamp to Unix seconds."""
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


# ---------------------------------------------------------------------------
# Plugin interface
# ---------------------------------------------------------------------------

def register(subparsers):
    """Register the 'metrics' subcommand."""
    p = subparsers.add_parser(
        "metrics",
        help="Show system metrics dashboard (tiers, commands, agents, anomalies)",
        description=(
            "Display Gaia system metrics dashboard.\n"
            "\n"
            "Data sources:\n"
            "  ~/.gaia/gaia.db  (episodes + episode_anomalies tables)\n"
            "  .claude/logs/audit-*.jsonl  (security tier events)\n"
        ),
    )
    p.add_argument(
        "--agent",
        metavar="NAME",
        default=None,
        help="Show detail view for a specific agent",
    )
    p.add_argument(
        "--workspace", default=None,
        help="Workspace identity override. Default: gaia.project.current().",
    )
    p.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output results as JSON",
    )
    return p


def cmd_metrics(args) -> int:
    """Execute the metrics subcommand."""
    root = _find_project_root()
    claude_dir = root / ".claude"
    agent_name = getattr(args, "agent", None)
    as_json = getattr(args, "json", False)
    workspace_override = getattr(args, "workspace", None)

    if not claude_dir.exists():
        if as_json:
            print(json.dumps({"error": "gaia not installed in this directory"}))
        else:
            print("\nGaia not installed in this directory")
            print("Run: gaia scan\n")
        return 1

    audit_logs = _read_audit_logs(root)
    workflow_metrics = _read_workflow_metrics(root, workspace_override)
    run_snapshots = _read_run_snapshots(root, workspace_override)
    skill_snapshots = _read_agent_skill_snapshots(root)
    anomaly_entries = _read_anomaly_entries(root, workspace_override)

    if not audit_logs and not workflow_metrics and not run_snapshots and not skill_snapshots and not anomaly_entries:
        snapshot = MetricsSnapshot.empty(workspace_override)
        if as_json:
            print(json.dumps(snapshot.to_dict(), indent=2))
        else:
            print("\nNo metrics data available yet")
            print("Metrics will be generated as you use the system\n")
        return 0

    # The --agent detail view (non-JSON) stays a single-pass path of its own:
    # it renders raw episode/audit rows for one agent rather than the
    # aggregate dashboard, so there is no double-computation to unify here.
    if agent_name and not as_json:
        data = {
            "workflow_metrics": workflow_metrics,
            "audit_logs": audit_logs,
            "run_snapshots": run_snapshots,
            "skill_snapshots": skill_snapshots,
            "anomaly_entries": anomaly_entries,
        }
        _display_agent_detail(root, agent_name, data)
        return 0

    # Build the unified snapshot ONCE -- both --json and the console render
    # read from this single computation (dashboard v2, dim #18).
    snapshot = MetricsSnapshot.build(
        workspace=workspace_override,
        audit_logs=audit_logs,
        workflow_metrics=workflow_metrics,
        run_snapshots=run_snapshots,
        skill_snapshots=skill_snapshots,
        anomaly_entries=anomaly_entries,
        agent_filter=agent_name,
    )

    if as_json:
        print(json.dumps(snapshot.to_dict(), indent=2))
    else:
        render_console(snapshot)

    return 0
