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


class TestAWSNewlyBlocked:
    """Test newly added AWS DESTRUCTIVE patterns are blocked."""

    @pytest.mark.parametrize("command", [
        "aws ec2 terminate-instances --instance-ids i-1234567",
        "aws kms schedule-key-deletion --key-id 1234",
        "aws organizations delete-organization",
        "aws route53 delete-hosted-zone --id Z12345",
    ])
    def test_aws_new_destructive_blocked(self, command):
        """Test newly added AWS destructive operations are blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is True
        assert result.category == "aws_critical"


class TestAWSApprovableNotBlocked:
    """Test AWS operations that are MUTATIVE (approvable T3) are NOT blocked."""

    @pytest.mark.parametrize("command", [
        "aws iam delete-user --user-name test-user",
        "aws iam delete-role --role-name test-role",
        "aws iam delete-access-key --user-name test --access-key-id AKIA123",
        "aws iam delete-policy --policy-arn arn:aws:iam::123:policy/test",
        "aws iam detach-role-policy --role-name test --policy-arn arn:aws:iam::123:policy/test",
        "aws iam remove-user-from-group --user-name test --group-name test-group",
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
        """Test AWS operations that are MUTATIVE (approvable) are NOT blocked."""
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


class TestGitDestructiveBlockedCommands:
    """Test Git destructive operations are permanently blocked."""

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
        assert result.category == "git_destructive"

    def test_git_force_with_lease_not_blocked(self):
        """Test git push --force-with-lease is NOT blocked."""
        result = is_blocked_command("git push --force-with-lease origin main")
        assert result.is_blocked is False

    def test_git_reset_hard_blocked(self):
        """Test git reset --hard is blocked."""
        result = is_blocked_command("git reset --hard HEAD~1")
        assert result.is_blocked is True
        assert result.category == "git_destructive"

    def test_git_reset_soft_not_blocked(self):
        """Test git reset --soft is NOT blocked."""
        result = is_blocked_command("git reset --soft HEAD~1")
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
        assert result.category == "git_destructive"


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
    """Test that safe and approvable commands are NOT blocked."""

    @pytest.mark.parametrize("command", [
        # Read-only commands
        "ls -la",
        "kubectl get pods",
        "terraform plan",
        "aws ec2 describe-instances",
        "gcloud compute instances list",

        # Commands that require APPROVAL but NOT permanently blocked
        "terraform apply",
        "terraform destroy -target=aws_instance.web",  # targeted destroy is approvable
        "kubectl apply -f manifest.yaml",
        "kubectl delete pod my-pod",
        "helm install my-release chart/",
        "helm uninstall my-release",  # helm uninstall is approvable
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


class TestBlockedPatternsCategories:
    """Test that all expected categories exist in BLOCKED_PATTERNS."""

    @pytest.mark.parametrize("category", [
        "aws_critical",
        "gcp_critical",
        "kubernetes_critical",
        "terraform_destroy",
        "docker_critical",
        "flux_critical",
        "git_destructive",
        "repo_delete",
        "npm_critical",
        "sql_critical",
        "disk_operations",
        "rm_critical",
    ])
    def test_category_exists(self, category):
        """Test that expected category exists."""
        assert category in BLOCKED_PATTERNS
        assert len(BLOCKED_PATTERNS[category]) > 0

    def test_exactly_thirteen_categories(self):
        """Test there are exactly 13 categories (including azure_critical)."""
        assert len(BLOCKED_PATTERNS) == 13


class TestTerraformDestroyBlocked:
    """Test terraform/terragrunt destroy patterns."""

    @pytest.mark.parametrize("command", [
        "terraform destroy",
        "terraform destroy --auto-approve",
        "terragrunt destroy",
        "terragrunt run-all destroy",
        "terragrunt destroy-all",
    ])
    def test_terraform_destroy_blocked(self, command):
        """Bare terraform/terragrunt destroy is blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is True

    @pytest.mark.parametrize("command", [
        "terraform destroy -target=aws_instance.web",
        "terraform destroy -target=module.vpc",
        "terragrunt destroy -target=aws_s3_bucket.data",
    ])
    def test_terraform_destroy_with_target_not_blocked(self, command):
        """terraform destroy -target=X is NOT blocked (approvable)."""
        result = is_blocked_command(command)
        assert result.is_blocked is False


class TestDockerCriticalBlocked:
    """Test Docker bulk prune operations are blocked."""

    @pytest.mark.parametrize("command", [
        "docker system prune -a",
        "docker system prune --all",
        "docker system prune --volumes",
        "docker system prune -a --volumes",
        "docker volume prune",
    ])
    def test_docker_prune_blocked(self, command):
        """Docker bulk prune operations are blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is True

    @pytest.mark.parametrize("command", [
        "docker system prune",  # without -a or --volumes, only dangling
        "docker rm my-container",
        "docker rmi my-image",
    ])
    def test_docker_safe_operations_not_blocked(self, command):
        """Docker single-resource operations are NOT blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is False


class TestFluxUninstallBlocked:
    """Test flux uninstall is blocked."""

    def test_flux_uninstall_blocked(self):
        result = is_blocked_command("flux uninstall")
        assert result.is_blocked is True
        assert result.category == "flux_critical"

    def test_flux_uninstall_silent_blocked(self):
        result = is_blocked_command("flux uninstall --silent")
        assert result.is_blocked is True


class TestRepoDeleteBlocked:
    """Test repo deletion commands are blocked."""

    def test_gh_repo_delete_blocked(self):
        result = is_blocked_command("gh repo delete my-org/my-repo")
        assert result.is_blocked is True
        assert result.category == "repo_delete"

    def test_glab_project_delete_blocked(self):
        result = is_blocked_command("glab project delete my-project")
        assert result.is_blocked is True
        assert result.category == "repo_delete"


class TestNpmUnpublishBlocked:
    """Test npm unpublish without version is blocked."""

    def test_npm_unpublish_bare_blocked(self):
        result = is_blocked_command("npm unpublish my-package")
        assert result.is_blocked is True
        assert result.category == "npm_critical"

    def test_npm_unpublish_with_version_not_blocked(self):
        result = is_blocked_command("npm unpublish my-package@1.0.0")
        assert result.is_blocked is False


class TestSQLCriticalBlocked:
    """Test SQL destructive commands are blocked."""

    @pytest.mark.parametrize("command", [
        "drop database production",
        "DROP TABLE users",
    ])
    def test_sql_drop_blocked(self, command):
        result = is_blocked_command(command)
        assert result.is_blocked is True
        assert result.category == "sql_critical"


class TestRmCriticalBlocked:
    """Test rm -rf / and rm -rf ~ are blocked."""

    @pytest.mark.parametrize("command", [
        "rm -rf /",
        "rm -rf /*",
        "rm -rf ~/",
        "rm -rf ~",
        "rm -fr /",
    ])
    def test_rm_critical_blocked(self, command):
        result = is_blocked_command(command)
        assert result.is_blocked is True

    def test_rm_normal_not_blocked(self):
        result = is_blocked_command("rm -rf /tmp/build")
        assert result.is_blocked is False


class TestKubectlDeleteAll:
    """Test kubectl delete with --all flag is blocked."""

    @pytest.mark.parametrize("command", [
        "kubectl delete pods --all",
        "kubectl delete deployments --all",
        "kubectl delete services --all",
    ])
    def test_kubectl_delete_all_blocked(self, command):
        result = is_blocked_command(command)
        assert result.is_blocked is True
        assert result.category == "kubernetes_critical"


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
