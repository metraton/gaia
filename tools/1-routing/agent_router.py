#!/usr/bin/env python3
"""
Unified Agent Router for Claude Agent System

- If the request references a Spec-Kit task (Txxx or TASK-xxx), the router reads
  the task metadata and routes to the agent declared there.
- Otherwise it falls back to keyword/pattern scoring.

This script replaces the previous split between TaskAnalyzer routing and the
keyword-only router.
"""

import argparse
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Week 2: Try to import SemanticMatcher (optional - graceful degradation if not available)
try:
    from semantic_matcher import SemanticMatcher
    SEMANTIC_MATCHER_AVAILABLE = True
except ImportError:
    SEMANTIC_MATCHER_AVAILABLE = False
    logger.debug("SemanticMatcher not available (embeddings not generated yet)")


# ============================================================================
# WEEK 1: Enhanced Semantic Keywords - IntentClassifier
# ============================================================================

class IntentClassifier:
    """Classify request intent using semantic keywords (Week 1 addition)"""

    def __init__(self):
        self.intent_keywords = {
            "infrastructure_creation": {
                "include": [
                    "create", "provision", "deploy", "setup", "build",
                    "infrastructure", "infra", "resources", "services",
                    "cluster", "network", "vpc", "database", "instance"
                ],
                "exclude": ["diagnose", "troubleshoot", "debug", "check", "analyze"],
                "confidence_boost": 0.85
            },
            "infrastructure_diagnosis": {
                "include": [
                    "diagnose", "troubleshoot", "debug", "check", "analyze",
                    "problem", "issue", "error", "failing", "broken",
                    "crash", "latency", "timeout", "connectivity"
                ],
                "exclude": ["create", "provision", "deploy", "setup"],
                "confidence_boost": 0.88
            },
            "kubernetes_operations": {
                "include": [
                    "pod", "deployment", "service", "namespace", "helm",
                    "flux", "kubectl", "verify", "status", "logs",
                    "scale", "restart", "update"
                ],
                "exclude": ["create infrastructure", "provision"],
                "confidence_boost": 0.82
            },
            "application_development": {
                "include": [
                    "build", "docker", "compile", "test", "run",
                    "npm", "python", "node", "typescript", "lint",
                    "unit test", "integration test"
                ],
                "exclude": ["infrastructure"],
                "confidence_boost": 0.80
            },
            "infrastructure_validation": {
                "include": [
                    "validate", "check", "verify", "scan", "lint",
                    "terraform", "hcl", "syntax", "security", "plan"
                ],
                "exclude": ["deploy", "apply", "execute"],
                "confidence_boost": 0.86
            }
        }

    def classify(self, request: str) -> Tuple[Optional[str], float]:
        """
        Classify intent using semantic keyword matching

        Returns:
            (intent_name, confidence_score)
        """
        request_lower = request.lower()
        scores = {}

        for intent, keywords in self.intent_keywords.items():
            score = 0.0

            # Count matching include keywords
            include_matches = sum(
                1 for kw in keywords["include"]
                if kw in request_lower
            )

            # Check exclusion keywords (veto)
            exclude_matches = sum(
                1 for kw in keywords["exclude"]
                if kw in request_lower
            )

            if exclude_matches > 0:
                score = -1.0  # Veto this intent
            else:
                score = include_matches * keywords["confidence_boost"]

            scores[intent] = score

        # Find best intent (exclude vetoed)
        valid_scores = {k: v for k, v in scores.items() if v >= 0}

        if not valid_scores:
            return None, 0.0

        best_intent = max(valid_scores, key=valid_scores.get)
        # Normalize to 0-1 range (adjust divisor based on typical scores)
        # Most requests have 1-5 keyword matches, so we normalize with factor of 5
        raw_score = valid_scores[best_intent]
        confidence = min(raw_score / 5, 1.0)

        return best_intent, confidence


class CapabilityValidator:
    """Validate that selected agent has required capabilities (Week 1 addition)"""

    def __init__(self):
        self.agent_capabilities = {
            "terraform-architect": {
                "can_do": ["infrastructure_creation", "infrastructure_validation"],
                "cannot_do": ["kubernetes_operations", "infrastructure_diagnosis"],
                "requires_context": ["infrastructure", "iac"]
            },
            "gitops-operator": {
                "can_do": ["kubernetes_operations", "infrastructure_diagnosis"],
                "cannot_do": ["infrastructure_creation", "application_development"],
                "requires_context": ["kubernetes", "gitops"]
            },
            "gcp-troubleshooter": {
                "can_do": ["infrastructure_diagnosis"],
                "cannot_do": ["infrastructure_creation", "application_development"],
                "requires_context": ["gcp", "monitoring"]
            },
            "devops-developer": {
                "can_do": ["application_development", "infrastructure_validation"],
                "cannot_do": ["kubernetes_operations", "infrastructure_creation"],
                "requires_context": ["application", "development"]
            }
        }

    def validate(self, agent: str, intent: str) -> bool:
        """Check if agent can handle the intent"""
        if agent not in self.agent_capabilities:
            return False

        capabilities = self.agent_capabilities[agent]

        # Can't do list is hard veto
        if intent in capabilities["cannot_do"]:
            return False

        # Can do list is preferred
        return intent in capabilities["can_do"]

    def find_fallback_agent(self, intent: str, exclude: str = None) -> str:
        """Find alternative agent if primary fails"""
        for agent, capabilities in self.agent_capabilities.items():
            if agent == exclude:
                continue
            if intent in capabilities["can_do"]:
                return agent

        return "devops-developer"  # Ultimate fallback

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_TASK_FILE = ROOT_DIR / "spec-kit-tcm-plan" / "specs" / "001-tcm-deployment-plan" / "tasks.md"


@dataclass
class RoutingRule:
    """Defines routing rules for an agent"""
    agent: str
    keywords: List[str]
    patterns: List[str]
    description: str


class AgentRouter:
    """Unified router that prefers task metadata and falls back to keywords."""

    def __init__(self, task_file: Optional[Path] = None):
        if task_file:
            candidate = Path(task_file)
        else:
            candidate = DEFAULT_TASK_FILE

        if candidate and candidate.exists():
            self.task_file: Optional[Path] = candidate
        else:
            self.task_file = None

        self.tasks_metadata = self._load_tasks_metadata()
        self.routing_rules = self._load_routing_rules()

        # Week 1 additions: Semantic routing components
        self.intent_classifier = IntentClassifier()
        self.capability_validator = CapabilityValidator()

        # Week 2 additions: Semantic matcher (with embeddings)
        self.semantic_matcher = None
        if SEMANTIC_MATCHER_AVAILABLE:
            try:
                self.semantic_matcher = SemanticMatcher()
            except Exception as e:
                logger.warning(f"Could not initialize SemanticMatcher: {e}")
                self.semantic_matcher = None

    def _get_agent_for_intent(self, intent: str) -> str:
        """Map intent to primary agent (Week 1 addition)"""
        intent_to_agent = {
            "infrastructure_creation": "terraform-architect",
            "infrastructure_diagnosis": "gcp-troubleshooter",
            "kubernetes_operations": "gitops-operator",
            "application_development": "devops-developer",
            "infrastructure_validation": "terraform-architect"
        }

        return intent_to_agent.get(intent, "devops-developer")

    def _load_routing_rules(self) -> Dict[str, RoutingRule]:
        """Load routing rules for all agents"""
        return {
            "gitops-operator": RoutingRule(
                agent="gitops-operator",
                keywords=[
                    "pod", "pods", "deployment", "deployments",
                    "service", "services", "flux", "helmrelease",
                    "kubernetes", "k8s", "namespace", "namespaces",
                    "ingress", "configmap", "secret",
                    "reconcile", "kustomization"
                ],
                patterns=[
                    r"check.*pod[s]?",
                    r"validate.*deployment",
                    r"flux.*status",
                    r"helmrelease",
                    r"kubernetes.*health",
                    r"k8s.*status"
                ],
                description="Flux/Kubernetes operations (read-only)"
            ),

            "gcp-troubleshooter": RoutingRule(
                agent="gcp-troubleshooter",
                keywords=[
                    "gke", "gcp", "google cloud",
                    "cloud sql", "cloudsql", "postgres", "postgresql",
                    "redis", "memorystore",
                    "iam", "workload identity", "service account",
                    "networking", "vpc", "firewall",
                    "artifact registry", "gcr",
                    "load balancer", "ip address"
                ],
                patterns=[
                    r"gke.*cluster",
                    r"cloud\s*sql",
                    r"iam.*binding",
                    r"workload.*identity",
                    r"gcp.*diagnose",
                    r"google.*cloud"
                ],
                description="GCP/GKE/IAM diagnostics"
            ),

            "terraform-architect": RoutingRule(
                agent="terraform-architect",
                keywords=[
                    "terraform", "terragrunt",
                    "infrastructure", "iac", "infrastructure as code",
                    "tfstate", "state file",
                    "module", "modules",
                    "plan", "validate", "fmt"
                ],
                patterns=[
                    r"terraform.*validate",
                    r"terraform.*plan",
                    r"terragrunt.*plan",
                    r"infrastructure.*code",
                    r"iac.*validation"
                ],
                description="Terraform/Terragrunt validation (T0-T2)"
            ),

            "devops-developer": RoutingRule(
                agent="devops-developer",
                keywords=[
                    "build", "docker", "container", "image",
                    "npm", "yarn", "pnpm", "turborepo",
                    "test", "tests", "testing",
                    "ci", "cd", "pipeline", "github actions",
                    "lint", "prettier", "eslint",
                    "package", "dependencies"
                ],
                patterns=[
                    r"build.*image",
                    r"docker.*build",
                    r"run.*tests",
                    r"npm.*install",
                    r"turborepo",
                    r"ci.*cd",
                    r"github.*action"
                ],
                description="Application development and CI/CD"
            ),
        }

    def _load_tasks_metadata(self) -> Dict[str, Dict[str, Any]]:
        """Parse tasks.md metadata to map task IDs to suggested agents."""
        metadata: Dict[str, Dict[str, Any]] = {}

        if not self.task_file:
            return metadata

        try:
            lines = self.task_file.read_text(encoding="utf-8").splitlines()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Could not read task metadata file %s: %s", self.task_file, exc)
            return metadata

        current_id: Optional[str] = None

        for line in lines:
            stripped = line.strip()
            id_match = re.match(r"- \[[ xX]\] ((?:TASK-)?T\d+)", stripped)
            if id_match:
                raw_id = id_match.group(1).upper()
                norm_id = f"T{raw_id.split('-', 1)[1]}" if raw_id.startswith("TASK-") else raw_id
                metadata[norm_id] = {
                    "raw_id": raw_id,
                    "agent": None,
                    "tier": None,
                    "confidence": None,
                }
                current_id = norm_id
                continue

            if current_id and stripped.startswith("<!--") and "Agent:" in stripped:
                comment = stripped.lstrip("<!- ").rstrip("-> ").strip()
                parts = [part.strip() for part in comment.split("|")]

                for part in parts:
                    if "Agent:" in part:
                        metadata[current_id]["agent"] = part.split("Agent:", 1)[1].strip()

                    tier_match = re.search(r"T[0-3]", part)
                    if tier_match:
                        metadata[current_id]["tier"] = tier_match.group(0)

                    # Parse numeric confidence (e.g. 0.90) if present
                    conf_match = re.search(r"([0-9]+(?:\.[0-9]+)?)", part)
                    if (
                        conf_match
                        and "Agent" not in part
                        and not tier_match
                    ):
                        try:
                            metadata[current_id]["confidence"] = float(conf_match.group(1))
                        except ValueError:
                            pass

        # Drop entries without agent info
        return {task_id: meta for task_id, meta in metadata.items() if meta.get("agent")}

    def _route_by_task(self, user_request: str) -> Optional[Dict[str, Any]]:
        """Return routing decision based on task metadata if present."""
        if not self.tasks_metadata:
            return None

        matches = re.findall(r"(?:TASK-)?T\d+", user_request, flags=re.IGNORECASE)
        for raw in matches:
            raw_upper = raw.upper()
            norm = f"T{raw_upper.split('-', 1)[1]}" if raw_upper.startswith("TASK-") else raw_upper
            meta = self.tasks_metadata.get(norm)
            if not meta:
                continue
            agent = meta.get("agent")
            if not agent:
                continue

            base_conf = meta.get("confidence")
            confidence_score = 100 if base_conf is None else max(10, int(round(base_conf * 10)))

            reason_parts = [f"task {meta.get('raw_id', norm)} metadata"]
            if meta.get("tier"):
                reason_parts.append(f"tier {meta['tier']}")
            if base_conf is not None:
                reason_parts.append(f"confidence {base_conf:.2f}")

            return {
                "agent": agent,
                "confidence": confidence_score,
                "reason": ", ".join(reason_parts),
                "metadata": meta,
                "task_id": meta.get("raw_id", norm),
            }

        return None

    def _calculate_keyword_scores(self, request: str) -> Dict[str, float]:
        """
        Calculate keyword scores for all intents (Week 2 addition)

        Used for embedding-enhanced routing
        """
        request_lower = request.lower()
        scores = {}

        for intent, keywords in self.intent_classifier.intent_keywords.items():
            score = 0.0

            # Count matching include keywords
            include_matches = sum(
                1 for kw in keywords["include"]
                if kw in request_lower
            )

            # Check exclusion keywords (veto)
            exclude_matches = sum(
                1 for kw in keywords["exclude"]
                if kw in request_lower
            )

            if exclude_matches > 0:
                score = -1.0  # Veto this intent
            else:
                score = include_matches * keywords["confidence_boost"]

            scores[intent] = score

        # Filter out negative scores
        valid_scores = {k: v for k, v in scores.items() if v >= 0}
        return valid_scores if valid_scores else {}

    def _route_semantic(self, user_request: str) -> Tuple[str, float, str]:
        """
        Route using semantic keywords and capability validation
        Enhanced with embeddings if available (Week 1-2 additions)

        Returns:
            (agent, confidence, reasoning)
        """
        # Step 1: Classify intent using semantic keywords
        intent, confidence = self.intent_classifier.classify(user_request)

        # Week 2: Try to enhance with embedding-based matching
        if self.semantic_matcher and self.semantic_matcher.is_available():
            try:
                # Get keyword scores for embedding matcher
                keyword_scores = self._calculate_keyword_scores(user_request)

                if keyword_scores:
                    # Use embedding-enhanced similarity
                    enhanced_intent, enhanced_conf = self.semantic_matcher.find_similar_intent(
                        user_request,
                        keyword_scores
                    )

                    if enhanced_intent is not None:
                        intent = enhanced_intent
                        confidence = enhanced_conf

                        logger.debug(f"Embedding-enhanced routing: {intent} ({confidence:.3f})")
            except Exception as e:
                logger.warning(f"Embedding enhancement failed, using semantic keywords: {e}")
                # Continue with non-enhanced confidence

        if intent is None:
            # Fallback to keyword routing
            agent, kw_conf, reason = self.suggest_agent(user_request, verbose=False)
            return agent, kw_conf / 100, f"Semantic: no intent match, used keyword fallback: {reason}"

        # Step 2: Select agent for intent
        agent = self._get_agent_for_intent(intent)

        # Step 3: Validate agent has capability
        if not self.capability_validator.validate(agent, intent):
            fallback_agent = self.capability_validator.find_fallback_agent(intent, exclude=agent)
            confidence *= 0.8  # Lower confidence for fallback
            agent = fallback_agent

        reasoning = f"Semantic: {intent} (conf: {confidence:.2f}) → {agent}"
        return agent, confidence, reasoning

    def suggest_agent(self, user_request: str, verbose: bool = False) -> Tuple[str, int, str]:
        """
        Suggest agent based on keywords and patterns in user request

        Args:
            user_request: User's request text
            verbose: If True, log scoring details

        Returns:
            Tuple of (agent_name, confidence_score, reason)
        """
        task_result = self._route_by_task(user_request)
        if task_result:
            if verbose:
                logger.info(
                    "Routing via task metadata: %s -> %s",
                    task_result["task_id"],
                    task_result["agent"],
                )
            return (
                task_result["agent"],
                task_result["confidence"],
                task_result["reason"],
            )

        # ACTIVATED: Try semantic routing first (Week 1-3 feature flag)
        if self.semantic_matcher and self.semantic_matcher.is_available():
            try:
                agent, sem_conf, reasoning = self._route_semantic(user_request)
                if verbose:
                    logger.info(f"Semantic routing: {agent} (confidence: {sem_conf:.2f})")
                # Convert float confidence to int (0-100 scale) for compatibility
                return agent, int(sem_conf * 100), f"semantic | {reasoning}"
            except Exception as e:
                logger.debug(f"Semantic routing failed, fallback to keyword: {e}")
                # Fall through to keyword routing below

        request_lower = user_request.lower()
        scores = {}
        reasons = {}

        for agent_name, rule in self.routing_rules.items():
            score = 0
            matched_keywords = []
            matched_patterns = []

            # Check keywords (1 point each)
            for keyword in rule.keywords:
                if keyword in request_lower:
                    score += 1
                    matched_keywords.append(keyword)

            # Check patterns (2 points each - higher weight)
            for pattern in rule.patterns:
                if re.search(pattern, request_lower, re.IGNORECASE):
                    score += 2
                    matched_patterns.append(pattern)

            if score > 0:
                scores[agent_name] = score
                reason_parts = []
                if matched_keywords:
                    reason_parts.append(f"keywords: {', '.join(matched_keywords[:3])}")
                if matched_patterns:
                    reason_parts.append(f"patterns: {len(matched_patterns)} matched")
                reasons[agent_name] = " | ".join(reason_parts)

        if not scores:
            # No matches - use fallback
            fallback = "devops-developer"
            if verbose:
                logger.info(f"No keyword matches, using fallback: {fallback}")
            return fallback, 0, "fallback (no keyword matches)"

        # Return highest scoring agent
        best_agent = max(scores, key=scores.get)
        confidence = scores[best_agent]
        reason = reasons[best_agent]

        if verbose:
            logger.info(f"Agent routing scores: {scores}")
            logger.info(f"Selected: {best_agent} (confidence: {confidence})")

        return best_agent, confidence, reason

    def get_agent_description(self, agent_name: str) -> Optional[str]:
        """Get description of an agent's responsibilities"""
        rule = self.routing_rules.get(agent_name)
        return rule.description if rule else None

    def list_agents(self) -> List[str]:
        """List all available agents"""
        return list(self.routing_rules.keys())

    def explain_routing(self, user_request: str) -> str:
        """Provide detailed explanation of routing decision."""
        task_result = self._route_by_task(user_request)
        lines = [
            f'Request: "{user_request}"',
            "",
        ]

        if task_result:
            meta = task_result["metadata"]
            lines.append(f"Suggested Agent: {task_result['agent']} (confidence: {task_result['confidence']})")
            lines.append(f"Reason: {task_result['reason']}")
            lines.append("")
            lines.append("Task Metadata:")
            lines.append(f"  Task ID: {task_result['task_id']}")
            if meta.get("tier"):
                lines.append(f"  Tier: {meta['tier']}")
            if meta.get("confidence") is not None:
                lines.append(f"  Confidence: {meta['confidence']:.2f}")
            return "\n".join(lines)

        # No task metadata match -> fall back to keyword scoring
        agent, confidence, reason = self.suggest_agent(user_request, verbose=False)
        request_lower = user_request.lower()

        lines.extend([
            f"Suggested Agent: {agent} (confidence: {confidence})",
            f"Reason: {reason}",
            "",
            "Detailed Scoring:",
        ])

        for agent_name, rule in self.routing_rules.items():
            matched_keywords = [kw for kw in rule.keywords if kw in request_lower]
            matched_patterns = [
                pattern for pattern in rule.patterns if re.search(pattern, request_lower, re.IGNORECASE)
            ]
            score = len(matched_keywords) + (len(matched_patterns) * 2)

            if score > 0:
                lines.append(f"  {agent_name}: {score} points")
                if matched_keywords:
                    lines.append(f"    - Keywords: {', '.join(matched_keywords[:5])}")
                if matched_patterns:
                    lines.append(f"    - Patterns: {len(matched_patterns)} matched")

        return "\n".join(lines)

def main():
    """CLI interface for testing agent router."""
    parser = argparse.ArgumentParser(description="Unified agent router for Claude Code.")
    parser.add_argument("--task-file", help="Path to tasks.md metadata file (optional).")
    parser.add_argument("--test", action="store_true", help="Run router smoke tests.")
    parser.add_argument("--semantic", action="store_true", help="Use semantic routing (Week 1 addition).")
    parser.add_argument("--explain", metavar="REQUEST", help="Explain routing decision for a request.")
    parser.add_argument("--json", action="store_true", help="Output result as JSON (for programmatic parsing).")
    parser.add_argument("request", nargs="*", help="Free-form request to route.")
    args = parser.parse_args()

    task_file = Path(args.task_file) if args.task_file else None
    router = AgentRouter(task_file=task_file)

    if args.test:
        test_cases = [
            "Check pods in default namespace",
            "Validate terraform configuration",
            "Build docker image for API",
            "Diagnose GKE cluster connectivity",
            "Run tests for web application",
            "Check flux reconciliation status",
            "Review Cloud SQL IAM bindings",
            "Plan infrastructure changes with terragrunt",
        ]

        if router.tasks_metadata:
            sample_task = next(iter(router.tasks_metadata.keys()))
            test_cases.insert(0, f"Work on {sample_task}")

        print("Running agent router smoke tests...\n")

        for request in test_cases:
            agent, confidence, reason = router.suggest_agent(request, verbose=False)
            print(f'Request: "{request}"')
            print(f"  -> Agent: {agent} (confidence: {confidence})")
            print(f"  -> Reason: {reason}")
            print()
        return

    if args.explain:
        print(router.explain_routing(args.explain))
        return

    if not args.request:
        parser.error("Please provide a request, --test, or --explain.")

    request = " ".join(args.request)

    # Week 1 addition: Use semantic routing if flag is set
    if args.semantic:
        agent, confidence, reason = router._route_semantic(request)
        # Normalize confidence to 0-100 for consistency with suggest_agent
        confidence_normalized = int(confidence * 100)
    else:
        agent, confidence_normalized, reason = router.suggest_agent(request, verbose=not args.json)
        confidence = confidence_normalized / 100

    # JSON output for programmatic parsing
    if args.json:
        result = {
            "agent": agent,
            "confidence": confidence_normalized,
            "reason": reason,
            "semantic": args.semantic,
            "description": router.get_agent_description(agent),
        }
        print(json.dumps(result, indent=2))
        return

    # Human-readable text output
    print()
    routing_method = "Semantic" if args.semantic else "Keyword"
    print(f"Suggested Agent: {agent} ({routing_method})")
    print(f"Confidence: {confidence_normalized}")
    print(f"Reason: {reason}")
    print()
    description = router.get_agent_description(agent)
    if description:
        print(f"Agent Description: {description}")


if __name__ == "__main__":
    main()
