"""
Cloud pipe/redirect/chaining validator.

Detects pipe, redirect, and command-chaining violations in cloud/infra commands.
Cloud CLIs (gcloud, kubectl, aws, terraform, helm, flux) expose native flags
for filtering and formatting — there is never a valid reason to pipe their output.

This validator runs before tier classification so violations are caught early
and the agent receives a corrective response rather than a blocked execution.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Cloud/infra CLIs covered by this policy
CLOUD_CLI_PATTERN = re.compile(
    r'^\s*(gcloud|kubectl|aws|terraform|helm|flux)\b',
    re.IGNORECASE
)

# Violation definitions: (name, regex, corrected_approach)
VIOLATIONS = [
    (
        "pipe",
        re.compile(r'\|'),
        (
            "Use native output flags instead of piping to shell utilities.\n"
            "  gcloud: --filter='...' --format='value(field)'\n"
            "  kubectl: -o jsonpath='{...}' or -o go-template='{{...}}'\n"
            "  aws: --query '...' --output text\n"
            "  terraform: use terraform output or -json flag"
        ),
    ),
    (
        "redirect",
        re.compile(r'>>?'),
        (
            "Use the Write tool to write output to a file instead of shell redirection.\n"
            "  Write tool: creates or overwrites files cleanly without shell quoting issues.\n"
            "  For append patterns, use the Edit tool or Read + Write."
        ),
    ),
    (
        "chaining",
        re.compile(r';|&&'),
        (
            "Run each command as a separate, atomic Bash call instead of chaining.\n"
            "  One command per step preserves exit-code isolation and avoids\n"
            "  interactive prompts mid-chain that block Claude Code execution."
        ),
    ),
]


@dataclass
class PipeViolation:
    """A detected pipe/redirect/chaining violation."""
    rule: str            # e.g. "pipe", "redirect", "chaining"
    pattern: str         # the literal character(s) that triggered it
    correction: str      # human-readable corrected approach


def _find_violation(command: str) -> Optional[PipeViolation]:
    """
    Return the first pipe/redirect/chaining violation found in command,
    or None if the command is clean.

    Only checks commands that start with a cloud/infra CLI.
    Skips characters inside single or double quoted strings to avoid
    false positives (e.g. --filter='status:RUNNING' contains no violation).
    """
    if not CLOUD_CLI_PATTERN.match(command):
        return None

    # Strip quoted substrings before scanning for operators.
    # This prevents false positives from flag values like --filter='a|b'.
    unquoted = _strip_quoted_sections(command)

    for rule_name, pattern, correction in VIOLATIONS:
        match = pattern.search(unquoted)
        if match:
            return PipeViolation(
                rule=rule_name,
                pattern=match.group(0),
                correction=correction,
            )

    return None


def _strip_quoted_sections(text: str) -> str:
    """
    Replace content inside single and double quotes with spaces.
    Handles simple quoting (no nested quotes, no escape sequences needed
    for the operators we scan for).
    """
    result = []
    in_single = False
    in_double = False

    for ch in text:
        if ch == "'" and not in_double:
            in_single = not in_single
            result.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            result.append(ch)
        elif in_single or in_double:
            result.append(' ')  # mask the character
        else:
            result.append(ch)

    return ''.join(result)


def build_block_response(violation: PipeViolation, command: str) -> dict:
    """
    Build the structured JSON block that tells Claude Code to block the command
    and return a corrective reason to the agent.

    Uses permissionDecision: "block" with exit 0 (NOT exit 2) so the agent
    receives the correction message and adjusts rather than stopping entirely.

    Args:
        violation: The detected violation.
        command:   The original command string (truncated in reason for readability).

    Returns:
        Dict suitable for json.dumps() and print() in the hook entry point.
    """
    truncated = command[:120] + ('...' if len(command) > 120 else '')

    reason = (
        f"Command-execution rule violated: no {violation.rule}s in cloud/infra commands.\n\n"
        f"Violating pattern: '{violation.pattern}' detected in:\n"
        f"  {truncated}\n\n"
        f"Corrected approach:\n"
        f"{violation.correction}"
    )

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "block",
            "permissionDecisionReason": reason,
        }
    }


def validate_cloud_pipe(command: str) -> Optional[dict]:
    """
    Check a command for cloud pipe/redirect/chaining violations.

    Returns a block-response dict if a violation is found, None otherwise.
    The caller should json.dumps() the result and exit(0).

    Args:
        command: The raw bash command string.

    Returns:
        Block response dict, or None if command is clean.
    """
    violation = _find_violation(command)
    if violation is None:
        return None

    logger.warning(
        f"Cloud pipe violation [{violation.rule}] pattern='{violation.pattern}' "
        f"in: {command[:80]}"
    )
    return build_block_response(violation, command)
