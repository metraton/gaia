"""
Non-interactive flag auto-append for CLI commands.

Detects commands that would prompt for interactive yes/no confirmation
and appends the appropriate non-interactive flag to prevent shell hangs
in Claude Code's non-interactive environment.

Supported commands:
- terraform/terragrunt apply/destroy -> -auto-approve
- terragrunt run-all apply/destroy   -> -auto-approve
- gcloud *                           -> --quiet (after "gcloud")
- apt-get *                          -> -y
- apt install/remove                 -> -y
"""

import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================================
# NON-INTERACTIVE FLAGS CONFIGURATION
# ============================================================================
# Maps command prefixes to (flag, insert_position).
#
# insert_position:
#   "append"  - add flag at the end of the command
#   "after:N" - insert flag after the Nth token (0-indexed)
#
# Entries are checked in order; first match wins.
# More specific prefixes MUST appear before less specific ones.
# ============================================================================

_FLAG_RULES: List[Tuple[str, str, str]] = [
    # (prefix, flag, position)

    # Terragrunt run-all (must precede plain terragrunt)
    ("terragrunt run-all apply",   "-auto-approve", "append"),
    ("terragrunt run-all destroy", "-auto-approve", "append"),

    # Terragrunt
    ("terragrunt apply",   "-auto-approve", "append"),
    ("terragrunt destroy", "-auto-approve", "append"),

    # Terraform
    ("terraform apply",   "-auto-approve", "append"),
    ("terraform destroy", "-auto-approve", "append"),

    # GCP CLI: --quiet must appear right after "gcloud"
    ("gcloud", "--quiet", "after:1"),

    # APT package manager
    ("apt-get", "-y", "append"),
    ("apt install", "-y", "append"),
    ("apt remove",  "-y", "append"),
]

# Pre-compute a dict of flag -> set of aliases for fast "already present" checks.
# Some flags have short/long forms that mean the same thing.
_FLAG_EQUIVALENTS: Dict[str, List[str]] = {
    "-auto-approve": ["-auto-approve"],
    "--quiet":       ["--quiet", "-q"],
    "-y":            ["-y", "--yes", "--assume-yes"],
}


def _flag_already_present(command: str, flag: str) -> bool:
    """
    Check whether the non-interactive flag (or an equivalent) is already in the command.

    Args:
        command: The full shell command string.
        flag: The canonical flag to look for.

    Returns:
        True if the flag or a known equivalent is already present.
    """
    equivalents = _FLAG_EQUIVALENTS.get(flag, [flag])
    tokens = command.split()
    for equiv in equivalents:
        if equiv in tokens:
            return True
    return False


def ensure_non_interactive(command: str) -> Optional[str]:
    """
    Append the appropriate non-interactive flag if not already present.

    Scans the command against known interactive CLI prefixes and inserts
    the correct flag to suppress confirmation prompts.

    Args:
        command: The full shell command string.

    Returns:
        Modified command string with the flag appended, or None if no
        modification was needed (command doesn't match or flag is already present).
    """
    if not command or not command.strip():
        return None

    stripped = command.strip()

    for prefix, flag, position in _FLAG_RULES:
        if not stripped.startswith(prefix):
            continue

        # Ensure prefix matches at a word boundary.
        # "terraform apply-foo" should NOT match "terraform apply".
        remainder = stripped[len(prefix):]
        if remainder and not remainder[0].isspace():
            continue

        # Already has the flag (or an equivalent)?
        if _flag_already_present(stripped, flag):
            logger.debug(
                "Flag %s already present in command: %s", flag, stripped
            )
            return None

        # Insert at the correct position
        if position == "append":
            modified = f"{stripped} {flag}"
        elif position.startswith("after:"):
            index = int(position.split(":")[1])
            tokens = stripped.split()
            tokens.insert(index + 1, flag)
            modified = " ".join(tokens)
        else:
            logger.warning("Unknown insert position %r for prefix %r", position, prefix)
            return None

        logger.info(
            "Auto-appended non-interactive flag: %s -> %s", stripped, modified
        )
        return modified

    # No matching prefix found
    return None
