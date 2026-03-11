"""
Bash command validator.

Primary security gate for all Bash tool invocations. With Bash(*) in the
settings.json allow list, ALL commands reach this hook -- it is the sole
enforcement layer for dangerous command detection.

Simplified three-category pipeline:
1. blocked_commands FIRST -- permanently denied patterns (exit 2)
2. Claude footer stripping -- transparent cleanup via updatedInput
3. Commit message validation -- conventional commits format
4. Cloud pipe/redirect/chain check -- corrective deny (exit 0)
5. Mutative verb detection -- MUTATIVE -> nonce-based deny (exit 0)
6. Everything else -> SAFE (auto-approved by elimination)
"""

import re
import json
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from ..security.tiers import SecurityTier
from ..security.blocked_commands import is_blocked_command
from ..security.gitops_validator import validate_gitops_workflow
from ..security.mutative_verbs import (
    detect_mutative_command,
    build_t3_block_response,
)
from ..security.approval_grants import (
    check_approval_grant,
    confirm_grant,
    generate_nonce,
    write_pending_approval,
)
from ..security.approval_messages import (
    build_pending_approval_unavailable_message,
    build_t3_approval_instructions,
)
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
    modified_input: Optional[Dict[str, Any]] = None
    # When set, the caller should return this dict (exit 0) instead of a
    # plain error string (exit 2).  Used for structured block responses that
    # should correct the agent rather than terminate execution.
    block_response: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.suggestions is None:
            self.suggestions = []


# Patterns for Claude Code attribution footers (forbidden)
FORBIDDEN_FOOTER_PATTERNS = [
    r"Generated with\s+Claude Code",
    r"Co-Authored-By:\s+Claude",
]

class BashValidator:
    """Validator for Bash tool invocations."""

    def __init__(self):
        """Initialize validator."""
        self.shell_parser = get_shell_parser()

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

        # Parse compound commands once (reused for blocked-command check and validation dispatch).
        has_operators = self._has_operators(command)
        parsed_components = None
        if has_operators:
            parsed_components = self.shell_parser.parse(command)
            # Check each component of compound commands against the deny list.
            # This catches "ls && kubectl delete namespace prod" early.
            for component in parsed_components:
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

        # Cloud pipe/redirect/chaining check -- runs AFTER blocked commands.
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

        # Dispatch to single or compound validation using already-parsed components
        if not has_operators:
            result = self._validate_single_command(command)
        elif parsed_components is not None and len(parsed_components) > 1:
            result = self._validate_compound_command(parsed_components)
        else:
            result = self._validate_single_command(command)

        # Attach cleaned command for hook to emit via updatedInput
        if command_was_modified and result.allowed:
            result.modified_input = {"command": command}

        return result

    def _validate_single_command(self, command: str) -> BashValidationResult:
        """Validate a single command (no operators).

        Simplified pipeline:
        1. Mutative verb detection -> block with nonce or allow with grant
        2. GitOps policy validation (for kubectl/helm/flux)
        3. Everything else -> SAFE by elimination

        Note: is_blocked_command() is NOT called here because validate()
        already checks the full command AND each compound component against
        the deny list before dispatching to this method.
        """

        # Mutative verb detection
        result = detect_mutative_command(command)
        if result.is_mutative:
            # Check for an active approval grant before blocking.
            grant = check_approval_grant(command)
            if grant is not None:
                if not grant.confirmed:
                    # First execution after nonce activation: return "ask"
                    # to trigger Claude Code's native permission dialog
                    # (double-barrier security). Mark the grant as confirmed
                    # so subsequent executions within TTL auto-allow.
                    confirm_grant(command)
                    logger.info(
                        "T3 command requires native confirmation: %s (scope='%s')",
                        command[:80], grant.approved_scope,
                    )
                    hook_ask = build_hook_permission_response(
                        "ask",
                        "T3 operation approved. Confirm execution.",
                    )
                    return BashValidationResult(
                        allowed=False,
                        tier=SecurityTier.T3_BLOCKED,
                        reason="T3 approved by user, confirming execution via native dialog",
                        block_response=hook_ask,
                    )
                logger.info(
                    "T3 command allowed via approval grant: %s (scope='%s')",
                    command[:80], grant.approved_scope,
                )
                return BashValidationResult(
                    allowed=True,
                    tier=SecurityTier.T3_BLOCKED,
                    reason=f"Command allowed via approval grant (tier T3)",
                )
            else:
                # Generate a cryptographic nonce and write a pending approval.
                nonce = generate_nonce()
                pending_file = write_pending_approval(
                    nonce=nonce,
                    command=command,
                    danger_verb=result.verb,
                    danger_category=result.category,
                )
                if pending_file is None:
                    hook_block = build_hook_permission_response(
                        "deny",
                        build_pending_approval_unavailable_message(),
                    )
                    return BashValidationResult(
                        allowed=False,
                        tier=SecurityTier.T3_BLOCKED,
                        reason="Failed to persist pending approval for T3 command",
                        block_response=hook_block,
                    )

                t3_block = build_t3_block_response(command, result, nonce=nonce)
                hook_block = build_hook_permission_response("deny", t3_block["message"])
                return BashValidationResult(
                    allowed=False,
                    tier=SecurityTier.T3_BLOCKED,
                    reason=f"Dangerous {result.category.lower()} command: {result.reason}",
                    block_response=hook_block,
                )

        # Check GitOps policy for kubectl/helm/flux commands
        if any(keyword in command for keyword in ("kubectl", "helm", "flux")):
            gitops_result = validate_gitops_workflow(command)
            if not gitops_result.allowed:
                return BashValidationResult(
                    allowed=False,
                    tier=SecurityTier.T3_BLOCKED,
                    reason=f"GitOps policy violation: {gitops_result.reason}",
                    suggestions=gitops_result.suggestions,
                )

        # Not blocked, not mutative -> SAFE by elimination
        return BashValidationResult(
            allowed=True,
            tier=SecurityTier.T0_READ_ONLY,
            reason="Safe by elimination (not blocked, not mutative)",
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
                    block_response=result.block_response,
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
