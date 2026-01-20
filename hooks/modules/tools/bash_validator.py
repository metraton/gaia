"""
Bash command validator.

Validates Bash tool invocations:
- Security tier classification
- Blocked command detection
- GitOps workflow validation
- Compound command validation
"""

import re
import json
import logging
from typing import Tuple, Dict, Any, Optional, List
from dataclasses import dataclass

from ..security.tiers import SecurityTier, classify_command_tier
from ..security.safe_commands import is_read_only_command
from ..security.blocked_commands import is_blocked_command, get_suggestion_for_blocked
from ..security.gitops_validator import validate_gitops_workflow
from .shell_parser import get_shell_parser

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

        # Check for forbidden footers
        if self._detect_claude_footers(command):
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason="Command contains Claude Code attribution footers",
                suggestions=[
                    "Remove 'Generated with Claude Code'",
                    "Remove 'Co-Authored-By: Claude'"
                ],
            )

        # Validate git commit messages
        if "git commit" in command and "-m" in command:
            commit_validation = self._validate_commit_message(command)
            if not commit_validation.allowed:
                return commit_validation

        # Fast-path: Check if command has operators BEFORE parsing
        if not self._has_operators(command):
            # Single command - validate directly
            return self._validate_single_command(command)

        # Parse compound commands only if operators detected
        components = self.shell_parser.parse(command)

        if len(components) > 1:
            return self._validate_compound_command(components)

        # Single command validation
        return self._validate_single_command(command)

    def _validate_single_command(self, command: str) -> BashValidationResult:
        """Validate a single command (no operators)."""

        # Fast-path: Auto-approve read-only commands
        is_safe, reason = is_read_only_command(command)
        if is_safe:
            return BashValidationResult(
                allowed=True,
                tier=SecurityTier.T0_READ_ONLY,
                reason=f"Auto-approved: {reason}",
            )

        # Check for blocked patterns first
        blocked_result = is_blocked_command(command)
        if blocked_result.is_blocked:
            suggestion = get_suggestion_for_blocked(command)
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=f"Command blocked by security policy: {blocked_result.category}",
                suggestions=[suggestion] if suggestion else [],
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

        # Classify tier
        tier = classify_command_tier(command)

        # T3 commands that reached here already passed blocked_commands.py check
        # Allow them to pass - settings.json will handle approval prompts (double validation)
        # This ensures:
        # - Truly dangerous commands blocked by blocked_commands.py (redundant layer)
        # - Other T3 commands go to settings.json for user approval
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

        # All components validated
        # Return highest tier among components
        tiers = [classify_command_tier(c) for c in components]
        highest_tier = max(tiers, key=lambda t: ["T0", "T1", "T2", "T3"].index(t.value))

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

            # Add tools to path
            tools_path = Path(__file__).parent.parent.parent.parent / "tools"
            sys.path.insert(0, str(tools_path))

            from validation.commit_validator import validate_commit_message

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

        Returns:
            Extracted message or None if cannot extract
        """
        # First try to detect heredoc pattern
        if "cat <<" in command and "EOF" in command:
            # Extract content between heredoc delimiters
            heredoc_match = re.search(r"<<['\"]?EOF['\"]?\s*\n(.+?)\nEOF", command, re.DOTALL)
            if heredoc_match:
                return heredoc_match.group(1).strip()

        # Pattern for -m "message" or -m 'message' (non-heredoc)
        # Use non-greedy match but ensure we get the full quoted string
        match = re.search(r'-m\s+(["\'])(.+?)\1', command, re.DOTALL)
        if match:
            return match.group(2).strip()

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
    response = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": reason
        }
    }
    return json.dumps(response)
