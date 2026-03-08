#!/usr/bin/env python3
"""
Tests for Blocked Command Detection.

Tests ONLY commands that are PERMANENTLY BLOCKED (deny list).
Commands that are approvable T3 operations (detected by the universal verb
detector) are NOT tested here.

Validates:
1. AWS critical networking/data infrastructure deletes are blocked
2. GCP project/cluster/database deletes are blocked
3. Kubernetes critical operations (namespace, pv, node, cluster, CRD, webhooks) are blocked
4. Git force push is blocked (but not --force-with-lease)
5. Disk destruction operations are blocked
6. Safe commands and approvable T3 commands are NOT blocked
"""

import re
import sys
import pytest
from pathlib import Path

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.blocked_commands import (
    is_blocked_command,
    get_blocked_patterns,
    get_blocked_patterns_by_category,
    get_suggestion_for_blocked,
    BlockedCommandResult,
    BLOCKED_PATTERNS,
    BLOCKED_COMMAND_SUGGESTIONS,
)


class TestAWSCriticalBlockedCommands:
    """Test AWS critical infrastructure delete operations are permanently blocked."""

    @pytest.mark.parametrize("command", [
        "aws ec2 delete-vpc --vpc-id vpc-123",
        "aws ec2 delete-subnet --subnet-id subnet-123",
        "aws ec2 delete-internet-gateway --internet-gateway-id igw-123",
        "aws ec2 delete-route-table --route-table-id rtb-123",
        "aws ec2 delete-route --route-table-id rtb-123 --destination-cidr-block 0.0.0.0/0",
        "aws rds delete-db-instance --db-instance-identifier my-db",
        "aws rds delete-db-cluster --db-cluster-identifier my-cluster",
        "aws dynamodb delete-table --table-name my-table",
        "aws s3 rb s3://my-bucket --force",
        "aws s3api delete-bucket --bucket my-bucket",
        "aws elasticache delete-cache-cluster --cache-cluster-id my-cluster",
        "aws elasticache delete-replication-group --replication-group-id my-group",
        "aws eks delete-cluster --name my-cluster",
    ])
    def test_aws_critical_delete_blocked(self, command):
        """Test AWS critical delete operations are blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is True
        assert result.category == "aws_critical"


class TestAWSApprovableNotBlocked:
    """Test AWS operations removed from deny (now approvable T3) are NOT blocked."""

    @pytest.mark.parametrize("command", [
        "aws iam delete-user --user-name test-user",
        "aws iam delete-role --role-name test-role",
        "aws iam delete-access-key --user-name test --access-key-id AKIA123",
        "aws iam delete-policy --policy-arn arn:aws:iam::123:policy/test",
        "aws iam detach-role-policy --role-name test --policy-arn arn:aws:iam::123:policy/test",
        "aws iam remove-user-from-group --user-name test --group-name test-group",
        "aws ec2 terminate-instances --instance-ids i-1234567",
        "aws ec2 delete-key-pair --key-name test-key",
        "aws ec2 delete-snapshot --snapshot-id snap-123",
        "aws ec2 delete-volume --volume-id vol-123",
        "aws ec2 delete-security-group --group-id sg-123",
        "aws ec2 delete-network-interface --network-interface-id eni-123",
        "aws lambda delete-function --function-name my-function",
        "aws rds delete-db-parameter-group --db-parameter-group-name my-pg",
        "aws rds delete-db-cluster-parameter-group --db-cluster-parameter-group-name my-cpg",
        "aws cloudformation delete-stack --stack-name my-stack",
        "aws s3api delete-objects --bucket my-bucket --delete file://delete.json",
        "aws sns delete-topic --topic-arn arn:aws:sns:us-east-1:123:test",
        "aws sqs delete-queue --queue-url https://sqs.us-east-1.amazonaws.com/123/test",
        "aws dynamodb delete-item --table-name my-table --key '{\"id\":{\"S\":\"1\"}}'",
        "aws backup delete-recovery-point --backup-vault-name test --recovery-point-arn arn:123",
        "aws eks delete-nodegroup --cluster-name my-cluster --nodegroup-name my-ng",
        "aws eks delete-addon --cluster-name my-cluster --addon-name my-addon",
    ])
    def test_aws_approvable_not_blocked(self, command):
        """Test AWS operations moved to approvable T3 are NOT blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is False


class TestGCPCriticalBlockedCommands:
    """Test GCP critical delete operations are permanently blocked."""

    @pytest.mark.parametrize("command", [
        "gcloud projects delete my-project",
        "gcloud container clusters delete my-cluster --region us-central1",
        "gcloud sql instances delete my-sql-instance",
        "gcloud sql databases delete my-db --instance my-instance",
        "gcloud services disable compute.googleapis.com",
        "gsutil rb gs://my-bucket",
        "gsutil rm -r gs://my-bucket/*",
    ])
    def test_gcp_critical_delete_blocked(self, command):
        """Test GCP critical delete operations are blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is True
        assert result.category == "gcp_critical"


class TestGCPApprovableNotBlocked:
    """Test GCP operations removed from deny (now approvable T3) are NOT blocked."""

    @pytest.mark.parametrize("command", [
        "gcloud compute firewall-rules delete my-rule",
        "gcloud compute instances delete my-instance",
        "gcloud compute networks delete my-network",
        "gcloud compute disks delete my-disk",
        "gcloud compute images delete my-image",
        "gcloud compute snapshots delete my-snapshot",
        "gcloud container node-pools delete my-pool --cluster my-cluster",
        "gcloud iam roles delete my-role --project my-project",
        "gcloud storage rm gs://bucket/object",
    ])
    def test_gcp_approvable_not_blocked(self, command):
        """Test GCP operations moved to approvable T3 are NOT blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is False


class TestKubernetesCriticalBlockedCommands:
    """Test Kubernetes CRITICAL operations are permanently blocked."""

    @pytest.mark.parametrize("command", [
        "kubectl delete namespace production",
        "kubectl delete node worker-node-1",
        "kubectl delete cluster my-cluster",
        "kubectl delete pv my-persistent-volume",
        "kubectl delete persistentvolume my-pv",
        "kubectl delete pvc my-claim",
        "kubectl delete persistentvolumeclaim my-claim",
        "kubectl delete crd mycustomresources.example.com",
        "kubectl delete customresourcedefinition mycrd.example.com",
        "kubectl delete mutatingwebhookconfiguration my-webhook",
        "kubectl delete validatingwebhookconfiguration my-webhook",
        "kubectl drain worker-node-1",
    ])
    def test_kubernetes_critical_blocked(self, command):
        """Test Kubernetes CRITICAL operations are blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is True
        assert result.category == "kubernetes_critical"


class TestKubernetesApprovableNotBlocked:
    """Test Kubernetes operations removed from deny are NOT blocked."""

    @pytest.mark.parametrize("command", [
        "kubectl delete clusterrole my-role",
        "kubectl delete clusterrolebinding my-binding",
        "kubectl delete pod my-pod",
        "kubectl delete deployment my-deployment",
        "kubectl delete service my-service",
    ])
    def test_kubernetes_approvable_not_blocked(self, command):
        """Test Kubernetes operations moved to approvable T3 are NOT blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is False


class TestGitForceBlockedCommands:
    """Test Git force push operations are permanently blocked."""

    @pytest.mark.parametrize("command", [
        "git push --force origin main",
        "git push -f origin main",
        "git push origin --force",
        "git push origin -f",
    ])
    def test_git_force_push_blocked(self, command):
        """Test Git force push is blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is True
        assert result.category == "git_force"

    def test_git_force_with_lease_not_blocked(self):
        """Test git push --force-with-lease is NOT blocked."""
        result = is_blocked_command("git push --force-with-lease origin main")
        assert result.is_blocked is False


class TestBlockedCommandVariants:
    """Deny-listed commands stay blocked even with leading global flags."""

    def test_aws_delete_vpc_with_profile_and_region_is_blocked(self):
        result = is_blocked_command(
            "aws --profile prod --region us-east-1 ec2 delete-vpc --vpc-id vpc-123"
        )
        assert result.is_blocked is True
        assert result.category == "aws_critical"

    def test_gcloud_delete_cluster_with_project_is_blocked(self):
        result = is_blocked_command(
            "gcloud --project dev container clusters delete cluster-a --region us-central1"
        )
        assert result.is_blocked is True
        assert result.category == "gcp_critical"

    def test_kubectl_delete_namespace_with_context_is_blocked(self):
        result = is_blocked_command(
            "kubectl --context prod --namespace default delete namespace payments"
        )
        assert result.is_blocked is True
        assert result.category == "kubernetes_critical"

    def test_git_force_push_with_worktree_flag_is_blocked(self):
        result = is_blocked_command("git -C repo push origin main --force")
        assert result.is_blocked is True
        assert result.category == "git_force"


class TestFluxDeleteNotBlocked:
    """Test Flux delete operations are no longer blocked (moved to approvable T3)."""

    @pytest.mark.parametrize("command", [
        "flux delete source git my-source",
        "flux delete helmrelease my-release",
        "flux delete kustomization my-kustomization",
    ])
    def test_flux_delete_not_blocked(self, command):
        """Test Flux delete operations are NOT blocked (approvable T3)."""
        result = is_blocked_command(command)
        assert result.is_blocked is False


class TestDiskOperationsBlocked:
    """Test disk destruction operations are permanently blocked."""

    @pytest.mark.parametrize("command", [
        "dd if=/dev/zero of=/dev/sda",
        "fdisk /dev/sda",
        "mkfs.ext4 /dev/sda1",
        "mkfs /dev/sda1",
    ])
    def test_disk_operations_blocked(self, command):
        """Test disk destruction operations are blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is True
        assert result.category == "disk_operations"


class TestSafeCommandsNotBlocked:
    """Test that safe commands are NOT blocked."""

    @pytest.mark.parametrize("command", [
        # Read-only commands
        "ls -la",
        "kubectl get pods",
        "terraform plan",
        "aws ec2 describe-instances",
        "gcloud compute instances list",

        # Commands that require APPROVAL but NOT blocked
        "terraform apply",
        "terraform destroy",
        "kubectl apply -f manifest.yaml",
        "kubectl delete pod my-pod",
        "helm install my-release chart/",
        "git commit -m 'message'",
        "git push origin main",
        "git push --force-with-lease origin main",

        # Dry-run commands
        "terraform plan -out=plan.tfplan",
        "kubectl apply --dry-run=client -f manifest.yaml",
        "flux reconcile source git my-source",
    ])
    def test_safe_and_approvable_commands_not_blocked(self, command):
        """Test safe commands and approvable T3 commands are NOT blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is False


class TestBlockedCommandResult:
    """Test BlockedCommandResult structure."""

    def test_blocked_command_has_category(self):
        """Blocked command result includes category."""
        result = is_blocked_command("aws eks delete-cluster my-cluster")
        assert result.is_blocked is True
        assert result.category == "aws_critical"
        assert result.pattern_matched is not None

    def test_safe_command_has_no_category(self):
        """Safe command result has no category."""
        result = is_blocked_command("ls -la")
        assert result.is_blocked is False
        assert result.category is None
        assert result.pattern_matched is None


class TestGetBlockedPatterns:
    """Test get_blocked_patterns() function."""

    def test_returns_list(self):
        """get_blocked_patterns() returns a list."""
        patterns = get_blocked_patterns()
        assert isinstance(patterns, list)
        assert len(patterns) > 0

    def test_patterns_are_compiled(self):
        """All patterns are pre-compiled regex objects."""
        patterns = get_blocked_patterns()
        assert all(isinstance(p, re.Pattern) for p in patterns)

    def test_contains_critical_patterns(self):
        """Patterns include critical commands."""
        patterns = get_blocked_patterns()
        patterns_str = " ".join(p.pattern for p in patterns)

        # Should contain AWS critical
        assert "aws" in patterns_str
        assert "delete" in patterns_str

        # Should contain Kubernetes critical
        assert "kubectl" in patterns_str
        assert "namespace" in patterns_str


class TestGetBlockedPatternsByCategory:
    """Test get_blocked_patterns_by_category() function."""

    @pytest.mark.parametrize("category", [
        "aws_critical",
        "gcp_critical",
        "kubernetes_critical",
        "git_force",
        "disk_operations",
    ])
    def test_returns_patterns_for_valid_category(self, category):
        """Returns patterns for valid categories."""
        patterns = get_blocked_patterns_by_category(category)
        assert isinstance(patterns, list)
        assert len(patterns) > 0

    def test_returns_empty_for_invalid_category(self):
        """Returns empty list for invalid category."""
        patterns = get_blocked_patterns_by_category("nonexistent_category")
        assert patterns == []

    def test_removed_categories_no_longer_exist(self):
        """Verify removed categories are gone."""
        assert "aws_delete" not in BLOCKED_PATTERNS
        assert "gcp_delete" not in BLOCKED_PATTERNS
        assert "flux_delete" not in BLOCKED_PATTERNS


class TestGetSuggestionForBlocked:
    """Test get_suggestion_for_blocked() function."""

    def test_returns_suggestion_for_known_commands(self):
        """Returns suggestions for known blocked commands."""
        suggestion = get_suggestion_for_blocked("aws eks delete-cluster")
        assert suggestion is not None
        assert "BLOCKED" in suggestion or "Terraform" in suggestion

    def test_returns_none_for_unknown_commands(self):
        """Returns None for unknown commands."""
        suggestion = get_suggestion_for_blocked("unknown_command")
        assert suggestion is None

    def test_returns_suggestion_for_vpc_delete(self):
        """Returns suggestion for VPC delete."""
        suggestion = get_suggestion_for_blocked("aws ec2 delete-vpc --vpc-id vpc-123")
        assert suggestion is not None
        assert "BLOCKED" in suggestion

    def test_returns_suggestion_for_namespace_delete(self):
        """Returns suggestion for namespace delete."""
        suggestion = get_suggestion_for_blocked("kubectl delete namespace production")
        assert suggestion is not None
        assert "BLOCKED" in suggestion


class TestBlockedPatternsCategories:
    """Test that all expected categories exist in BLOCKED_PATTERNS."""

    @pytest.mark.parametrize("category", [
        "aws_critical",
        "gcp_critical",
        "kubernetes_critical",
        "git_force",
        "disk_operations",
    ])
    def test_category_exists(self, category):
        """Test that expected category exists."""
        assert category in BLOCKED_PATTERNS
        assert len(BLOCKED_PATTERNS[category]) > 0

    def test_exactly_five_categories(self):
        """Test there are exactly 5 categories (no more, no less)."""
        assert len(BLOCKED_PATTERNS) == 5


class TestEdgeCases:
    """Test edge cases and special scenarios."""

    def test_empty_command_not_blocked(self):
        """Empty command is not blocked."""
        result = is_blocked_command("")
        assert result.is_blocked is False

    def test_case_insensitive_matching(self):
        """Commands should match case-insensitively."""
        result1 = is_blocked_command("aws eks delete-cluster")
        assert result1.is_blocked is True

        result2 = is_blocked_command("AWS eks delete-cluster")
        assert result2.is_blocked is True

    def test_blocked_within_compound_command(self):
        """Detects blocked command even in compound statements."""
        result = is_blocked_command("echo 'test' && aws eks delete-cluster my-cluster")
        assert result.is_blocked is True
