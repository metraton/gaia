"""
Blocked command patterns - PERMANENTLY BLOCKED operations (settings.json deny list).

IMPORTANT: This file is synchronized with settings.json "deny" list.
Commands here are ALWAYS BLOCKED, regardless of user approval.

For commands that require approval but can be allowed, see settings.json "ask" list.

Categories:
- AWS delete operations (specific destructive commands)
- GCP delete operations (specific destructive commands)
- Kubernetes critical delete operations (cluster, namespace, pv, node, etc.)
- Git force push operations
- Disk/filesystem destruction operations (dd, fdisk, mkfs, etc.)
- Flux delete operations

NOT included here (handled by settings.json "ask"):
- rm (simple file removal - requires approval)
- terraform apply/destroy (requires approval)
- kubectl apply/delete (most operations - requires approval)
- helm install/upgrade (requires approval)
- docker build/push (requires approval)
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


# ============================================================================
# BLOCKED PATTERNS - Synchronized with settings.json "deny" list
# ============================================================================
# These commands are PERMANENTLY BLOCKED and cannot be executed even with approval.
# They represent irreversible, destructive operations that could cause severe damage.
#
# For operations that require approval but can be allowed (terraform apply,
# kubectl apply, rm, etc.), see settings.json "ask" list.
# ============================================================================

BLOCKED_PATTERNS = {
    # AWS - Critical delete operations (synchronized with settings.json deny)
    "aws_delete": [
        r"aws\s+backup\s+delete",
        r"aws\s+cloudformation\s+delete-stack",
        r"aws\s+dynamodb\s+delete-table",
        r"aws\s+dynamodb\s+delete-item",
        r"aws\s+ec2\s+delete-(key-pair|snapshot|volume)",
        r"aws\s+ec2\s+terminate-instances",
        r"aws\s+elasticache\s+delete-(cache-cluster|replication-group)",
        r"aws\s+iam\s+delete-(user|role|access-key|group|instance-profile|policy)",
        r"aws\s+iam\s+delete-(role-policy|user-policy|group-policy)",
        r"aws\s+iam\s+detach-(user-policy|role-policy|group-policy)",
        r"aws\s+iam\s+remove-user-from-group",
        r"aws\s+lambda\s+delete-function",
        r"aws\s+rds\s+delete-db-(cluster-parameter-group|cluster|instance|parameter-group)",
        r"aws\s+s3\s+rb",
        r"aws\s+s3api\s+delete-(bucket|objects)",
        r"aws\s+sns\s+delete-topic",
        r"aws\s+sqs\s+delete-queue",
        r"aws\s+ec2\s+delete-(security-group|network-interface|internet-gateway|subnet|vpc|route|route-table)",
        r"aws\s+eks\s+delete-(cluster|nodegroup|addon)",
    ],

    # GCP - Critical delete operations (synchronized with settings.json deny)
    "gcp_delete": [
        r"gcloud\s+compute\s+firewall-rules\s+delete",
        r"gcloud\s+compute\s+instances\s+delete",
        r"gcloud\s+compute\s+networks\s+delete",
        r"gcloud\s+compute\s+(disks|images|snapshots)\s+delete",
        r"gcloud\s+container\s+clusters\s+delete",
        r"gcloud\s+container\s+node-pools\s+delete",
        r"gcloud\s+iam\s+roles\s+delete",
        r"gcloud\s+projects\s+delete",
        r"gcloud\s+services\s+disable",
        r"gcloud\s+sql\s+(databases|instances)\s+delete",
        r"gcloud\s+storage\s+rm",
        r"gsutil\s+rb",
        r"gsutil\s+rm\s+-r",
    ],

    # Kubernetes - Critical cluster operations (synchronized with settings.json deny)
    "kubernetes_critical": [
        r"kubectl\s+delete\s+(cluster|clusterrole|clusterrolebinding)",
        r"kubectl\s+delete\s+(namespace|node)",
        r"kubectl\s+delete\s+(pv|pvc|persistentvolume|persistentvolumeclaim)",
        r"kubectl\s+delete\s+(crd|customresourcedefinition)",
        r"kubectl\s+delete\s+(mutatingwebhookconfiguration|validatingwebhookconfiguration)",
        r"kubectl\s+drain",
    ],

    # Git - Force push operations (synchronized with settings.json deny)
    "git_force": [
        r"git\s+push\s+(--force|-f)",
        r"git\s+push\s+origin\s+(--force|-f)",
    ],

    # Flux - Delete operations (synchronized with settings.json deny)
    "flux_delete": [
        r"flux\s+delete",
    ],

    # Disk operations - Irreversible data destruction (synchronized with settings.json deny)
    "disk_operations": [
        r"^dd\s+",
        r"^fdisk\s+",
        r"^mkfs(\.(ext[34]|fat|ntfs))?\s+",
    ],
}

# Suggestions for permanently blocked commands (synchronized with deny list)
BLOCKED_COMMAND_SUGGESTIONS = {
    # AWS suggestions
    "aws cloudformation delete-stack": "BLOCKED: Use Terraform/Terragrunt to manage infrastructure lifecycle",
    "aws ec2 terminate-instances": "BLOCKED: Use Terraform/Terragrunt for instance management",
    "aws rds delete-db-instance": "BLOCKED: Use Terraform/Terragrunt for RDS management",
    "aws eks delete-cluster": "BLOCKED: Use Terraform/Terragrunt for EKS management",

    # GCP suggestions
    "gcloud container clusters delete": "BLOCKED: Use Terraform/Terragrunt for GKE management",
    "gcloud projects delete": "BLOCKED: Critical operation - must be done via Cloud Console",
    "gcloud sql instances delete": "BLOCKED: Use Terraform/Terragrunt for Cloud SQL management",

    # Kubernetes suggestions
    "kubectl delete namespace": "BLOCKED: Critical operation - namespace deletion is irreversible",
    "kubectl delete pv": "BLOCKED: Persistent volume deletion would lose data",
    "kubectl drain": "BLOCKED: Node draining can cause service disruption",

    # Git suggestions
    "git push --force": "BLOCKED: Force push rewrites history - use git push --force-with-lease if necessary",
    "git push -f": "BLOCKED: Force push rewrites history - use git push --force-with-lease if necessary",

    # Disk operations
    "dd": "BLOCKED: Low-level disk operations can destroy data",
    "fdisk": "BLOCKED: Disk partitioning can destroy data",
    "mkfs": "BLOCKED: Filesystem creation destroys all data on target",
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
