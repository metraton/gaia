"""
Safe command detection and classification.

Single source of truth for read-only/safe commands.
Used by both auto-approval (bypass Claude Code ASK prompt) and tier classification.
"""

import re
import logging
from typing import Tuple, List, Set, Dict

logger = logging.getLogger(__name__)


# ============================================================================
# UNIFIED SAFE COMMANDS CONFIGURATION
# ============================================================================
# Single source of truth for safe commands.
# Both auto-approval (is_read_only_command) and tier classification
# (classify_command_tier) use this configuration.
# ============================================================================

SAFE_COMMANDS_CONFIG: Dict[str, any] = {
    # Commands that are ALWAYS read-only (no dangerous flags possible)
    "always_safe": {
        # System info
        "uname", "hostname", "whoami", "date", "uptime", "free", "id", "groups",
        "arch", "nproc", "lscpu", "lsmem", "locale", "printenv", "env",

        # Directory/file listing (read-only)
        "ls", "pwd", "tree", "which", "whereis", "type", "realpath", "dirname", "basename",

        # File info (read-only)
        "stat", "file", "wc", "du", "df",

        # Text processing (always read-only)
        "awk", "cut", "tr", "sort", "uniq", "head", "tail", "less", "more",
        "grep", "egrep", "fgrep", "diff", "comm",

        # Output/display (read-only - just prints to stdout)
        "echo", "printf", "true", "false",

        # JSON/YAML processing
        "jq", "yq",

        # Network diagnostics
        "ping", "traceroute", "nslookup", "dig", "host", "netstat", "ss",
        "ifconfig", "ip", "route", "arp",

        # Encoding utilities (read-only)
        "base64", "md5sum", "sha256sum", "sha1sum",

        # Archive listing (read-only)
        "tar", "gzip", "gunzip", "zip", "unzip",

        # Shell utilities
        "test", "time", "timeout", "sleep",
    },

    # Multi-word commands that are always safe (prefix matching)
    "always_safe_multiword": {
        # Git read-only
        "git status", "git diff", "git log", "git show", "git branch",
        "git remote", "git describe", "git rev-parse", "git ls-files",
        "git cat-file", "git blame", "git shortlog", "git reflog", "git tag",

        # Terraform read-only
        "terraform version", "terraform validate", "terraform fmt",
        "terraform show", "terraform output", "terraform plan",
        "terragrunt plan", "terragrunt output", "terragrunt validate",

        # Kubernetes read-only
        "kubectl get", "kubectl describe", "kubectl logs", "kubectl explain",
        "kubectl version", "kubectl cluster-info", "kubectl api-resources",
        "kubectl top", "kubectl auth",

        # Helm read-only
        "helm list", "helm status", "helm template", "helm lint",
        "helm version", "helm show", "helm search",

        # Flux read-only
        "flux check", "flux get", "flux version", "flux logs",

        # Docker read-only
        "docker ps", "docker images", "docker inspect", "docker logs",
        "docker stats", "docker version", "docker info",

        # GCP read-only (list/describe operations)
        "gcloud compute instances list", "gcloud compute instances describe",
        "gcloud container clusters list", "gcloud container clusters describe",
        "gcloud sql instances list", "gcloud sql instances describe",
        "gcloud config list", "gcloud auth list",

        # AWS read-only (list/describe operations)
        "aws ec2 describe", "aws s3 ls", "aws rds describe",
        "aws iam list", "aws iam get", "aws sts get-caller-identity",
    },

    # Commands that are read-only UNLESS certain flags are present
    "conditional_safe": {
        # sed is safe unless -i (in-place edit) is used
        "sed": [r"-i\b", r"--in-place"],

        # cat is always safe (just reads files)
        "cat": [],

        # sort is safe unless -o (output to file) is used
        "sort": [r"-o\b", r"--output"],

        # tee writes to files, but is often used in pipes for read-only display
        "tee": [],

        # curl is safe for GET, dangerous with upload flags
        "curl": [r"-T\b", r"--upload-file", r"-X\s*(PUT|POST|DELETE|PATCH)", r"--data", r"-d\b"],

        # wget is safe for download, dangerous with POST
        "wget": [r"--post-data", r"--post-file"],

        # find is read-only unless -delete or -exec with dangerous commands
        "find": [r"-delete", r"-exec\s+rm", r"-exec\s+chmod"],

        # xargs can be dangerous depending on what it executes
        "xargs": [],

        # openssl is mostly read-only
        "openssl": [],
    },
}

# Derived sets for fast lookup
ALWAYS_SAFE_COMMANDS: Set[str] = SAFE_COMMANDS_CONFIG["always_safe"]
ALWAYS_SAFE_MULTIWORD: Set[str] = SAFE_COMMANDS_CONFIG["always_safe_multiword"]
CONDITIONAL_SAFE_COMMANDS: Dict[str, List[str]] = SAFE_COMMANDS_CONFIG["conditional_safe"]


def is_single_command_safe(single_cmd: str) -> Tuple[bool, str]:
    """
    Check if a single command (no operators) is read-only and safe.

    This is the core safety check for individual commands.
    Uses SAFE_COMMANDS_CONFIG as single source of truth.

    Args:
        single_cmd: A single shell command (no pipes or chains)

    Returns:
        (is_safe, reason) - Tuple of boolean and explanation string
    """
    if not single_cmd or not single_cmd.strip():
        return False, "Empty command"

    single_cmd = single_cmd.strip()

    # Extract base command (first word, without path)
    parts = single_cmd.split()
    if not parts:
        return False, "No command parts"

    base_cmd = parts[0]
    # Remove path if present: /usr/bin/cat -> cat
    if '/' in base_cmd:
        base_cmd = base_cmd.split('/')[-1]

    # Check multi-word commands first (more specific)
    for safe_cmd in ALWAYS_SAFE_MULTIWORD:
        if single_cmd.startswith(safe_cmd):
            return True, f"Always-safe: {safe_cmd}"

    # Check single-word always safe commands
    if base_cmd in ALWAYS_SAFE_COMMANDS:
        return True, f"Always-safe: {base_cmd}"

    # Check CONDITIONAL_SAFE_COMMANDS
    if base_cmd in CONDITIONAL_SAFE_COMMANDS:
        dangerous_patterns = CONDITIONAL_SAFE_COMMANDS[base_cmd]

        if not dangerous_patterns:
            # No dangerous patterns defined - always safe
            return True, f"Conditional-safe: {base_cmd}"

        # Check if any dangerous pattern is present
        for pattern in dangerous_patterns:
            if re.search(pattern, single_cmd):
                return False, f"Dangerous flag: {pattern}"

        # No dangerous patterns found
        return True, f"Conditional-safe: {base_cmd}"

    # Not in our safe lists
    return False, f"Not in safe list: {base_cmd}"


def is_read_only_command(command: str, shell_parser=None) -> Tuple[bool, str]:
    """
    Detect if a command is purely read-only and safe to auto-approve.

    Supports compound commands - if ALL components are safe, auto-approve.

    Args:
        command: Full shell command (may include pipes, &&, ||, etc.)
        shell_parser: Optional ShellCommandParser instance

    Returns:
        (is_safe, reason) - Tuple of boolean and explanation string

    This function is used to bypass Claude Code's ASK prompt for commands
    that are clearly read-only and should not require user approval.

    Examples:
        "ls -la"                    -> True (simple safe command)
        "tail -100 file.log"        -> True (simple safe command)
        "cat file | grep foo"       -> True (all components safe)
        "ls && pwd"                 -> True (all components safe)
        "tail file || echo error"   -> True (all components safe)
        "ls && rm -rf /"            -> False (rm is dangerous)
        "cat file | kubectl apply"  -> False (kubectl apply is dangerous)
    """
    if not command or not command.strip():
        return False, "Empty command"

    command = command.strip()

    # Get shell parser
    if shell_parser is None:
        # Import here to avoid circular imports
        from ..tools.shell_parser import get_shell_parser
        shell_parser = get_shell_parser()

    components = shell_parser.parse(command)

    if len(components) == 0:
        return False, "No command components"

    if len(components) == 1:
        # Simple command - check directly
        return is_single_command_safe(components[0])

    # Compound command - check ALL components
    # ALL must be safe for auto-approval
    safe_components = []
    for i, comp in enumerate(components):
        is_safe, reason = is_single_command_safe(comp)
        if not is_safe:
            return False, f"Component {i+1}/{len(components)} not safe: {reason}"
        safe_components.append(reason)

    # All components are safe!
    return True, f"All {len(components)} components safe: {', '.join(safe_components)}"
