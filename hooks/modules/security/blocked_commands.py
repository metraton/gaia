"""
Blocked command patterns - Dangerous operations that should be blocked or require approval.

Unified source of truth for all blocked command patterns across:
- Terraform destructive operations
- Kubernetes write operations
- Helm write operations
- Flux write operations
- Cloud provider write operations
- File/system destruction operations
"""

import re
import logging
from typing import List, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class BlockedCommandResult:
    """Result of blocked command check."""
    is_blocked: bool
    pattern_matched: Optional[str] = None
    category: Optional[str] = None
    suggestion: Optional[str] = None


# Blocked command patterns organized by category
BLOCKED_PATTERNS = {
    "terraform": [
        r"terraform\s+apply(?!\s+--help)",
        r"terraform\s+destroy",
        r"terragrunt\s+apply(?!\s+--help)",
        r"terragrunt\s+destroy",
    ],

    "kubernetes": [
        r"kubectl\s+apply(?!\s+.*--dry-run)",
        r"kubectl\s+create(?!\s+.*--dry-run)",
        r"kubectl\s+delete",
        r"kubectl\s+patch",
        r"kubectl\s+replace(?!\s+.*--dry-run)",
    ],

    "helm": [
        r"helm\s+install(?!\s+.*--dry-run)",
        r"helm\s+upgrade(?!\s+.*--dry-run)",
        r"helm\s+uninstall",
        r"helm\s+delete",
    ],

    "flux": [
        r"flux\s+reconcile(?!\s+.*--dry-run)",
        r"flux\s+create",
        r"flux\s+delete",
    ],

    "gcp": [
        r"gcloud\s+[\w-]+\s+(create|update|delete|patch)",
        r"gcloud\s+[\w-]+\s+[\w-]+\s+(create|update|delete|patch)",
    ],

    "aws": [
        r"aws\s+[\w-]+\s+(?!--)(create|update|delete|put)",
        r"aws\s+[\w-]+\s+[\w-]+\s+(?!--)(create|update|delete|put)",
    ],

    "docker": [
        r"docker\s+build",
        r"docker\s+push",
        r"docker\s+run(?!\s+.*--rm)",
    ],

    "git": [
        r"git\s+push(?!\s+--dry-run)",
        r"git\s+commit(?!\s+.*--dry-run)",
    ],

    "file_destruction": [
        r"^rm\s+",
        r"\brm\s+-[rRfF]",
        r"^shred\s+",
        r"^wipe\s+",
        r"^srm\s+",
    ],

    "disk_operations": [
        r"^dd\s+",
        r"^fdisk\s+",
        r"^parted\s+",
        r"^gdisk\s+",
        r"^cfdisk\s+",
        r"^sfdisk\s+",
    ],

    "system_modification": [
        r"^systemctl\s+(stop|disable|mask)",
        r"^service\s+\w+\s+stop",
        r"^kill\s+-9",
        r"^killall\s+-9",
        r"^pkill\s+-9",
    ],

    "network_security": [
        r"^iptables\s+-[FD]",
        r"^nmap\s+.*-s[USATFXMNO]",
        r"^hping3\s+",
    ],

    "privilege_escalation": [
        r"^sudo\s+",
        r"^su\s+-",
    ],

    "dangerous_flags": [
        r"curl\s+.*(-T|--upload-file)",
        r"wget\s+.*(--post-data|--post-file)",
        r"nc\s+.*-e",
        r"socat\s+.*EXEC",
        r"docker\s+run\s+.*--privileged",
        r"chmod\s+(000|777)",
        r"git\s+clean\s+-[fdxFDX]",
    ],
}

# Suggestions for common blocked commands
BLOCKED_COMMAND_SUGGESTIONS = {
    "terraform apply": "Use: terraform plan (to review changes first)",
    "kubectl apply": "Use: kubectl apply --dry-run=client -f <file>",
    "kubectl delete": "Use: kubectl get <resource> (to verify before manual deletion)",
    "helm install": "Use: helm template or helm install --dry-run",
    "helm upgrade": "Use: helm upgrade --dry-run",
    "flux reconcile": "Use: flux reconcile --dry-run",
    "git push": "Use: git push --dry-run (to verify before pushing)",
    "rm -rf": "Use: ls <path> (to verify before manual deletion)",
}


def get_blocked_patterns() -> List[str]:
    """
    Get flat list of all blocked patterns.

    Returns:
        List of regex patterns
    """
    patterns = []
    for category_patterns in BLOCKED_PATTERNS.values():
        patterns.extend(category_patterns)
    return patterns


def get_blocked_patterns_by_category(category: str) -> List[str]:
    """
    Get blocked patterns for a specific category.

    Args:
        category: Category name (terraform, kubernetes, etc.)

    Returns:
        List of regex patterns for that category
    """
    return BLOCKED_PATTERNS.get(category, [])


def is_blocked_command(command: str) -> BlockedCommandResult:
    """
    Check if command matches any blocked pattern.

    Args:
        command: Shell command to check

    Returns:
        BlockedCommandResult with match details
    """
    if not command or not command.strip():
        return BlockedCommandResult(is_blocked=False)

    command = command.strip()

    for category, patterns in BLOCKED_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, command, re.IGNORECASE):
                # Find suggestion for this command
                suggestion = None
                for cmd_prefix, cmd_suggestion in BLOCKED_COMMAND_SUGGESTIONS.items():
                    if cmd_prefix in command.lower():
                        suggestion = cmd_suggestion
                        break

                return BlockedCommandResult(
                    is_blocked=True,
                    pattern_matched=pattern,
                    category=category,
                    suggestion=suggestion,
                )

    return BlockedCommandResult(is_blocked=False)


def get_suggestion_for_blocked(command: str) -> Optional[str]:
    """
    Get a safe alternative suggestion for a blocked command.

    Args:
        command: The blocked command

    Returns:
        Suggestion string or None
    """
    command_lower = command.lower()
    for cmd_prefix, suggestion in BLOCKED_COMMAND_SUGGESTIONS.items():
        if cmd_prefix in command_lower:
            return suggestion
    return None
