"""
Task tool validator.

Validates Task tool invocations:
- Agent existence verification
- Context provisioning enforcement
- T3 operation approval requirement
"""

import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

from ..security.tiers import SecurityTier

logger = logging.getLogger(__name__)

# ============================================================================
# ROUTER INTEGRATION - REMOVED
# ============================================================================
# Router suggestion removed for simplicity. Orchestrator should choose correct
# agent based on improved descriptions in CLAUDE.md. If wrong agent selected,
# error message shows available agents and orchestrator self-corrects.


# Available agents for Task invocation
AVAILABLE_AGENTS = [
    "terraform-architect",
    "gitops-operator",
    "cloud-troubleshooter",
    "devops-developer",
    "gaia",
    "Explore",
    "Plan",
    "speckit-planner",
]

# Meta-agents that don't require context_provider
META_AGENTS = ["gaia", "Explore", "Plan", "speckit-planner"]

# Keywords indicating T3 operations
T3_KEYWORDS = [
      "git commit",
      "git push",
      "terraform apply",
      "terragrunt apply",
      "kubectl apply",
      "kubectl delete",
      "kubectl create",
      "git push origin main",
      "git push origin master",
      "helm install",
      "helm upgrade",
]

# Indicators that approval was received
APPROVAL_INDICATORS = [
    "user approval received",
    "approved by user",
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
    has_approval: bool = False


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

        # Use original user task for T3 detection if available (prevents false positives from injected context)
        user_task_for_t3_check = parameters.get("_original_user_task", prompt)

        logger.info(f"Task tool validation for agent: {agent_name}")

        # Check agent exists
        if agent_name not in self.available_agents:
            error_msg = f"Unknown agent: '{agent_name}'\n\n"
            error_msg += f"Available agents:\n"
            for agent in sorted(self.available_agents):
                error_msg += f"  - {agent}\n"
            error_msg += "\nRefer to agent descriptions in CLAUDE.md routing decision tree.\n"
            error_msg += f"\nCorrect usage: Task(subagent_type=\"<agent-name>\", ...)"

            return TaskValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=error_msg,
                agent_name=agent_name,
            )

        # Check context provisioning (for project agents)
        has_context = self._check_context_provisioning(prompt, agent_name)

        if not has_context and agent_name not in META_AGENTS:
            logger.warning(
                f"Task invocation for {agent_name} without apparent context provisioning. "
                f"Orchestrator should call context_provider.py first (Phase 2)."
            )

        # Check for T3 operations (use original user task to avoid false positives from context)
        is_t3 = self._is_t3_operation(user_task_for_t3_check, description)
        has_approval = False

        if is_t3:
            has_approval = self._check_approval(prompt)

            if not has_approval:
                return TaskValidationResult(
                    allowed=False,
                    tier=SecurityTier.T3_BLOCKED,
                    reason=self._get_approval_required_message(),
                    agent_name=agent_name,
                    has_context=has_context,
                    is_t3_operation=True,
                    has_approval=False,
                )

        logger.info(
            f"Task invocation validated: {agent_name} "
            f"(T3={is_t3}, approval={'yes' if has_approval else 'no' if is_t3 else 'N/A'}, "
            f"context={has_context})"
        )

        tier = SecurityTier.T3_BLOCKED if is_t3 and not has_approval else SecurityTier.T0_READ_ONLY

        return TaskValidationResult(
            allowed=True,
            tier=tier,
            reason=f"Task invocation allowed for {agent_name}",
            agent_name=agent_name,
            has_context=has_context,
            is_t3_operation=is_t3,
            has_approval=has_approval,
        )

    def _check_context_provisioning(self, prompt: str, agent_name: str) -> bool:
        """Check if context was properly provisioned."""
        return (
            "# Project Context" in prompt or
            "contract" in prompt.lower() or
            "context_provider.py" in prompt.lower()
        )

    def _is_t3_operation(self, prompt: str, description: str) -> bool:
        """Check if this is a T3 (destructive) operation."""
        combined = f"{description.lower()} {prompt.lower()}"
        return any(keyword in combined for keyword in T3_KEYWORDS)

    def _check_approval(self, prompt: str) -> bool:
        """Check if approval was received."""
        prompt_lower = prompt.lower()
        return any(indicator in prompt_lower for indicator in APPROVAL_INDICATORS)

    def _get_approval_required_message(self) -> str:
        """Get the approval required error message."""
        return (
            "T3 operation detected without approval indication.\n\n"
            "Phase 4 (Approval Gate) is MANDATORY before Task invocation.\n"
            "Orchestrator must:\n"
            "  1. Call approval_gate.request_approval()\n"
            "  2. Get user approval via AskUserQuestion\n"
            "  3. Validate with approval_gate.process_approval_response()\n"
            "  4. Include exact token 'User approval received' in Task prompt\n\n"
            "See CLAUDE.md Rule 5.2 for approval gate protocol."
        )



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
