"""
Actionable BLOCKED message formatter for permanently blocked commands.

When the hook blocks a T3 command (exit 2), this module produces messages that:
- Line 1: What domain was blocked and why (irreversible)
- Line 2: The specific suggestion (from BLOCKED_COMMAND_SUGGESTIONS if available)
- Line 3: Which agent to dispatch to (mapped from command category)

This replaces the generic "[BLOCKED] Command blocked by security policy" message
with actionable guidance for the orchestrator.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# Category-to-agent mapping: which specialist handles remediation
CATEGORY_AGENT_MAP = {
    "aws_critical": "terraform-architect",
    "gcp_critical": "terraform-architect",
    "terraform_destroy": "terraform-architect",
    "kubernetes_critical": "gitops-operator",
    "flux_critical": "gitops-operator",
    "git_destructive": "developer",
    "docker_critical": "developer",
    "npm_critical": "developer",
    "sql_critical": "developer",
    "disk_operations": "developer",
    "rm_critical": "developer",
    "repo_delete": "developer",
}


def format_blocked_message(result) -> str:
    """Format a blocked command result into an actionable message.

    Args:
        result: A BashValidationResult with tier, reason, and suggestions fields.

    Returns:
        A multi-line message with domain, suggestion, and agent routing.
    """
    # Extract category from reason (format: "Command blocked by security policy: <category>")
    category = _extract_category(result.reason)

    # Line 1: What was blocked and why
    msg = f"[BLOCKED] {result.reason} (irreversible operation)\n"

    # Line 2: Specific suggestion
    suggestion = _get_suggestion(result, category)
    if suggestion:
        msg += f"Suggestion: {suggestion}\n"

    # Line 3: Agent to dispatch to
    agent = CATEGORY_AGENT_MAP.get(category)
    if agent:
        msg += f"Dispatch to: {agent}\n"

    return msg


def _extract_category(reason: str) -> Optional[str]:
    """Extract category name from the reason string.

    The reason format from bash_validator is:
        "Command blocked by security policy: <category>"
    """
    prefix = "Command blocked by security policy: "
    if prefix in reason:
        return reason.split(prefix, 1)[1].strip()
    return None


def _get_suggestion(result, category: Optional[str]) -> Optional[str]:
    """Get the best suggestion for the blocked command.

    Prefers the suggestion from the result (sourced from BLOCKED_COMMAND_SUGGESTIONS),
    falls back to the reason text.
    """
    if result.suggestions:
        return result.suggestions[0]
    if category:
        return f"Category '{category}' commands are permanently blocked"
    return result.reason
