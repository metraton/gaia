"""
Routing simulator for gaia-ops surface routing analysis.

Simulates what would happen when a prompt enters the orchestrator:
which surfaces activate, which agent is selected, what skills load,
what context sections are injected, and what contract permissions apply.

Uses the real classify_surfaces() from surface_router.py for fidelity.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

_TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from context.surface_router import classify_surfaces, load_surface_routing_config


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class RoutingResult:
    """Result of simulating a prompt through the routing pipeline."""

    prompt: str
    surfaces_active: list[str]
    primary_agent: str
    adjacent_agents: list[str]
    skills_loaded: list[str]
    context_sections: list[str]
    tokens_estimate: int
    contracts: dict[str, list[str]]  # {"read": [...], "write": [...]}
    confidence: float
    multi_surface: bool


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

_RE_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_RE_SKILLS_LINE = re.compile(r"^\s+-\s+(.+)$", re.MULTILINE)


def _parse_frontmatter(content: str) -> dict[str, Any]:
    """Parse YAML frontmatter from agent .md files."""
    if not content.startswith("---"):
        return {}

    match = _RE_FRONTMATTER.match(content)
    if not match:
        return {}

    yaml_block = match.group(1)
    result: dict[str, Any] = {}

    for line in yaml_block.splitlines():
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith("#"):
            continue
        if ":" not in line_stripped:
            continue

        key, _, value = line_stripped.partition(":")
        key = key.strip()
        value = value.strip()

        if key == "skills":
            skills: list[str] = []
            if value.startswith("[") and value.endswith("]"):
                skills = [s.strip() for s in value[1:-1].split(",") if s.strip()]
            else:
                skills_start = yaml_block.index("skills:")
                skills_section = yaml_block[skills_start:]
                for skill_match in _RE_SKILLS_LINE.finditer(skills_section):
                    skill_name = skill_match.group(1).strip()
                    if skill_name:
                        skills.append(skill_name)
            result["skills"] = skills
        else:
            result[key] = value

    return result


def _load_agent_skills(agents_dir: Path) -> dict[str, list[str]]:
    """Load skills lists from all agent .md files in a directory.

    Returns:
        Dict mapping agent name to list of skill names.
    """
    agent_skills: dict[str, list[str]] = {}

    if not agents_dir.is_dir():
        return agent_skills

    for md_file in sorted(agents_dir.glob("*.md")):
        content = md_file.read_text(encoding="utf-8", errors="replace")
        frontmatter = _parse_frontmatter(content)
        agent_name = frontmatter.get("name", md_file.stem)
        agent_skills[agent_name] = frontmatter.get("skills", [])

    return agent_skills


# ---------------------------------------------------------------------------
# RoutingSimulator
# ---------------------------------------------------------------------------


class RoutingSimulator:
    """Simulates the gaia-ops routing pipeline for a given prompt.

    Loads surface-routing.json, context-contracts.json, and agent frontmatter
    to predict: which surfaces activate, which agent handles, what skills and
    context sections are injected, and what contract permissions apply.
    """

    def __init__(self, config_dir: Path, agents_dir: Path):
        """Initialize the simulator with config and agents directories.

        Args:
            config_dir: Path to the config/ directory containing
                       surface-routing.json and context-contracts.json.
            agents_dir: Path to the agents/ directory containing agent .md files.
        """
        self._config_dir = config_dir
        self._agents_dir = agents_dir

        # Load routing config
        routing_file = config_dir / "surface-routing.json"
        self._routing_config = load_surface_routing_config(routing_file)

        # Load contracts
        contracts_file = config_dir / "context-contracts.json"
        if contracts_file.is_file():
            self._contracts = json.loads(contracts_file.read_text(encoding="utf-8"))
        else:
            self._contracts = {"agents": {}}

        # Load agent skills from frontmatter
        self._agent_skills = _load_agent_skills(agents_dir)

    def simulate(self, prompt: str, agent_type: Optional[str] = None) -> RoutingResult:
        """Simulate routing for a prompt.

        Args:
            prompt: The user prompt to classify.
            agent_type: If provided, show what this specific agent would receive.
                       If not, determine the agent from surface routing.

        Returns:
            RoutingResult with full routing prediction.
        """
        routing = classify_surfaces(
            prompt,
            current_agent=agent_type or "",
            routing_config=self._routing_config,
        )

        active_surfaces = routing.get("active_surfaces", [])
        confidence = routing.get("confidence", 0.0)
        multi_surface = routing.get("multi_surface", False)
        recommended_agents = routing.get("recommended_agents", [])

        surfaces_cfg = self._routing_config.get("surfaces", {})
        if agent_type:
            primary_agent = agent_type
        elif recommended_agents:
            primary_agent = recommended_agents[0]
        else:
            primary_agent = self._routing_config.get(
                "reconnaissance_agent", "developer"
            )

        skills = self._agent_skills.get(primary_agent, [])

        context_sections: list[str] = []
        seen_sections: set[str] = set()
        for surface in active_surfaces:
            surface_cfg = surfaces_cfg.get(surface, {})
            for section in surface_cfg.get("contract_sections", []):
                if section not in seen_sections:
                    context_sections.append(section)
                    seen_sections.add(section)

        agent_contract = self._contracts.get("agents", {}).get(primary_agent, {})
        read_sections = agent_contract.get("read", [])
        write_sections = agent_contract.get("write", [])

        tokens_estimate = len(context_sections) * 100

        adjacent_agents = [a for a in recommended_agents if a != primary_agent]

        return RoutingResult(
            prompt=prompt,
            surfaces_active=active_surfaces,
            primary_agent=primary_agent,
            adjacent_agents=adjacent_agents,
            skills_loaded=skills,
            context_sections=context_sections,
            tokens_estimate=tokens_estimate,
            contracts={"read": read_sections, "write": write_sections},
            confidence=confidence,
            multi_surface=multi_surface,
        )

    def simulate_from_log(self, events: list[dict[str, Any]]) -> list[RoutingResult]:
        """Simulate routing for all events from logs.

        Args:
            events: List of event dicts, each with prompt or tool_input data.

        Returns:
            List of RoutingResult instances.
        """
        results: list[RoutingResult] = []
        for event in events:
            prompt = event.get("prompt", "")
            if not prompt:
                tool_input = event.get("tool_input", {})
                if isinstance(tool_input, dict):
                    prompt = tool_input.get(
                        "command", tool_input.get("description", "")
                    )
            if not prompt:
                continue
            agent_used = event.get("agent", event.get("subagent_type", None))
            result = self.simulate(prompt, agent_type=agent_used)
            results.append(result)
        return results

    def compare_routing(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Compare simulated routing vs actual agent used in logs.

        Args:
            events: List of event dicts, each with prompt and agent keys.

        Returns:
            Dict with matches, mismatches, and statistics.
        """
        matches: list[dict[str, Any]] = []
        mismatches: list[dict[str, Any]] = []

        for event in events:
            prompt = event.get("prompt", "")
            actual_agent = event.get("agent", event.get("subagent_type", ""))
            if not prompt or not actual_agent:
                continue

            result = self.simulate(prompt)

            entry = {
                "prompt": prompt[:120],
                "simulated_agent": result.primary_agent,
                "actual_agent": actual_agent,
                "surfaces": result.surfaces_active,
                "confidence": result.confidence,
            }

            if result.primary_agent == actual_agent:
                matches.append(entry)
            else:
                mismatches.append(entry)

        total = len(matches) + len(mismatches)
        return {
            "total": total,
            "matches": len(matches),
            "mismatches": len(mismatches),
            "match_rate": round(len(matches) / max(total, 1), 2),
            "match_details": matches,
            "mismatch_details": mismatches,
        }


def format_routing_result(result: RoutingResult) -> str:
    """Format a RoutingResult as human-readable text.

    Args:
        result: The routing simulation result.

    Returns:
        Formatted multi-line string.
    """
    lines = [
        "=" * 60,
        "ROUTING SIMULATION",
        "=" * 60,
    ]
    lines.append("Prompt:           " + result.prompt[:100])
    lines.append("Primary agent:    " + result.primary_agent)
    adj = ", ".join(result.adjacent_agents) or "none"
    lines.append("Adjacent agents:  " + adj)
    lines.append("Confidence:       " + str(result.confidence))
    lines.append("Multi-surface:    " + str(result.multi_surface))
    lines.append("")
    lines.append("Active surfaces:")
    for surface in result.surfaces_active:
        lines.append("  - " + surface)
    if not result.surfaces_active:
        lines.append("  (none)")
    lines.append("")
    lines.append("Skills loaded:")
    for skill in result.skills_loaded:
        lines.append("  - " + skill)
    if not result.skills_loaded:
        lines.append("  (none)")
    lines.append("")
    lines.append("Context sections:")
    for section in result.context_sections:
        lines.append("  - " + section)
    if not result.context_sections:
        lines.append("  (none)")
    lines.append("Tokens estimate:  ~" + str(result.tokens_estimate))
    lines.append("")
    lines.append("Contracts:")
    read_str = ", ".join(result.contracts.get("read", [])) or "none"
    write_str = ", ".join(result.contracts.get("write", [])) or "none"
    lines.append("  Read:  " + read_str)
    lines.append("  Write: " + write_str)
    lines.append("=" * 60)
    return chr(10).join(lines)
