"""
Blocked command patterns - PERMANENTLY BLOCKED operations (settings.json deny list).

IMPORTANT: This file is synchronized with settings.json "deny" list.
Commands here are ALWAYS BLOCKED, regardless of user approval.

All other T3 (state-modifying) commands are detected by the universal verb detector
(dangerous_verbs.py) and routed through the approval workflow.

Categories:
- AWS networking/data infrastructure delete operations
- GCP project/cluster/database delete operations
- Kubernetes critical delete operations (cluster, namespace, pv, node, CRD, webhooks)
- Git force push operations (not --force-with-lease)
- Disk/filesystem destruction operations (dd, fdisk, mkfs)
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
# They represent irreversible, catastrophic operations.
#
# Commands removed from here (now handled by the universal verb detector as
# approvable T3 operations):
# - aws iam delete-*, detach-*, remove-user-from-group
# - aws ec2 terminate-instances, delete-key-pair, delete-snapshot, delete-volume,
#   delete-security-group, delete-network-interface
# - aws lambda delete-function
# - aws rds delete-db-parameter-group, delete-db-cluster-parameter-group
# - aws cloudformation delete-stack
# - aws s3api delete-objects
# - aws sns delete-topic, aws sqs delete-queue
# - aws dynamodb delete-item
# - aws backup delete
# - aws eks delete-nodegroup, delete-addon
# - gcloud compute firewall-rules/instances/networks/disks/images/snapshots delete
# - gcloud container node-pools delete
# - gcloud iam roles delete
# - gcloud storage rm
# - kubectl delete clusterrole, clusterrolebinding
# - flux delete
# ============================================================================

BLOCKED_PATTERNS = {
    # AWS - Networking and data infrastructure (irreversible)
    # Patterns use (?!-) negative lookahead to prevent prefix matching
    # (e.g., delete-db-cluster must not match delete-db-cluster-parameter-group).
    # \b alone is insufficient because hyphens are non-word characters.
    "aws_critical": [
        r"aws\s+ec2\s+delete-vpc(?!-)\b",
        r"aws\s+ec2\s+delete-subnet(?!-)\b",
        r"aws\s+ec2\s+delete-internet-gateway(?!-)\b",
        r"aws\s+ec2\s+delete-route-table(?!-)\b",
        r"aws\s+ec2\s+delete-route(?!-)\b",
        r"aws\s+rds\s+delete-db-instance(?!-)\b",
        r"aws\s+rds\s+delete-db-cluster(?!-)\b",
        r"aws\s+dynamodb\s+delete-table(?!-)\b",
        r"aws\s+s3\s+rb(?!-)\b",
        r"aws\s+s3api\s+delete-bucket(?!-)\b",
        r"aws\s+elasticache\s+delete-cache-cluster(?!-)\b",
        r"aws\s+elasticache\s+delete-replication-group(?!-)\b",
        r"aws\s+eks\s+delete-cluster(?!-)\b",
    ],

    # GCP - Project, cluster, and database operations (irreversible)
    "gcp_critical": [
        r"gcloud\s+projects\s+delete\b",
        r"gcloud\s+container\s+clusters\s+delete\b",
        r"gcloud\s+sql\s+instances\s+delete\b",
        r"gcloud\s+sql\s+databases\s+delete\b",
        r"gcloud\s+services\s+disable\b",
        r"gsutil\s+rb\b",
        r"gsutil\s+rm\s+-r\b",
    ],

    # Kubernetes - Critical cluster operations (irreversible)
    # Word boundaries prevent "cluster" from matching "clusterrole" etc.
    "kubernetes_critical": [
        r"kubectl\s+delete\s+namespace\b",
        r"kubectl\s+delete\s+node\b",
        r"kubectl\s+delete\s+cluster\b",
        r"kubectl\s+delete\s+(pv|persistentvolume)\b",
        r"kubectl\s+delete\s+(pvc|persistentvolumeclaim)\b",
        r"kubectl\s+delete\s+(crd|customresourcedefinition)\b",
        r"kubectl\s+delete\s+mutatingwebhookconfiguration\b",
        r"kubectl\s+delete\s+validatingwebhookconfiguration\b",
        r"kubectl\s+drain\b",
    ],

    # Git - Force push (history rewrite, not --force-with-lease)
    "git_force": [
        r"git\s+push\s+.*--force(?!-with-lease)",
        r"git\s+push\s+.*(?<!\w)-f\b",
    ],

    # Disk operations - Irreversible data destruction
    "disk_operations": [
        r"^dd\s+",
        r"^fdisk\s+",
        r"^mkfs(\.(ext[34]|fat|ntfs))?\s+",
    ],
}

# Suggestions for permanently blocked commands
BLOCKED_COMMAND_SUGGESTIONS = {
    # AWS suggestions
    "aws ec2 delete-vpc": "BLOCKED: VPC deletion is irreversible - use Terraform/Terragrunt",
    "aws rds delete-db-instance": "BLOCKED: Use Terraform/Terragrunt for RDS lifecycle management",
    "aws rds delete-db-cluster": "BLOCKED: Use Terraform/Terragrunt for RDS cluster management",
    "aws dynamodb delete-table": "BLOCKED: Table deletion loses all data - use Terraform/Terragrunt",
    "aws s3 rb": "BLOCKED: Bucket removal is irreversible - use Terraform/Terragrunt",
    "aws s3api delete-bucket": "BLOCKED: Bucket deletion is irreversible - use Terraform/Terragrunt",
    "aws eks delete-cluster": "BLOCKED: Use Terraform/Terragrunt for EKS management",
    "aws elasticache delete": "BLOCKED: Use Terraform/Terragrunt for ElastiCache management",

    # GCP suggestions
    "gcloud projects delete": "BLOCKED: Project deletion is irreversible - must be done via Cloud Console",
    "gcloud container clusters delete": "BLOCKED: Use Terraform/Terragrunt for GKE management",
    "gcloud sql instances delete": "BLOCKED: Use Terraform/Terragrunt for Cloud SQL management",
    "gcloud sql databases delete": "BLOCKED: Database deletion loses all data - use Terraform/Terragrunt",
    "gcloud services disable": "BLOCKED: Disabling services can break dependent resources",
    "gsutil rb": "BLOCKED: Bucket removal is irreversible",
    "gsutil rm -r": "BLOCKED: Recursive deletion of cloud storage is irreversible",

    # Kubernetes suggestions
    "kubectl delete namespace": "BLOCKED: Namespace deletion destroys all resources within it",
    "kubectl delete node": "BLOCKED: Node deletion removes the node from the cluster",
    "kubectl delete pv": "BLOCKED: Persistent volume deletion loses data",
    "kubectl delete pvc": "BLOCKED: PVC deletion can trigger PV reclaim and data loss",
    "kubectl delete crd": "BLOCKED: CRD deletion destroys all custom resources of that type",
    "kubectl drain": "BLOCKED: Node draining can cause service disruption",

    # Git suggestions
    "git push --force": "BLOCKED: Force push rewrites history - use git push --force-with-lease",
    "git push -f": "BLOCKED: Force push rewrites history - use git push --force-with-lease",

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
        category: Category name (aws_critical, kubernetes_critical, etc.)

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
