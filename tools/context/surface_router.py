#!/usr/bin/env python3
"""
Surface routing and investigation brief generation.

Provides deterministic surface classification for Gaia tasks using generic
cross-repo surfaces instead of repo-specific routing tables.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from ._paths import resolve_config_dir
except ImportError:
    from _paths import resolve_config_dir


DEFAULT_SURFACE_ROUTING_FILE = "surface-routing.json"
EVIDENCE_REPORT_FIELDS = [
    "PATTERNS_CHECKED",
    "FILES_CHECKED",
    "COMMANDS_RUN",
    "KEY_OUTPUTS",
    "VERBATIM_OUTPUTS",
    "CROSS_LAYER_IMPACTS",
    "OPEN_GAPS",
]
# All fields including OWNERSHIP_ASSESSMENT (for investigation brief injection).
# The runtime validator in response_contract.py separates OWNERSHIP_ASSESSMENT
# for enum validation; its CONSOLIDATION_FIELDS list excludes it.
CONSOLIDATION_REPORT_FIELDS = [
    "OWNERSHIP_ASSESSMENT",
    "CONFIRMED_FINDINGS",
    "SUSPECTED_FINDINGS",
    "CONFLICTS",
    "OPEN_GAPS",
    "NEXT_BEST_AGENT",
]


def _get_config_dir() -> Path:
    """Resolve config directory from installed project or package checkout."""
    return resolve_config_dir()


def load_surface_routing_config(config_file: Optional[Path] = None) -> Dict[str, Any]:
    """Load surface routing config. Returns empty config if missing or invalid."""
    if config_file is None:
        config_file = _get_config_dir() / DEFAULT_SURFACE_ROUTING_FILE

    if not config_file.is_file():
        return {"version": "missing", "reconnaissance_agent": "devops-developer", "surfaces": {}}

    try:
        return json.loads(config_file.read_text())
    except Exception:
        return {"version": "invalid", "reconnaissance_agent": "devops-developer", "surfaces": {}}


@dataclass(frozen=True)
class SurfaceMatch:
    surface: str
    score: float
    matched_signals: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _normalize_text(text: str) -> str:
    return " ".join((text or "").lower().split())


def _score_surface(task_text: str, surface_name: str, surface_cfg: Dict[str, Any]) -> SurfaceMatch:
    signals = surface_cfg.get("signals", {})
    matched: List[str] = []
    score = 0.0

    for keyword in signals.get("keywords", []):
        if keyword.lower() in task_text:
            matched.append(keyword)
            score += 1.0

    for command in signals.get("commands", []):
        if command.lower() in task_text:
            matched.append(command)
            score += 1.5

    for artifact in signals.get("artifacts", []):
        if artifact.lower() in task_text:
            matched.append(artifact)
            score += 1.0

    # Small boost for explicit surface names.
    if surface_name.lower() in task_text:
        matched.append(surface_name)
        score += 1.0

    return SurfaceMatch(surface=surface_name, score=score, matched_signals=matched)


def classify_surfaces(
    task: str,
    *,
    current_agent: str = "",
    routing_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Deterministically classify active surfaces for a task.

    The classifier uses generic surface signals, not repo-specific topology.
    """
    config = routing_config or load_surface_routing_config()
    surfaces_cfg = config.get("surfaces", {})
    reconnaissance_agent = config.get("reconnaissance_agent", "devops-developer")
    task_text = _normalize_text(task)

    matches: List[SurfaceMatch] = []
    for surface_name, surface_cfg in surfaces_cfg.items():
        match = _score_surface(task_text, surface_name, surface_cfg)
        if match.score > 0:
            matches.append(match)

    matches.sort(key=lambda item: item.score, reverse=True)

    if matches:
        top_score = matches[0].score
        active_matches = [
            match for match in matches
            if match.score >= 1.0 and (match.score == top_score or match.score >= (top_score * 0.55))
        ]
    else:
        active_matches = []

    agent_to_surface = {
        cfg.get("primary_agent", ""): surface_name
        for surface_name, cfg in surfaces_cfg.items()
    }
    fallback_surface = agent_to_surface.get(current_agent, "")

    if not active_matches and fallback_surface:
        active_matches = [SurfaceMatch(surface=fallback_surface, score=0.2, matched_signals=["agent-fallback"])]

    active_surfaces = [match.surface for match in active_matches]
    match_map = {match.surface: match for match in active_matches}

    if not active_surfaces:
        return {
            "active_surfaces": [],
            "primary_surface": "",
            "multi_surface": False,
            "dispatch_mode": "reconnaissance",
            "confidence": 0.0,
            "recommended_agents": [reconnaissance_agent],
            "matched_signals": {},
            "reconnaissance_agent": reconnaissance_agent,
        }

    if current_agent in agent_to_surface and agent_to_surface[current_agent] in active_surfaces:
        primary_surface = agent_to_surface[current_agent]
    else:
        primary_surface = active_surfaces[0]

    recommended_agents = []
    for surface_name in active_surfaces:
        agent = surfaces_cfg.get(surface_name, {}).get("primary_agent", "")
        if agent and agent not in recommended_agents:
            recommended_agents.append(agent)

    if len(active_surfaces) == 1:
        dispatch_mode = "single_surface"
    elif "planning_specs" in active_surfaces:
        dispatch_mode = "sequential"
    else:
        dispatch_mode = "parallel"

    confidence = round(min(1.0, sum(match.score for match in active_matches) / max(len(active_matches) * 3.0, 1.0)), 2)

    return {
        "active_surfaces": active_surfaces,
        "primary_surface": primary_surface,
        "multi_surface": len(active_surfaces) > 1,
        "dispatch_mode": dispatch_mode,
        "confidence": confidence,
        "recommended_agents": recommended_agents or [reconnaissance_agent],
        "matched_signals": {surface: match_map[surface].matched_signals for surface in active_surfaces},
        "reconnaissance_agent": reconnaissance_agent,
    }


def build_investigation_brief(
    task: str,
    agent_name: str,
    contract_context: Dict[str, Any],
    *,
    routing_config: Optional[Dict[str, Any]] = None,
    routing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build a deterministic investigation brief for the current agent.
    """
    config = routing_config or load_surface_routing_config()
    surfaces_cfg = config.get("surfaces", {})
    if routing is None:
        routing = classify_surfaces(task, current_agent=agent_name, routing_config=config)

    primary_surface = routing.get("primary_surface", "")
    primary_cfg = surfaces_cfg.get(primary_surface, {})
    active_surfaces = routing.get("active_surfaces", [])
    adjacent_surfaces = []
    for surface_name in active_surfaces:
        if surface_name != primary_surface and surface_name not in adjacent_surfaces:
            adjacent_surfaces.append(surface_name)
    for surface_name in primary_cfg.get("adjacent_surfaces", []):
        if surface_name != primary_surface and surface_name not in adjacent_surfaces:
            adjacent_surfaces.append(surface_name)

    recommended_agents = routing.get("recommended_agents", [])
    peer_agents = [agent for agent in recommended_agents if agent != agent_name]

    agent_surface = ""
    for surface_name, cfg in surfaces_cfg.items():
        if cfg.get("primary_agent") == agent_name:
            agent_surface = surface_name
            break

    if not active_surfaces:
        role = "reconnaissance"
    elif agent_surface == primary_surface:
        role = "primary"
    elif agent_surface in active_surfaces:
        role = "cross_check"
    else:
        role = "adjacent"

    cross_check_required = len(active_surfaces) > 1 or (agent_surface and agent_surface != primary_surface)

    search_anchors = sorted(contract_context.keys())
    required_checks = list(primary_cfg.get("required_checks", []))
    for surface_name in adjacent_surfaces:
        for check in surfaces_cfg.get(surface_name, {}).get("required_checks", []):
            if check not in required_checks:
                required_checks.append(check)

    return {
        "goal": task,
        "agent_role": role,
        "primary_surface": primary_surface,
        "active_surfaces": active_surfaces,
        "adjacent_surfaces": adjacent_surfaces,
        "dispatch_mode": routing.get("dispatch_mode", "single_surface"),
        "cross_check_required": cross_check_required,
        "patterns_required": True,
        "contract_sections_to_anchor": search_anchors,
        "required_checks": required_checks,
        "evidence_required": EVIDENCE_REPORT_FIELDS,
        "consolidation_required": cross_check_required,
        "consolidation_fields": CONSOLIDATION_REPORT_FIELDS if cross_check_required else [],
        "recommended_peer_agents": peer_agents,
        "stop_conditions": [
            "Stop when additional files or commands only confirm the same conclusion without changing the decision.",
            "Do not declare cross-surface work complete without filling CROSS_LAYER_IMPACTS and OPEN_GAPS.",
            "If another surface owns the fix, name the next agent instead of guessing across domains.",
        ],
    }


__all__ = [
    "EVIDENCE_REPORT_FIELDS",
    "CONSOLIDATION_REPORT_FIELDS",
    "build_investigation_brief",
    "classify_surfaces",
    "load_surface_routing_config",
]
