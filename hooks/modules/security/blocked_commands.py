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
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from .command_semantics import CommandSemantics, analyze_command, _contains_ordered_sequence

logger = logging.getLogger(__name__)


@dataclass
class BlockedCommandResult:
    """Result of blocked command check."""
    is_blocked: bool
    pattern_matched: Optional[str] = None
    category: Optional[str] = None
    suggestion: Optional[str] = None


@dataclass(frozen=True)
class SemanticBlockedRule:
    """Ordered-token signature for deny-listed commands."""

    category: str
    sequence: Tuple[str, ...]
    suggestion_key: str
    required_flags: Tuple[str, ...] = ()
    forbidden_flags: Tuple[str, ...] = ()
    head_only: bool = True

    def matches(self, semantics: CommandSemantics) -> bool:
        tokens = semantics.semantic_head_tokens if self.head_only else semantics.semantic_tokens
        if not _contains_ordered_sequence(tokens, self.sequence):
            return False

        flags = set(semantics.flag_tokens)
        if any(flag not in flags for flag in self.required_flags):
            return False
        if any(flag in flags for flag in self.forbidden_flags):
            return False
        return True


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

BLOCKED_PATTERNS: Dict[str, List[re.Pattern]] = {
    # AWS - Networking and data infrastructure (irreversible)
    # Patterns use (?!-) negative lookahead to prevent prefix matching
    # (e.g., delete-db-cluster must not match delete-db-cluster-parameter-group).
    # \b alone is insufficient because hyphens are non-word characters.
    "aws_critical": [
        re.compile(r"aws\s+ec2\s+delete-vpc(?!-)\b", re.IGNORECASE),
        re.compile(r"aws\s+ec2\s+delete-subnet(?!-)\b", re.IGNORECASE),
        re.compile(r"aws\s+ec2\s+delete-internet-gateway(?!-)\b", re.IGNORECASE),
        re.compile(r"aws\s+ec2\s+delete-route-table(?!-)\b", re.IGNORECASE),
        re.compile(r"aws\s+ec2\s+delete-route(?!-)\b", re.IGNORECASE),
        re.compile(r"aws\s+rds\s+delete-db-instance(?!-)\b", re.IGNORECASE),
        re.compile(r"aws\s+rds\s+delete-db-cluster(?!-)\b", re.IGNORECASE),
        re.compile(r"aws\s+dynamodb\s+delete-table(?!-)\b", re.IGNORECASE),
        re.compile(r"aws\s+s3\s+rb(?!-)\b", re.IGNORECASE),
        re.compile(r"aws\s+s3api\s+delete-bucket(?!-)\b", re.IGNORECASE),
        re.compile(r"aws\s+elasticache\s+delete-cache-cluster(?!-)\b", re.IGNORECASE),
        re.compile(r"aws\s+elasticache\s+delete-replication-group(?!-)\b", re.IGNORECASE),
        re.compile(r"aws\s+eks\s+delete-cluster(?!-)\b", re.IGNORECASE),
    ],

    # GCP - Project, cluster, and database operations (irreversible)
    "gcp_critical": [
        re.compile(r"gcloud\s+projects\s+delete\b", re.IGNORECASE),
        re.compile(r"gcloud\s+container\s+clusters\s+delete\b", re.IGNORECASE),
        re.compile(r"gcloud\s+sql\s+instances\s+delete\b", re.IGNORECASE),
        re.compile(r"gcloud\s+sql\s+databases\s+delete\b", re.IGNORECASE),
        re.compile(r"gcloud\s+services\s+disable\b", re.IGNORECASE),
        re.compile(r"gsutil\s+rb\b", re.IGNORECASE),
        re.compile(r"gsutil\s+rm\s+-r\b", re.IGNORECASE),
    ],

    # Kubernetes - Critical cluster operations (irreversible)
    # Word boundaries prevent "cluster" from matching "clusterrole" etc.
    "kubernetes_critical": [
        re.compile(r"kubectl\s+delete\s+namespace\b", re.IGNORECASE),
        re.compile(r"kubectl\s+delete\s+node\b", re.IGNORECASE),
        re.compile(r"kubectl\s+delete\s+cluster\b", re.IGNORECASE),
        re.compile(r"kubectl\s+delete\s+(pv|persistentvolume)\b", re.IGNORECASE),
        re.compile(r"kubectl\s+delete\s+(pvc|persistentvolumeclaim)\b", re.IGNORECASE),
        re.compile(r"kubectl\s+delete\s+(crd|customresourcedefinition)\b", re.IGNORECASE),
        re.compile(r"kubectl\s+delete\s+mutatingwebhookconfiguration\b", re.IGNORECASE),
        re.compile(r"kubectl\s+delete\s+validatingwebhookconfiguration\b", re.IGNORECASE),
        re.compile(r"kubectl\s+drain\b", re.IGNORECASE),
    ],

    # Git - Force push (history rewrite, not --force-with-lease)
    "git_force": [
        re.compile(r"git\s+push\s+.*--force(?!-with-lease)", re.IGNORECASE),
        re.compile(r"git\s+push\s+.*(?<!\w)-f\b", re.IGNORECASE),
    ],

    # Disk operations - Irreversible data destruction
    "disk_operations": [
        re.compile(r"^dd\s+", re.IGNORECASE),
        re.compile(r"^fdisk\s+", re.IGNORECASE),
        re.compile(r"^mkfs(\.(ext[34]|fat|ntfs))?\s+", re.IGNORECASE),
    ],
}

# Suggestions for permanently blocked commands
BLOCKED_COMMAND_SUGGESTIONS = {
    # AWS suggestions
    "aws ec2 delete-vpc": "BLOCKED: VPC deletion is irreversible - use Terraform/Terragrunt",
    "aws ec2 delete-subnet": "BLOCKED: Subnet deletion is irreversible - use Terraform/Terragrunt",
    "aws ec2 delete-internet-gateway": "BLOCKED: Internet gateway deletion is irreversible - use Terraform/Terragrunt",
    "aws ec2 delete-route-table": "BLOCKED: Route table deletion is irreversible - use Terraform/Terragrunt",
    "aws ec2 delete-route": "BLOCKED: Route deletion should be done via Terraform/Terragrunt",
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
    "kubectl delete cluster": "BLOCKED: Cluster deletion is irreversible and impacts all workloads",
    "kubectl delete pv": "BLOCKED: Persistent volume deletion loses data",
    "kubectl delete pvc": "BLOCKED: PVC deletion can trigger PV reclaim and data loss",
    "kubectl delete crd": "BLOCKED: CRD deletion destroys all custom resources of that type",
    "kubectl delete mutatingwebhookconfiguration": "BLOCKED: Webhook deletion can break admission control and cluster safety",
    "kubectl delete validatingwebhookconfiguration": "BLOCKED: Webhook deletion can break admission control and cluster safety",
    "kubectl drain": "BLOCKED: Node draining can cause service disruption",

    # Git suggestions
    "git push --force": "BLOCKED: Force push rewrites history - use git push --force-with-lease",
    "git push -f": "BLOCKED: Force push rewrites history - use git push --force-with-lease",

    # Disk operations
    "dd": "BLOCKED: Low-level disk operations can destroy data",
    "fdisk": "BLOCKED: Disk partitioning can destroy data",
    "mkfs": "BLOCKED: Filesystem creation destroys all data on target",
}

# Structured deny signatures complement the raw regex patterns above.
# They make the deny list resilient to extra flag/value pairs inserted before
# the real subcommand, e.g.:
#   kubectl --context prod delete namespace
#   aws --profile prod ec2 delete-vpc
#   git -C repo push origin main --force
SEMANTIC_BLOCKED_RULES = (
    # AWS
    SemanticBlockedRule("aws_critical", ("aws", "ec2", "delete-vpc"), "aws ec2 delete-vpc"),
    SemanticBlockedRule("aws_critical", ("aws", "ec2", "delete-subnet"), "aws ec2 delete-subnet"),
    SemanticBlockedRule("aws_critical", ("aws", "ec2", "delete-internet-gateway"), "aws ec2 delete-internet-gateway"),
    SemanticBlockedRule("aws_critical", ("aws", "ec2", "delete-route-table"), "aws ec2 delete-route-table"),
    SemanticBlockedRule("aws_critical", ("aws", "ec2", "delete-route"), "aws ec2 delete-route"),
    SemanticBlockedRule("aws_critical", ("aws", "rds", "delete-db-instance"), "aws rds delete-db-instance"),
    SemanticBlockedRule("aws_critical", ("aws", "rds", "delete-db-cluster"), "aws rds delete-db-cluster"),
    SemanticBlockedRule("aws_critical", ("aws", "dynamodb", "delete-table"), "aws dynamodb delete-table"),
    SemanticBlockedRule("aws_critical", ("aws", "s3", "rb"), "aws s3 rb"),
    SemanticBlockedRule("aws_critical", ("aws", "s3api", "delete-bucket"), "aws s3api delete-bucket"),
    SemanticBlockedRule(
        "aws_critical",
        ("aws", "elasticache", "delete-cache-cluster"),
        "aws elasticache delete",
    ),
    SemanticBlockedRule(
        "aws_critical",
        ("aws", "elasticache", "delete-replication-group"),
        "aws elasticache delete",
    ),
    SemanticBlockedRule("aws_critical", ("aws", "eks", "delete-cluster"), "aws eks delete-cluster"),

    # GCP
    SemanticBlockedRule("gcp_critical", ("gcloud", "projects", "delete"), "gcloud projects delete"),
    SemanticBlockedRule(
        "gcp_critical",
        ("gcloud", "container", "clusters", "delete"),
        "gcloud container clusters delete",
    ),
    SemanticBlockedRule("gcp_critical", ("gcloud", "sql", "instances", "delete"), "gcloud sql instances delete"),
    SemanticBlockedRule("gcp_critical", ("gcloud", "sql", "databases", "delete"), "gcloud sql databases delete"),
    SemanticBlockedRule("gcp_critical", ("gcloud", "services", "disable"), "gcloud services disable"),
    SemanticBlockedRule("gcp_critical", ("gsutil", "rb"), "gsutil rb"),
    SemanticBlockedRule("gcp_critical", ("gsutil", "rm"), "gsutil rm -r", required_flags=("-r",)),

    # Kubernetes
    SemanticBlockedRule("kubernetes_critical", ("kubectl", "delete", "namespace"), "kubectl delete namespace"),
    SemanticBlockedRule("kubernetes_critical", ("kubectl", "delete", "node"), "kubectl delete node"),
    SemanticBlockedRule("kubernetes_critical", ("kubectl", "delete", "cluster"), "kubectl delete cluster"),
    SemanticBlockedRule("kubernetes_critical", ("kubectl", "delete", "pv"), "kubectl delete pv"),
    SemanticBlockedRule(
        "kubernetes_critical",
        ("kubectl", "delete", "persistentvolume"),
        "kubectl delete pv",
    ),
    SemanticBlockedRule("kubernetes_critical", ("kubectl", "delete", "pvc"), "kubectl delete pvc"),
    SemanticBlockedRule(
        "kubernetes_critical",
        ("kubectl", "delete", "persistentvolumeclaim"),
        "kubectl delete pvc",
    ),
    SemanticBlockedRule("kubernetes_critical", ("kubectl", "delete", "crd"), "kubectl delete crd"),
    SemanticBlockedRule(
        "kubernetes_critical",
        ("kubectl", "delete", "customresourcedefinition"),
        "kubectl delete crd",
    ),
    SemanticBlockedRule(
        "kubernetes_critical",
        ("kubectl", "delete", "mutatingwebhookconfiguration"),
        "kubectl delete mutatingwebhookconfiguration",
    ),
    SemanticBlockedRule(
        "kubernetes_critical",
        ("kubectl", "delete", "validatingwebhookconfiguration"),
        "kubectl delete validatingwebhookconfiguration",
    ),
    SemanticBlockedRule("kubernetes_critical", ("kubectl", "drain"), "kubectl drain"),

    # Git
    SemanticBlockedRule("git_force", ("git", "push"), "git push --force", required_flags=("--force",)),
    SemanticBlockedRule("git_force", ("git", "push"), "git push -f", required_flags=("-f",)),
)


def get_blocked_patterns() -> List[re.Pattern]:
    """
    Get flat list of all blocked patterns (pre-compiled).

    Returns:
        List of compiled regex patterns
    """
    patterns = []
    for category_patterns in BLOCKED_PATTERNS.values():
        patterns.extend(category_patterns)
    return patterns


def get_blocked_patterns_by_category(category: str) -> List[re.Pattern]:
    """
    Get blocked patterns for a specific category.

    Args:
        category: Category name (aws_critical, kubernetes_critical, etc.)

    Returns:
        List of compiled regex patterns for that category
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

    semantic_rule = _match_semantic_block_rule(command)
    if semantic_rule is not None:
        suggestion = BLOCKED_COMMAND_SUGGESTIONS.get(semantic_rule.suggestion_key)
        return BlockedCommandResult(
            is_blocked=True,
            pattern_matched=f"semantic:{' '.join(semantic_rule.sequence)}",
            category=semantic_rule.category,
            suggestion=suggestion,
        )

    for category, patterns in BLOCKED_PATTERNS.items():
        for pattern in patterns:
            if pattern.search(command):
                # Find suggestion for this command
                suggestion = None
                for cmd_prefix, cmd_suggestion in BLOCKED_COMMAND_SUGGESTIONS.items():
                    if cmd_prefix in command.lower():
                        suggestion = cmd_suggestion
                        break

                return BlockedCommandResult(
                    is_blocked=True,
                    pattern_matched=pattern.pattern,
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
    semantic_rule = _match_semantic_block_rule(command)
    if semantic_rule is not None:
        return BLOCKED_COMMAND_SUGGESTIONS.get(semantic_rule.suggestion_key)

    command_lower = command.lower()
    for cmd_prefix, suggestion in BLOCKED_COMMAND_SUGGESTIONS.items():
        if cmd_prefix in command_lower:
            return suggestion
    return None


def _match_semantic_block_rule(command: str) -> Optional[SemanticBlockedRule]:
    """Return the first semantic deny rule that matches a command."""
    semantics = analyze_command(command)
    for rule in SEMANTIC_BLOCKED_RULES:
        if rule.matches(semantics):
            return rule
    return None
