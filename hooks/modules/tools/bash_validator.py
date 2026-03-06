"""
Bash command validator.

Primary security gate for all Bash tool invocations. With Bash(*) in the
settings.json allow list, ALL commands reach this hook -- it is the sole
enforcement layer for dangerous command detection.

Validation order (short-circuit on first match):
1. blocked_commands FIRST -- permanently denied patterns (exit 2)
2. Claude footer stripping -- transparent cleanup via updatedInput
3. Commit message validation -- conventional commits format
4. Cloud pipe/redirect/chain check -- corrective deny (exit 0)
5. Safe command fast-path -- auto-approve read-only commands
6. Dangerous verb detector -- DESTRUCTIVE/MUTATIVE -> nonce-based deny (exit 0)
7. GitOps policy validation
8. Tier classification fallback
"""

import re
import json
import logging
from typing import Tuple, Dict, Any, Optional, List
from dataclasses import dataclass

from ..security.tiers import SecurityTier
from ..security.command_semantics import analyze_command
from ..security.safe_commands import is_read_only_command
from ..security.blocked_commands import is_blocked_command
from ..security.gitops_validator import validate_gitops_workflow
from ..security.dangerous_verbs import (
    detect_dangerous_command,
    build_t3_block_response,
    ALWAYS_SAFE_CLIS,
    CLI_FAMILY_LOOKUP,
    CATEGORY_DESTRUCTIVE,
    CATEGORY_MUTATIVE,
    CATEGORY_READ_ONLY,
    CATEGORY_SIMULATION,
)
from ..security.approval_grants import (
    check_approval_grant,
    consume_grant,
    generate_nonce,
    write_pending_approval,
)
from ..security.interactive_handler import ensure_non_interactive
from .shell_parser import get_shell_parser
from .cloud_pipe_validator import validate_cloud_pipe
from .hook_response import build_hook_permission_response

logger = logging.getLogger(__name__)


@dataclass
class BashValidationResult:
    """Result of Bash command validation."""
    allowed: bool
    tier: SecurityTier
    reason: str
    suggestions: List[str] = None
    requires_credentials: bool = False
    credential_warning: str = None
    modified_input: Optional[Dict[str, Any]] = None
    # When set, the caller should return this dict (exit 0) instead of a
    # plain error string (exit 2).  Used for structured block responses that
    # should correct the agent rather than terminate execution.
    block_response: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.suggestions is None:
            self.suggestions = []


# Patterns for commands that require credentials
CREDENTIAL_REQUIRED_PATTERNS = [
    r"kubectl\s+(?!version)",
    r"flux\s+(?!version)",
    r"helm\s+(?!version)",
    r"gcloud\s+container\s+",
    r"gcloud\s+sql\s+",
    r"gcloud\s+redis\s+",
]

# Patterns for Claude Code attribution footers (forbidden)
FORBIDDEN_FOOTER_PATTERNS = [
    r"Generated with\s+Claude Code",
    r"Co-Authored-By:\s+Claude",
]

# CLI families that should fail closed if they cannot be proven safe.
# These commands operate against infrastructure, cluster state, system state,
# or history-bearing resources where an "unknown" subcommand is not acceptable.
FAIL_CLOSED_CLI_FAMILIES = frozenset({
    "cloud",
    "k8s",
    "iac",
    "git",
    "docker",
    "system",
})


class BashValidator:
    """Validator for Bash tool invocations."""

    def __init__(self):
        """Initialize validator."""
        self.shell_parser = get_shell_parser()

    def _requires_fail_closed_managed_cli(self, command: str) -> bool:
        """Return True when a known high-impact CLI should not auto-allow unknown T3."""
        semantics = analyze_command(command)
        base_cmd = semantics.base_cmd
        if not base_cmd or base_cmd in ALWAYS_SAFE_CLIS:
            return False

        family = CLI_FAMILY_LOOKUP.get(base_cmd, "unknown")
        return family in FAIL_CLOSED_CLI_FAMILIES

    def _has_operators(self, command: str) -> bool:
        """Quick check if command has operators (before parsing)."""
        # Fast check for common operators outside quotes
        # This avoids expensive parsing for 70% of commands
        if not any(op in command for op in ['|', '&&', '||', ';', '\n']):
            return False
        return True

    def validate(self, command: str) -> BashValidationResult:
        """
        Validate a Bash command.

        Args:
            command: Command string to validate

        Returns:
            BashValidationResult with validation details
        """
        if not command or not command.strip():
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason="Empty command not allowed",
            )

        command = command.strip()

        # ================================================================
        # PRIORITY 1: Blocked commands check on FULL command (exit 2).
        # This MUST run before any other validator to ensure permanently
        # blocked commands (kubectl delete namespace, etc.) are caught
        # with a reliable exit 2 — even if the command also triggers
        # cloud_pipe_validator or has compound operators.
        # ================================================================
        blocked_result = is_blocked_command(command)
        if blocked_result.is_blocked:
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=f"Command blocked by security policy: {blocked_result.category}",
                suggestions=[blocked_result.suggestion] if blocked_result.suggestion else [],
            )

        # Also check each component of compound commands against the deny list.
        # This catches "ls && kubectl delete namespace prod" early.
        if self._has_operators(command):
            components = self.shell_parser.parse(command)
            for component in components:
                comp_blocked = is_blocked_command(component.strip())
                if comp_blocked.is_blocked:
                    return BashValidationResult(
                        allowed=False,
                        tier=SecurityTier.T3_BLOCKED,
                        reason=f"Command blocked by security policy: {comp_blocked.category}",
                        suggestions=[comp_blocked.suggestion] if comp_blocked.suggestion else [],
                    )

        # Auto-strip forbidden footers from git commits (instead of blocking).
        # Uses updatedInput to transparently clean the command before execution.
        command_was_modified = False
        if self._detect_claude_footers(command):
            command = self._strip_claude_footers(command)
            command_was_modified = True
            logger.info("Auto-stripped Claude Code footer from commit command")

        # Validate git commit messages (on the potentially cleaned command)
        if "git commit" in command and "-m" in command:
            commit_validation = self._validate_commit_message(command)
            if not commit_validation.allowed:
                return commit_validation

        # Cloud pipe/redirect/chaining check — runs AFTER blocked commands.
        # Returns a structured block response dict if a violation is found.
        # block_response is set so the caller emits JSON and exits 0 (corrective),
        # not a plain string with exit 2 (which would terminate the agent).
        pipe_block = validate_cloud_pipe(command)
        if pipe_block is not None:
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=pipe_block["hookSpecificOutput"]["permissionDecisionReason"],
                suggestions=[],
                modified_input=None,
                block_response=pipe_block,
            )

        # Fast-path: Check if command has operators BEFORE parsing
        if not self._has_operators(command):
            result = self._validate_single_command(command)
        else:
            # Parse compound commands only if operators detected
            components = self.shell_parser.parse(command)
            if len(components) > 1:
                result = self._validate_compound_command(components)
            else:
                result = self._validate_single_command(command)

        # Attach cleaned command for hook to emit via updatedInput
        if command_was_modified and result.allowed:
            result.modified_input = {"command": command}

        return result

    def _validate_single_command(self, command: str) -> BashValidationResult:
        """Validate a single command (no operators).

        Note: is_blocked_command() is NOT called here because validate()
        already checks the full command AND each compound component against
        the deny list before dispatching to this method.  Calling it again
        would be redundant work for the same string.
        """

        # Fast-path: Auto-approve read-only commands
        is_safe, reason = is_read_only_command(command)
        if is_safe:
            return BashValidationResult(
                allowed=True,
                tier=SecurityTier.T0_READ_ONLY,
                reason=f"Auto-approved: {reason}",
            )

        # Dangerous verb detection: block DESTRUCTIVE/MUTATIVE commands
        # and direct the agent to follow the T3 approval workflow.
        # NOTE: PreToolUse hooks fire regardless of settings.json allow list.
        # The allow list only controls whether a PermissionRequest dialog is shown.
        # With Bash(*) in allow, ALL Bash commands reach this hook — the hook is
        # the primary security gate for dangerous command detection.
        danger = detect_dangerous_command(command)
        grant_consumed = False
        if danger.is_dangerous:
            # Check for an active approval grant before blocking.
            # When the orchestrator resumes an agent with "User approved: ...",
            # the pre_tool_use hook writes a time-limited grant. If a matching
            # grant exists, allow the command through instead of blocking.
            grant = check_approval_grant(command)
            if grant is not None:
                logger.info(
                    "T3 command allowed via approval grant: %s (scope='%s')",
                    command[:80], grant.approved_scope,
                )
                consume_grant(grant)
                grant_consumed = True
                # Fall through to tier classification below (do NOT block)
            else:
                # Generate a cryptographic nonce and write a pending approval.
                # The nonce is included in the block response so the agent can
                # present it for user approval.
                nonce = generate_nonce()
                write_pending_approval(
                    nonce=nonce,
                    command=command,
                    danger_verb=danger.verb,
                    danger_category=danger.category,
                )

                t3_block = build_t3_block_response(command, danger, nonce=nonce)
                # Wrap in hookSpecificOutput format so Claude Code receives the
                # corrective message (exit 0) instead of a terminal error (exit 2).
                hook_block = build_hook_permission_response("deny", t3_block["message"])
                return BashValidationResult(
                    allowed=False,
                    tier=SecurityTier.T3_BLOCKED,
                    reason=f"Dangerous {danger.category.lower()} command: {danger.reason}",
                    block_response=hook_block,
                )

        # Semantic fallback for commands that are clearly read-only/simulation
        # but were not matched by the stricter safe_commands heuristics.
        if danger.category == CATEGORY_READ_ONLY:
            return BashValidationResult(
                allowed=True,
                tier=SecurityTier.T0_READ_ONLY,
                reason=f"Semantic read-only detection: {danger.reason}",
            )

        if danger.category == CATEGORY_SIMULATION:
            return BashValidationResult(
                allowed=True,
                tier=SecurityTier.T2_DRY_RUN,
                reason=f"Semantic simulation detection: {danger.reason}",
            )

        # Check GitOps commands
        if any(keyword in command for keyword in ("kubectl", "helm", "flux")):
            gitops_result = validate_gitops_workflow(command)
            if not gitops_result.allowed:
                return BashValidationResult(
                    allowed=False,
                    tier=SecurityTier.T3_BLOCKED,
                    reason=f"GitOps policy violation: {gitops_result.reason}",
                    suggestions=gitops_result.suggestions,
                )

        # Check credentials requirement
        requires_creds, cred_warning = self._check_credentials_required(command)

        # Derive tier from results already computed above.
        # All T0 (read-only), T2 (simulation), and blocked paths have already
        # returned.  The only commands reaching here are:
        #   - danger.is_dangerous=True with an active approval grant (T3)
        #   - danger.category="UNKNOWN" with no dangerous flags (unknown verb)
        # Both default to T3; no need to call classify_command_tier() again.
        tier = SecurityTier.T3_BLOCKED

        # Fail-closed: block unknown subcommands for managed CLIs UNLESS the
        # command was explicitly approved via a grant (grant_consumed=True).
        if not grant_consumed and self._requires_fail_closed_managed_cli(command):
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=(
                    "Managed CLI command could not be classified as safe; "
                    "review the subcommand or extend security rules before allowing it"
                ),
            )

        # Commands that reached here passed both blocked_commands.py and the
        # dangerous verb detector (either safe, granted, or unknown verb).
        # With Bash(*) in allow and empty ask list, the hook is the sole
        # security gate -- settings.json no longer prompts for approval.
        return BashValidationResult(
            allowed=True,
            tier=tier,
            reason=f"Command allowed (tier {tier})",
            requires_credentials=requires_creds,
            credential_warning=cred_warning if requires_creds else None,
        )

    def _validate_compound_command(self, components: List[str]) -> BashValidationResult:
        """Validate a compound command (multiple components)."""
        logger.info(f"Compound command detected with {len(components)} components")

        component_results: List[BashValidationResult] = []
        for i, component in enumerate(components, 1):
            result = self._validate_single_command(component)

            if not result.allowed:
                return BashValidationResult(
                    allowed=False,
                    tier=SecurityTier.T3_BLOCKED,
                    reason=(
                        f"Compound command blocked: component {i}/{len(components)} "
                        f"'{component[:50]}' is not allowed\n"
                        f"Reason: {result.reason}"
                    ),
                    suggestions=result.suggestions,
                )
            component_results.append(result)

        # All components validated -- derive highest tier from results already
        # computed by _validate_single_command (avoids redundant classification).
        tier_order = ["T0", "T1", "T2", "T3"]
        highest_tier = max(
            (r.tier for r in component_results),
            key=lambda t: tier_order.index(t.value),
        )

        return BashValidationResult(
            allowed=True,
            tier=highest_tier,
            reason=f"All {len(components)} components validated",
        )

    def _detect_claude_footers(self, command: str) -> bool:
        """Detect Claude Code attribution footers in command."""
        for pattern in FORBIDDEN_FOOTER_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return True
        return False

    def _strip_claude_footers(self, command: str) -> str:
        """
        Strip Claude Code attribution footers from a command.

        Removes full lines matching forbidden footer patterns.
        Works on raw command string regardless of quoting/HEREDOC format.

        Args:
            command: Raw command string

        Returns:
            Command with footer lines removed
        """
        # Remove full lines that contain forbidden patterns
        footer_line_patterns = [
            r'\n\s*Co-Authored-By:\s+Claude[^\n]*',
            r'\n\s*Generated with\s+\[?Claude Code\]?[^\n]*',
            r'\n\s*🤖\s*Generated with[^\n]*',
        ]
        for pattern in footer_line_patterns:
            command = re.sub(pattern, '', command, flags=re.IGNORECASE)

        # Clean up trailing whitespace inside quotes/heredoc
        # Collapse 3+ consecutive newlines to 2
        command = re.sub(r'\n{3,}', '\n\n', command)

        return command

    def _validate_commit_message(self, command: str) -> BashValidationResult:
        """
        Validate git commit message using commit_validator.

        Args:
            command: Git commit command to validate

        Returns:
            BashValidationResult with validation status
        """
        # Extract commit message from command
        # Handles both: git commit -m "message" and git commit -m "$(cat <<'EOF'...)"
        message = self._extract_commit_message(command)

        if not message:
            # Could not extract message - let it pass, git will handle it
            return BashValidationResult(
                allowed=True,
                tier=SecurityTier.T2_DRY_RUN,
                reason="Could not extract commit message for validation"
            )

        # Import validator (lazy import to avoid startup cost)
        try:
            import sys
            from pathlib import Path

            # Import from sibling module (hooks/modules/validation)
            from ..validation.commit_validator import validate_commit_message

            # Validate message
            validation = validate_commit_message(message)

            if not validation.valid:
                # Build suggestions from errors
                suggestions = []
                for error in validation.errors:
                    suggestions.append(f"{error['type']}: {error['fix']}")

                return BashValidationResult(
                    allowed=False,
                    tier=SecurityTier.T3_BLOCKED,
                    reason=f"Commit message validation failed: {validation.errors[0]['message']}",
                    suggestions=suggestions[:3]  # Limit to 3 suggestions
                )

            return BashValidationResult(
                allowed=True,
                tier=SecurityTier.T2_DRY_RUN,
                reason="Commit message validated successfully"
            )

        except Exception as e:
            logger.warning(f"Failed to validate commit message: {e}")
            # If validation fails, allow the command (don't block on validator failure)
            return BashValidationResult(
                allowed=True,
                tier=SecurityTier.T2_DRY_RUN,
                reason=f"Commit validation skipped (validator error: {e})"
            )

    def _extract_commit_message(self, command: str) -> Optional[str]:
        """
        Extract commit message from git commit command.

        Handles formats:
        - git commit -m "message"
        - git commit -m 'message'
        - git commit -m "$(cat <<'EOF'\nmessage\nEOF\n)"
        - git commit -m "$(cat <<EOF\nmessage\nEOF\n)"

        Returns:
            Extracted message or None if cannot extract
        """
        # Level 1: HEREDOC pattern (most common in Claude Code)
        # Handles: <<'EOF', <<EOF, <<"EOF" with flexible whitespace
        if "<<" in command:
            heredoc_match = re.search(
                r"<<['\"]?EOF['\"]?\s*\n(.*?)\n\s*EOF",
                command, re.DOTALL
            )
            if heredoc_match:
                return heredoc_match.group(1).strip()

        # Level 2: Simple -m "message" or -m 'message' (non-heredoc)
        match = re.search(r'-m\s+(["\'])(.*?)\1', command, re.DOTALL)
        if match:
            msg = match.group(2)
            # Skip if it's a $(cat... wrapper — heredoc parse failed above
            if msg.lstrip().startswith("$(cat"):
                return None
            return msg.strip()

        return None

    def _check_credentials_required(self, command: str) -> Tuple[bool, str]:
        """Check if command requires credentials and provide guidance."""
        for pattern in CREDENTIAL_REQUIRED_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                # Skip if it's a credential setup command
                if "gcloud auth" in command or "gcloud config" in command:
                    return False, ""
                if "source" in command and "load-cluster-credentials.sh" in command:
                    return False, ""

                warning = (
                    "This command requires GCP/Kubernetes credentials to be loaded.\n\n"
                    "Recommended patterns:\n"
                    "  1. Load credentials inline:\n"
                    "     gcloud auth application-default login && kubectl ...\n\n"
                    "  2. Use gcloud container clusters get-credentials first:\n"
                    "     gcloud container clusters get-credentials <cluster> --region <region>\n\n"
                    "  3. Ensure KUBECONFIG is set for kubectl/helm/flux commands\n"
                )
                return True, warning

        return False, ""


def validate_bash_command(command: str) -> BashValidationResult:
    """
    Validate a Bash command (convenience function).

    Args:
        command: Command to validate

    Returns:
        BashValidationResult
    """
    validator = BashValidator()
    return validator.validate(command)


def create_permission_allow_response(reason: str) -> str:
    """
    Create JSON response to auto-approve a command.

    This response tells Claude Code to skip the permission check.
    """
    return json.dumps(build_hook_permission_response("allow", reason))
