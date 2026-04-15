"""
gaia metrics -- mirror of gaia-metrics.js

Displays system metrics dashboard:
  - Security tier usage distribution
  - Command type breakdown
  - Top commands by frequency
  - Agent invocations
  - Agent outcomes
  - Token usage (approx)
  - Anomaly summary (last 30 days)
  - Activity today

With --agent NAME shows a detail view for that agent.

Data sources:
  .claude/logs/audit-*.jsonl
  .claude/project-context/episodic-memory/index.json
  .claude/project-context/workflow-episodic-memory/metrics.jsonl  (fallback)
  .claude/project-context/workflow-episodic-memory/anomalies.jsonl
  .claude/project-context/workflow-episodic-memory/run-snapshots.jsonl
  .claude/project-context/workflow-episodic-memory/agent-skills.jsonl

Flags:
  --agent NAME    Show detail view for a specific agent
  --json          Machine-readable output
"""

import fnmatch
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
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


def _read_workflow_metrics(root: Path) -> list:
    """Primary: episodic-memory/index.json; fallback: workflow metrics.jsonl."""
    index_path = root / ".claude" / "project-context" / "episodic-memory" / "index.json"
    if index_path.exists():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            episodes = [e for e in (data.get("episodes") or []) if e.get("agent")]
            if episodes:
                return episodes
        except (json.JSONDecodeError, OSError):
            pass

    metrics_path = root / ".claude" / "project-context" / "workflow-episodic-memory" / "metrics.jsonl"
    return [e for e in _read_jsonl(metrics_path) if e.get("agent")]


def _read_run_snapshots(root: Path) -> list:
    return _read_jsonl(
        root / ".claude" / "project-context" / "workflow-episodic-memory" / "run-snapshots.jsonl"
    )


def _read_agent_skill_snapshots(root: Path) -> list:
    return _read_jsonl(
        root / ".claude" / "project-context" / "workflow-episodic-memory" / "agent-skills.jsonl"
    )


def _read_anomaly_entries(root: Path) -> list:
    return _read_jsonl(
        root / ".claude" / "project-context" / "workflow-episodic-memory" / "anomalies.jsonl"
    )


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


def _make_bar(percentage: float, max_width: int = 14) -> str:
    filled = max(0, round((percentage / 100) * max_width))
    return "#" * filled


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
    with_tokens = [r for r in workflow_metrics if isinstance(r.get("output_tokens_approx"), (int, float))]
    if not with_tokens:
        return None

    agent_map = {}
    for e in with_tokens:
        name = e.get("agent") or "unknown"
        if name not in agent_map:
            agent_map[name] = {"total": 0, "count": 0}
        agent_map[name]["total"] += e["output_tokens_approx"]
        agent_map[name]["count"] += 1

    grand_total = sum(e["output_tokens_approx"] for e in with_tokens)
    agents = sorted(
        [
            {
                "name": n,
                "total": v["total"],
                "avg": round(v["total"] / v["count"]) if v["count"] else 0,
                "count": v["count"],
            }
            for n, v in agent_map.items()
        ],
        key=lambda x: -x["total"],
    )
    return {"agents": agents, "grand_total": grand_total, "entry_count": len(with_tokens)}


def _calculate_anomaly_summary(anomaly_entries: list):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    entries = [e for e in anomaly_entries if e and (e.get("timestamp") or "") >= cutoff]
    if not entries:
        return None

    type_counts = {}
    agent_counts = {}
    for e in entries:
        agent = (e.get("metrics") or {}).get("agent", "unknown")
        for anomaly in e.get("anomalies") or []:
            t = anomaly.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1
            agent_counts[agent] = agent_counts.get(agent, 0) + 1

    total = sum(type_counts.values())
    by_type = sorted(
        [{"type": t, "count": c, "percentage": c / total * 100 if total else 0}
         for t, c in type_counts.items()],
        key=lambda x: -x["count"],
    )
    by_agent = _sorted_counts(agent_counts)[:5]

    return {
        "total": total,
        "session_count": len(entries),
        "by_type": by_type,
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
# Display functions
# ---------------------------------------------------------------------------

def _display_metrics(data: dict):
    SEP = "=" * 52

    print("\nGaia-Ops System Metrics")
    print(SEP)

    tiers = data["tiers"]
    cmd_types = data["cmd_types"]
    top_cmds = data["top_cmds"]
    agent_inv = data["agent_invocations"]
    error_stats = data["error_stats"]
    audit_total = data["audit_total"]
    agent_outcomes = data["agent_outcomes"]
    token_usage = data["token_usage"]
    anomaly_summary = data["anomaly_summary"]
    runtime_skills = data["runtime_skills"]
    ctx_snapshots = data["context_snapshots"]
    ctx_updates = data["context_updates"]

    # Security Tier Usage
    print(f"\nSecurity Tier Usage  ({tiers['total']} operations)")
    tier_label = {"T0": "read-only", "T1": "validation", "T2": "simulation", "T3": "realization"}
    if tiers["total"] == 0:
        print("  no tier data")
    else:
        for item in tiers["distribution"]:
            tier = item["tier"]
            count = item["count"]
            pct = item["percentage"]
            bar = _make_bar(pct, 14)
            label = tier_label.get(tier, tier)
            suffix = "  realization (!)" if tier == "T3" else f"  {label}"
            print(f"  {tier:<4} {count:>4}  {bar:<14}  {pct:>5.1f}%{suffix}")

    # Command Type Breakdown
    print(f"\nCommand Type Breakdown  (derived from {audit_total} audit entries)")
    if not cmd_types["breakdown"]:
        print("  no command data")
    else:
        for item in cmd_types["breakdown"]:
            bar = _make_bar(item["percentage"], 10)
            print(f"  {item['type']:<12} {item['count']:>4}  {bar:<10}  {item['percentage']:>5.1f}%")

    # Top Commands
    print("\nTop Commands")
    if not top_cmds:
        print("  no command data")
    else:
        for item in top_cmds:
            warn = "  (!)" if item["t3count"] > 0 else ""
            print(f"  {item['label']:<30} {item['count']:>4}  {item['tier']}{warn}")

    # Agent Invocations
    if agent_inv["today_count"] > 0:
        agent_header = f"({agent_inv['today_count']} sessions today)"
    else:
        agent_header = f"({agent_inv['total']} total)"
    print(f"\nAgent Invocations  {agent_header}")
    if not agent_inv["agents"]:
        print("  no invocation data")
    else:
        for item in agent_inv["agents"]:
            bar = _make_bar(item["percentage"], 8)
            avg = f"avg {_format_chars(item['avg_output']):>6} chars"
            ok_pct = item["success_rate"]
            ok_str = f"{ok_pct:.0f}% ok"
            print(f"  {item['name']:<24} {item['count']:>3}  {bar:<8}  {avg}  {ok_str}")
        print("  tip: gaia metrics --agent <name>  for detail view")

    # Agent Outcomes
    if agent_outcomes:
        print(f"\nAgent Outcomes  ({agent_outcomes['total']} sessions with status)")
        for item in agent_outcomes["distribution"]:
            bar = _make_bar(item["percentage"], 10)
            print(f"  {item['status']:<16} {item['count']:>3}  {bar:<10}  {item['percentage']:>5.1f}%")

    # Token Usage
    if token_usage:
        print(f"\nToken Usage (approx)  total: ~{_format_tokens(token_usage['grand_total'])}")
        for item in token_usage["agents"]:
            print(
                f"  {item['name']:<24} {item['count']:>3} sessions"
                f"  total {_format_tokens(item['total']):>6}"
                f"  avg {_format_tokens(item['avg']):>6}"
            )

    # Runtime Skill Snapshots
    if runtime_skills and runtime_skills["agent_count"] > 0:
        rs = runtime_skills
        print(
            f"\nRuntime Skill Snapshots  ({rs['agent_count']} agents, "
            f"{rs['explicit_count']} explicit, {rs['run_default_count']} run defaults)"
        )
        for profile in rs["latest_profiles"][:6]:
            model = profile.get("model") or "default"
            print(
                f"  {profile['agent']:<24} model {model:<8} "
                f"skills {profile['skills_count']:>2}  tools {len(profile['tools']):>2}  "
                f"{_format_skills(profile['skills'], 3)}"
            )
        if len(rs["latest_profiles"]) > 6:
            print(f"  ... {len(rs['latest_profiles']) - 6} more agents with captured snapshots")
        print(f"  Common skills: {_format_count_summary(rs['top_skills'])}")

    # Context Snapshot Summary
    if ctx_snapshots:
        print(f"\nContext Snapshot Summary  ({ctx_snapshots['total']} sessions)")
        print(f"  Primary surfaces: {_format_count_summary(ctx_snapshots['primary_surfaces'])}")
        print(f"  Multi-surface:    {ctx_snapshots['multi_surface_count']}/{ctx_snapshots['total']} sessions")
        print(f"  Contract sections: {_format_count_summary(ctx_snapshots['contract_sections'])}")
        if ctx_snapshots["writable_sections"]:
            print(f"  Writable scope:   {_format_count_summary(ctx_snapshots['writable_sections'])}")

    # Context Updates
    if ctx_updates:
        print(f"\nContext Updates  ({ctx_updates['updated_runs']}/{ctx_updates['total_runs']} sessions updated)")
        print(f"  Rejected writes:  {ctx_updates['rejected_runs']} sessions")
        print(f"  Updated sections: {_format_count_summary(ctx_updates['updated_sections'])}")
        if ctx_updates["rejected_sections"]:
            print(f"  Rejected sections: {_format_count_summary(ctx_updates['rejected_sections'])}")

    # Anomaly Summary
    if anomaly_summary and anomaly_summary["total"] > 0:
        a = anomaly_summary
        print(f"\nAnomaly Summary (last 30 days)  {a['total']} anomalies across {a['session_count']} sessions")
        for item in a["by_type"]:
            bar = _make_bar(item["percentage"], 10)
            print(f"  {item['type']:<28} {item['count']:>3}  {bar:<10}  {item['percentage']:>5.1f}%")
        if a["by_agent"]:
            print(f"  Agents: {_format_count_summary(a['by_agent'])}")

    # Activity Today
    print("\nActivity Today")
    print(f"  Total calls:   {tiers['today_count']}")
    print(f"  T3 operations: {tiers['today_t3']}" + (" (!)" if tiers["today_t3"] > 0 else ""))
    if tiers["peak_hour"] is not None:
        print(f"  Peak hour:     {tiers['peak_hour']}:00-{tiers['peak_hour']}:59  ({tiers['peak_count']} calls)")
    else:
        print("  Peak hour:     no data for today")

    if error_stats["limited_by_api"]:
        print("  Error rate:    n/a  (hook API limitation -- exit_code always 0)")
    elif error_stats["total"] == 0:
        print("  Error rate:    no exit_code data")
    else:
        print(f"  Error rate:    {error_stats['errors']}/{error_stats['total']} ({error_stats['error_rate']:.1f}%)")

    print("\n" + SEP)
    print("Source: .claude/logs/audit-*.jsonl  |  episodic-memory/index.json  |  workflow-episodic-memory/*.jsonl\n")


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
        print("  no invocations found in episodic-memory/index.json")
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
            print(f"  Context updated:   {agent_ctx_updates['updated_runs']}/{agent_ctx_updates['total_runs']} sessions")
            print(f"  Updated sections:  {_format_count_summary(agent_ctx_updates['updated_sections'])}")
            if agent_ctx_updates["rejected_sections"]:
                print(f"  Rejected sections: {_format_count_summary(agent_ctx_updates['rejected_sections'])}")
        if agent_anomaly_total:
            print(f"  Anomalies:         {agent_anomaly_total} across {len(agent_anomalies_entries)} sessions")
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
            "Display Gaia-Ops system metrics dashboard.\n"
            "\n"
            "Data sources:\n"
            "  .claude/logs/audit-*.jsonl\n"
            "  .claude/project-context/episodic-memory/index.json\n"
            "  .claude/project-context/workflow-episodic-memory/\n"
        ),
    )
    p.add_argument(
        "--agent",
        metavar="NAME",
        default=None,
        help="Show detail view for a specific agent",
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

    if not claude_dir.exists():
        if as_json:
            print(json.dumps({"error": "gaia-ops not installed in this directory"}))
        else:
            print("\nGaia-ops not installed in this directory")
            print("Run: npx gaia-scan\n")
        return 1

    audit_logs = _read_audit_logs(root)
    workflow_metrics = _read_workflow_metrics(root)
    run_snapshots = _read_run_snapshots(root)
    skill_snapshots = _read_agent_skill_snapshots(root)
    anomaly_entries = _read_anomaly_entries(root)

    if not audit_logs and not workflow_metrics and not run_snapshots and not skill_snapshots and not anomaly_entries:
        if as_json:
            empty_output = {
                "security_tiers": {"total": 0, "distribution": [], "today_count": 0, "today_t3": 0, "peak_hour": None, "peak_count": 0},
                "cmd_types": {"total": 0, "breakdown": []},
                "top_cmds": [],
                "agent_invocations": {"agents": [], "total": 0, "today_count": 0},
                "error_stats": {"total": 0, "errors": 0, "error_rate": 0, "limited_by_api": False},
                "agent_outcomes": None,
                "token_usage": None,
                "anomaly_summary": None,
                "runtime_skills": {"explicit_count": 0, "run_default_count": 0, "agent_count": 0, "latest_profiles": [], "top_skills": []},
                "context_snapshots": None,
                "context_updates": None,
            }
            print(json.dumps(empty_output))
        else:
            print("\nNo metrics data available yet")
            print("Metrics will be generated as you use the system\n")
        return 0

    if as_json:
        # Compute all metrics and return as JSON
        tiers = _calculate_tier_usage(audit_logs)
        cmd_types = _calculate_command_type_breakdown(audit_logs)
        top_cmds = _calculate_top_commands(audit_logs)
        agent_inv = _calculate_agent_invocations(workflow_metrics)
        error_stats = _calculate_error_rate(audit_logs)
        agent_outcomes = _calculate_agent_outcomes(workflow_metrics)
        token_usage = _calculate_token_usage(workflow_metrics)
        anomaly_summary = _calculate_anomaly_summary(anomaly_entries)
        runtime_skills = _calculate_runtime_skill_summary(skill_snapshots, run_snapshots)
        ctx_snapshots = _calculate_context_snapshot_summary(run_snapshots)
        ctx_updates = _calculate_context_update_summary(run_snapshots)

        output = {
            "security_tiers": tiers,
            "cmd_types": cmd_types,
            "top_cmds": top_cmds,
            "agent_invocations": agent_inv,
            "error_stats": error_stats,
            "agent_outcomes": agent_outcomes,
            "token_usage": token_usage,
            "anomaly_summary": anomaly_summary,
            "runtime_skills": runtime_skills,
            "context_snapshots": ctx_snapshots,
            "context_updates": ctx_updates,
        }
        if agent_name:
            output["agent_filter"] = agent_name
        print(json.dumps(output, indent=2))
        return 0

    data = {
        "workflow_metrics": workflow_metrics,
        "audit_logs": audit_logs,
        "run_snapshots": run_snapshots,
        "skill_snapshots": skill_snapshots,
        "anomaly_entries": anomaly_entries,
    }

    if agent_name:
        _display_agent_detail(root, agent_name, data)
    else:
        tiers = _calculate_tier_usage(audit_logs)
        cmd_types = _calculate_command_type_breakdown(audit_logs)
        top_cmds = _calculate_top_commands(audit_logs)
        agent_inv = _calculate_agent_invocations(workflow_metrics)
        error_stats = _calculate_error_rate(audit_logs)
        agent_outcomes = _calculate_agent_outcomes(workflow_metrics)
        token_usage = _calculate_token_usage(workflow_metrics)
        anomaly_summary = _calculate_anomaly_summary(anomaly_entries)
        runtime_skills = _calculate_runtime_skill_summary(skill_snapshots, run_snapshots)
        ctx_snapshots = _calculate_context_snapshot_summary(run_snapshots)
        ctx_updates = _calculate_context_update_summary(run_snapshots)

        _display_metrics({
            "tiers": tiers,
            "cmd_types": cmd_types,
            "top_cmds": top_cmds,
            "agent_invocations": agent_inv,
            "error_stats": error_stats,
            "audit_total": len(audit_logs),
            "agent_outcomes": agent_outcomes,
            "token_usage": token_usage,
            "anomaly_summary": anomaly_summary,
            "runtime_skills": runtime_skills,
            "context_snapshots": ctx_snapshots,
            "context_updates": ctx_updates,
        })

    return 0
