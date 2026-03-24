"""
Blocked command patterns - PERMANENTLY BLOCKED operations (exit 2, never approvable).

This is the single source of truth for DESTRUCTIVE commands. Commands matched here
are blocked with exit 2 and no nonce is generated -- they cannot be approved.

All other state-modifying commands are detected by the universal verb detector
(mutative_verbs.py) as MUTATIVE and routed through the nonce approval workflow.

Categories:
- AWS networking/data infrastructure delete operations
- AWS KMS/Route53/Organizations operations
- Azure resource group/networking/data/AKS/Key Vault delete operations
- GCP project/cluster/database delete operations
- Kubernetes critical delete operations (cluster, namespace, pv, node, CRD, webhooks)
- Kubernetes bulk delete operations (--all flag)
- Terraform/Terragrunt destroy (without -target)
- Docker bulk prune operations
- Flux uninstall (removes all Flux components)
- Git force push operations (not --force-with-lease)
- Git reset --hard
- GitHub/GitLab repo delete
- npm unpublish (entire package)
- SQL destructive operations (drop database, drop table)
- Disk/filesystem destruction operations (dd, fdisk, mkfs)
- rm -rf / and rm -rf ~
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
        # forbidden_flags uses prefix matching to handle -flag=value forms
        # (e.g., "-target=aws_instance.web" should match forbidden flag "-target")
        if self.forbidden_flags:
            for flag_token in semantics.flag_tokens:
                for forbidden in self.forbidden_flags:
                    if flag_token == forbidden or flag_token.startswith(forbidden + "="):
                        return False
        return True


# ============================================================================
# BLOCKED PATTERNS - PERMANENTLY BLOCKED (exit 2, no nonce, never approvable)
# ============================================================================
# These commands are PERMANENTLY BLOCKED and cannot be executed even with approval.
# They represent irreversible, catastrophic operations at scale.
#
# The following are MUTATIVE (approvable via nonce, handled by mutative_verbs.py):
# - aws iam delete-*, detach-*, remove-user-from-group
# - aws ec2 delete-key-pair, delete-snapshot, delete-volume,
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
# - kubectl delete pod/deployment/service/configmap/secret/clusterrole/clusterrolebinding
# - flux delete (single resource)
# - terraform destroy -target=<resource> (targeted)
# - helm uninstall (any release)
# - docker rm, docker rmi (individual containers/images)
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
        re.compile(r"aws\s+ec2\s+terminate-instances\b", re.IGNORECASE),
        re.compile(r"aws\s+rds\s+delete-db-instance(?!-)\b", re.IGNORECASE),
        re.compile(r"aws\s+rds\s+delete-db-cluster(?!-)\b", re.IGNORECASE),
        re.compile(r"aws\s+dynamodb\s+delete-table(?!-)\b", re.IGNORECASE),
        re.compile(r"aws\s+s3\s+rb(?!-)\b", re.IGNORECASE),
        re.compile(r"aws\s+s3api\s+delete-bucket(?!-)\b", re.IGNORECASE),
        re.compile(r"aws\s+elasticache\s+delete-cache-cluster(?!-)\b", re.IGNORECASE),
        re.compile(r"aws\s+elasticache\s+delete-replication-group(?!-)\b", re.IGNORECASE),
        re.compile(r"aws\s+eks\s+delete-cluster(?!-)\b", re.IGNORECASE),
        re.compile(r"aws\s+kms\s+schedule-key-deletion\b", re.IGNORECASE),
        re.compile(r"aws\s+organizations\s+delete-organization\b", re.IGNORECASE),
        re.compile(r"aws\s+route53\s+delete-hosted-zone\b", re.IGNORECASE),
    ],

    # Azure - Resource group, networking, data infrastructure (irreversible)
    "azure_critical": [
        re.compile(r"az\s+group\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+network\s+vnet\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+network\s+vnet\s+subnet\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+network\s+nsg\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+network\s+public-ip\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+network\s+application-gateway\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+network\s+lb\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+network\s+dns\s+zone\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+network\s+private-dns\s+zone\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+vm\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+vmss\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+disk\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+snapshot\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+image\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+sql\s+server\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+sql\s+db\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+cosmosdb\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+redis\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+storage\s+account\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+storage\s+container\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+storage\s+blob\s+delete-batch\b", re.IGNORECASE),
        re.compile(r"az\s+aks\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+aks\s+nodepool\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+acr\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+keyvault\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+keyvault\s+key\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+keyvault\s+secret\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+functionapp\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+webapp\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+ad\s+app\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+ad\s+sp\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+servicebus\s+namespace\s+delete\b", re.IGNORECASE),
        re.compile(r"az\s+eventhubs\s+namespace\s+delete\b", re.IGNORECASE),
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
        re.compile(r"kubectl\s+delete\s+ns\b", re.IGNORECASE),
        re.compile(r"kubectl\s+delete\s+node\b", re.IGNORECASE),
        re.compile(r"kubectl\s+delete\s+cluster\b", re.IGNORECASE),
        re.compile(r"kubectl\s+delete\s+(pv|persistentvolume)\b", re.IGNORECASE),
        re.compile(r"kubectl\s+delete\s+(pvc|persistentvolumeclaim)\b", re.IGNORECASE),
        re.compile(r"kubectl\s+delete\s+(crd|customresourcedefinition)\b", re.IGNORECASE),
        re.compile(r"kubectl\s+delete\s+mutatingwebhookconfiguration\b", re.IGNORECASE),
        re.compile(r"kubectl\s+delete\s+validatingwebhookconfiguration\b", re.IGNORECASE),
        re.compile(r"kubectl\s+drain\b", re.IGNORECASE),
        # Bulk delete with --all flag on any resource type
        re.compile(r"kubectl\s+delete\s+\w+\s+.*--all\b", re.IGNORECASE),
    ],

    # Terraform / Terragrunt - Whole-state destruction
    "terraform_destroy": [
        # terraform destroy WITHOUT -target (bare destroy is destructive)
        # Uses negative lookahead to allow "terraform destroy -target=X" through
        re.compile(r"terraform\s+destroy\b(?!.*-target)", re.IGNORECASE),
        re.compile(r"terragrunt\s+destroy\b(?!.*-target)", re.IGNORECASE),
        re.compile(r"terragrunt\s+run-all\s+destroy\b", re.IGNORECASE),
        re.compile(r"terragrunt\s+destroy-all\b", re.IGNORECASE),
    ],

    # Docker - Bulk prune operations
    "docker_critical": [
        re.compile(r"docker\s+system\s+prune\s+.*(-a|--all)\b", re.IGNORECASE),
        re.compile(r"docker\s+system\s+prune\s+.*--volumes\b", re.IGNORECASE),
        re.compile(r"docker\s+volume\s+prune\b", re.IGNORECASE),
    ],

    # Flux - Uninstall removes ALL Flux components from cluster
    "flux_critical": [
        re.compile(r"flux\s+uninstall\b", re.IGNORECASE),
    ],

    # Git - Force push (history rewrite, not --force-with-lease) and reset --hard
    "git_destructive": [
        re.compile(r"git\s+push\s+.*--force(?!-with-lease)", re.IGNORECASE),
        re.compile(r"git\s+push\s+.*(?<!\w)-f\b", re.IGNORECASE),
        re.compile(r"git\s+reset\s+.*--hard\b", re.IGNORECASE),
    ],

    # GitHub/GitLab - Repo deletion
    "repo_delete": [
        re.compile(r"gh\s+repo\s+delete\b", re.IGNORECASE),
        re.compile(r"glab\s+project\s+delete\b", re.IGNORECASE),
    ],

    # npm - Unpublish entire package (without @version)
    "npm_critical": [
        # Matches "npm unpublish packagename" but NOT "npm unpublish package@1.0.0"
        re.compile(r"npm\s+unpublish\s+(?!.*@)\S+", re.IGNORECASE),
    ],

    # SQL - Drop database/table
    "sql_critical": [
        re.compile(r"drop\s+database\b", re.IGNORECASE),
        re.compile(r"drop\s+table\b", re.IGNORECASE),
    ],

    # Disk operations - Irreversible data destruction
    "disk_operations": [
        re.compile(r"^dd\s+", re.IGNORECASE),
        re.compile(r"^fdisk\s+", re.IGNORECASE),
        re.compile(r"^mkfs(\.(ext[34]|fat|ntfs))?\s+", re.IGNORECASE),
    ],

    # rm -rf / and rm -rf ~ (catastrophic filesystem destruction)
    "rm_critical": [
        re.compile(r"rm\s+.*-[a-z]*r[a-z]*f[a-z]*\s+/\s*$", re.IGNORECASE),
        re.compile(r"rm\s+.*-[a-z]*f[a-z]*r[a-z]*\s+/\s*$", re.IGNORECASE),
        re.compile(r"rm\s+.*-[a-z]*r[a-z]*f[a-z]*\s+/\*", re.IGNORECASE),
        re.compile(r"rm\s+.*-[a-z]*f[a-z]*r[a-z]*\s+/\*", re.IGNORECASE),
        re.compile(r"rm\s+.*-[a-z]*r[a-z]*f[a-z]*\s+~/?", re.IGNORECASE),
        re.compile(r"rm\s+.*-[a-z]*f[a-z]*r[a-z]*\s+~/?", re.IGNORECASE),
    ],
}

# Suggestions for permanently blocked commands
BLOCKED_COMMAND_SUGGESTIONS = {
    # AWS suggestions
    "aws ec2 delete-vpc": "[BLOCKED] VPC deletion is irreversible - use Terraform/Terragrunt",
    "aws ec2 delete-subnet": "[BLOCKED] Subnet deletion is irreversible - use Terraform/Terragrunt",
    "aws ec2 delete-internet-gateway": "[BLOCKED] Internet gateway deletion is irreversible - use Terraform/Terragrunt",
    "aws ec2 delete-route-table": "[BLOCKED] Route table deletion is irreversible - use Terraform/Terragrunt",
    "aws ec2 delete-route": "[BLOCKED] Route deletion should be done via Terraform/Terragrunt",
    "aws ec2 terminate-instances": "[BLOCKED] Instance termination is irreversible - use Terraform/Terragrunt",
    "aws rds delete-db-instance": "[BLOCKED] Use Terraform/Terragrunt for RDS lifecycle management",
    "aws rds delete-db-cluster": "[BLOCKED] Use Terraform/Terragrunt for RDS cluster management",
    "aws dynamodb delete-table": "[BLOCKED] Table deletion loses all data - use Terraform/Terragrunt",
    "aws s3 rb": "[BLOCKED] Bucket removal is irreversible - use Terraform/Terragrunt",
    "aws s3api delete-bucket": "[BLOCKED] Bucket deletion is irreversible - use Terraform/Terragrunt",
    "aws eks delete-cluster": "[BLOCKED] Use Terraform/Terragrunt for EKS management",
    "aws elasticache delete": "[BLOCKED] Use Terraform/Terragrunt for ElastiCache management",
    "aws kms schedule-key-deletion": "[BLOCKED] KMS key deletion renders all encrypted data permanently unrecoverable",
    "aws organizations delete-organization": "[BLOCKED] Organization deletion is irreversible",
    "aws route53 delete-hosted-zone": "[BLOCKED] DNS zone deletion causes widespread outage",

    # Azure suggestions
    "az group delete": "[BLOCKED] Resource group deletion destroys all contained resources - use Terraform/Terragrunt",
    "az network vnet delete": "[BLOCKED] VNet deletion is irreversible - use Terraform/Terragrunt",
    "az network vnet subnet delete": "[BLOCKED] Subnet deletion is irreversible - use Terraform/Terragrunt",
    "az network nsg delete": "[BLOCKED] NSG deletion removes all security rules - use Terraform/Terragrunt",
    "az vm delete": "[BLOCKED] VM deletion is irreversible - use Terraform/Terragrunt",
    "az vmss delete": "[BLOCKED] Scale set deletion is irreversible - use Terraform/Terragrunt",
    "az disk delete": "[BLOCKED] Disk deletion loses all data - use Terraform/Terragrunt",
    "az sql server delete": "[BLOCKED] SQL Server deletion destroys all databases - use Terraform/Terragrunt",
    "az sql db delete": "[BLOCKED] Database deletion loses all data - use Terraform/Terragrunt",
    "az cosmosdb delete": "[BLOCKED] CosmosDB deletion is irreversible - use Terraform/Terragrunt",
    "az redis delete": "[BLOCKED] Redis deletion loses all cached data - use Terraform/Terragrunt",
    "az storage account delete": "[BLOCKED] Storage account deletion destroys all data - use Terraform/Terragrunt",
    "az aks delete": "[BLOCKED] AKS cluster deletion is irreversible - use Terraform/Terragrunt",
    "az acr delete": "[BLOCKED] Container registry deletion destroys all images - use Terraform/Terragrunt",
    "az keyvault delete": "[BLOCKED] Key Vault deletion can render encrypted data unrecoverable",
    "az functionapp delete": "[BLOCKED] Function App deletion is irreversible - use Terraform/Terragrunt",
    "az webapp delete": "[BLOCKED] Web App deletion is irreversible - use Terraform/Terragrunt",
    "az ad app delete": "[BLOCKED] App registration deletion breaks all dependent services",
    "az ad sp delete": "[BLOCKED] Service principal deletion breaks authentication for dependent services",
    "az servicebus namespace delete": "[BLOCKED] Service Bus namespace deletion is irreversible - use Terraform/Terragrunt",
    "az eventhubs namespace delete": "[BLOCKED] Event Hubs namespace deletion is irreversible - use Terraform/Terragrunt",

    # GCP suggestions
    "gcloud projects delete": "[BLOCKED] Project deletion is irreversible - must be done via Cloud Console",
    "gcloud container clusters delete": "[BLOCKED] Use Terraform/Terragrunt for GKE management",
    "gcloud sql instances delete": "[BLOCKED] Use Terraform/Terragrunt for Cloud SQL management",
    "gcloud sql databases delete": "[BLOCKED] Database deletion loses all data - use Terraform/Terragrunt",
    "gcloud services disable": "[BLOCKED] Disabling services can break dependent resources",
    "gsutil rb": "[BLOCKED] Bucket removal is irreversible",
    "gsutil rm -r": "[BLOCKED] Recursive deletion of cloud storage is irreversible",

    # Kubernetes suggestions
    "kubectl delete namespace": "[BLOCKED] Namespace deletion destroys all resources within it",
    "kubectl delete ns": "[BLOCKED] Namespace deletion destroys all resources within it",
    "kubectl delete node": "[BLOCKED] Node deletion removes the node from the cluster",
    "kubectl delete cluster": "[BLOCKED] Cluster deletion is irreversible and impacts all workloads",
    "kubectl delete pv": "[BLOCKED] Persistent volume deletion loses data",
    "kubectl delete pvc": "[BLOCKED] PVC deletion can trigger PV reclaim and data loss",
    "kubectl delete crd": "[BLOCKED] CRD deletion destroys all custom resources of that type",
    "kubectl delete mutatingwebhookconfiguration": "[BLOCKED] Webhook deletion can break admission control and cluster safety",
    "kubectl delete validatingwebhookconfiguration": "[BLOCKED] Webhook deletion can break admission control and cluster safety",
    "kubectl drain": "[BLOCKED] Node draining can cause service disruption",
    "kubectl delete --all": "[BLOCKED] Bulk deletion of all resources is irreversible",

    # Terraform / Terragrunt suggestions
    "terraform destroy": "[BLOCKED] terraform destroy without -target destroys entire state - use terraform destroy -target=<resource>",
    "terragrunt destroy": "[BLOCKED] terragrunt destroy without -target destroys entire state",
    "terragrunt run-all destroy": "[BLOCKED] Recursive destruction of all modules",
    "terragrunt destroy-all": "[BLOCKED] Recursive destruction of all modules",

    # Docker suggestions
    "docker system prune": "[BLOCKED] docker system prune with -a or --volumes removes all unused resources",
    "docker volume prune": "[BLOCKED] docker volume prune removes all unused volumes and data",

    # Flux suggestions
    "flux uninstall": "[BLOCKED] flux uninstall removes ALL Flux components from the cluster",

    # Git suggestions
    "git push --force": "[BLOCKED] Force push rewrites history - use git push --force-with-lease",
    "git push -f": "[BLOCKED] Force push rewrites history - use git push --force-with-lease",
    "git reset --hard": "[BLOCKED] git reset --hard permanently discards uncommitted changes",

    # GitHub/GitLab suggestions
    "gh repo delete": "[BLOCKED] Repository deletion is irreversible - destroys all code and history",
    "glab project delete": "[BLOCKED] Project deletion is irreversible - destroys all code and history",

    # npm suggestions
    "npm unpublish": "[BLOCKED] npm unpublish without @version removes entire package from registry",

    # SQL suggestions
    "drop database": "[BLOCKED] DROP DATABASE is irreversible - destroys all data",
    "drop table": "[BLOCKED] DROP TABLE is irreversible - destroys all data",

    # Disk operations
    "dd": "[BLOCKED] Low-level disk operations can destroy data",
    "fdisk": "[BLOCKED] Disk partitioning can destroy data",
    "mkfs": "[BLOCKED] Filesystem creation destroys all data on target",

    # rm critical
    "rm -rf /": "[BLOCKED] Filesystem destruction is irreversible",
    "rm -rf ~": "[BLOCKED] Home directory destruction is irreversible",
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
    SemanticBlockedRule("aws_critical", ("aws", "ec2", "terminate-instances"), "aws ec2 terminate-instances"),
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
    SemanticBlockedRule("aws_critical", ("aws", "kms", "schedule-key-deletion"), "aws kms schedule-key-deletion"),
    SemanticBlockedRule("aws_critical", ("aws", "organizations", "delete-organization"), "aws organizations delete-organization"),
    SemanticBlockedRule("aws_critical", ("aws", "route53", "delete-hosted-zone"), "aws route53 delete-hosted-zone"),

    # Azure
    SemanticBlockedRule("azure_critical", ("az", "group", "delete"), "az group delete"),
    SemanticBlockedRule("azure_critical", ("az", "network", "vnet", "delete"), "az network vnet delete"),
    SemanticBlockedRule("azure_critical", ("az", "network", "vnet", "subnet", "delete"), "az network vnet subnet delete"),
    SemanticBlockedRule("azure_critical", ("az", "network", "nsg", "delete"), "az network nsg delete"),
    SemanticBlockedRule("azure_critical", ("az", "network", "public-ip", "delete"), "az network vnet delete"),
    SemanticBlockedRule("azure_critical", ("az", "network", "application-gateway", "delete"), "az network vnet delete"),
    SemanticBlockedRule("azure_critical", ("az", "network", "lb", "delete"), "az network vnet delete"),
    SemanticBlockedRule("azure_critical", ("az", "network", "dns", "zone", "delete"), "az network vnet delete"),
    SemanticBlockedRule("azure_critical", ("az", "network", "private-dns", "zone", "delete"), "az network vnet delete"),
    SemanticBlockedRule("azure_critical", ("az", "vm", "delete"), "az vm delete"),
    SemanticBlockedRule("azure_critical", ("az", "vmss", "delete"), "az vmss delete"),
    SemanticBlockedRule("azure_critical", ("az", "disk", "delete"), "az disk delete"),
    SemanticBlockedRule("azure_critical", ("az", "snapshot", "delete"), "az disk delete"),
    SemanticBlockedRule("azure_critical", ("az", "image", "delete"), "az disk delete"),
    SemanticBlockedRule("azure_critical", ("az", "sql", "server", "delete"), "az sql server delete"),
    SemanticBlockedRule("azure_critical", ("az", "sql", "db", "delete"), "az sql db delete"),
    SemanticBlockedRule("azure_critical", ("az", "cosmosdb", "delete"), "az cosmosdb delete"),
    SemanticBlockedRule("azure_critical", ("az", "redis", "delete"), "az redis delete"),
    SemanticBlockedRule("azure_critical", ("az", "storage", "account", "delete"), "az storage account delete"),
    SemanticBlockedRule("azure_critical", ("az", "storage", "container", "delete"), "az storage account delete"),
    SemanticBlockedRule("azure_critical", ("az", "storage", "blob", "delete-batch"), "az storage account delete"),
    SemanticBlockedRule("azure_critical", ("az", "aks", "delete"), "az aks delete"),
    SemanticBlockedRule("azure_critical", ("az", "aks", "nodepool", "delete"), "az aks delete"),
    SemanticBlockedRule("azure_critical", ("az", "acr", "delete"), "az acr delete"),
    SemanticBlockedRule("azure_critical", ("az", "keyvault", "delete"), "az keyvault delete"),
    SemanticBlockedRule("azure_critical", ("az", "keyvault", "key", "delete"), "az keyvault delete"),
    SemanticBlockedRule("azure_critical", ("az", "keyvault", "secret", "delete"), "az keyvault delete"),
    SemanticBlockedRule("azure_critical", ("az", "functionapp", "delete"), "az functionapp delete"),
    SemanticBlockedRule("azure_critical", ("az", "webapp", "delete"), "az webapp delete"),
    SemanticBlockedRule("azure_critical", ("az", "ad", "app", "delete"), "az ad app delete"),
    SemanticBlockedRule("azure_critical", ("az", "ad", "sp", "delete"), "az ad sp delete"),
    SemanticBlockedRule("azure_critical", ("az", "servicebus", "namespace", "delete"), "az servicebus namespace delete"),
    SemanticBlockedRule("azure_critical", ("az", "eventhubs", "namespace", "delete"), "az eventhubs namespace delete"),

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
    SemanticBlockedRule("kubernetes_critical", ("kubectl", "delete", "ns"), "kubectl delete ns"),
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

    # Terraform / Terragrunt (semantic rules use forbidden_flags to allow -target through)
    SemanticBlockedRule(
        "terraform_destroy",
        ("terraform", "destroy"),
        "terraform destroy",
        forbidden_flags=("-target", "--target"),
    ),
    SemanticBlockedRule(
        "terraform_destroy",
        ("terragrunt", "destroy"),
        "terragrunt destroy",
        forbidden_flags=("-target", "--target"),
    ),
    SemanticBlockedRule("terraform_destroy", ("terragrunt", "run-all", "destroy"), "terragrunt run-all destroy"),
    SemanticBlockedRule("terraform_destroy", ("terragrunt", "destroy-all"), "terragrunt destroy-all"),

    # Docker
    SemanticBlockedRule("docker_critical", ("docker", "system", "prune"), "docker system prune", required_flags=("-a",)),
    SemanticBlockedRule("docker_critical", ("docker", "system", "prune"), "docker system prune", required_flags=("--all",)),
    SemanticBlockedRule("docker_critical", ("docker", "system", "prune"), "docker system prune", required_flags=("--volumes",)),
    SemanticBlockedRule("docker_critical", ("docker", "volume", "prune"), "docker volume prune"),

    # Flux
    SemanticBlockedRule("flux_critical", ("flux", "uninstall"), "flux uninstall"),

    # Git
    SemanticBlockedRule("git_destructive", ("git", "push"), "git push --force", required_flags=("--force",)),
    SemanticBlockedRule("git_destructive", ("git", "push"), "git push -f", required_flags=("-f",)),
    SemanticBlockedRule("git_destructive", ("git", "reset"), "git reset --hard", required_flags=("--hard",)),

    # GitHub/GitLab
    SemanticBlockedRule("repo_delete", ("gh", "repo", "delete"), "gh repo delete"),
    SemanticBlockedRule("repo_delete", ("glab", "project", "delete"), "glab project delete"),
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
