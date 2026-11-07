"""
Actionable BLOCKED message formatter for categorical denials.

The message carries:
- Line 1: What was blocked and why (irreversible, for a blocked-command pattern)
- Line 2: The specific suggestion, when the result or its category supplies one
- Line 3: Which agent to dispatch to (mapped from command category)
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# Category-to-agent mapping: which specialist handles remediation
CATEGORY_AGENT_MAP = {
    "aws_critical": "platform-architect",
    "gcp_critical": "platform-architect",
    "terragrunt_destroy_all": "platform-architect",
    "kubernetes_critical": "gitops-operator",
    "flux_critical": "gitops-operator",
    "git_destructive": "developer",
    "docker_critical": "developer",
    "npm_critical": "developer",
    "sql_critical": "developer",
    "disk_operations": "developer",
    "rm_critical": "developer",
    "repo_delete": "developer",
    "email_critical": "gaia-operator",
}


def format_blocked_message(result) -> str:
    """Format a blocked command result into an actionable message.

    Args:
        result: A BashValidationResult with tier, reason, and suggestions fields.

    Returns:
        A multi-line message with domain, suggestion, and agent routing.
    """
    category = _extract_category(result.reason)

    # Only a blocked-command pattern is an irreversible operation; a
    # categorical guard (a sensitive read, a protected path, a shell writer)
    # states its own reason.
    irreversible = " (irreversible operation)" if category else ""
    msg = f"[BLOCKED] {result.reason}{irreversible}\n"

    suggestion = _get_suggestion(result, category)
    if suggestion:
        msg += f"Suggestion: {suggestion}\n"

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

    Prefers the suggestion from the result, then a notice naming the category,
    then the reason text.
    """
    if result.suggestions:
        return result.suggestions[0]
    if category:
        return f"Category '{category}' commands are permanently blocked"
    return result.reason
