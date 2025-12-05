#!/usr/bin/env python3
"""
Progressive Disclosure Manager
Analyzes user queries and determines optimal context level for agent contracts
"""

import re
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class ProgressiveDisclosureManager:
    """
    Manages progressive disclosure of context based on query analysis.
    Determines how much context to include in agent contracts.
    """

    def __init__(self):
        """Initialize with keyword categories for intent analysis."""

        # Keywords that indicate debugging or troubleshooting
        self.debug_keywords = [
            "error", "failing", "crash", "problema", "problem",
            "arreglar", "fix", "debug", "logs", "troubleshoot",
            "resolver", "solve", "broken", "issue", "fault",
            "exception", "trace", "stack", "diagnose"
        ]

        # Keywords that indicate need for detailed information
        self.detail_keywords = [
            "específicamente", "specifically", "exactamente", "exactly",
            "detalles", "details", "completo", "complete", "full",
            "todo", "all", "comprehensive", "thorough", "verbose",
            "explain", "analyze", "deep", "investigation"
        ]

        # Keywords that indicate simple status queries
        self.simple_keywords = [
            "status", "estado", "cuántos", "how many", "listar", "list",
            "count", "show", "display", "summary", "overview",
            "check", "verify", "quick", "brief"
        ]

        # Keywords that reference previous context
        self.reference_keywords = [
            "esos", "estos", "those", "these", "anterior", "previous",
            "mencionaste", "mentioned", "último", "last", "recent",
            "mismo", "same", "above", "before", "earlier"
        ]

        # Keywords that indicate infrastructure needs
        self.infrastructure_keywords = [
            "database", "postgres", "mysql", "redis", "network",
            "vpc", "subnet", "firewall", "load balancer", "ingress",
            "storage", "bucket", "volume", "disk", "certificate"
        ]

        # Action keywords for task classification
        self.action_keywords = {
            "check_status": ["check", "verify", "status", "state", "health"],
            "get_logs": ["logs", "output", "stdout", "stderr", "trace"],
            "diagnose_error": ["diagnose", "analyze", "investigate", "debug"],
            "fix_issue": ["fix", "repair", "resolve", "correct", "patch"],
            "deploy": ["deploy", "rollout", "release", "apply"],
            "rollback": ["rollback", "revert", "undo", "restore"],
            "validate": ["validate", "test", "verify", "check"],
            "plan": ["plan", "preview", "simulate", "dry-run"]
        }

    def analyze_query_intent(self, query: str) -> Dict[str, Any]:
        """
        Analyze user query to determine intent and required context level.

        Args:
            query: User's query string

        Returns:
            Dictionary with intent analysis results
        """
        query_lower = query.lower()

        # Analyze various aspects of the query
        intent = {
            "query": query,
            "timestamp": datetime.now().isoformat(),

            # Boolean flags for different intents
            "needs_debugging": self._contains_keywords(query_lower, self.debug_keywords),
            "needs_detail": self._contains_keywords(query_lower, self.detail_keywords),
            "is_simple": self._contains_keywords(query_lower, self.simple_keywords),
            "references_previous": self._contains_keywords(query_lower, self.reference_keywords),
            "needs_infrastructure": self._contains_keywords(query_lower, self.infrastructure_keywords),

            # Detected action type
            "action": self._detect_action(query_lower),

            # Complexity scoring
            "complexity_score": 0,

            # Recommended context level (1-4)
            "recommended_level": 1,

            # Extracted entities
            "entities": self._extract_entities(query),

            # Confidence in analysis (0-1)
            "confidence": 0.0
        }

        # Calculate complexity score
        intent["complexity_score"] = self._calculate_complexity(intent)

        # Determine recommended context level
        intent["recommended_level"] = self._determine_context_level(intent["complexity_score"])

        # Calculate confidence
        intent["confidence"] = self._calculate_confidence(intent)

        logger.info(f"Query analysis complete: Level {intent['recommended_level']}, "
                   f"Complexity {intent['complexity_score']}, "
                   f"Confidence {intent['confidence']:.2f}")

        return intent

    def _contains_keywords(self, text: str, keywords: List[str]) -> bool:
        """Check if text contains any of the keywords."""
        return any(keyword in text for keyword in keywords)

    def _detect_action(self, query_lower: str) -> str:
        """Detect the primary action type from the query."""
        for action, keywords in self.action_keywords.items():
            if self._contains_keywords(query_lower, keywords):
                return action
        return "custom"

    def _extract_entities(self, query: str) -> Dict[str, List[str]]:
        """Extract named entities from the query."""
        entities = {
            "namespaces": [],
            "resources": [],
            "services": []
        }

        # Extract namespace patterns
        namespace_pattern = r'namespace[s]?\s+(\S+)|(\S+)\s+namespace'
        namespace_matches = re.findall(namespace_pattern, query, re.IGNORECASE)
        for match in namespace_matches:
            ns = match[0] or match[1]
            if ns and ns not in entities["namespaces"]:
                entities["namespaces"].append(ns)

        # Extract common resource names (pods, deployments, etc.)
        resource_pattern = r'\b(pod|deployment|service|configmap|secret|ingress|job)s?\s+(\S+)'
        resource_matches = re.findall(resource_pattern, query, re.IGNORECASE)
        for resource_type, resource_name in resource_matches:
            if resource_name and resource_name not in entities["resources"]:
                entities["resources"].append(f"{resource_type}/{resource_name}")

        # Extract service/app names (common patterns)
        service_pattern = r'\b(tcm-\w+|app-\w+|service-\w+|\w+-api|\w+-web|\w+-worker)\b'
        service_matches = re.findall(service_pattern, query, re.IGNORECASE)
        entities["services"] = list(set(service_matches))

        return entities

    def _calculate_complexity(self, intent: Dict[str, Any]) -> int:
        """Calculate complexity score based on intent flags."""
        score = 0

        # Add points based on different factors
        if intent["needs_debugging"]:
            score += 3
        if intent["needs_detail"]:
            score += 2
        if intent["references_previous"]:
            score += 2
        if intent["needs_infrastructure"]:
            score += 2
        if not intent["is_simple"]:
            score += 1

        # Add complexity based on entities
        total_entities = sum(len(v) for v in intent["entities"].values())
        if total_entities > 3:
            score += 2
        elif total_entities > 0:
            score += 1

        return min(score, 10)  # Cap at 10

    def _determine_context_level(self, complexity_score: int) -> int:
        """
        Determine recommended context level based on complexity score.

        Levels:
        1 - Minimal context (basic info only)
        2 - Standard context (includes key facts)
        3 - Detailed context (includes errors and recent actions)
        4 - Full context (complete history and all details)
        """
        if complexity_score >= 7:
            return 4  # Full context
        elif complexity_score >= 5:
            return 3  # Detailed context
        elif complexity_score >= 2:
            return 2  # Standard context
        else:
            return 1  # Minimal context

    def _calculate_confidence(self, intent: Dict[str, Any]) -> float:
        """Calculate confidence in the analysis."""
        confidence = 0.5  # Base confidence

        # Increase confidence based on clear signals
        if intent["action"] != "custom":
            confidence += 0.2

        if intent["entities"]["namespaces"] or intent["entities"]["resources"]:
            confidence += 0.15

        # Clear intent signals increase confidence
        clear_intents = sum([
            intent["needs_debugging"],
            intent["is_simple"],
            intent["references_previous"]
        ])
        confidence += clear_intents * 0.1

        return min(confidence, 1.0)

    def build_progressive_context(self,
                                 level: int,
                                 project_context: Dict[str, Any],
                                 agent_responses: List[Dict[str, Any]],
                                 conversation_state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Build context progressively based on the determined level.

        Args:
            level: Context level (1-4)
            project_context: Static project information
            agent_responses: Previous agent responses
            conversation_state: Current conversation state

        Returns:
            Dictionary with appropriate context for the level
        """
        context = {}

        # Level 1: Minimal context
        if level >= 1:
            context["basic"] = {
                "cluster": project_context.get("cluster", {}).get("name"),
                "region": project_context.get("cluster", {}).get("region"),
                "project_id": project_context.get("project_details", {}).get("project_id")
            }

            # Add basic metrics if available
            if agent_responses and agent_responses[-1].get("findings", {}).get("metrics"):
                context["metrics"] = agent_responses[-1]["findings"]["metrics"]

        # Level 2: Standard context (add key facts)
        if level >= 2:
            # Add resource states from recent responses
            if agent_responses:
                recent_response = agent_responses[-1]
                if "findings" in recent_response:
                    if "resources" in recent_response["findings"]:
                        context["resources"] = recent_response["findings"]["resources"][:10]

                    # Add summary of errors without details
                    if "errors" in recent_response["findings"]:
                        context["error_summary"] = [
                            {"type": e.get("type"), "count": 1}
                            for e in recent_response["findings"]["errors"][:5]
                        ]

        # Level 3: Detailed context (add errors and recent actions)
        if level >= 3:
            if agent_responses:
                recent_response = agent_responses[-1]

                # Include full error details
                if "findings" in recent_response and "errors" in recent_response["findings"]:
                    context["errors"] = recent_response["findings"]["errors"]

                # Include recent actions performed
                if "actions" in recent_response and "performed" in recent_response["actions"]:
                    context["recent_actions"] = recent_response["actions"]["performed"][-5:]

                # Include analysis if available
                if "findings" in recent_response and "analysis" in recent_response["findings"]:
                    context["analysis"] = recent_response["findings"]["analysis"]

        # Level 4: Full context (complete history)
        if level >= 4:
            # Include full infrastructure details
            context["infrastructure"] = {
                "databases": project_context.get("databases", {}),
                "networking": project_context.get("networking", {}),
                "storage": project_context.get("storage", {})
            }

            # Include last 3 agent responses
            if agent_responses:
                context["response_history"] = agent_responses[-3:]

            # Include full conversation state if available
            if conversation_state:
                context["conversation_state"] = conversation_state

        # Add metadata about the context
        context["_metadata"] = {
            "level": level,
            "timestamp": datetime.now().isoformat(),
            "token_estimate": self._estimate_tokens(context)
        }

        logger.info(f"Built progressive context: Level {level}, "
                   f"Estimated tokens: {context['_metadata']['token_estimate']}")

        return context

    def _estimate_tokens(self, data: Any) -> int:
        """Estimate token count for the context data."""
        # Rough estimation: 1 token per 4 characters
        json_str = str(data)
        return len(json_str) // 4

    def optimize_for_continuation(self,
                                 current_query: str,
                                 previous_query: str,
                                 previous_response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Optimize context for conversation continuation.

        Args:
            current_query: Current user query
            previous_query: Previous user query
            previous_response: Previous agent response

        Returns:
            Optimized context focusing on relevant continuations
        """
        current_intent = self.analyze_query_intent(current_query)

        # Check if queries are related
        is_continuation = (
            current_intent["references_previous"] or
            self._queries_are_related(current_query, previous_query)
        )

        optimization = {
            "is_continuation": is_continuation,
            "reuse_previous": False,
            "focus_areas": []
        }

        if is_continuation:
            # Determine what to carry forward
            if "resources" in previous_response.get("findings", {}):
                optimization["carry_forward"] = {
                    "resources": previous_response["findings"]["resources"]
                }

            if "errors" in previous_response.get("findings", {}) and current_intent["needs_debugging"]:
                optimization["carry_forward"]["errors"] = previous_response["findings"]["errors"]

            # Identify focus areas
            if current_intent["action"] == "get_logs" and "resources" in optimization.get("carry_forward", {}):
                optimization["focus_areas"].append("logs_for_specific_resources")
            elif current_intent["action"] == "fix_issue" and "errors" in optimization.get("carry_forward", {}):
                optimization["focus_areas"].append("remediation_for_errors")

            optimization["reuse_previous"] = True

        return optimization

    def _queries_are_related(self, query1: str, query2: str) -> bool:
        """Determine if two queries are related."""
        # Extract entities from both queries
        entities1 = self._extract_entities(query1)
        entities2 = self._extract_entities(query2)

        # Check for common entities
        for key in entities1:
            if entities1[key] and entities2[key]:
                common = set(entities1[key]) & set(entities2[key])
                if common:
                    return True

        # Check for semantic similarity (simplified)
        words1 = set(query1.lower().split())
        words2 = set(query2.lower().split())

        # Remove common stop words
        stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for'}
        words1 -= stop_words
        words2 -= stop_words

        # Calculate overlap
        if words1 and words2:
            overlap = len(words1 & words2) / min(len(words1), len(words2))
            return overlap > 0.3

        return False


# Example usage and testing
if __name__ == "__main__":
    # Set up logging
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

    # Create manager
    manager = ProgressiveDisclosureManager()

    # Test queries
    test_queries = [
        "check the status of pods",  # Simple - Level 1
        "show me the logs of those pods",  # Reference - Level 2/3
        "debug the database connection error",  # Debug - Level 3/4
        "give me all details about the crashed pods and how to fix them"  # Complex - Level 4
    ]

    print("Progressive Disclosure Analysis")
    print("=" * 60)

    for query in test_queries:
        print(f"\nQuery: '{query}'")
        intent = manager.analyze_query_intent(query)
        print(f"  Action: {intent['action']}")
        print(f"  Complexity: {intent['complexity_score']}")
        print(f"  Recommended Level: {intent['recommended_level']}")
        print(f"  Confidence: {intent['confidence']:.2%}")
        print(f"  Flags: Debug={intent['needs_debugging']}, "
              f"Detail={intent['needs_detail']}, "
              f"Simple={intent['is_simple']}, "
              f"Reference={intent['references_previous']}")
        if intent['entities']['namespaces'] or intent['entities']['resources']:
            print(f"  Entities: {intent['entities']}")

    print("\n" + "=" * 60)
    print("Testing complete!")