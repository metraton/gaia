"""
Bash command validator.

Primary security gate for all Bash tool invocations. With Bash(*) in the
settings.json allow list, ALL commands reach this hook -- it is the sole
enforcement layer for dangerous command detection.

Pipeline (ordered by priority):
0. Indirect execution detection -- bash -c, eval, python -c etc. (T2 approval)
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
    find_pending_for_command,
    generate_nonce,
    last_check_found_expired,
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


# Patterns for AI tool attribution footers (auto-stripped from commits).
# Covers Claude Code, GitHub Copilot, Aider, Windsurf, and any future
# tool using the Co-authored-by git trailer convention.
FORBIDDEN_FOOTER_PATTERNS = [
    r"Generated with\s+Claude Code",
    r"Generated with\s+\[?Claude Code\]?",
    r"Co-Authored-By:\s+Claude\b",
    r"Co-authored-by:\s+GitHub Copilot\b",
    r"Co-authored-by:\s+aider\b",
    r"Co-authored-by:\s+Windsurf\b",
    r"Co-authored-by:\s+Cursor\b",
    r"Co-authored-by:\s+Codex\b",
    r"Co-authored-by:\s+Gemini\b",
]

# ---------------------------------------------------------------------------
# Indirect execution wrappers — commands that execute arbitrary strings.
# These bypass regex-based command blocking because the real command is
# hidden inside a string argument.  Classified as T2 (requires approval)
# so the user sees what will actually run.
# ---------------------------------------------------------------------------
INDIRECT_EXEC_PATTERNS = [
    re.compile(r"^bash\s+-c\s+", re.IGNORECASE),
    re.compile(r"^sh\s+-c\s+", re.IGNORECASE),
    re.compile(r"^zsh\s+-c\s+", re.IGNORECASE),
    re.compile(r"^dash\s+-c\s+", re.IGNORECASE),
    re.compile(r"^\s*eval\s+", re.IGNORECASE),
    re.compile(r"^python3?\s+-c\s+", re.IGNORECASE),
    re.compile(r"^node\s+-e\s+", re.IGNORECASE),
    re.compile(r"^perl\s+-e\s+", re.IGNORECASE),
    re.compile(r"^ruby\s+-e\s+", re.IGNORECASE),
    # Process substitution and heredoc piped to shell
    re.compile(r"^bash\s+<\(", re.IGNORECASE),
    re.compile(r"^sh\s+<\(", re.IGNORECASE),
]

class BashValidator:
    """Validator for Bash tool invocations."""

    def __init__(self):
        """Initialize validator."""
        self.shell_parser = get_shell_parser()

    def _detect_indirect_execution(self, command: str) -> Optional[BashValidationResult]:
        """Detect indirect execution wrappers that can bypass regex blocking.

        Commands like 'bash -c "az group delete"' hide the real command inside
        a string.  We classify these as T2 (mutative) so they require user
        approval via the nonce workflow, giving the human a chance to inspect
        what will actually run.

        Returns BashValidationResult if indirect execution detected, else None.
        """
        for pattern in INDIRECT_EXEC_PATTERNS:
            if pattern.search(command):
                # Also check if the inner payload contains a blocked command.
                # Extract the string argument after the wrapper.
                inner = self._extract_inner_command(command)
                if inner:
                    blocked = is_blocked_command(inner)
                    if blocked.is_blocked:
                        return BashValidationResult(
                            allowed=False,
                            tier=SecurityTier.T3_BLOCKED,
                            reason=(
                                f"Indirect execution of blocked command detected: "
                                f"{blocked.category} (via wrapper)"
                            ),
                            suggestions=[
                                blocked.suggestion or "Run the command directly instead of via a shell wrapper.",
                            ],
                        )

                # Not blocked but still indirect — route through approval
                logger.info("Indirect execution detected: %s", command[:80])
                result = detect_mutative_command(command)
                if result.is_mutative:
                    return None  # Already mutative, will be caught by mutative_verbs

                # For interpreters with inline code analysis (python3 -c),
                # mutative_verbs.py has dedicated pattern scanning that
                # distinguishes safe code (json.dumps, sys.version) from
                # dangerous code (os.system, subprocess.run). If it classified
                # the inline code as safe, trust that analysis and allow it
                # through without forcing an "ask" dialog.
                from ..security.mutative_verbs import _INLINE_CODE_CLIS
                base_cmd = command.strip().split()[0].rsplit("/", 1)[-1].lower()
                if base_cmd in _INLINE_CODE_CLIS:
                    logger.info(
                        "Inline code classified as safe by pattern scanner: %s",
                        command[:80],
                    )
                    return None  # Safe inline code, proceed to normal validation

                # Shell wrappers (bash -c, eval, etc.) hide the real command
                # in a string — no dedicated scanner exists. Force "ask" so
                # the user can inspect what will actually run.
                hook_block = build_hook_permission_response(
                    "ask",
                    (
                        "Indirect execution detected. The command uses a shell "
                        "wrapper (bash -c, eval, etc.) that can bypass "
                        "security checks. Please confirm you want to run this."
                    ),
                )
                return BashValidationResult(
                    allowed=False,
                    tier=SecurityTier.T2_DRY_RUN,
                    reason="Indirect execution wrapper detected — requires confirmation",
                    block_response=hook_block,
                )
        return None

    def _extract_inner_command(self, command: str) -> Optional[str]:
        """Extract the inner command from an indirect execution wrapper.

        E.g., 'bash -c "az group delete --name foo"' → 'az group delete --name foo'
        """
        # Match: shell -c "..." or shell -c '...'
        match = re.search(r"""-[ce]\s+(['"])(.*?)\1""", command, re.DOTALL)
        if match:
            return match.group(2).strip()
        # Match: shell -c ... (unquoted, take rest of line)
        match = re.search(r"-[ce]\s+(\S+.*)", command)
        if match:
            return match.group(1).strip()
        return None

    def _has_operators(self, command: str) -> bool:
        """Quick check if command has operators (before parsing)."""
        # Fast check for common operators outside quotes
        # This avoids expensive parsing for 70% of commands
        if not any(op in command for op in ['|', '&&', '||', ';', '\n']):
            return False
        return True

    def validate(
        self,
        command: str,
        is_subagent: bool = False,
        session_id: str = "",
    ) -> BashValidationResult:
        """
        Validate a Bash command.

        Args:
            command: Command string to validate
            is_subagent: True when running in subagent context
            session_id: Session ID for approval scoping

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
        # EARLY NORMALIZATION: Strip AI attribution footers before any
        # other processing.  This ensures the same normalized command
        # string is used for blocked-command checks, compound parsing,
        # mutative verb detection, pending approval writes, AND pending
        # approval lookups.  Without this, write_pending_approval() and
        # find_pending_for_command() could see different strings on the
        # first attempt vs. retry, causing nonce mismatch loops.
        # ================================================================
        command_was_modified = False
        if self._detect_claude_footers(command):
            command = self._strip_claude_footers(command)
            command_was_modified = True
            logger.info("Auto-stripped Claude Code footer from commit command")

        # ================================================================
        # PRIORITY 0: Indirect execution detection.
        # Commands like "bash -c '...'" or "eval '...'" can hide blocked
        # commands inside string arguments, bypassing regex patterns.
        # Detected wrappers are routed to approval or blocked if the inner
        # payload matches a blocked command.
        # ================================================================
        indirect_result = self._detect_indirect_execution(command)
        if indirect_result is not None:
            return indirect_result

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
        # Runs AFTER footer stripping so components also use the normalized command.
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
            result = self._validate_single_command(
                command, is_subagent=is_subagent, session_id=session_id,
            )
        elif parsed_components is not None and len(parsed_components) > 1:
            result = self._validate_compound_command(
                parsed_components, is_subagent=is_subagent, session_id=session_id,
            )
        else:
            result = self._validate_single_command(
                command, is_subagent=is_subagent, session_id=session_id,
            )

        # Attach cleaned command for hook to emit via updatedInput.
        # Set regardless of result.allowed so the ask path can include it too.
        if command_was_modified:
            result.modified_input = {"command": command}
            # If the result is an "ask" block_response, inject updatedInput
            # so the modification survives the native permission dialog.
            if (
                result.block_response is not None
                and result.block_response.get("hookSpecificOutput", {}).get(
                    "permissionDecision"
                ) == "ask"
            ):
                result.block_response["hookSpecificOutput"]["updatedInput"] = {
                    "command": command
                }

        return result

    def _validate_single_command(
        self,
        command: str,
        is_subagent: bool = False,
        session_id: str = "",
    ) -> BashValidationResult:
        """Validate a single command (no operators).

        Simplified pipeline:
        0. Indirect execution detection (for compound command components)
        1. Mutative verb detection -> block with nonce or allow with grant
        2. GitOps policy validation (for kubectl/helm/flux)
        3. Everything else -> SAFE by elimination

        Args:
            command: The command to validate.
            is_subagent: True when running in subagent context (generates
                approval_id + deny). False for orchestrator (returns ask).
            session_id: Session ID for pending approval scoping.

        Note: is_blocked_command() is NOT called here because validate()
        already checks the full command AND each compound component against
        the deny list before dispatching to this method.
        """

        # Indirect execution check for compound command components.
        # When validate() splits "cd /tmp && python3 -c '...'" into parts,
        # the python3 -c component needs the same indirect execution gate
        # that the full command gets in validate().
        indirect_result = self._detect_indirect_execution(command)
        if indirect_result is not None:
            return indirect_result

        # Mutative verb detection
        result = detect_mutative_command(command)
        if result.is_mutative:
            # Check for an active approval grant before blocking.
            grant = check_approval_grant(command, session_id=session_id)
            if grant is not None:
                if not grant.confirmed:
                    # First execution after nonce activation: return "ask"
                    # to trigger Claude Code's native permission dialog
                    # (double-barrier security).
                    #
                    # DO NOT call confirm_grant() here. The native dialog
                    # has not been answered yet. If the user declines, the
                    # grant must stay unconfirmed. Confirmation happens on
                    # the NEXT hook invocation: if we see the same command
                    # with an unconfirmed grant again, the native dialog
                    # must have succeeded (the command executed and the
                    # agent is retrying or a new matching command arrived).
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
                if is_subagent:
                    # Subagent context: check for an existing pending
                    # approval first (retry scenario). If found, reuse
                    # the same nonce to prevent infinite approval_id
                    # generation loops while the user reviews.
                    existing_nonce = find_pending_for_command(
                        session_id or "", command,
                    )
                    if existing_nonce:
                        approval_id = existing_nonce
                        logger.info(
                            "Reusing pending approval_id=%s for retry: %s",
                            approval_id, command[:80],
                        )
                        reason = (
                            f"[T3_BLOCKED] This command requires user approval.\n"
                            f"Do NOT retry this command. Report REVIEW with this approval_id in your json:contract.\n"
                            f"Command: {command}\n"
                            f"Verb: '{result.verb}' ({result.category})\n"
                            f"approval_id: {approval_id}"
                        )
                        hook_deny = build_hook_permission_response("deny", reason)
                        return BashValidationResult(
                            allowed=False,
                            tier=SecurityTier.T3_BLOCKED,
                            reason=f"T3 {result.category.lower()} command: {result.reason}",
                            block_response=hook_deny,
                        )
                    # No existing pending -- generate a new nonce.
                    # The ElicitationResult hook will activate the
                    # grant when the user approves via AskUserQuestion.
                    approval_id = generate_nonce()
                    pending_path = write_pending_approval(
                        nonce=approval_id,
                        command=command,
                        danger_verb=result.verb,
                        danger_category=result.category,
                        session_id=session_id or None,
                    )
                    if pending_path is None:
                        # Persistence failure — fall back to ask
                        logger.warning(
                            "Failed to persist pending approval for subagent; "
                            "falling back to ask: %s",
                            command[:80],
                        )
                        reason = build_pending_approval_unavailable_message()
                        hook_ask = build_hook_permission_response("ask", reason)
                        return BashValidationResult(
                            allowed=False,
                            tier=SecurityTier.T3_BLOCKED,
                            reason="Pending approval persistence failed",
                            block_response=hook_ask,
                        )
                    reason = (
                        f"[T3_BLOCKED] This command requires user approval.\n"
                        f"Do NOT retry this command. Report REVIEW with this approval_id in your json:contract.\n"
                        f"Command: {command}\n"
                        f"Verb: '{result.verb}' ({result.category})\n"
                        f"approval_id: {approval_id}"
                    )
                    hook_deny = build_hook_permission_response("deny", reason)
                    return BashValidationResult(
                        allowed=False,
                        tier=SecurityTier.T3_BLOCKED,
                        reason=f"T3 {result.category.lower()} command: {result.reason}",
                        block_response=hook_deny,
                    )
                else:
                    # Orchestrator context: route through native 'ask' dialog.
                    # The user sees the native permission prompt and approves
                    # directly. No approval_id is generated.
                    reason = (
                        f"[T3_APPROVAL_REQUIRED] {result.category} operation detected.\n"
                        f"Command: {command}\n"
                        f"Verb: '{result.verb}' ({result.category})\n"
                        f"Reason: {result.reason}"
                    )
                    hook_ask = build_hook_permission_response("ask", reason)
                    return BashValidationResult(
                        allowed=False,
                        tier=SecurityTier.T3_BLOCKED,
                        reason=f"Dangerous {result.category.lower()} command: {result.reason}",
                        block_response=hook_ask,
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

    def _validate_compound_command(
        self,
        components: List[str],
        is_subagent: bool = False,
        session_id: str = "",
    ) -> BashValidationResult:
        """Validate a compound command (multiple components)."""
        logger.info(f"Compound command detected with {len(components)} components")

        component_results: List[BashValidationResult] = []
        for i, component in enumerate(components, 1):
            result = self._validate_single_command(
                component, is_subagent=is_subagent, session_id=session_id,
            )

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
        Preserves trailing quote/paren characters that close the commit
        message (e.g., the closing " in -m "...footer").

        Args:
            command: Raw command string

        Returns:
            Command with footer lines removed
        """
        # Remove full lines that contain AI attribution patterns.
        # Each pattern matches the newline + footer content, then uses a
        # lookahead to stop before any trailing quote/paren/bracket
        # sequence that closes the command structure.  The captured group
        # is replaced with empty string, leaving the closing chars intact.
        footer_line_patterns = [
            r'\n\s*Co-[Aa]uthored-[Bb]y:\s+(?:Claude|GitHub Copilot|aider|Windsurf|Cursor|Codex|Gemini)[^\n]*?(?=["\')\]]*(?:\n|$))',
            r'\n\s*Generated with\s+\[?Claude Code\]?[^\n]*?(?=["\')\]]*(?:\n|$))',
            r'\n\s*🤖\s*Generated with[^\n]*?(?=["\')\]]*(?:\n|$))',
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

def validate_bash_command(
    command: str,
    is_subagent: bool = False,
    session_id: str = "",
) -> BashValidationResult:
    """
    Validate a Bash command (convenience function).

    Args:
        command: Command to validate
        is_subagent: True when running in subagent context
        session_id: Session ID for approval scoping

    Returns:
        BashValidationResult
    """
    validator = BashValidator()
    return validator.validate(command, is_subagent=is_subagent, session_id=session_id)
