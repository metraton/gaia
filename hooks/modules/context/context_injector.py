"""Core context injection subsystem for project agents.

Handles:
- build_project_context: builds context string for additionalContext injection
- check_recent_critical_anomalies: surfaces critical anomalies from JSONL log
- consume_anomaly_flag: reads and deletes anomaly signal flags
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from ..core.paths import get_plugin_data_dir
from ..session.session_manager import get_or_create_session_id
from .anchor_tracker import extract_anchors, save_anchors
from .contracts_loader import build_context_update_reminder

logger = logging.getLogger(__name__)


def _ensure_context_provider_importable(hooks_dir: Path) -> None:
    """Make tools.context.context_provider importable from in-process callers.

    The hooks live under hooks/; the gaia tools package sits as a sibling
    at the same level. We add the package root (hooks_dir.parent) to sys.path
    so ``from tools.context.context_provider import ...`` resolves regardless
    of cwd.
    """
    pkg_root = str(hooks_dir.parent)
    if pkg_root not in sys.path:
        sys.path.insert(0, pkg_root)

# Inline explanations for every field the investigation brief can contain.
# Appended as YAML comments when the brief is rendered via _dict_to_yaml_annotated().
BRIEF_FIELD_DESCRIPTIONS: dict[str, str] = {
    "goal": "the task as stated by the orchestrator",
    "agent_role": "your relationship to this task: primary (owns it), cross_check (verify peer's work), adjacent (related surface), reconnaissance (explore)",
    "primary_surface": "the surface that owns the fix or decision",
    "active_surfaces": "all surfaces involved; drives cross_check_required",
    "adjacent_surfaces": "surfaces that may be impacted but do not own the fix",
    "dispatch_mode": "single_surface = one agent owns it; multi_surface = multiple agents coordinate",
    "cross_check_required": "true when >1 active surface or this agent is not primary; fill CROSS_LAYER_IMPACTS",
    "patterns_required": "true means load the domain skill for this surface before executing",
    "contract_sections_to_anchor": "project-context sections to cite as evidence in your contract",
    "required_checks": "mandatory verifications before declaring COMPLETE",
    "evidence_required": "fields that must appear in your evidence_report agent_contract_handoff block",
    "consolidation_required": "true when cross_check_required; fill consolidation_report with ownership_assessment and conflicts",
    "consolidation_fields": "fields required inside consolidation_report when consolidation_required is true",
    "recommended_peer_agents": "agents to delegate to or coordinate with for non-primary surfaces",
    "stop_conditions": "conditions under which further investigation adds no value",
}


def _dict_to_yaml_annotated(d: dict, descriptions: dict[str, str], indent: int = 0) -> str:
    """Render a dict as YAML-like key-value pairs with inline description comments.

    Works like _dict_to_yaml but appends ``# <description>`` after each
    top-level scalar or list header when a matching entry exists in
    *descriptions*.  Nested structures and list items are rendered without
    annotations to keep the output readable.
    """
    lines = []
    prefix = "  " * indent
    for key, value in d.items():
        desc = descriptions.get(key, "") if indent == 0 else ""
        comment = f"  # {desc}" if desc else ""
        if value is None or value == "" or value == [] or value == {}:
            continue
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:{comment}")
            lines.append(_dict_to_yaml(value, indent + 1))
        elif isinstance(value, list):
            lines.append(f"{prefix}{key}:{comment}")
            for item in value:
                if isinstance(item, dict):
                    item_lines = _dict_to_yaml(item, indent + 1).splitlines()
                    if item_lines:
                        first = item_lines[0].lstrip()
                        lines.append(f"{prefix}  - {first}")
                        for il in item_lines[1:]:
                            lines.append(f"{prefix}  {il}")
                else:
                    lines.append(f"{prefix}  - {item}")
        else:
            lines.append(f"{prefix}{key}: {value}{comment}")
    return "\n".join(lines)


def _dict_to_yaml(d, indent: int = 0) -> str:
    """Convert a dict to indented YAML-like key-value pairs (no external dependency).

    - Nested dicts are indented by 2 spaces per level.
    - Lists are rendered as markdown bullet lists (- item).
    - Scalar values are rendered inline.
    - None/empty values are skipped.
    """
    lines = []
    prefix = "  " * indent
    for key, value in d.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.append(_dict_to_yaml(value, indent + 1))
        elif isinstance(value, list):
            lines.append(f"{prefix}{key}:")
            for item in value:
                if isinstance(item, dict):
                    # Render first key inline, rest indented
                    item_lines = _dict_to_yaml(item, indent + 1).splitlines()
                    if item_lines:
                        first = item_lines[0].lstrip()
                        lines.append(f"{prefix}  - {first}")
                        for il in item_lines[1:]:
                            lines.append(f"{prefix}  {il}")
                else:
                    lines.append(f"{prefix}  - {item}")
        else:
            lines.append(f"{prefix}{key}: {value}")
    return "\n".join(lines)


def _prune_empty_values(data: dict) -> dict:
    """Drop keys with empty telemetry values while preserving False/0."""
    pruned = {}
    for key, value in data.items():
        if value in (None, "", [], {}):
            continue
        pruned[key] = value
    return pruned


def build_context_telemetry_snapshot(context_payload: dict) -> dict:
    """Build a compact telemetry snapshot from injected context payload data."""
    if not isinstance(context_payload, dict) or not context_payload:
        return {}

    project_knowledge = context_payload.get("project_knowledge") or {}
    metadata = context_payload.get("metadata") or {}
    surface_routing = context_payload.get("surface_routing") or {}
    # T2.1a: renamed from investigation_brief -> agent_contract_handoff
    # Read from new key first; fall back to legacy key during dual-mode window
    _brief_raw = (
        context_payload.get("agent_contract_handoff")
        or context_payload.get("investigation_brief")
        or {}
    )
    write_permissions = context_payload.get("write_permissions") or {}

    contract_sections = sorted(project_knowledge.keys())
    readable_sections = sorted(write_permissions.get("readable_sections") or [])
    writable_sections = sorted(write_permissions.get("writable_sections") or [])

    return _prune_empty_values({
        "contract_sections": contract_sections,
        "contract_sections_count": len(contract_sections),
        "metadata": _prune_empty_values({
            "cloud_provider": metadata.get("cloud_provider"),
            "contract_version": metadata.get("contract_version"),
            "historical_episodes_count": metadata.get("historical_episodes_count"),
            "surface_routing_version": metadata.get("surface_routing_version"),
            "active_surfaces_count": metadata.get("active_surfaces_count"),
            "surface_routing_confidence": metadata.get("surface_routing_confidence"),
        }),
        "surface_routing": _prune_empty_values({
            "primary_surface": surface_routing.get("primary_surface"),
            "active_surfaces": sorted(surface_routing.get("active_surfaces") or []),
            "dispatch_mode": surface_routing.get("dispatch_mode"),
            "multi_surface": surface_routing.get("multi_surface"),
            "recommended_agents": sorted(surface_routing.get("recommended_agents") or []),
        }),
        "agent_contract_handoff": _prune_empty_values({
            "agent_role": _brief_raw.get("agent_role"),
            "primary_surface": _brief_raw.get("primary_surface"),
            "adjacent_surfaces": sorted(_brief_raw.get("adjacent_surfaces") or []),
            "cross_check_required": _brief_raw.get("cross_check_required"),
            "consolidation_required": _brief_raw.get("consolidation_required"),
            "required_checks_count": len(_brief_raw.get("required_checks") or []),
            "evidence_required": sorted(_brief_raw.get("evidence_required") or []),
        }),
        "context_update_scope": _prune_empty_values({
            "readable_sections": readable_sections,
            "readable_sections_count": len(readable_sections),
            "writable_sections": writable_sections,
            "writable_sections_count": len(writable_sections),
        }),
    })


def check_recent_critical_anomalies() -> str:
    """Check episode_anomalies table in gaia.db for recent critical anomalies.

    T6 migration: reads from episode_anomalies table in gaia.db instead of
    the legacy workflow-episodic-memory/anomalies.jsonl file.

    Scans anomalies from the past hour with severity=critical.
    Returns a short warning string suitable for context injection,
    or empty string if nothing noteworthy is found.
    """
    try:
        import sys as _sys
        _hooks_dir = Path(__file__).resolve().parent.parent.parent
        _repo_root = _hooks_dir.parent
        for p in (str(_repo_root),):
            if p not in _sys.path:
                _sys.path.insert(0, p)
        from gaia.store.writer import _connect as _store_connect
        from gaia.project import current as _project_current
    except ImportError:
        return ""

    try:
        ws = _project_current()
    except Exception:
        ws = None

    try:
        con = _store_connect()
        try:
            one_hour_ago_dt = datetime.now() - timedelta(hours=1)
            one_hour_ago_iso = one_hour_ago_dt.strftime("%Y-%m-%dT%H:%M:%S")

            if ws:
                rows = con.execute(
                    "SELECT type FROM episode_anomalies "
                    "WHERE workspace = ? AND severity = 'critical' "
                    "AND timestamp >= ? "
                    "ORDER BY timestamp DESC LIMIT 20",
                    (ws, one_hour_ago_iso),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT type FROM episode_anomalies "
                    "WHERE severity = 'critical' "
                    "AND timestamp >= ? "
                    "ORDER BY timestamp DESC LIMIT 20",
                    (one_hour_ago_iso,),
                ).fetchall()
        finally:
            con.close()
    except Exception as e:
        logger.debug(f"Critical anomaly DB check failed (non-fatal): {e}")
        return ""

    critical_types = [r[0] for r in rows if r[0]]
    if not critical_types:
        return ""

    unique_types = sorted(set(critical_types))
    return (
        f"\n# Recent Critical Anomalies\n"
        f"{len(critical_types)} critical anomaly(ies) in the last hour "
        f"(types: {', '.join(unique_types)}). "
        f"Consider investigating with /gaia.\n"
    )


def consume_anomaly_flag(enriched_prompt: str) -> str:
    """No-op stub for legacy anomaly flag consumption.

    T4 retired the needs_analysis.flag mechanism -- anomaly signals are
    now written directly to the episode_anomalies table in gaia.db and
    surfaced via check_recent_critical_anomalies() which queries the DB.

    This function is kept as a no-op to avoid breaking any callers that
    reference it. Any remaining flag files from before T4 are silently
    cleaned up if found, but no warning is injected.

    T6 migration: no longer reads workflow-episodic-memory/signals/*.flag.
    """
    # Silently clean up any stale flag files from before T4 (non-blocking)
    try:
        flag_path = (
            get_plugin_data_dir()
            / "project-context"
            / "workflow-episodic-memory"
            / "signals"
            / "needs_analysis.flag"
        )
        if flag_path.exists():
            flag_path.unlink()
            logger.debug("Removed stale anomaly flag file (T4 retired flag mechanism)")
    except Exception:
        pass
    return enriched_prompt


def build_project_context(
    parameters: dict,
    project_agents: list,
    hooks_dir: Path = None,
) -> tuple:
    """
    Build project context string for a project agent without mutating parameters.

    Returns the context string suitable for additionalContext injection, plus a
    telemetry snapshot. Does NOT modify parameters in any way.

    Args:
        parameters: Task tool parameters (read-only).
        project_agents: List of valid project agent names.
        hooks_dir: Path to the hooks directory (for fallback paths).
            Defaults to Path(__file__).parent.parent.parent if None.

    Returns:
        (context_string, telemetry_snapshot) or (None, {}) if no context to inject.
    """
    if hooks_dir is None:
        hooks_dir = Path(__file__).parent.parent.parent

    subagent_type = parameters.get("subagent_type", "")

    # Only inject for project agents (not for generic agents like Explore, general-purpose, etc.)
    if subagent_type not in project_agents:
        logger.debug(f"Skipping context injection for non-project agent: {subagent_type}")
        return None, {}

    prompt = parameters.get("prompt", "")
    if not prompt:
        logger.warning(f"No prompt provided for {subagent_type}, skipping context injection")
        return None, {}

    try:
        # Build context payload in-process. context_provider lives at
        # <pkg_root>/tools/context/context_provider.py and is invoked directly
        # rather than as a subprocess, saving ~100-200ms per dispatch and
        # removing the stdout/stderr parsing path.
        _ensure_context_provider_importable(hooks_dir)
        try:
            from tools.context.context_provider import build_context_payload
        except ImportError as exc:
            logger.warning("context_provider import failed, skipping context injection: %s", exc)
            return None, {}

        logger.info(f"Building context for {subagent_type}...")
        try:
            context_payload = build_context_payload(
                agent_name=subagent_type,
                user_task=prompt,
            )
        except Exception as exc:
            logger.error("build_context_payload failed: %s", exc, exc_info=True)
            return None, {}

        # Extract and save context anchors for hit tracking
        try:
            anchors = extract_anchors(context_payload)
            if anchors:
                session_id = get_or_create_session_id()
                save_anchors(session_id, subagent_type, anchors)
                logger.debug(
                    "Saved %d context anchors for %s", len(anchors), subagent_type,
                )
        except Exception as exc:
            logger.debug("Anchor extraction failed (non-fatal): %s", exc)

        # Build context update reminder for empty writable sections
        update_reminder = build_context_update_reminder(
            subagent_type, project_agents, hooks_dir
        )

        # Build context sections from payload
        project_knowledge = context_payload.get("project_knowledge", {})
        write_perms = context_payload.get("write_permissions", {})
        # T2.1a: read from new key first, fall back to legacy key during dual-mode window
        investigation_brief = (
            context_payload.get("agent_contract_handoff")
            or context_payload.get("investigation_brief")
            or {}
        )
        surface_routing_data = context_payload.get("surface_routing", {})
        metadata = context_payload.get("metadata", {})
        historical = context_payload.get("historical_context", {})

        # Extract memory_index from historical before JSON rendering to avoid duplication
        memory_index_text = historical.pop("memory_index", "") if historical else ""
        memory_index_section = f"\n### Memory Index\n\n{memory_index_text}\n" if memory_index_text else ""

        # Optional sections
        routing_section = f"\n## Surface Routing\n\n{json.dumps(surface_routing_data, indent=2)}\n" if surface_routing_data else ""
        metadata_section = f"\n## Metadata\n\n{json.dumps(metadata, indent=2)}\n" if metadata else ""
        historical_section = f"\n## Historical Context\n\n{json.dumps(historical, indent=2)}\n" if historical else ""

        # Build Context Orientation header listing which sections are present
        orientation_lines = ["# Context Orientation\n"]
        orientation_lines.append("Sections present in this payload:\n")
        if project_knowledge:
            orientation_lines.append("- **Project Context** -- structured knowledge about the current project; guides scope and conventions")
        if routing_section:
            orientation_lines.append("- **Surface Routing** -- intent-to-agent mapping; use when delegating or checking ownership")
        if investigation_brief:
            orientation_lines.append("- **Agent Contract Handoff** -- goal, acceptance criteria, and scope for the current task")
        if write_perms:
            orientation_lines.append("- **Permissions** -- which context sections are writable vs readable; required before emitting update_contracts")
        if memory_index_section:
            orientation_lines.append("- **Memory Index** -- ranked memory documents relevant to this session; read high-score entries first")
        if historical_section:
            orientation_lines.append("- **Historical Context** -- past episodes and learned patterns; consult before repeating prior work")
        if metadata_section:
            orientation_lines.append("- **Metadata** -- session and environment metadata; use for debugging and traceability")
        orientation_section = "\n".join(orientation_lines) + "\n"

        # Save context_payload to disk for downstream hooks (SubagentStop).
        # Keyed by agent name (subagent_type): it is the ONLY identifier that
        # both this PreToolUse write side and the SubagentStop read side share
        # reliably. The subagent's transcript hash does not exist yet at
        # dispatch time, and the previously-used parameters['_agent_id'] was
        # never populated anywhere in the codebase (it always fell through to
        # subagent_type), so it was not a usable key. The read side
        # (transcript_reader.extract_injected_context_payload_from_transcript)
        # must resolve the same f"{agent_name}.json" name.
        try:
            payload_dir = Path(os.environ.get("TMPDIR", "/tmp")) / "gaia-context-payloads"
            payload_dir.mkdir(parents=True, exist_ok=True)
            payload_path = payload_dir / f"{subagent_type}.json"
            payload_path.write_text(json.dumps(context_payload, separators=(',', ':')))
            logger.debug(f"Context payload saved to {payload_path}")
        except Exception as exc:
            logger.debug(f"Failed to save context payload to disk (non-fatal): {exc}")

        # Build brief as YAML/Markdown-KV with inline field descriptions
        brief_mkv = _dict_to_yaml_annotated(investigation_brief, BRIEF_FIELD_DESCRIPTIONS) if investigation_brief else ""

        # Build write permissions as YAML/Markdown-KV
        write_perms_dict = {
            "writable": write_perms.get("writable_sections", []),
            "readable": write_perms.get("readable_sections", []),
            "context_update_required": [s for s in write_perms.get("writable_sections", [])
                                         if not project_knowledge.get(s)],
        }
        write_perms_mkv = _dict_to_yaml(write_perms_dict)

        context_string = f"""{orientation_section}
# Project Context

{_dict_to_yaml(project_knowledge)}

# Routing
{routing_section}
# Agent Contract Handoff

{brief_mkv}

# Permissions

{write_perms_mkv}
{memory_index_section}{update_reminder}{metadata_section}{historical_section}"""

        # Append anomaly signal flag (consume once)
        context_string = consume_anomaly_flag(context_string)

        # Surface recent critical anomalies from the JSONL log
        critical_summary = check_recent_critical_anomalies()
        if critical_summary:
            context_string += critical_summary

        # Inject recent operational events (non-blocking).
        # Brief 54 / Task 2.2: read from the harness_events DB table via
        # gaia.store.reader.cross_surface_query instead of the legacy
        # events.jsonl reader. The reader returns rows shaped as
        # {surface, timestamp, type, agent, summary, raw} -- NOT the old
        # {ts, type, agent, result} JSONL shape -- so the formatting loop
        # below is remapped to those keys (audit Risk 4: without the remap
        # the "Recent Events" block silently goes blank).
        try:
            import sys as _sys
            from pathlib import Path as _Path
            try:
                from gaia.store import reader as _reader
            except ImportError:
                _repo_root = _Path(__file__).resolve().parents[3]
                _sys.path.insert(0, str(_repo_root))
                from gaia.store import reader as _reader
            recent = _reader.cross_surface_query(
                surface="harness_events", since="24h", last=20,
            )
            if recent:
                lines = ["\n# Recent Events (last 24h)"]
                for evt in recent:
                    ts_short = (evt.get("timestamp") or "")[:16]
                    etype = evt.get("type") or ""
                    agent_name = evt.get("agent") or ""
                    result_str = evt.get("summary") or ""
                    label = f"{agent_name}: " if agent_name else ""
                    lines.append(f"- [{ts_short}] {etype}: {label}{result_str}")
                context_string += "\n".join(lines) + "\n"
        except Exception as exc:
            logger.debug("Event context injection failed (non-fatal): %s", exc)

        # Build telemetry snapshot
        telemetry = build_context_telemetry_snapshot(context_payload)

        sections_count = len(context_payload.get("project_knowledge", {}))

        logger.info(
            f"Context built for {subagent_type} "
            f"(sections={sections_count})"
        )

        return context_string, telemetry

    except Exception as e:
        logger.error(f"Error building context: {e}", exc_info=True)
        return None, {}


