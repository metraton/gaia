#!/usr/bin/env python3
"""
LLM-based Intent Classifier for Agent Routing

Uses Claude Haiku for fast, accurate intent classification.
Replaces keyword-based IntentClassifier and embedding-based SemanticMatcher.

Key Features:
- Single LLM call for classification (fast with Haiku)
- Clear agent descriptions in prompt
- Simple in-memory cache for repeated queries
- Graceful fallback on errors/timeouts
"""

import json
import logging
import os
import hashlib
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Any
from functools import lru_cache

logger = logging.getLogger(__name__)

# Agent definitions for the LLM prompt
AGENT_DEFINITIONS = {
    "terraform-architect": {
        "description": "Infrastructure as Code specialist. Handles Terraform/Terragrunt operations.",
        "capabilities": [
            "Create, modify, validate Terraform/Terragrunt code",
            "Plan and apply infrastructure changes (VPC, databases, clusters, etc.)",
            "Manage cloud resources (GCP, AWS, Azure)",
            "State management and drift detection"
        ],
        "keywords": ["terraform", "terragrunt", "infrastructure", "provision", "vpc", "cloudsql", "rds", "iam policy"]
    },
    "gitops-operator": {
        "description": "Kubernetes and GitOps specialist. Handles deployments and cluster operations.",
        "capabilities": [
            "Deploy applications to Kubernetes clusters",
            "Manage Flux/ArgoCD GitOps workflows",
            "Create/modify Kubernetes manifests (Deployments, Services, Ingresses)",
            "Verify pod status, logs, and cluster health"
        ],
        "keywords": ["kubernetes", "k8s", "pod", "deployment", "service", "flux", "helm", "kubectl", "namespace"]
    },
    "cloud-troubleshooter": {
        "description": "Cloud diagnostic specialist. Diagnoses issues in GCP and AWS environments.",
        "capabilities": [
            "Diagnose GCP issues (GKE, CloudSQL, IAM)",
            "Diagnose AWS issues (EKS, RDS, EC2)",
            "Compare intended state (IaC) vs actual state (live)",
            "Analyze logs and metrics from cloud providers"
        ],
        "keywords": ["gcp", "aws", "diagnose", "troubleshoot", "debug", "gke", "eks", "cloudsql", "rds", "iam"]
    },
    "devops-developer": {
        "description": "Application development specialist. Handles build, test, and code operations.",
        "capabilities": [
            "Build and test applications",
            "Debug application code",
            "Manage dependencies and packages",
            "Git operations (commits, branches, PRs)"
        ],
        "keywords": ["build", "test", "docker", "npm", "python", "git commit", "code", "debug app"]
    },
    "speckit-planner": {
        "description": "Feature planning specialist. Creates specifications and task breakdowns.",
        "capabilities": [
            "Create feature specifications",
            "Generate task breakdowns",
            "Plan implementation workflows",
            "Analyze requirements"
        ],
        "keywords": ["spec", "plan", "feature", "tasks", "requirements", "specification"]
    }
}

# Simple in-memory cache (cleared on restart)
_classification_cache: Dict[str, Dict[str, Any]] = {}
CACHE_MAX_SIZE = 100


@dataclass
class ClassificationResult:
    """Result of LLM classification"""
    agent: str
    confidence: float
    reasoning: str
    from_cache: bool = False


def _build_classification_prompt(query: str) -> str:
    """Build the prompt for LLM classification."""
    agent_descriptions = []
    for agent, info in AGENT_DEFINITIONS.items():
        desc = f"- **{agent}**: {info['description']}\n"
        desc += f"  Capabilities: {', '.join(info['capabilities'][:2])}\n"
        desc += f"  Keywords: {', '.join(info['keywords'][:5])}"
        agent_descriptions.append(desc)
    
    prompt = f"""You are an agent router. Classify the user request to the most appropriate agent.

## Available Agents:
{chr(10).join(agent_descriptions)}

## User Request:
"{query}"

## Instructions:
1. Analyze the request intent
2. Select the SINGLE best matching agent
3. Provide confidence (0.0-1.0) based on how clear the match is
4. Explain your reasoning briefly

## Response Format (JSON only):
{{"agent": "<agent-name>", "confidence": <0.0-1.0>, "reasoning": "<brief explanation>"}}

Respond with ONLY the JSON, no other text."""
    
    return prompt


def _get_cache_key(query: str) -> str:
    """Generate cache key for query."""
    normalized = query.lower().strip()
    return hashlib.md5(normalized.encode()).hexdigest()[:16]


def _parse_llm_response(response_text: str) -> Optional[Dict[str, Any]]:
    """Parse LLM response, handling various formats."""
    text = response_text.strip()
    
    # Try direct JSON parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # Try to extract JSON from markdown code blocks
    if "```" in text:
        import re
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
    
    # Try to find JSON object in text
    import re
    json_match = re.search(r'\{[^{}]*"agent"[^{}]*\}', text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass
    
    return None


def classify_intent_with_llm(query: str, use_cache: bool = True) -> ClassificationResult:
    """
    Classify user intent using Claude Haiku.
    
    Args:
        query: User request to classify
        use_cache: Whether to use cached results (default: True)
    
    Returns:
        ClassificationResult with agent, confidence, reasoning
    
    Raises:
        No exceptions - always returns a result (fallback on errors)
    """
    # Check cache first
    cache_key = _get_cache_key(query)
    if use_cache and cache_key in _classification_cache:
        cached = _classification_cache[cache_key]
        logger.debug(f"Cache hit for query: {query[:50]}...")
        return ClassificationResult(
            agent=cached["agent"],
            confidence=cached["confidence"],
            reasoning=cached["reasoning"],
            from_cache=True
        )
    
    # Try LLM classification
    try:
        result = _call_llm_for_classification(query)
        
        # Validate agent name
        if result["agent"] not in AGENT_DEFINITIONS:
            logger.warning(f"LLM returned unknown agent: {result['agent']}, using fallback")
            return _fallback_classification(query, reason="unknown_agent")
        
        # Cache successful result
        if use_cache and len(_classification_cache) < CACHE_MAX_SIZE:
            _classification_cache[cache_key] = result
        
        return ClassificationResult(
            agent=result["agent"],
            confidence=float(result.get("confidence", 0.8)),
            reasoning=result.get("reasoning", "LLM classification")
        )
        
    except Exception as e:
        logger.warning(f"LLM classification failed: {e}, using fallback")
        return _fallback_classification(query, reason=str(e))


def _call_llm_for_classification(query: str) -> Dict[str, Any]:
    """
    Call Claude Haiku API for classification.
    
    Uses the Anthropic SDK if available, falls back to keyword matching otherwise.
    """
    # Check for API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.debug("ANTHROPIC_API_KEY not set, using keyword fallback")
        raise EnvironmentError("ANTHROPIC_API_KEY not configured")
    
    try:
        import anthropic
    except ImportError:
        logger.debug("anthropic package not installed, using keyword fallback")
        raise ImportError("anthropic package not available")
    
    client = anthropic.Anthropic(api_key=api_key)
    prompt = _build_classification_prompt(query)
    
    # Use Haiku for speed and cost efficiency
    response = client.messages.create(
        model="claude-3-5-haiku-20241022",
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}]
    )
    
    response_text = response.content[0].text
    parsed = _parse_llm_response(response_text)
    
    if not parsed:
        raise ValueError(f"Could not parse LLM response: {response_text[:100]}")
    
    return parsed


def _fallback_classification(query: str, reason: str = "fallback") -> ClassificationResult:
    """
    Fallback classification using simple keyword matching.
    
    Used when LLM is unavailable or fails.
    """
    query_lower = query.lower()
    
    # Score each agent based on keyword matches
    scores: Dict[str, float] = {}
    
    for agent, info in AGENT_DEFINITIONS.items():
        score = 0.0
        for keyword in info["keywords"]:
            if keyword.lower() in query_lower:
                score += 1.0
        scores[agent] = score
    
    # Find best match
    if scores:
        best_agent = max(scores, key=scores.get)
        best_score = scores[best_agent]
        
        if best_score > 0:
            # Normalize confidence (0-1)
            max_possible = len(AGENT_DEFINITIONS[best_agent]["keywords"])
            confidence = min(best_score / max_possible, 0.9)  # Cap at 0.9 for fallback
            
            return ClassificationResult(
                agent=best_agent,
                confidence=confidence,
                reasoning=f"Keyword fallback ({reason}): matched {int(best_score)} keywords"
            )
    
    # Ultimate fallback
    return ClassificationResult(
        agent="devops-developer",
        confidence=0.3,
        reasoning=f"Default fallback ({reason}): no clear match"
    )


def classify_intent(query: str) -> Dict[str, Any]:
    """
    Main entry point for intent classification.
    
    Returns dict for compatibility with existing code:
    {"agent": str, "confidence": float, "reasoning": str}
    """
    result = classify_intent_with_llm(query)
    return {
        "agent": result.agent,
        "confidence": result.confidence,
        "reasoning": result.reasoning,
        "from_cache": result.from_cache
    }


def clear_cache():
    """Clear the classification cache."""
    global _classification_cache
    _classification_cache = {}
    logger.info("Classification cache cleared")


def get_cache_stats() -> Dict[str, Any]:
    """Get cache statistics."""
    return {
        "size": len(_classification_cache),
        "max_size": CACHE_MAX_SIZE
    }


# For testing without API key
def classify_intent_mock(query: str) -> Dict[str, Any]:
    """
    Mock classification for testing without API key.
    Uses keyword matching only.
    """
    result = _fallback_classification(query, reason="mock")
    return {
        "agent": result.agent,
        "confidence": result.confidence,
        "reasoning": result.reasoning,
        "from_cache": False
    }


if __name__ == "__main__":
    import sys
    
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    
    if len(sys.argv) < 2:
        print("Usage: python llm_classifier.py '<query>'")
        print("\nExample: python llm_classifier.py 'deploy tcm-api to kubernetes'")
        sys.exit(1)
    
    query = " ".join(sys.argv[1:])
    print(f"\nQuery: {query}")
    print("-" * 50)
    
    result = classify_intent(query)
    print(f"Agent: {result['agent']}")
    print(f"Confidence: {result['confidence']:.2f}")
    print(f"Reasoning: {result['reasoning']}")
    print(f"From Cache: {result['from_cache']}")
