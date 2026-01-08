"""
GitOps workflow validation for kubectl, helm, and flux commands.

Ensures commands follow GitOps principles:
- No direct cluster modifications
- Use --dry-run for apply operations
- Prefer read-only commands
"""

import re
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class GitOpsValidationResult:
    """Result of GitOps validation."""
    allowed: bool
    reason: str
    severity: str = "info"  # info, warning, high, critical
    suggestions: List[str] = field(default_factory=list)


# Safe read-only commands (always allowed)
SAFE_KUBECTL_COMMANDS = [
    r'kubectl\s+get',
    r'kubectl\s+describe',
    r'kubectl\s+logs',
    r'kubectl\s+top',
    r'kubectl\s+explain',
    r'kubectl\s+version',
    r'kubectl\s+cluster-info',
    r'kubectl\s+config\s+view',
    r'kubectl\s+api-resources',
    r'kubectl\s+api-versions',
]

SAFE_FLUX_COMMANDS = [
    r'flux\s+get',
    r'flux\s+check',
    r'flux\s+version',
    r'flux\s+logs',
    r'flux\s+stats',
    r'flux\s+tree',
]

SAFE_HELM_COMMANDS = [
    r'helm\s+list',
    r'helm\s+status',
    r'helm\s+history',
    r'helm\s+template',
    r'helm\s+lint',
    r'helm\s+version',
    r'helm\s+show',
    r'helm\s+search',
]

# Forbidden commands (modify cluster state)
FORBIDDEN_KUBECTL_COMMANDS = [
    r'kubectl\s+apply(?!\s+.*--dry-run)',
    r'kubectl\s+create(?!\s+.*--dry-run)',
    r'kubectl\s+patch',
    r'kubectl\s+replace',
    r'kubectl\s+delete',
    r'kubectl\s+scale',
    r'kubectl\s+rollout\s+restart',
    r'kubectl\s+annotate(?!\s+.*--dry-run)',
    r'kubectl\s+label(?!\s+.*--dry-run)',
]

FORBIDDEN_FLUX_COMMANDS = [
    r'flux\s+create',
    r'flux\s+delete',
    r'flux\s+suspend',
    r'flux\s+resume',
]

FORBIDDEN_HELM_COMMANDS = [
    r'helm\s+install(?!\s+.*--dry-run)',
    r'helm\s+upgrade(?!\s+.*--dry-run)',
    r'helm\s+uninstall',
    r'helm\s+rollback',
]


def is_safe_gitops_command(command: str) -> bool:
    """Check if command is explicitly safe (read-only)."""
    safe_patterns = SAFE_KUBECTL_COMMANDS + SAFE_FLUX_COMMANDS + SAFE_HELM_COMMANDS
    for pattern in safe_patterns:
        if re.search(pattern, command, re.IGNORECASE):
            return True
    return False


def is_forbidden_gitops_command(command: str) -> bool:
    """Check if command is forbidden (modifies cluster state)."""
    forbidden_patterns = (
        FORBIDDEN_KUBECTL_COMMANDS +
        FORBIDDEN_FLUX_COMMANDS +
        FORBIDDEN_HELM_COMMANDS
    )
    for pattern in forbidden_patterns:
        if re.search(pattern, command, re.IGNORECASE):
            return True
    return False


def validate_gitops_workflow(
    command: str,
    agent_type: Optional[str] = None
) -> GitOpsValidationResult:
    """
    Validate command against GitOps security principles.

    Args:
        command: Shell command to validate
        agent_type: Optional agent type for stricter validation

    Returns:
        GitOpsValidationResult with status and suggestions
    """
    # Check if command is explicitly safe
    if is_safe_gitops_command(command):
        return GitOpsValidationResult(
            allowed=True,
            reason="Read-only operation - safe to execute",
        )

    # Check if command is forbidden
    if is_forbidden_gitops_command(command):
        suggestions = []

        # Provide specific suggestions based on command type
        if "kubectl apply" in command and "--dry-run" not in command:
            suggestions.extend([
                "Use: kubectl apply --dry-run=client -f <file>",
                "Create manifests in gitops repository first",
                "Commit changes and let Flux CD reconcile"
            ])
        elif "flux reconcile" in command and "--dry-run" not in command:
            suggestions.extend([
                "Use: flux reconcile <resource> --dry-run",
                "Follow GitOps workflow: commit -> push -> automatic reconciliation"
            ])
        elif "helm install" in command or "helm upgrade" in command:
            suggestions.extend([
                "Use: helm template or helm upgrade --dry-run",
                "Deploy via HelmRelease manifests in gitops repository"
            ])
        else:
            suggestions.append("Use read-only commands or --dry-run alternatives")

        return GitOpsValidationResult(
            allowed=False,
            reason="Command violates GitOps principles - modifies cluster state directly",
            severity="critical",
            suggestions=suggestions,
        )

    # For gitops-operator agent, be extra strict
    if agent_type == "gitops-operator":
        if ("apply" in command or "create" in command) and "--dry-run" not in command:
            return GitOpsValidationResult(
                allowed=False,
                reason="GitOps operator must use --dry-run for all apply operations",
                severity="high",
                suggestions=["Add --dry-run=client flag to command"],
            )

    # Default: allow but warn about unclear intent
    return GitOpsValidationResult(
        allowed=True,
        reason="Command not explicitly validated - proceed with caution",
        severity="warning",
        suggestions=["Verify command follows GitOps principles"],
    )


def to_dict(result: GitOpsValidationResult) -> Dict[str, Any]:
    """Convert GitOpsValidationResult to dictionary for backward compatibility."""
    return {
        "allowed": result.allowed,
        "reason": result.reason,
        "severity": result.severity,
        "suggestions": result.suggestions,
    }
