"""
Task tool validator.

Validates Task tool invocations:
- Agent existence verification
- Context provisioning enforcement
- T3 operation approval requirement
"""

import re
import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

from ..security.tiers import SecurityTier
from ..security.approval_constants import APPROVAL_INDICATORS
from ..security.dangerous_verbs import (
    detect_dangerous_command,
    DangerResult,
    CATEGORY_DESTRUCTIVE,
    CATEGORY_MUTATIVE,
)

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

# Meta-agents that don't require context_provider.
# speckit-planner is a project agent that DOES receive context, so it is NOT a meta-agent.
META_AGENTS = ["gaia", "Explore", "Plan"]

# Legacy T3_KEYWORDS kept for backward compatibility (tests import it).
# Detection now uses detect_dangerous_command() from the verb detector.
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
    # Known CLI prefixes that signal a command
    cli_prefixes = (
        "git ", "kubectl ", "helm ", "flux ", "terraform ", "terragrunt ",
        "gcloud ", "gsutil ", "aws ", "az ", "docker ", "podman ",
        "npm ", "pnpm ", "yarn ", "pip ", "pip3 ",
        "rm ", "mv ", "cp ", "dd ", "mkfs ",
        "systemctl ", "service ",
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
            if fragment:
                candidates.append(fragment)
            idx = pos + len(prefix)

    return candidates


def _scan_text_for_t3(text: str) -> Tuple[bool, str, Optional[DangerResult]]:
    """Scan free-form text for T3 (dangerous) command intent using the verb detector.

    Args:
        text: Combined prompt/description text.

    Returns:
        (is_t3, matched_command, danger_result) tuple.
    """
    candidates = _extract_command_candidates(text)

    for candidate in candidates:
        result = detect_dangerous_command(candidate)
        if result.is_dangerous and result.category in (CATEGORY_DESTRUCTIVE, CATEGORY_MUTATIVE):
            return True, candidate, result

    # Fallback: check legacy keywords for anything the extractor might miss
    text_lower = text.lower()
    for keyword in T3_KEYWORDS:
        if keyword in text_lower:
            return True, keyword, None

    return False, "", None

# Re-export from canonical source so tests that import from here keep working.
# Do NOT define indicators here — edit approval_constants.py instead.
__all__ = [
    "TaskValidator",
    "TaskValidationResult",
    "validate_task_invocation",
    "AVAILABLE_AGENTS",
    "META_AGENTS",
    "T3_KEYWORDS",
    "APPROVAL_INDICATORS",
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
            matched_keyword = self._matched_t3_keyword(user_task_for_t3_check, description)

            if not has_approval:
                return TaskValidationResult(
                    allowed=False,
                    tier=SecurityTier.T3_BLOCKED,
                    reason=self._get_approval_required_message(agent_name, matched_keyword),
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
        """Check if this is a T3 (destructive) operation using the verb detector."""
        combined = f"{description} {prompt}"
        is_t3, _, _ = _scan_text_for_t3(combined)
        return is_t3

    def _matched_t3_keyword(self, prompt: str, description: str) -> str:
        """Return the first T3 command found in the prompt/description, or empty string."""
        combined = f"{description} {prompt}"
        is_t3, matched, _ = _scan_text_for_t3(combined)
        return matched if is_t3 else ""

    def _check_approval(self, prompt: str) -> bool:
        """Check if approval was received."""
        prompt_lower = prompt.lower()
        return any(indicator in prompt_lower for indicator in APPROVAL_INDICATORS)

    def _extract_approval_scope(self, prompt: str) -> str:
        """
        Extract the scope from a scoped approval token.

        Looks for: 'User approved: <scope>'
        Returns the scope string or empty if not found / scope is generic.
        """
        match = re.search(r"user approved:\s*(.+)", prompt, re.IGNORECASE)
        if not match:
            return ""
        scope = match.group(1).strip()
        # Warn on generic/empty scopes but don't block
        generic_scopes = {"the changes", "everything", "all", "yes", "ok", "proceed"}
        if scope.lower() in generic_scopes:
            logger.warning(
                "Approval scope '%s' is generic — consider describing the operation "
                "(e.g. 'User approved: terraform apply prod/vpc')", scope
            )
        return scope

    def _get_approval_required_message(self, agent_name: str = "", matched_keyword: str = "") -> str:
        """Get the approval required error message with detected operation details."""
        detected_line = (
            f'Detected: "{matched_keyword}" in agent prompt\n'
            if matched_keyword
            else "Detected: T3 operation in agent prompt\n"
        )
        agent_line = f"Agent: {agent_name}\n" if agent_name else ""

        return (
            "❌ T3 Operation Blocked — Approval Required\n\n"
            f"{detected_line}"
            f"{agent_line}"
            "\nTo proceed, the orchestrator must:\n"
            "  1. Present the plan to the user (AskUserQuestion)\n"
            f'  2. Resume with: "User approved: {matched_keyword or "<operation> [scope]"}"\n'
            "\nThis operation requires explicit user approval before execution."
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
