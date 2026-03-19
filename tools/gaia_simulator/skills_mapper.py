"""
Skills mapper for gaia-ops agent/skill/surface/contract analysis.

Builds a complete map of agents, their skills, the surfaces that route
to them, and the contract permissions they hold. Flags orphan skills.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


_TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from gaia_simulator.routing_simulator import _parse_frontmatter


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SkillMapping:
    """Maps a skill to the agents that use it."""

    skill_name: str
    skill_path: str
    used_by_agents: list[str]
    is_orphan: bool  # no agent uses it


@dataclass
class AgentProfile:
    """Full profile for an agent."""

    agent_name: str
    skills: list[str]
    invocation_count: int  # from metrics/logs
    surfaces: list[str]  # which surfaces route to this agent
    read_sections: list[str]
    write_sections: list[str]


# ---------------------------------------------------------------------------
# SkillsMapper
# ---------------------------------------------------------------------------


class SkillsMapper:
    """Builds the complete map of agents, skills, surfaces, and contracts."""

    def __init__(self, agents_dir: Path, skills_dir: Path, config_dir: Path):
        """Initialize the mapper.

        Args:
            agents_dir: Path to agents/ directory with agent .md files.
            skills_dir: Path to skills/ directory with skill subdirectories.
            config_dir: Path to config/ directory with routing and contracts.
        """
        self._agents_dir = agents_dir
        self._skills_dir = skills_dir
        self._config_dir = config_dir

        # Load agent frontmatter
        self._agent_frontmatter: dict[str, dict[str, Any]] = {}
        if agents_dir.is_dir():
            for md_file in sorted(agents_dir.glob("*.md")):
                content = md_file.read_text(encoding="utf-8", errors="replace")
                fm = _parse_frontmatter(content)
                name = fm.get("name", md_file.stem)
                self._agent_frontmatter[name] = fm

        # Load surface routing config
        routing_file = config_dir / "surface-routing.json"
        if routing_file.is_file():
            self._routing_config = json.loads(
                routing_file.read_text(encoding="utf-8")
            )
        else:
            self._routing_config = {"surfaces": {}}

        # Load contracts
        contracts_file = config_dir / "context-contracts.json"
        if contracts_file.is_file():
            self._contracts = json.loads(
                contracts_file.read_text(encoding="utf-8")
            )
        else:
            self._contracts = {"agents": {}}

        # Discover all skill directories
        self._all_skills: list[str] = []
        if skills_dir.is_dir():
            for item in sorted(skills_dir.iterdir()):
                if item.is_dir() and not item.name.startswith("."):
                    self._all_skills.append(item.name)

    def _get_surfaces_for_agent(self, agent_name: str) -> list[str]:
        """Find which surfaces route to a given agent."""
        surfaces: list[str] = []
        for surface_name, surface_cfg in self._routing_config.get(
            "surfaces", {}
        ).items():
            if surface_cfg.get("primary_agent") == agent_name:
                surfaces.append(surface_name)
        return surfaces

    def get_agent_profiles(self) -> list[AgentProfile]:
        """Full profile for each agent.

        Returns:
            List of AgentProfile instances, one per agent.
        """
        profiles: list[AgentProfile] = []

        for agent_name, fm in self._agent_frontmatter.items():
            skills = fm.get("skills", [])
            surfaces = self._get_surfaces_for_agent(agent_name)
            contract = self._contracts.get("agents", {}).get(agent_name, {})
            read_sections = contract.get("read", [])
            write_sections = contract.get("write", [])

            profiles.append(
                AgentProfile(
                    agent_name=agent_name,
                    skills=skills,
                    invocation_count=0,
                    surfaces=surfaces,
                    read_sections=read_sections,
                    write_sections=write_sections,
                )
            )

        return profiles

    def get_skill_mappings(self) -> list[SkillMapping]:
        """Which skills are used by which agents. Flag orphans.

        Returns:
            List of SkillMapping instances, one per discovered skill.
        """
        # Build reverse map: skill -> list of agents
        skill_to_agents: dict[str, list[str]] = {}
        for agent_name, fm in self._agent_frontmatter.items():
            for skill in fm.get("skills", []):
                skill_to_agents.setdefault(skill, []).append(agent_name)

        mappings: list[SkillMapping] = []
        for skill_name in self._all_skills:
            skill_path = str(self._skills_dir / skill_name)
            used_by = skill_to_agents.get(skill_name, [])
            mappings.append(
                SkillMapping(
                    skill_name=skill_name,
                    skill_path=skill_path,
                    used_by_agents=used_by,
                    is_orphan=len(used_by) == 0,
                )
            )

        return mappings

    def get_unused_skills(self) -> list[str]:
        """Skills that no agent references.

        Returns:
            List of orphan skill names.
        """
        return [m.skill_name for m in self.get_skill_mappings() if m.is_orphan]

    def enrich_from_logs(self, metrics_path: Path) -> dict[str, Any]:
        """Cross-reference with production metrics.

        Reads audit-*.jsonl files to count agent invocations.

        Args:
            metrics_path: Path to directory containing audit JSONL files.

        Returns:
            Dict with agent_invocations and skill_loads counts.
        """
        agent_counts: dict[str, int] = {}
        skill_counts: dict[str, int] = {}

        if not metrics_path.is_dir():
            return {"agent_invocations": agent_counts, "skill_loads": skill_counts}

        for jsonl_file in sorted(metrics_path.glob("audit-*.jsonl")):
            for line in jsonl_file.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                agent = record.get("agent", record.get("subagent_type", ""))
                if agent:
                    agent_counts[agent] = agent_counts.get(agent, 0) + 1

        # Map agent counts to skill counts
        for agent_name, count in agent_counts.items():
            fm = self._agent_frontmatter.get(agent_name, {})
            for skill in fm.get("skills", []):
                skill_counts[skill] = skill_counts.get(skill, 0) + count

        return {"agent_invocations": agent_counts, "skill_loads": skill_counts}

    def format_report(self) -> str:
        """Human-readable report of the agent/skill/surface map.

        Returns:
            Formatted multi-line string.
        """
        lines = []
        lines.append("=" * 60)
        lines.append("SKILLS MAPPER REPORT")
        lines.append("=" * 60)

        # Agent -> Skills table
        lines.append("")
        lines.append("AGENT -> SKILLS:")
        lines.append("-" * 40)
        for profile in self.get_agent_profiles():
            skills_str = ", ".join(profile.skills) or "(none)"
            surfaces_str = ", ".join(profile.surfaces) or "(none)"
            lines.append("  " + profile.agent_name + ":")
            lines.append("    Skills:   " + skills_str)
            lines.append("    Surfaces: " + surfaces_str)
            lines.append("    Read:     " + str(len(profile.read_sections)) + " sections")
            lines.append("    Write:    " + str(len(profile.write_sections)) + " sections")

        # Skill -> Agents table (reverse)
        lines.append("")
        lines.append("SKILL -> AGENTS:")
        lines.append("-" * 40)
        for mapping in self.get_skill_mappings():
            agents_str = ", ".join(mapping.used_by_agents) or "ORPHAN"
            orphan_tag = " [ORPHAN]" if mapping.is_orphan else ""
            lines.append("  " + mapping.skill_name + ": " + agents_str + orphan_tag)

        # Orphan skills
        unused = self.get_unused_skills()
        if unused:
            lines.append("")
            lines.append("ORPHAN SKILLS (" + str(len(unused)) + "):")
            lines.append("-" * 40)
            for skill in unused:
                lines.append("  - " + skill)

        lines.append("=" * 60)
        return chr(10).join(lines)
