#!/usr/bin/env python3
"""
Context Lazy Loader
Carga progresiva de contexto basada en necesidades reales del agente.
Reduce tokens consumidos en 40-60% mediante carga inteligente.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Set
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class ContextPriority(Enum):
    """Prioridad de carga de contexto"""
    REQUIRED = "required"      # Siempre cargar
    RECOMMENDED = "recommended"  # Cargar si tier >= T2
    OPTIONAL = "optional"      # Solo si explícitamente pedido
    ON_DEMAND = "on_demand"    # Nunca pre-cargar


@dataclass
class ContextSection:
    """Representa una sección de contexto"""
    name: str
    priority: ContextPriority
    size_estimate: int  # Estimated tokens
    dependencies: List[str] = field(default_factory=list)
    loaded: bool = False
    content: Optional[Dict[str, Any]] = None


class LazyContextLoader:
    """
    Carga progresiva de contexto para minimizar token usage.

    Estrategias:
    1. Minimal loading: Solo required fields
    2. Tier-based loading: T0=minimal, T3=full
    3. Task-based loading: Analiza keywords para decidir qué cargar
    4. On-demand loading: Carga adicional si el agente lo pide
    """

    # Definición de secciones y sus prioridades por agente
    AGENT_CONTEXT_MAP = {
        "terraform-architect": {
            "project_details": ContextPriority.REQUIRED,
            "terraform_infrastructure": ContextPriority.REQUIRED,
            "terraform_state": ContextPriority.RECOMMENDED,
            "terraform_modules": ContextPriority.OPTIONAL,
            "operational_guidelines": ContextPriority.RECOMMENDED,
            "cost_estimates": ContextPriority.OPTIONAL,
            "compliance_requirements": ContextPriority.ON_DEMAND
        },
        "gitops-operator": {
            "project_details": ContextPriority.REQUIRED,
            "gitops_configuration": ContextPriority.REQUIRED,
            "cluster_details": ContextPriority.REQUIRED,
            "namespaces": ContextPriority.RECOMMENDED,
            "deployments": ContextPriority.RECOMMENDED,
            "services": ContextPriority.OPTIONAL,
            "operational_guidelines": ContextPriority.RECOMMENDED,
            "secrets": ContextPriority.ON_DEMAND
        },
        "cloud-troubleshooter": {
            "project_details": ContextPriority.REQUIRED,
            "cluster_details": ContextPriority.REQUIRED,
            "error_logs": ContextPriority.REQUIRED,
            "metrics": ContextPriority.RECOMMENDED,
            "recent_changes": ContextPriority.RECOMMENDED,
            "infrastructure_state": ContextPriority.OPTIONAL,
            "cost_analysis": ContextPriority.ON_DEMAND
        },
        "devops-developer": {
            "project_details": ContextPriority.REQUIRED,
            "operational_guidelines": ContextPriority.REQUIRED,
            "development_environment": ContextPriority.RECOMMENDED,
            "ci_cd_pipeline": ContextPriority.OPTIONAL,
            "dependencies": ContextPriority.OPTIONAL,
            "test_results": ContextPriority.ON_DEMAND
        }
    }

    # Estimated token sizes for common sections
    SECTION_SIZE_ESTIMATES = {
        "project_details": 500,
        "terraform_infrastructure": 1500,
        "terraform_state": 800,
        "terraform_modules": 1200,
        "gitops_configuration": 600,
        "cluster_details": 1000,
        "namespaces": 2000,
        "deployments": 2500,
        "services": 3000,
        "operational_guidelines": 400,
        "error_logs": 1500,
        "metrics": 1000,
        "recent_changes": 800
    }

    def __init__(self, context_file: Path = None, max_tokens: int = 3000):
        """
        Initialize lazy loader.

        Args:
            context_file: Path to project-context.json
            max_tokens: Maximum tokens to load initially
        """
        self.context_file = context_file or Path(".claude/project-context.json")
        self.max_tokens = max_tokens
        self.loaded_sections: Dict[str, ContextSection] = {}
        self.usage_history: List[str] = []  # Track what gets used
        self.full_context: Optional[Dict[str, Any]] = None

    def load_minimal_context(
        self,
        agent: str,
        task: str,
        tier: str
    ) -> Dict[str, Any]:
        """
        Load minimal context based on agent, task, and tier.

        Args:
            agent: Agent name (e.g., "terraform-architect")
            task: Task description
            tier: Security tier (T0-T3)

        Returns:
            Minimal context dictionary with only necessary sections
        """
        logger.info(f"Loading minimal context for {agent} (tier: {tier})")

        # Load full context if not already loaded
        if not self.full_context:
            self._load_full_context()

        # Get agent's context map
        agent_map = self.AGENT_CONTEXT_MAP.get(agent, {})

        # Determine what to load based on tier
        priorities_to_load = self._get_priorities_for_tier(tier)

        # Analyze task to identify additional needs
        task_sections = self._analyze_task_requirements(task, agent)

        # Build minimal context
        minimal_context = {
            "metadata": {
                "loader": "lazy",
                "agent": agent,
                "tier": tier,
                "loaded_sections": []
            }
        }

        current_size = 0

        # Load sections by priority
        for section_name, priority in agent_map.items():
            # Check if should load based on priority
            if priority not in priorities_to_load:
                continue

            # Check if task specifically needs this
            if priority == ContextPriority.OPTIONAL and section_name not in task_sections:
                continue

            # Check size limit
            section_size = self.SECTION_SIZE_ESTIMATES.get(section_name, 500)
            if current_size + section_size > self.max_tokens:
                logger.warning(f"Skipping {section_name} due to token limit")
                continue

            # Load section
            section_content = self._load_section(section_name)
            if section_content:
                minimal_context[section_name] = section_content
                minimal_context["metadata"]["loaded_sections"].append(section_name)
                current_size += section_size

                # Track usage
                self.usage_history.append(section_name)

        logger.info(
            f"Loaded {len(minimal_context['metadata']['loaded_sections'])} sections "
            f"({current_size} estimated tokens)"
        )

        return minimal_context

    def load_on_demand(
        self,
        section_name: str,
        current_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Load additional context on-demand when agent requests it.

        Args:
            section_name: Name of section to load
            current_context: Current context to augment

        Returns:
            Updated context with requested section
        """
        logger.info(f"On-demand loading of section: {section_name}")

        # Check if already loaded
        if section_name in current_context:
            logger.debug(f"Section {section_name} already loaded")
            return current_context

        # Load section
        section_content = self._load_section(section_name)
        if section_content:
            current_context[section_name] = section_content
            current_context["metadata"]["loaded_sections"].append(section_name)

            # Track on-demand usage (important for learning)
            self.usage_history.append(f"on_demand:{section_name}")

        return current_context

    def get_usage_stats(self) -> Dict[str, Any]:
        """
        Get statistics on what sections are actually used.

        Returns:
            Usage statistics for optimization
        """
        from collections import Counter

        usage_counts = Counter(self.usage_history)
        on_demand_counts = Counter(
            s.split(":")[1] for s in self.usage_history
            if s.startswith("on_demand:")
        )

        return {
            "total_loads": len(self.usage_history),
            "unique_sections": len(set(self.usage_history)),
            "most_used": dict(usage_counts.most_common(10)),
            "on_demand_requests": dict(on_demand_counts),
            "recommendations": self._generate_recommendations(usage_counts, on_demand_counts)
        }

    def _load_full_context(self):
        """Load full context from file."""
        if not self.context_file.exists():
            logger.error(f"Context file not found: {self.context_file}")
            self.full_context = {}
            return

        try:
            with open(self.context_file) as f:
                self.full_context = json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse context file: {e}")
            self.full_context = {}

    def _load_section(self, section_name: str) -> Optional[Dict[str, Any]]:
        """Load a specific section from full context."""
        if not self.full_context:
            return None

        # Check if it's a nested path (e.g., "terraform.modules")
        if "." in section_name:
            parts = section_name.split(".")
            content = self.full_context
            for part in parts:
                content = content.get(part, {})
                if not content:
                    return None
            return content

        return self.full_context.get(section_name)

    def _get_priorities_for_tier(self, tier: str) -> Set[ContextPriority]:
        """Determine which priorities to load based on tier."""
        if tier == "T0":
            return {ContextPriority.REQUIRED}
        elif tier == "T1":
            return {ContextPriority.REQUIRED}
        elif tier == "T2":
            return {ContextPriority.REQUIRED, ContextPriority.RECOMMENDED}
        elif tier == "T3":
            return {ContextPriority.REQUIRED, ContextPriority.RECOMMENDED, ContextPriority.OPTIONAL}
        else:
            return {ContextPriority.REQUIRED}

    def _analyze_task_requirements(self, task: str, agent: str) -> Set[str]:
        """
        Analyze task to identify which optional sections might be needed.

        Args:
            task: Task description
            agent: Agent name

        Returns:
            Set of section names that should be loaded
        """
        task_lower = task.lower()
        needed_sections = set()

        # Task-specific keywords mapping to sections
        keyword_map = {
            "terraform": ["terraform_infrastructure", "terraform_state", "terraform_modules"],
            "deploy": ["deployments", "services"],
            "pod": ["namespaces", "deployments"],
            "service": ["services", "cluster_details"],
            "error": ["error_logs", "metrics"],
            "cost": ["cost_estimates", "cost_analysis"],
            "compliance": ["compliance_requirements"],
            "secret": ["secrets"],
            "test": ["test_results"]
        }

        for keyword, sections in keyword_map.items():
            if keyword in task_lower:
                needed_sections.update(sections)

        return needed_sections

    def _generate_recommendations(
        self,
        usage_counts: Dict[str, int],
        on_demand_counts: Dict[str, int]
    ) -> List[str]:
        """Generate recommendations for context optimization."""
        recommendations = []

        # Check for frequently on-demand sections that should be pre-loaded
        for section, count in on_demand_counts.items():
            if count > 5:
                recommendations.append(
                    f"Consider upgrading '{section}' from ON_DEMAND to OPTIONAL "
                    f"(requested {count} times)"
                )

        # Check for never-used REQUIRED sections
        for agent, sections in self.AGENT_CONTEXT_MAP.items():
            for section, priority in sections.items():
                if priority == ContextPriority.REQUIRED and usage_counts.get(section, 0) == 0:
                    recommendations.append(
                        f"Consider downgrading '{section}' for {agent} from REQUIRED "
                        f"(never used in recent history)"
                    )

        return recommendations


# CLI for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test loader
    loader = LazyContextLoader(max_tokens=2000)

    # Test minimal loading for different scenarios
    print("🧪 Testing Lazy Context Loading...\n")

    # Test 1: T0 operation (minimal)
    context_t0 = loader.load_minimal_context(
        agent="terraform-architect",
        task="show terraform state",
        tier="T0"
    )
    print(f"T0 Context: {len(context_t0)} sections loaded")
    print(f"  Sections: {context_t0.get('metadata', {}).get('loaded_sections', [])}")
    print()

    # Test 2: T3 operation (more context)
    context_t3 = loader.load_minimal_context(
        agent="terraform-architect",
        task="apply terraform changes to production",
        tier="T3"
    )
    print(f"T3 Context: {len(context_t3)} sections loaded")
    print(f"  Sections: {context_t3.get('metadata', {}).get('loaded_sections', [])}")
    print()

    # Test 3: On-demand loading
    context_t3 = loader.load_on_demand("compliance_requirements", context_t3)
    print(f"After on-demand: {len(context_t3)} sections")
    print()

    # Show usage stats
    stats = loader.get_usage_stats()
    print("📊 Usage Statistics:")
    print(f"  Total loads: {stats['total_loads']}")
    print(f"  Unique sections: {stats['unique_sections']}")
    print(f"  Most used: {stats['most_used']}")
    print(f"  On-demand: {stats['on_demand_requests']}")
    if stats['recommendations']:
        print("  Recommendations:")
        for rec in stats['recommendations']:
            print(f"    - {rec}")