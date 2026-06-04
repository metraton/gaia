"""
Security module - Security tiers, blocked patterns, mutative verb detection.

Provides:
- tiers: SecurityTier enum and classification
- blocked_commands: Permanently blocked pattern matching
- mutative_verbs: Mutative verb detection (user approval workflow)
- gitops_validator: kubectl/helm/flux validation
- approval_constants: Approval token patterns (legacy APPROVE: and ElicitationResult)
- approval_grants: Time-limited T3 command passthrough after user approval
- shell_unwrapper: Detect and strip wrapper shells for inner command classification
- flag_classifiers: Flag-dependent classifiers for 15 command families
- composition_rules: Cross-stage pipe composition rules (exfiltration, RCE, obfuscation)
- network_hosts: Network host classification for curl/wget/httpie targets
"""

from .tiers import SecurityTier, classify_command_tier
from .command_semantics import analyze_command, CommandSemantics
from .blocked_commands import (
    is_blocked_command,
    get_blocked_patterns,
    BlockedCommandResult,
)
from .gitops_validator import validate_gitops_workflow, GitOpsValidationResult
from .mutative_verbs import (
    CLI_FAMILY_LOOKUP,
    CATEGORY_MUTATIVE,
    CATEGORY_SIMULATION,
    CATEGORY_READ_ONLY,
    CATEGORY_UNKNOWN,
)
from .approval_constants import NONCE_APPROVAL_PATTERN, NONCE_APPROVAL_PREFIX
from .approval_messages import (
    CANONICAL_APPROVAL_TOKEN,
    CANONICAL_APPROVAL_TOKEN_FORMAT,
    CANONICAL_APPROVAL_TOKEN_GUIDANCE,
    CANONICAL_APPROVAL_FORMAT_GUIDANCE,
    LATEST_BLOCKED_COMMAND_PHRASE,
)
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
    get_latest_pending_approval,
    last_check_found_expired,
    ApprovalGrant,
)
from .shell_unwrapper import ShellUnwrapper
from .flag_classifiers import classify_by_flags, FlagClassifierResult
from .composition_rules import (
    check_composition,
    build_composition_stages,
    CompositionResult,
    CompositionStage,
    CompositionDecision,
    StageType,
)
from .network_hosts import classify_host, extract_url_from_tokens, HostClassification

__all__ = [
    # Tiers
    "SecurityTier",
    "classify_command_tier",
    "analyze_command",
    "CommandSemantics",
    # Blocked commands
    "is_blocked_command",
    "get_blocked_patterns",
    "BlockedCommandResult",
    # GitOps
    "validate_gitops_workflow",
    "GitOpsValidationResult",
    # Mutative verbs
    "CLI_FAMILY_LOOKUP",
    "CATEGORY_MUTATIVE",
    "CATEGORY_SIMULATION",
    "CATEGORY_READ_ONLY",
    "CATEGORY_UNKNOWN",
    # Approval
    "NONCE_APPROVAL_PREFIX",
    "NONCE_APPROVAL_PATTERN",
    "CANONICAL_APPROVAL_TOKEN",
    "CANONICAL_APPROVAL_TOKEN_FORMAT",
    "CANONICAL_APPROVAL_TOKEN_GUIDANCE",
    "CANONICAL_APPROVAL_FORMAT_GUIDANCE",
    "LATEST_BLOCKED_COMMAND_PHRASE",
    "ApprovalSignature",
    "SCOPE_EXACT_COMMAND",
    "SCOPE_SEMANTIC_SIGNATURE",
    "build_approval_signature",
    "matches_approval_signature",
    # Approval Grants
    "check_approval_grant",
    "cleanup_expired_grants",
    "get_latest_pending_approval",
    "last_check_found_expired",
    "ApprovalGrant",
    # Shell unwrapper
    "ShellUnwrapper",
    # Flag classifiers
    "classify_by_flags",
    "FlagClassifierResult",
    # Composition rules
    "check_composition",
    "build_composition_stages",
    "CompositionResult",
    "CompositionStage",
    "CompositionDecision",
    "StageType",
    # Network hosts
    "classify_host",
    "extract_url_from_tokens",
    "HostClassification",
]
