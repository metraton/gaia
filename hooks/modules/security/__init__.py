"""
Security module - Security tiers, safe commands, blocked patterns.

Provides:
- tiers: SecurityTier enum and classification
- safe_commands: Read-only command detection
- blocked_commands: Dangerous pattern matching
- gitops_validator: kubectl/helm/flux validation
- approval_constants: Canonical APPROVAL_INDICATORS list
- approval_grants: Time-limited T3 command passthrough after user approval
"""

from .tiers import SecurityTier, classify_command_tier
from .safe_commands import (
    is_read_only_command,
    is_single_command_safe,
    SAFE_COMMANDS_CONFIG,
)
from .blocked_commands import (
    is_blocked_command,
    get_blocked_patterns,
    BlockedCommandResult,
)
from .gitops_validator import validate_gitops_workflow, GitOpsValidationResult
from .approval_constants import APPROVAL_INDICATORS
from .approval_grants import (
    write_approval_grant,
    check_approval_grant,
    consume_grant,
    cleanup_expired_grants,
    ApprovalGrant,
)

__all__ = [
    # Tiers
    "SecurityTier",
    "classify_command_tier",
    # Safe commands
    "is_read_only_command",
    "is_single_command_safe",
    "SAFE_COMMANDS_CONFIG",
    # Blocked commands
    "is_blocked_command",
    "get_blocked_patterns",
    "BlockedCommandResult",
    # GitOps
    "validate_gitops_workflow",
    "GitOpsValidationResult",
    # Approval
    "APPROVAL_INDICATORS",
    # Approval Grants
    "write_approval_grant",
    "check_approval_grant",
    "consume_grant",
    "cleanup_expired_grants",
    "ApprovalGrant",
]
