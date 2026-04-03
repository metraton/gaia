"""
Task tool validator.

Validates Task tool invocations:
- Agent existence verification
- Context provisioning enforcement
- T3 operation detection for user approval workflow
"""

import logging
import re
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

from ..security.tiers import SecurityTier
from ..security.mutative_verbs import (
    detect_mutative_command,
    MutativeResult,
    CLI_FAMILY_LOOKUP,
    COMMAND_ALIASES,
)

logger = logging.getLogger(__name__)

# Available agents for Task invocation — both bare and plugin-namespaced forms
_BASE_AGENTS = [
    "terraform-architect",
    "gitops-operator",
    "cloud-troubleshooter",
    "developer",
    "gaia-system",
    "Explore",
    "Plan",
    "speckit-planner",
    "claude-code-guide",
    "general-purpose",
]
# Support both "cloud-troubleshooter" and "gaia-ops:cloud-troubleshooter"
AVAILABLE_AGENTS = _BASE_AGENTS + [f"gaia-ops:{a}" for a in _BASE_AGENTS]

# Native Claude Code agent types — utility subagents built into the harness,
# not gaia domain specialists. They don't require context_provider and don't
# appear in surface routing. They are valid dispatch targets that the
# orchestrator can legitimately use.
# speckit-planner is a project agent that DOES receive context, so it is NOT listed here.
NATIVE_AGENTS = ["Explore", "Plan", "general-purpose", "claude-code-guide"]

# Meta-agents that don't require context_provider (superset: gaia-system + native agents).
META_AGENTS = ["gaia-system"] + NATIVE_AGENTS

# T3_KEYWORDS is test-only: used by tests and cross-layer consistency checks
# to verify that these commands are classified as T3 by the verb detector.
# NOT used at runtime -- detection is handled entirely by detect_mutative_command().
T3_KEYWORDS = [
    "git commit",
    "git push",
    "terraform apply",
    "terragrunt apply",
    "terragrunt run-all apply",
    "kubectl apply",
    "kubectl delete",
    "kubectl create",
    "kubectl rollout restart",
    "kubectl scale",
    "kubectl set image",
    "git push origin main",
    "git push origin master",
    "helm install",
    "helm upgrade",
    "flux reconcile",
    "npm publish",
    "docker push",
    "gcloud sql import",
    "gcloud storage cp",
    "gcloud storage rsync",
]


_EMBEDDED_COMMAND_QUOTE_CHARS = "\"'`"


def _sanitize_candidate_fragment(fragment: str) -> str:
    """Normalize a prose-embedded command fragment for verb detection.

    Task prompts often mention commands inside backticks or quotes:
      - Please run `terraform apply` in prod
      - Need to execute "terraform apply" in prod

    The detector only needs the command skeleton, so strip quote delimiters and
    collapse whitespace before handing the fragment to the dangerous verb
    classifier.
    """
    if not fragment:
        return ""
    cleaned = fragment.translate(str.maketrans({char: " " for char in _EMBEDDED_COMMAND_QUOTE_CHARS}))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.rstrip(".,;:!?")


def _extract_command_candidates(text: str) -> List[str]:
    """Extract command-like lines from free-form text for verb detection.

    Looks for lines that start with known CLI prefixes or contain command-like
    patterns (e.g., "git push", "terraform apply").

    Args:
        text: Free-form text (prompt or description).

    Returns:
        List of candidate command strings to scan.
    """
    if not text:
        return []

    candidates: List[str] = []
    # Derive CLI prefixes from the canonical CLI_FAMILY_LOOKUP and COMMAND_ALIASES
    cli_prefixes = tuple(
        f"{cli} " for cli in sorted(
            set(CLI_FAMILY_LOOKUP.keys()) | set(COMMAND_ALIASES.keys()),
            key=len,
            reverse=True,
        )
    )

    text_lower = text.lower()

    # Strategy 1: Scan the full text for known CLI command patterns
    for prefix in cli_prefixes:
        idx = 0
        while True:
            pos = text_lower.find(prefix, idx)
            if pos == -1:
                break
            # Only match at word boundaries (start of string or preceded by whitespace/punctuation)
            if pos > 0 and text_lower[pos - 1].isalnum():
                idx = pos + len(prefix)
                continue
            # Extract from the prefix to end of line (or next sentence boundary)
            end = text.find("\n", pos)
            if end == -1:
                end = len(text)
            fragment = text[pos:end].strip()
            # Trim trailing punctuation/quotes that are part of prose
            fragment = fragment.rstrip(".,;:!?\"')")
            fragment = _sanitize_candidate_fragment(fragment)
            if fragment:
                candidates.append(fragment)
            idx = pos + len(prefix)

    return candidates


def _scan_text_for_t3(text: str) -> Tuple[bool, str, Optional[MutativeResult]]:
    """Scan free-form text for T3 (dangerous) command intent using the verb detector.

    Args:
        text: Combined prompt/description text.

    Returns:
        (is_t3, matched_command, danger_result) tuple.
    """
    candidates = _extract_command_candidates(text)

    for candidate in candidates:
        result = detect_mutative_command(candidate)
        if result.is_mutative:
            return True, candidate, result

    return False, "", None

__all__ = [
    "TaskValidator",
    "TaskValidationResult",
    "validate_task_invocation",
    "AVAILABLE_AGENTS",
    "META_AGENTS",
    "NATIVE_AGENTS",
    "T3_KEYWORDS",
]


@dataclass
class TaskValidationResult:
    """Result of Task tool validation."""
    allowed: bool
    tier: SecurityTier
    reason: str
    agent_name: str = ""
    has_context: bool = False
    is_t3_operation: bool = False


class TaskValidator:
    """Validator for Task tool invocations."""

    def __init__(self, available_agents: Optional[List[str]] = None):
        """
        Initialize validator.

        Args:
            available_agents: Override available agents list
        """
        self.available_agents = available_agents or AVAILABLE_AGENTS

    def validate(self, parameters: Dict[str, Any]) -> TaskValidationResult:
        """
        Validate Task tool invocation.

        Args:
            parameters: Task tool parameters

        Returns:
            TaskValidationResult with validation details
        """
        agent_name = parameters.get("subagent_type", "unknown")
        prompt = parameters.get("prompt", "")
        description = parameters.get("description", "")

        # additionalContext means prompt is never mutated, so T3 detection
        # runs directly against the original user prompt.
        user_task_for_t3_check = prompt

        logger.info(f"Task tool validation for agent: {agent_name}")

        # Check agent exists
        if agent_name not in self.available_agents:
            error_msg = f"Unknown agent: '{agent_name}'\n\n"
            error_msg += f"Available agents:\n"
            for agent in sorted(self.available_agents):
                error_msg += f"  - {agent}\n"
            error_msg += "\nRefer to the Surface Routing Recommendation for agent selection.\n"
            error_msg += f"\nCorrect usage: Task(subagent_type=\"<agent-name>\", ...)"

            return TaskValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=error_msg,
                agent_name=agent_name,
            )

        # Context is injected via additionalContext by the adapter, not by
        # mutating the prompt.  The validator cannot check additionalContext
        # (it only sees parameters), so we determine context status by agent type.
        # Meta-agents never receive context by design.
        has_context = agent_name not in META_AGENTS

        # Check for T3 operations (use original user task to avoid false positives from context)
        is_t3 = self._is_t3_operation(user_task_for_t3_check, description)

        logger.info(
            f"Task invocation validated: {agent_name} "
            f"(T3={is_t3}, context={has_context})"
        )

        tier = SecurityTier.T3_BLOCKED if is_t3 else SecurityTier.T0_READ_ONLY
        reason = (
            f"Task invocation allowed for {agent_name}; T3 execution still requires "
            f"nonce-based approval at Bash time"
            if is_t3
            else f"Task invocation allowed for {agent_name}"
        )

        return TaskValidationResult(
            allowed=True,
            tier=tier,
            reason=reason,
            agent_name=agent_name,
            has_context=has_context,
            is_t3_operation=is_t3,
        )

    def _is_t3_operation(self, prompt: str, description: str) -> bool:
        """Check if this is a T3 (destructive) operation using the verb detector."""
        combined = f"{description} {prompt}"
        is_t3, _, _ = _scan_text_for_t3(combined)
        return is_t3



def validate_task_invocation(parameters: Dict[str, Any]) -> TaskValidationResult:
    """
    Validate Task tool invocation (convenience function).

    Args:
        parameters: Task tool parameters

    Returns:
        TaskValidationResult
    """
    validator = TaskValidator()
    return validator.validate(parameters)
