"""
Security module - Security tiers, safe commands, blocked patterns.

Provides:
- tiers: SecurityTier enum and classification
- safe_commands: Read-only command detection
- blocked_commands: Dangerous pattern matching
- gitops_validator: kubectl/helm/flux validation
- approval_constants: Canonical nonce approval token
- approval_grants: Time-limited T3 command passthrough after user approval
"""

from .tiers import SecurityTier, classify_command_tier
from .command_semantics import analyze_command, CommandSemantics
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
from .dangerous_verbs import (
    CLI_FAMILY_LOOKUP,
    CATEGORY_DESTRUCTIVE,
    CATEGORY_MUTATIVE,
    CATEGORY_SIMULATION,
    CATEGORY_READ_ONLY,
    CATEGORY_UNKNOWN,
)
from .approval_constants import NONCE_APPROVAL_PATTERN, NONCE_APPROVAL_PREFIX
from .approval_scopes import (
    ApprovalSignature,
    SCOPE_EXACT_COMMAND,
    SCOPE_SEMANTIC_SIGNATURE,
    build_approval_signature,
    matches_approval_signature,
)
from .approval_grants import (
    check_approval_grant,
    cleanup_expired_grants,
    ApprovalGrant,
)

__all__ = [
    # Tiers
    "SecurityTier",
    "classify_command_tier",
    "analyze_command",
    "CommandSemantics",
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
    # Dangerous verbs
    "CLI_FAMILY_LOOKUP",
    "CATEGORY_DESTRUCTIVE",
    "CATEGORY_MUTATIVE",
    "CATEGORY_SIMULATION",
    "CATEGORY_READ_ONLY",
    "CATEGORY_UNKNOWN",
    # Approval
    "NONCE_APPROVAL_PREFIX",
    "NONCE_APPROVAL_PATTERN",
    "ApprovalSignature",
    "SCOPE_EXACT_COMMAND",
    "SCOPE_SEMANTIC_SIGNATURE",
    "build_approval_signature",
    "matches_approval_signature",
    # Approval Grants
    "check_approval_grant",
    "cleanup_expired_grants",
    "ApprovalGrant",
]
