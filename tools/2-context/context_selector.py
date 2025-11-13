#!/usr/bin/env python3
"""
Smart Context Selector
Selección inteligente de contexto usando semantic matching y análisis de tareas.
Determina qué secciones de contexto son más relevantes para cada tarea.
"""

import sys
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Set, Optional, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

# Add semantic matcher to path
sys.path.insert(0, str(Path(__file__).parent.parent / "6-semantic"))

try:
    from semantic_matcher import SemanticMatcher
except ImportError:
    # Fallback if semantic matcher not available
    SemanticMatcher = None
    logger.warning("SemanticMatcher not available, using keyword-based selection")

logger = logging.getLogger(__name__)


@dataclass
class ContextRelevance:
    """Relevance score for a context section"""
    section_name: str
    relevance_score: float  # 0.0 to 1.0
    matched_keywords: List[str]
    semantic_similarity: Optional[float]
    tier_importance: float
    final_score: float = 0.0

    def calculate_final_score(self, weights: Dict[str, float] = None):
        """Calculate final relevance score with weighted components."""
        if weights is None:
            weights = {
                "keyword": 0.3,
                "semantic": 0.4,
                "tier": 0.3
            }

        keyword_score = min(len(self.matched_keywords) * 0.2, 1.0)
        semantic_score = self.semantic_similarity or 0.0

        self.final_score = (
            weights["keyword"] * keyword_score +
            weights["semantic"] * semantic_score +
            weights["tier"] * self.tier_importance
        )


class SmartContextSelector:
    """
    Selección inteligente de contexto basada en análisis de tareas.

    Utiliza:
    1. Keyword matching para identificación rápida
    2. Semantic similarity para relevancia profunda
    3. Historical usage patterns (si están disponibles)
    4. Tier-based importance weighting
    """

    # Mapeo de keywords a secciones de contexto
    KEYWORD_TO_SECTIONS = {
        # Infrastructure keywords
        "terraform": ["terraform_infrastructure", "terraform_state", "terraform_modules"],
        "infrastructure": ["terraform_infrastructure", "cluster_details"],
        "iac": ["terraform_infrastructure", "terraform_modules"],
        "state": ["terraform_state"],

        # Kubernetes keywords
        "kubernetes": ["cluster_details", "namespaces", "deployments", "services"],
        "k8s": ["cluster_details", "namespaces", "deployments", "services"],
        "kubectl": ["cluster_details", "namespaces"],
        "pod": ["deployments", "namespaces"],
        "deployment": ["deployments", "namespaces"],
        "service": ["services", "cluster_details"],
        "namespace": ["namespaces"],
        "ingress": ["services", "cluster_details"],

        # GitOps keywords
        "gitops": ["gitops_configuration", "cluster_details"],
        "flux": ["gitops_configuration", "deployments"],
        "argocd": ["gitops_configuration", "deployments"],

        # Operations keywords
        "deploy": ["deployments", "gitops_configuration", "operational_guidelines"],
        "rollback": ["deployments", "terraform_state", "gitops_configuration"],
        "scale": ["deployments", "cluster_details"],
        "update": ["deployments", "services", "terraform_infrastructure"],
        "apply": ["terraform_infrastructure", "gitops_configuration"],

        # Monitoring/debugging keywords
        "error": ["error_logs", "metrics", "recent_changes"],
        "debug": ["error_logs", "metrics", "deployments"],
        "log": ["error_logs"],
        "metric": ["metrics"],
        "monitor": ["metrics", "error_logs"],
        "alert": ["metrics", "operational_guidelines"],
        "troubleshoot": ["error_logs", "metrics", "recent_changes"],

        # Cost/compliance keywords
        "cost": ["cost_estimates", "cost_analysis", "terraform_infrastructure"],
        "billing": ["cost_analysis"],
        "compliance": ["compliance_requirements", "operational_guidelines"],
        "security": ["compliance_requirements", "secrets", "operational_guidelines"],
        "secret": ["secrets"],

        # Development keywords
        "test": ["test_results", "ci_cd_pipeline"],
        "build": ["ci_cd_pipeline", "development_environment"],
        "ci": ["ci_cd_pipeline"],
        "cd": ["ci_cd_pipeline", "deployments"],
        "pipeline": ["ci_cd_pipeline"]
    }

    # Importance by tier
    TIER_IMPORTANCE = {
        "T0": {"base": 0.3, "multiplier": 1.0},
        "T1": {"base": 0.5, "multiplier": 1.2},
        "T2": {"base": 0.7, "multiplier": 1.5},
        "T3": {"base": 0.9, "multiplier": 2.0}
    }

    def __init__(
        self,
        semantic_matcher: Optional[Any] = None,
        usage_history_file: Optional[Path] = None
    ):
        """
        Initialize smart context selector.

        Args:
            semantic_matcher: Instance of SemanticMatcher for similarity scoring
            usage_history_file: Path to historical usage patterns
        """
        self.semantic_matcher = semantic_matcher
        self.usage_patterns = self._load_usage_patterns(usage_history_file)
        self.selection_history = []

    def select_relevant_sections(
        self,
        task: str,
        agent: str,
        tier: str,
        available_sections: List[str],
        max_sections: int = 10
    ) -> List[Tuple[str, float]]:
        """
        Select most relevant context sections for a task.

        Args:
            task: Task description
            agent: Agent name
            tier: Security tier (T0-T3)
            available_sections: List of available context sections
            max_sections: Maximum number of sections to return

        Returns:
            List of (section_name, relevance_score) tuples, sorted by relevance
        """
        logger.info(f"Selecting relevant sections for task: {task[:100]}...")

        # Calculate relevance for each available section
        relevance_scores = []

        for section in available_sections:
            relevance = self._calculate_relevance(
                section=section,
                task=task,
                agent=agent,
                tier=tier
            )
            relevance_scores.append(relevance)

        # Sort by final score
        relevance_scores.sort(key=lambda r: r.final_score, reverse=True)

        # Select top sections
        selected = []
        for relevance in relevance_scores[:max_sections]:
            if relevance.final_score > 0.3:  # Minimum threshold
                selected.append((relevance.section_name, relevance.final_score))
                logger.debug(
                    f"  Selected: {relevance.section_name} "
                    f"(score: {relevance.final_score:.2f}, "
                    f"keywords: {relevance.matched_keywords})"
                )

        # Track selection for learning
        self.selection_history.append({
            "task": task,
            "agent": agent,
            "tier": tier,
            "selected": selected
        })

        return selected

    def _calculate_relevance(
        self,
        section: str,
        task: str,
        agent: str,
        tier: str
    ) -> ContextRelevance:
        """Calculate relevance score for a section."""
        # Keyword matching
        matched_keywords = self._match_keywords(task, section)

        # Semantic similarity (if available)
        semantic_sim = None
        if self.semantic_matcher and SemanticMatcher:
            try:
                semantic_sim = self._calculate_semantic_similarity(task, section)
            except Exception as e:
                logger.debug(f"Semantic matching failed: {e}")

        # Tier importance
        tier_data = self.TIER_IMPORTANCE.get(tier, self.TIER_IMPORTANCE["T0"])
        tier_importance = tier_data["base"]

        # Boost if section is commonly used by this agent
        if self.usage_patterns.get(agent, {}).get(section, 0) > 0.5:
            tier_importance *= tier_data["multiplier"]

        # Create relevance object
        relevance = ContextRelevance(
            section_name=section,
            relevance_score=0.0,
            matched_keywords=matched_keywords,
            semantic_similarity=semantic_sim,
            tier_importance=tier_importance
        )

        # Calculate final score
        relevance.calculate_final_score()

        return relevance

    def _match_keywords(self, task: str, section: str) -> List[str]:
        """Find keywords in task that match this section."""
        task_lower = task.lower()
        matched = []

        for keyword, sections in self.KEYWORD_TO_SECTIONS.items():
            if keyword in task_lower and section in sections:
                matched.append(keyword)

        return matched

    def _calculate_semantic_similarity(self, task: str, section: str) -> float:
        """
        Calculate semantic similarity between task and section.

        Uses the SemanticMatcher to compute similarity.
        """
        if not self.semantic_matcher:
            return 0.0

        # Create section description for matching
        section_descriptions = {
            "terraform_infrastructure": "terraform infrastructure state resources configuration",
            "terraform_state": "terraform state file current deployed resources",
            "cluster_details": "kubernetes cluster configuration nodes namespaces",
            "deployments": "kubernetes deployments pods containers replicas",
            "services": "kubernetes services endpoints ingress networking",
            "namespaces": "kubernetes namespaces resource isolation",
            "gitops_configuration": "gitops flux argocd deployment automation",
            "error_logs": "error logs debugging troubleshooting failures",
            "metrics": "metrics monitoring performance alerts dashboards",
            "operational_guidelines": "operations guidelines procedures runbooks"
        }

        section_desc = section_descriptions.get(section, section)

        # Use semantic matcher to compute similarity
        try:
            # This would use the actual SemanticMatcher.compute_similarity method
            # For now, return a mock similarity
            similarity = 0.5  # Placeholder
            return similarity
        except Exception:
            return 0.0

    def _load_usage_patterns(self, history_file: Optional[Path]) -> Dict[str, Dict[str, float]]:
        """Load historical usage patterns."""
        if not history_file or not history_file.exists():
            return {}

        try:
            with open(history_file) as f:
                data = json.load(f)
                # Convert to agent -> section -> frequency mapping
                patterns = defaultdict(lambda: defaultdict(float))
                for entry in data:
                    agent = entry.get("agent")
                    for section, frequency in entry.get("sections", {}).items():
                        patterns[agent][section] = frequency
                return dict(patterns)
        except Exception as e:
            logger.warning(f"Failed to load usage patterns: {e}")
            return {}

    def get_selection_insights(self) -> Dict[str, Any]:
        """
        Get insights from selection history.

        Returns analytics about what sections are selected most often.
        """
        if not self.selection_history:
            return {"message": "No selection history available"}

        # Analyze selection patterns
        total_selections = len(self.selection_history)
        section_counts = defaultdict(int)
        agent_patterns = defaultdict(lambda: defaultdict(int))

        for entry in self.selection_history:
            agent = entry["agent"]
            for section, score in entry["selected"]:
                section_counts[section] += 1
                agent_patterns[agent][section] += 1

        # Calculate insights
        insights = {
            "total_selections": total_selections,
            "most_selected_sections": dict(
                sorted(section_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            ),
            "agent_preferences": {
                agent: dict(sorted(sections.items(), key=lambda x: x[1], reverse=True)[:3])
                for agent, sections in agent_patterns.items()
            },
            "recommendations": self._generate_recommendations(section_counts, agent_patterns)
        }

        return insights

    def _generate_recommendations(
        self,
        section_counts: Dict[str, int],
        agent_patterns: Dict[str, Dict[str, int]]
    ) -> List[str]:
        """Generate recommendations based on selection patterns."""
        recommendations = []

        # Find rarely selected sections
        total_selections = sum(section_counts.values())
        for section, count in section_counts.items():
            if count < total_selections * 0.05:  # Selected less than 5% of the time
                recommendations.append(
                    f"Consider removing '{section}' from default context "
                    f"(only selected {count}/{total_selections} times)"
                )

        # Find agent-specific patterns
        for agent, sections in agent_patterns.items():
            top_section = max(sections.items(), key=lambda x: x[1])[0] if sections else None
            if top_section:
                recommendations.append(
                    f"Agent '{agent}' frequently uses '{top_section}' - "
                    f"consider pre-loading for this agent"
                )

        return recommendations[:5]  # Return top 5 recommendations


# CLI for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Initialize selector
    selector = SmartContextSelector()

    # Available sections (mock)
    available_sections = [
        "project_details",
        "terraform_infrastructure",
        "terraform_state",
        "terraform_modules",
        "cluster_details",
        "namespaces",
        "deployments",
        "services",
        "gitops_configuration",
        "error_logs",
        "metrics",
        "operational_guidelines",
        "cost_analysis",
        "compliance_requirements"
    ]

    print("🧪 Testing Smart Context Selection...\n")

    # Test cases
    test_cases = [
        {
            "task": "Deploy new version of API service to production using terraform",
            "agent": "terraform-architect",
            "tier": "T3"
        },
        {
            "task": "Debug why pods are crashing in the payment namespace",
            "agent": "gitops-operator",
            "tier": "T2"
        },
        {
            "task": "Check the current terraform state",
            "agent": "terraform-architect",
            "tier": "T0"
        },
        {
            "task": "Analyze cost trends for the past month",
            "agent": "devops-developer",
            "tier": "T1"
        }
    ]

    for i, test_case in enumerate(test_cases, 1):
        print(f"Test {i}: {test_case['task'][:50]}...")
        print(f"  Agent: {test_case['agent']}, Tier: {test_case['tier']}")

        selected = selector.select_relevant_sections(
            task=test_case["task"],
            agent=test_case["agent"],
            tier=test_case["tier"],
            available_sections=available_sections,
            max_sections=5
        )

        print(f"  Selected sections:")
        for section, score in selected:
            print(f"    - {section}: {score:.2f}")
        print()

    # Show insights
    print("📊 Selection Insights:")
    insights = selector.get_selection_insights()
    print(f"  Total selections: {insights['total_selections']}")
    print(f"  Most selected: {insights['most_selected_sections']}")
    if insights.get('recommendations'):
        print("  Recommendations:")
        for rec in insights['recommendations']:
            print(f"    - {rec}")