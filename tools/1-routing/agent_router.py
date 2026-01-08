#!/usr/bin/env python3
"""
Unified Agent Router for Claude Agent System

Routing Priority:
1. Task Metadata (if Txxx in request) - highest priority
2. LLM Classification (via llm_classifier.py) - primary method
3. Keyword Fallback (if LLM unavailable) - backup

This simplified router replaces the previous keyword/embedding system
with a single LLM call for more accurate intent classification.
"""

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================================
# LLM CLASSIFIER IMPORT
# ============================================================================

try:
    from llm_classifier import classify_intent, classify_intent_mock, AGENT_DEFINITIONS
    LLM_CLASSIFIER_AVAILABLE = True
except ImportError:
    LLM_CLASSIFIER_AVAILABLE = False
    logger.warning("llm_classifier not available, using keyword fallback only")
    AGENT_DEFINITIONS = {}


# ============================================================================
# TASK FILE RESOLUTION (unchanged from original)
# ============================================================================

def _resolve_task_file_from_env() -> Optional[Path]:
    """Resolve task file ONLY from explicit environment variable."""
    if env_tasks_file := os.environ.get("GAIA_TASKS_FILE"):
        candidate = Path(env_tasks_file)
        if candidate.exists():
            logger.debug(f"Using task file from GAIA_TASKS_FILE: {candidate}")
            return candidate
        else:
            logger.warning(f"GAIA_TASKS_FILE specified but not found: {candidate}")
    return None


DEFAULT_TASK_FILE = _resolve_task_file_from_env()


# ============================================================================
# ROUTING RULES (for fallback when LLM unavailable)
# ============================================================================

@dataclass
class RoutingRule:
    """Defines routing rules for an agent"""
    agent: str
    keywords: List[str]
    patterns: List[str]
    description: str


# Simplified routing rules for fallback
ROUTING_RULES: Dict[str, RoutingRule] = {
    "gitops-operator": RoutingRule(
        agent="gitops-operator",
        keywords=["pod", "deployment", "service", "flux", "kubernetes", "k8s", "namespace", "helm", "kubectl"],
        patterns=[r"check.*pod", r"deploy.*to", r"flux.*status", r"kubectl"],
        description="Kubernetes/GitOps operations"
    ),
    "cloud-troubleshooter": RoutingRule(
        agent="cloud-troubleshooter",
        keywords=["gke", "gcp", "google cloud", "cloudsql", "eks", "aws", "amazon", "rds", "ec2", "diagnose", "troubleshoot"],
        patterns=[r"gke.*cluster", r"cloud\s*sql", r"gcp.*diagnose", r"eks.*cluster", r"aws.*diagnose"],
        description="Cloud diagnostics (GCP and AWS)"
    ),
    "terraform-architect": RoutingRule(
        agent="terraform-architect",
        keywords=["terraform", "terragrunt", "infrastructure", "iac", "plan", "apply"],
        patterns=[r"terraform.*validate", r"terraform.*plan", r"terragrunt"],
        description="Terraform/Terragrunt operations"
    ),
    "devops-developer": RoutingRule(
        agent="devops-developer",
        keywords=["build", "docker", "test", "npm", "python", "git", "ci", "cd"],
        patterns=[r"build.*image", r"run.*tests", r"npm.*install"],
        description="Application development and CI/CD"
    ),
    "speckit-planner": RoutingRule(
        agent="speckit-planner",
        keywords=["spec-kit", "speckit", "specification", "feature", "plan", "tasks"],
        patterns=[r"create.*spec", r"plan.*feature", r"generate.*tasks"],
        description="Feature specification and planning"
    ),
}


# ============================================================================
# AGENT ROUTER CLASS
# ============================================================================

class AgentRouter:
    """
    Unified router that prefers task metadata, then LLM, then keywords.
    
    Routing Priority:
    1. Task metadata (if Txxx found in request)
    2. LLM classification (via llm_classifier.py)
    3. Keyword fallback (if LLM unavailable)
    """

    def __init__(self, task_file: Optional[Path] = None):
        """Initialize router with optional task file."""
        if task_file:
            candidate = Path(task_file)
        else:
            candidate = DEFAULT_TASK_FILE

        if candidate and candidate.exists():
            self.task_file: Optional[Path] = candidate
        else:
            self.task_file = None

        self.tasks_metadata = self._load_tasks_metadata()
        self.routing_rules = ROUTING_RULES

    def _load_tasks_metadata(self) -> Dict[str, Dict[str, Any]]:
        """Parse tasks.md metadata to map task IDs to suggested agents."""
        metadata: Dict[str, Dict[str, Any]] = {}

        if not self.task_file:
            return metadata

        try:
            lines = self.task_file.read_text(encoding="utf-8").splitlines()
        except Exception as exc:
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
                    conf_match = re.search(r"([0-9]+(?:\.[0-9]+)?)", part)
                    if conf_match and "Agent" not in part and not tier_match:
                        try:
                            metadata[current_id]["confidence"] = float(conf_match.group(1))
                        except ValueError:
                            pass

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
            confidence_score = 100 if base_conf is None else int(round(base_conf * 100))

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

    def _route_by_llm(self, user_request: str) -> Optional[Tuple[str, int, str]]:
        """Route using LLM classifier."""
        if not LLM_CLASSIFIER_AVAILABLE:
            return None

        try:
            result = classify_intent(user_request)
            agent = result["agent"]
            confidence = int(result["confidence"] * 100)
            reasoning = result["reasoning"]
            
            logger.debug(f"LLM routing: {agent} (confidence: {confidence}%)")
            return agent, confidence, f"llm | {reasoning}"
            
        except Exception as e:
            logger.warning(f"LLM routing failed: {e}")
            return None

    def _route_by_keywords(self, user_request: str) -> Tuple[str, int, str]:
        """Fallback routing using keyword matching."""
        request_lower = user_request.lower()
        scores: Dict[str, int] = {}
        reasons: Dict[str, str] = {}

        for agent_name, rule in self.routing_rules.items():
            score = 0
            matched = []

            # Check keywords
            for keyword in rule.keywords:
                if keyword in request_lower:
                    score += 1
                    matched.append(keyword)

            # Check patterns (higher weight)
            for pattern in rule.patterns:
                if re.search(pattern, request_lower, re.IGNORECASE):
                    score += 2

            if score > 0:
                scores[agent_name] = score
                reasons[agent_name] = f"keywords: {', '.join(matched[:3])}"

        if not scores:
            return "devops-developer", 0, "fallback (no keyword matches)"

        best_agent = max(scores, key=scores.get)
        # Normalize to 0-100
        max_possible = max(len(rule.keywords) + len(rule.patterns) * 2 
                          for rule in self.routing_rules.values())
        confidence = min(int((scores[best_agent] / max_possible) * 100), 100)
        
        return best_agent, confidence, f"keywords | {reasons[best_agent]}"

    def suggest_agent(self, user_request: str, verbose: bool = False) -> Tuple[str, int, str]:
        """
        Suggest agent based on request.
        
        Priority:
        1. Task metadata (if Txxx in request)
        2. LLM classification
        3. Keyword fallback
        
        Returns:
            Tuple of (agent_name, confidence_0_100, reason)
        """
        # Priority 1: Task metadata
        task_result = self._route_by_task(user_request)
        if task_result:
            if verbose:
                logger.info(f"Task routing: {task_result['task_id']} -> {task_result['agent']}")
            return task_result["agent"], task_result["confidence"], task_result["reason"]

        # Priority 2: LLM classification
        llm_result = self._route_by_llm(user_request)
        if llm_result:
            if verbose:
                logger.info(f"LLM routing: {llm_result[0]} ({llm_result[1]}%)")
            return llm_result

        # Priority 3: Keyword fallback
        agent, confidence, reason = self._route_by_keywords(user_request)
        if verbose:
            logger.info(f"Keyword routing: {agent} ({confidence}%)")
        return agent, confidence, reason

    def get_agent_description(self, agent_name: str) -> Optional[str]:
        """Get description of an agent's responsibilities."""
        rule = self.routing_rules.get(agent_name)
        return rule.description if rule else None

    def list_agents(self) -> List[str]:
        """List all available agents."""
        return list(self.routing_rules.keys())

    def explain_routing(self, user_request: str) -> str:
        """Provide detailed explanation of routing decision."""
        lines = [f'Request: "{user_request}"', ""]

        # Check task routing
        task_result = self._route_by_task(user_request)
        if task_result:
            lines.append(f"Routing Method: Task Metadata")
            lines.append(f"Task ID: {task_result['task_id']}")
            lines.append(f"Agent: {task_result['agent']}")
            lines.append(f"Confidence: {task_result['confidence']}%")
            return "\n".join(lines)

        # Try LLM
        agent, confidence, reason = self.suggest_agent(user_request)
        lines.append(f"Routing Method: {'LLM' if 'llm' in reason else 'Keywords'}")
        lines.append(f"Agent: {agent}")
        lines.append(f"Confidence: {confidence}%")
        lines.append(f"Reason: {reason}")

        return "\n".join(lines)


# ============================================================================
# DELEGATION MATRIX (unchanged)
# ============================================================================

def should_delegate(user_request: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Determine if request should be delegated to agent or executed locally.
    """
    sys.path.insert(0, str(Path(__file__).parent.parent / "0-guards"))
    try:
        from delegation_matrix import BinaryDelegationMatrix, DelegationDecision
    except ImportError:
        # Fallback if delegation_matrix not available
        return {
            "delegate": True,
            "decision": "DELEGATE",
            "reason": "Delegation matrix not available, defaulting to delegate",
            "confidence": 0.5
        }

    matrix = BinaryDelegationMatrix()
    conditions = matrix.analyze_request(user_request, context)
    decision, reason, confidence = matrix.decide(conditions)

    result = {
        "delegate": decision == DelegationDecision.DELEGATE,
        "decision": decision.value,
        "reason": reason,
        "confidence": confidence
    }

    if decision == DelegationDecision.DELEGATE:
        if conditions.task_agent:
            result["suggested_agent"] = conditions.task_agent
        else:
            router = AgentRouter()
            agent, routing_conf, routing_reason = router.suggest_agent(user_request)
            result["suggested_agent"] = agent
            result["routing_confidence"] = routing_conf

    return result


# ============================================================================
# LEGACY COMPATIBILITY (IntentClassifier, CapabilityValidator)
# Kept for backward compatibility with tests
# ============================================================================

class IntentClassifier:
    """Legacy intent classifier - now wraps LLM classifier."""
    
    def __init__(self):
        self.intent_keywords = {
            "infrastructure_creation": {
                "include": ["create", "provision", "deploy", "infrastructure"],
                "exclude": ["diagnose", "troubleshoot"],
                "confidence_boost": 0.85
            },
            "infrastructure_diagnosis": {
                "include": ["diagnose", "troubleshoot", "debug", "problem"],
                "exclude": ["create", "provision"],
                "confidence_boost": 0.88
            },
            "kubernetes_operations": {
                "include": ["pod", "deployment", "service", "namespace", "helm", "kubectl"],
                "exclude": [],
                "confidence_boost": 0.82
            },
            "application_development": {
                "include": ["build", "docker", "test", "npm", "python"],
                "exclude": [],
                "confidence_boost": 0.80
            },
            "feature_planning": {
                "include": ["spec", "plan", "feature", "tasks", "specification"],
                "exclude": [],
                "confidence_boost": 0.92
            },
            "infrastructure_validation": {
                "include": ["validate", "terraform", "lint", "syntax"],
                "exclude": [],
                "confidence_boost": 0.86
            }
        }

    def classify(self, request: str) -> Tuple[Optional[str], float]:
        """Classify intent using keyword matching."""
        request_lower = request.lower()
        scores = {}

        for intent, keywords in self.intent_keywords.items():
            include_matches = sum(1 for kw in keywords["include"] if kw in request_lower)
            exclude_matches = sum(1 for kw in keywords["exclude"] if kw in request_lower)

            if exclude_matches > 0:
                scores[intent] = -1.0
            else:
                scores[intent] = include_matches * keywords["confidence_boost"]

        valid_scores = {k: v for k, v in scores.items() if v >= 0}
        if not valid_scores:
            return None, 0.0

        best_intent = max(valid_scores, key=valid_scores.get)
        confidence = min(valid_scores[best_intent] / 5, 1.0)
        return best_intent, confidence


class CapabilityValidator:
    """Legacy capability validator - kept for test compatibility."""
    
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
            "cloud-troubleshooter": {
                "can_do": ["infrastructure_diagnosis"],
                "cannot_do": ["infrastructure_creation", "application_development"],
                "requires_context": ["gcp", "aws", "monitoring"]
            },
            "devops-developer": {
                "can_do": ["application_development", "infrastructure_validation"],
                "cannot_do": ["kubernetes_operations", "infrastructure_creation"],
                "requires_context": ["application", "development"]
            },
            "speckit-planner": {
                "can_do": ["feature_planning"],
                "cannot_do": ["infrastructure_creation", "kubernetes_operations"],
                "requires_context": ["speckit", "planning"]
            }
        }

    def validate(self, agent: str, intent: str) -> bool:
        if agent not in self.agent_capabilities:
            return False
        capabilities = self.agent_capabilities[agent]
        if intent in capabilities["cannot_do"]:
            return False
        return intent in capabilities["can_do"]

    def find_fallback_agent(self, intent: str, exclude: str = None) -> str:
        for agent, capabilities in self.agent_capabilities.items():
            if agent == exclude:
                continue
            if intent in capabilities["can_do"]:
                return agent
        return "devops-developer"


# ============================================================================
# CLI
# ============================================================================

def main():
    """CLI interface for testing agent router."""
    parser = argparse.ArgumentParser(description="Unified agent router for Claude Code.")
    parser.add_argument("--task-file", help="Path to tasks.md metadata file (optional).")
    parser.add_argument("--test", action="store_true", help="Run router smoke tests.")
    parser.add_argument("--explain", metavar="REQUEST", help="Explain routing decision.")
    parser.add_argument("--json", action="store_true", help="Output result as JSON.")
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
            "Create a spec for new authentication feature",
        ]

        print("Running agent router smoke tests...\n")
        for request in test_cases:
            agent, confidence, reason = router.suggest_agent(request)
            print(f'Request: "{request}"')
            print(f"  -> Agent: {agent} (confidence: {confidence}%)")
            print(f"  -> Reason: {reason}")
            print()
        return

    if args.explain:
        print(router.explain_routing(args.explain))
        return

    if not args.request:
        parser.error("Please provide a request, --test, or --explain.")

    request = " ".join(args.request)
    agent, confidence, reason = router.suggest_agent(request, verbose=not args.json)

    if args.json:
        result = {
            "agent": agent,
            "confidence": confidence,
            "reason": reason,
            "description": router.get_agent_description(agent),
        }
        print(json.dumps(result, indent=2))
        return

    print()
    print(f"Suggested Agent: {agent}")
    print(f"Confidence: {confidence}%")
    print(f"Reason: {reason}")
    description = router.get_agent_description(agent)
    if description:
        print(f"Description: {description}")


if __name__ == "__main__":
    main()
