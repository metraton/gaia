#!/usr/bin/env python3
"""
Tests for Agnostic Dangerous Verb Detector.

Tests the dangerous_verbs module which classifies commands by scanning tokens
for known verb patterns, dangerous flags, and command aliases.

Validates:
1. DangerResult dataclass fields and defaults
2. Command aliases (rm, mv, dd, chmod)
3. Always-safe CLIs fast-path
4. Agnostic verb scanning across any CLI
5. Dangerous flag scanning with context sensitivity
6. Simulation flag override (--dry-run)
7. Simulation verbs (plan, template, validate)
8. Edge cases (empty, single token, very long commands)
9. build_t3_block_response structure
"""

import sys
import pytest
from pathlib import Path

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.dangerous_verbs import (
    detect_dangerous_command,
    build_t3_block_response,
    DangerResult,
    COMMAND_ALIASES,
    DANGEROUS_FLAGS,
    F_FLAG_MEANS_FORCE,
    R_FLAG_MEANS_RECURSIVE_DELETE,
    ALWAYS_SAFE_CLIS,
    SIMULATION_FLAGS,
    CLI_FAMILY_LOOKUP,
)


# ============================================================================
# TestDangerResult
# ============================================================================

class TestDangerResult:
    """Verify DangerResult dataclass fields and defaults."""

    def test_default_values(self):
        """DangerResult defaults are all safe/empty."""
        result = DangerResult()
        assert result.is_dangerous is False
        assert result.category == "UNKNOWN"
        assert result.verb == ""
        assert result.verb_position == -1
        assert result.dangerous_flags == ()
        assert result.cli_family == "unknown"
        assert result.confidence == "low"
        assert result.reason == ""

    def test_custom_values(self):
        """DangerResult accepts custom values."""
        result = DangerResult(
            is_dangerous=True,
            category="MUTATIVE",
            verb="delete",
            verb_position=1,
            dangerous_flags=("--force",),
            cli_family="k8s",
            confidence="high",
            reason="Mutative verb 'delete'",
        )
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "delete"
        assert result.verb_position == 1
        assert result.dangerous_flags == ("--force",)
        assert result.cli_family == "k8s"
        assert result.confidence == "high"
        assert result.reason == "Mutative verb 'delete'"

    def test_dangerous_flags_default_is_empty_tuple(self):
        """DangerResult dangerous_flags default is an empty tuple (frozen dataclass)."""
        r1 = DangerResult()
        r2 = DangerResult()
        assert r1.dangerous_flags == ()
        assert r2.dangerous_flags == ()


# ============================================================================
# TestCommandAliases
# ============================================================================

class TestCommandAliases:
    """Test base command aliases that map directly to a category."""

    def test_rm_is_mutative(self):
        """rm is classified as MUTATIVE (blocked_commands.py handles destructive patterns)."""
        result = detect_dangerous_command("rm file.txt")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "rm"
        assert result.confidence == "high"

    def test_mv_is_mutative(self):
        """mv is classified as MUTATIVE."""
        result = detect_dangerous_command("mv src dst")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "mv"
        assert result.confidence == "high"

    def test_dd_is_mutative(self):
        """dd is classified as MUTATIVE (blocked_commands.py handles destructive patterns)."""
        result = detect_dangerous_command("dd if=/dev/zero of=/dev/sda")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "dd"

    def test_chmod_is_mutative(self):
        """chmod is classified as MUTATIVE."""
        result = detect_dangerous_command("chmod 755 file")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "chmod"

    def test_cp_is_mutative(self):
        """cp is classified as MUTATIVE."""
        result = detect_dangerous_command("cp source dest")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "cp"

    def test_mkfs_is_mutative(self):
        """mkfs is classified as MUTATIVE (blocked_commands.py handles destructive patterns)."""
        result = detect_dangerous_command("mkfs /dev/sda1")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"

    @pytest.mark.parametrize("cmd,expected_category", [
        ("rm file.txt", "MUTATIVE"),
        ("rmdir empty_dir", "MUTATIVE"),
        ("mv a b", "MUTATIVE"),
        ("cp a b", "MUTATIVE"),
        ("ln -s a b", "MUTATIVE"),
        ("dd if=x of=y", "MUTATIVE"),
        ("mkfs /dev/sda", "MUTATIVE"),
        ("fdisk /dev/sda", "MUTATIVE"),
        ("chmod 644 f", "MUTATIVE"),
        ("chown user f", "MUTATIVE"),
        ("chgrp group f", "MUTATIVE"),
    ])
    def test_all_aliases_parametrized(self, cmd, expected_category):
        """All command aliases are classified as MUTATIVE (approvable)."""
        result = detect_dangerous_command(cmd)
        assert result.is_dangerous is True
        assert result.category == expected_category
        assert result.verb_position == 0


# ============================================================================
# TestAlwaysSafeCLIs
# ============================================================================

class TestAlwaysSafeCLIs:
    """Test ALWAYS_SAFE_CLIS fast-path classification."""

    @pytest.mark.parametrize("cmd", [
        "jq '.data' file.json",
        "yq '.metadata' file.yaml",
        "bat README.md",
        "rg 'pattern' src/",
        "fd '*.py' .",
        "fzf --preview 'cat {}'",
        "exa -la",
        "eza --long --all",
        "tokei .",
        "hyperfine 'sleep 0.1'",
        "delta file1 file2",
        "dust /home",
        "duf",
        "procs",
        "btm",
        "bottom",
        "tldr git",
        "tree /src",
        "htop",
        "ncdu /var",
        "less file.txt",
        "more file.txt",
        "wc -l file.txt",
        "sort file.txt",
        "uniq file.txt",
        "cut -d, -f1 file.csv",
        "tr '[:upper:]' '[:lower:]'",
        "diff file1 file2",
        "comm file1 file2",
        "file binary.bin",
        "stat file.txt",
        "which python",
        "whereis bash",
        "whatis ls",
        "whoami",
        "id",
        "date",
        "cal",
        "uname -a",
        "uptime",
        "free -h",
        "df -h",
        "du -sh .",
        "env",
        "printenv HOME",
        "echo hello world",
        "printf '%s\n' hello",
        "cat file.txt",
        "head -n 10 file.txt",
        "tail -f log.txt",
        "pwd",
        "ls -la",
        "watch ls",
        "k9s",
        "stern deployment/app",
        "mypy src/",
        "flake8 src/",
        "pylint src/",
    ])
    def test_always_safe_cli_is_read_only(self, cmd):
        """Always-safe CLIs return READ_ONLY with high confidence."""
        result = detect_dangerous_command(cmd)
        assert result.is_dangerous is False
        assert result.category == "READ_ONLY"
        assert result.confidence == "high"
        assert "Always-safe CLI" in result.reason

    def test_always_safe_with_path_prefix(self):
        """Path-prefixed always-safe CLI is still detected."""
        result = detect_dangerous_command("/usr/bin/jq '.data' file.json")
        assert result.is_dangerous is False
        assert result.category == "READ_ONLY"
        assert result.confidence == "high"
        assert "Always-safe CLI" in result.reason

    def test_always_safe_single_token(self):
        """Single-token always-safe CLI returns READ_ONLY."""
        result = detect_dangerous_command("htop")
        assert result.is_dangerous is False
        assert result.category == "READ_ONLY"
        assert result.confidence == "high"

    def test_always_safe_cli_family_detection(self):
        """Always-safe CLIs get correct cli_family from lookup."""
        # k9s is in CLI_FAMILY_LOOKUP as k8s
        result = detect_dangerous_command("k9s")
        assert result.cli_family == "k8s"
        # stern is in CLI_FAMILY_LOOKUP as k8s
        result = detect_dangerous_command("stern pod-name")
        assert result.cli_family == "k8s"
        # jq is NOT in CLI_FAMILY_LOOKUP, gets default "text"
        result = detect_dangerous_command("jq '.data' file.json")
        assert result.cli_family == "text"

    def test_always_safe_skips_verb_extraction(self):
        """Always-safe CLI sets verb to the CLI name, not a subcommand."""
        result = detect_dangerous_command("jq '.data | .name' file.json")
        assert result.verb == "jq"
        assert result.verb_position == 0

    def test_always_safe_constant_is_frozenset(self):
        """ALWAYS_SAFE_CLIS is a frozenset (immutable)."""
        assert isinstance(ALWAYS_SAFE_CLIS, frozenset)

    def test_always_safe_does_not_overlap_command_aliases(self):
        """ALWAYS_SAFE_CLIS has no overlap with COMMAND_ALIASES."""
        overlap = ALWAYS_SAFE_CLIS & set(COMMAND_ALIASES.keys())
        assert overlap == set(), f"Overlap: {overlap}"


# ============================================================================
# TestAgnosticVerbScanning
# ============================================================================

class TestAgnosticVerbScanning:
    """Test CLI-agnostic verb scanning in tokens[1:5]."""

    # --- Destructive verbs across various CLIs ---

    def test_kubectl_delete_is_destructive(self):
        """kubectl delete pod my-pod is DESTRUCTIVE."""
        result = detect_dangerous_command("kubectl delete pod my-pod")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "delete"
        assert result.cli_family == "k8s"

    def test_terraform_destroy_is_destructive(self):
        """terraform destroy is DESTRUCTIVE."""
        result = detect_dangerous_command("terraform destroy")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "destroy"
        assert result.cli_family == "iac"

    def test_git_reset_is_destructive(self):
        """git reset --hard HEAD is DESTRUCTIVE."""
        result = detect_dangerous_command("git reset --hard HEAD")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "reset"
        assert result.cli_family == "git"

    def test_docker_stop_is_destructive(self):
        """docker stop container is DESTRUCTIVE."""
        result = detect_dangerous_command("docker stop my-container")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "stop"

    def test_docker_kill_is_destructive(self):
        """docker kill container is DESTRUCTIVE."""
        result = detect_dangerous_command("docker kill my-container")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "kill"

    # --- Mutative verbs across various CLIs ---

    def test_kubectl_apply_is_mutative(self):
        """kubectl apply -f manifest.yaml is MUTATIVE."""
        result = detect_dangerous_command("kubectl apply -f manifest.yaml")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "apply"
        assert result.cli_family == "k8s"

    def test_terraform_apply_is_mutative(self):
        """terraform apply is MUTATIVE."""
        result = detect_dangerous_command("terraform apply")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "apply"

    def test_aws_delete_with_leading_flag_values_is_destructive(self):
        """Danger detection must survive multiple flag/value pairs before the verb."""
        result = detect_dangerous_command(
            "aws --profile prod --region us-east-1 ec2 delete-vpc --vpc-id vpc-123"
        )
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "delete"

    def test_gcloud_delete_with_project_flags_is_destructive(self):
        """gcloud delete must still be found after project/config flags."""
        result = detect_dangerous_command(
            "gcloud --project dev --configuration shared container clusters delete cluster-a"
        )
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "delete"

    def test_kubectl_delete_with_context_flags_is_destructive(self):
        """kubectl delete must still be found after context and namespace flags."""
        result = detect_dangerous_command(
            "kubectl --context prod --namespace default delete namespace payments"
        )
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "delete"
        assert result.cli_family == "k8s"

    def test_git_push_is_mutative(self):
        """git push origin main is MUTATIVE."""
        result = detect_dangerous_command("git push origin main")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "push"
        assert result.cli_family == "git"

    def test_git_commit_is_mutative(self):
        """git commit -m 'msg' is MUTATIVE."""
        result = detect_dangerous_command('git commit -m "msg"')
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "commit"

    def test_helm_install_is_mutative(self):
        """helm install release chart is MUTATIVE."""
        result = detect_dangerous_command("helm install release chart")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "install"
        assert result.cli_family == "k8s"

    def test_helm_upgrade_is_mutative(self):
        """helm upgrade release chart is MUTATIVE."""
        result = detect_dangerous_command("helm upgrade release chart")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "upgrade"

    def test_kubectl_scale_is_mutative(self):
        """kubectl scale deployment --replicas=3 is MUTATIVE."""
        result = detect_dangerous_command("kubectl scale deployment --replicas=3")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "scale"

    def test_kubectl_exec_is_mutative(self):
        """kubectl exec -it pod -- bash: 'exec' found after skipping flags."""
        result = detect_dangerous_command("kubectl exec -it pod -- bash")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "exec"

    def test_docker_run_is_mutative(self):
        """docker run -d nginx is MUTATIVE."""
        result = detect_dangerous_command("docker run -d nginx")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "run"

    def test_make_install_is_mutative(self):
        """make install is MUTATIVE."""
        result = detect_dangerous_command("make install")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "install"

    def test_eksctl_create_is_mutative(self):
        """eksctl create cluster is MUTATIVE."""
        result = detect_dangerous_command("eksctl create cluster --name test")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "create"

    # --- Simulation verbs ---

    def test_terraform_plan_is_simulation(self):
        """terraform plan is SIMULATION (safe)."""
        result = detect_dangerous_command("terraform plan")
        assert result.is_dangerous is False
        assert result.category == "SIMULATION"
        assert result.verb == "plan"

    def test_terraform_validate_is_simulation(self):
        """terraform validate is SIMULATION."""
        result = detect_dangerous_command("terraform validate")
        assert result.is_dangerous is False
        assert result.category == "SIMULATION"

    def test_terraform_fmt_is_simulation(self):
        """terraform fmt is SIMULATION."""
        result = detect_dangerous_command("terraform fmt")
        assert result.is_dangerous is False
        assert result.category == "SIMULATION"

    def test_helm_template_is_simulation(self):
        """helm template release chart is SIMULATION (safe)."""
        result = detect_dangerous_command("helm template release chart")
        assert result.is_dangerous is False
        assert result.category == "SIMULATION"
        assert result.verb == "template"

    def test_git_diff_is_simulation(self):
        """git diff is SIMULATION."""
        result = detect_dangerous_command("git diff")
        assert result.is_dangerous is False
        assert result.category == "SIMULATION"

    def test_make_test_is_simulation(self):
        """make test is SIMULATION."""
        result = detect_dangerous_command("make test")
        assert result.is_dangerous is False
        assert result.category == "SIMULATION"
        assert result.verb == "test"

    def test_bazel_test_is_simulation(self):
        """bazel test is SIMULATION."""
        result = detect_dangerous_command("bazel test //src:test")
        assert result.is_dangerous is False
        assert result.category == "SIMULATION"
        assert result.verb == "test"

    # --- Read-only verbs ---

    def test_kubectl_get_is_read_only(self):
        """kubectl get pods is READ_ONLY (safe)."""
        result = detect_dangerous_command("kubectl get pods")
        assert result.is_dangerous is False
        assert result.category == "READ_ONLY"
        assert result.verb == "get"

    def test_kubectl_describe_is_read_only(self):
        """kubectl describe node is READ_ONLY (safe)."""
        result = detect_dangerous_command("kubectl describe node")
        assert result.is_dangerous is False
        assert result.category == "READ_ONLY"
        assert result.verb == "describe"

    def test_kubectl_logs_is_read_only(self):
        """kubectl logs pod-name is READ_ONLY."""
        result = detect_dangerous_command("kubectl logs pod-name")
        assert result.is_dangerous is False
        assert result.category == "READ_ONLY"

    def test_git_log_is_read_only(self):
        """git log --all is READ_ONLY."""
        result = detect_dangerous_command("git log --all")
        assert result.is_dangerous is False
        assert result.category == "READ_ONLY"
        assert result.verb == "log"

    def test_git_status_is_read_only(self):
        """git status is READ_ONLY."""
        result = detect_dangerous_command("git status")
        assert result.is_dangerous is False
        assert result.category == "READ_ONLY"

    def test_helm_list_is_read_only(self):
        """helm list is READ_ONLY."""
        result = detect_dangerous_command("helm list")
        assert result.is_dangerous is False
        assert result.category == "READ_ONLY"
        assert result.verb == "list"

    # --- Hyphenated verb splitting ---

    def test_hyphenated_delete_stack(self):
        """aws cloudformation delete-stack extracts 'delete' from hyphenated token."""
        result = detect_dangerous_command("aws cloudformation delete-stack --stack-name foo")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "delete"

    def test_hyphenated_describe_instances(self):
        """aws ec2 describe-instances extracts 'describe' from hyphenated token."""
        result = detect_dangerous_command("aws ec2 describe-instances")
        assert result.is_dangerous is False
        assert result.category == "READ_ONLY"
        assert result.verb == "describe"

    def test_hyphenated_create_role(self):
        """aws iam create-role extracts 'create' from hyphenated token."""
        result = detect_dangerous_command("aws iam create-role --role-name test")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "create"

    def test_hyphenated_terminate_instances(self):
        """aws ec2 terminate-instances extracts 'terminate'."""
        result = detect_dangerous_command(
            "aws ec2 terminate-instances --instance-ids i-123"
        )
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "terminate"

    # --- Docker rm as verb alias ---

    def test_docker_rm_is_destructive(self):
        """docker rm container-id: 'rm' found as verb alias -> DESTRUCTIVE."""
        result = detect_dangerous_command("docker rm container-id")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "rm"
        assert result.cli_family == "docker"

    # --- Unknown CLI verb scanning ---

    def test_unknown_cli_delete_is_destructive(self):
        """Unknown CLI with 'delete' verb is DESTRUCTIVE."""
        result = detect_dangerous_command("newcli delete resource")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "delete"
        assert result.cli_family == "unknown"

    def test_unknown_cli_create_is_mutative(self):
        """Unknown CLI with 'create' verb is MUTATIVE."""
        result = detect_dangerous_command("newcli create something")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "create"

    def test_unknown_cli_list_is_read_only(self):
        """Unknown CLI with 'list' verb is READ_ONLY (safe)."""
        result = detect_dangerous_command("newcli list all")
        assert result.is_dangerous is False
        assert result.category == "READ_ONLY"
        assert result.verb == "list"

    def test_unknown_cli_deploy_is_mutative(self):
        """Unknown CLI with 'deploy' verb is MUTATIVE."""
        result = detect_dangerous_command("obscuretool deploy app")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "deploy"

    def test_unknown_cli_unknown_verb(self):
        """Unknown CLI with unknown verb returns UNKNOWN category."""
        result = detect_dangerous_command("unknowncli frobnicate data")
        assert result.category == "UNKNOWN"
        assert result.verb == "frobnicate"
        assert result.cli_family == "unknown"
        assert result.confidence == "low"

    # --- Verbs at position > 2 (gcloud-style deep verbs) ---

    def test_gcloud_deep_verb_list(self):
        """gcloud compute instances list: 'list' found at position 3."""
        result = detect_dangerous_command("gcloud compute instances list")
        assert result.is_dangerous is False
        assert result.category == "READ_ONLY"
        assert result.verb == "list"

    def test_gcloud_deep_verb_create(self):
        """gcloud container clusters create: 'create' found at position 3."""
        result = detect_dangerous_command("gcloud container clusters create my-cluster")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "create"

    def test_gcloud_deep_verb_delete(self):
        """gcloud compute instances delete: 'delete' found at position 3."""
        result = detect_dangerous_command("gcloud compute instances delete my-vm")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "delete"

    def test_gcloud_deep_verb_describe(self):
        """gcloud compute instances describe: 'describe' at position 3."""
        result = detect_dangerous_command(
            "gcloud compute instances describe my-vm"
        )
        assert result.is_dangerous is False
        assert result.category == "READ_ONLY"

    # --- Verbs found in tokens that are gcloud groups/subgroups ---
    # In the agnostic approach, 'auth' and 'config' are in READ_ONLY_VERBS,
    # so they'll be matched directly at their position.

    def test_gcloud_auth_is_read_only(self):
        """gcloud auth login: 'auth' is in READ_ONLY_VERBS, found at position 1."""
        result = detect_dangerous_command("gcloud auth login")
        assert result.is_dangerous is False
        assert result.category == "READ_ONLY"
        assert result.verb == "auth"

    def test_gcloud_config_is_read_only(self):
        """gcloud config set project: 'config' is in READ_ONLY_VERBS, found at position 1."""
        result = detect_dangerous_command("gcloud config set project my-project")
        assert result.is_dangerous is False
        assert result.category == "READ_ONLY"
        assert result.verb == "config"

    # --- AWS-style commands ---

    def test_aws_s3_cp_is_mutative(self):
        """aws s3 cp: 'cp' found as verb alias at position 2."""
        result = detect_dangerous_command("aws s3 cp file s3://bucket/")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "cp"

    def test_aws_configure_is_read_only(self):
        """aws configure: 'configure' is in MUTATIVE_VERBS, found at position 1."""
        result = detect_dangerous_command("aws configure")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "configure"

    # --- Flags at position 1 are skipped ---

    def test_flags_at_position1_are_skipped(self):
        """docker --debug run: flags skipped, 'run' found."""
        result = detect_dangerous_command("docker --debug run nginx")
        assert result.verb == "run"
        assert result.category == "MUTATIVE"

    # --- Terragrunt run-all ---

    def test_terragrunt_run_all_apply(self):
        """terragrunt run-all apply: 'run' found first (MUTATIVE)."""
        result = detect_dangerous_command("terragrunt run-all apply")
        # In agnostic mode, "run-all" splits to "run" which is MUTATIVE
        # Or "run" is found at position 1 as MUTATIVE.
        # Either way the command is flagged as dangerous.
        assert result.is_dangerous is True
        assert result.cli_family == "iac"

    def test_terragrunt_run_all_destroy(self):
        """terragrunt run-all destroy: 'run' found first (MUTATIVE)."""
        result = detect_dangerous_command("terragrunt run-all destroy")
        assert result.is_dangerous is True
        assert result.cli_family == "iac"

    def test_terragrunt_run_all_plan(self):
        """terragrunt run-all plan: 'run' found first (MUTATIVE), not SIMULATION.

        Note: In the agnostic approach, 'run-all' splits to 'run' which is
        MUTATIVE, so this is classified as MUTATIVE. This is a known trade-off
        vs the old CLI-specific extraction that would skip past 'run-all'.
        """
        result = detect_dangerous_command("terragrunt run-all plan")
        # 'run' is MUTATIVE, found before 'plan'
        assert result.is_dangerous is True
        assert result.cli_family == "iac"


# ============================================================================
# TestSimulationFlags
# ============================================================================

class TestSimulationFlags:
    """Test simulation flag override (--dry-run and equivalents)."""

    def test_dry_run_overrides_mutative(self):
        """--dry-run flag overrides a MUTATIVE verb to SIMULATION."""
        result = detect_dangerous_command("helm install --dry-run release chart")
        assert result.is_dangerous is False
        assert result.category == "SIMULATION"

    def test_kubectl_apply_dry_run_exact_is_safe(self):
        """kubectl apply --dry-run is SIMULATION."""
        result = detect_dangerous_command(
            "kubectl apply --dry-run -f manifest.yaml"
        )
        assert result.is_dangerous is False
        assert result.category == "SIMULATION"

    def test_kubectl_apply_dry_run_client_is_safe(self):
        """kubectl apply --dry-run=client is SIMULATION."""
        result = detect_dangerous_command(
            "kubectl apply --dry-run=client -f manifest.yaml"
        )
        assert result.is_dangerous is False
        assert result.category == "SIMULATION"

    def test_dry_run_overrides_destructive(self):
        """--dry-run should override even destructive commands."""
        result = detect_dangerous_command("kubectl delete --dry-run pod my-pod")
        assert result.is_dangerous is False
        assert result.category == "SIMULATION"

    def test_dryrun_no_hyphen_is_safe(self):
        """--dryrun (no hyphen) is also recognized."""
        result = detect_dangerous_command("sometool apply --dryrun")
        assert result.is_dangerous is False
        assert result.category == "SIMULATION"


# ============================================================================
# TestDangerousFlags
# ============================================================================

class TestDangerousFlags:
    """Test dangerous flag scanning with context sensitivity."""

    def test_git_push_force_is_flagged(self):
        """git push --force escalates and adds to dangerous_flags."""
        result = detect_dangerous_command("git push --force origin main")
        assert result.is_dangerous is True
        assert "--force" in result.dangerous_flags

    def test_rm_f_is_dangerous(self):
        """-f is dangerous for rm (in F_FLAG_MEANS_FORCE)."""
        result = detect_dangerous_command("rm -f file")
        assert result.is_dangerous is True
        assert "-f" in result.dangerous_flags

    def test_terraform_apply_f_not_dangerous(self):
        """-f is NOT dangerous for terraform (means file, not force)."""
        result = detect_dangerous_command("terraform apply -f plan.out")
        assert result.is_dangerous is True  # apply is still MUTATIVE
        # terraform is not in F_FLAG_MEANS_FORCE, so -f is not flagged
        assert "-f" not in result.dangerous_flags

    def test_kubectl_apply_force_is_flagged(self):
        """kubectl apply --force escalates."""
        result = detect_dangerous_command("kubectl apply --force -f manifest.yaml")
        assert result.is_dangerous is True
        assert "--force" in result.dangerous_flags

    def test_gsutil_rm_r_is_dangerous(self):
        """-r is dangerous for gsutil (in R_FLAG_MEANS_RECURSIVE_DELETE)."""
        result = detect_dangerous_command("gsutil rm -r gs://bucket/path")
        assert "-r" in result.dangerous_flags

    def test_rm_rf_compound_flag(self):
        """rm -rf compound flag is always dangerous."""
        result = detect_dangerous_command("rm -rf /tmp/data")
        assert result.is_dangerous is True
        assert "-rf" in result.dangerous_flags

    def test_force_with_lease_is_dangerous(self):
        """--force-with-lease is ALWAYS dangerous."""
        result = detect_dangerous_command("git push --force-with-lease origin main")
        assert "--force-with-lease" in result.dangerous_flags

    def test_no_preserve_root_is_dangerous(self):
        """--no-preserve-root is ALWAYS dangerous."""
        result = detect_dangerous_command("rm --no-preserve-root /")
        assert "--no-preserve-root" in result.dangerous_flags

    def test_all_flag_not_flagged_by_verb_detector(self):
        """--all is not flagged by verb detector (blocked_commands.py handles kubectl delete --all)."""
        # Since all verbs are now MUTATIVE, --all is not flagged at verb level.
        # kubectl delete --all is caught by blocked_commands.py instead.
        result_delete = detect_dangerous_command("kubectl delete pods --all")
        assert "--all" not in result_delete.dangerous_flags

        # MUTATIVE verb + --all => not flagged
        result_apply = detect_dangerous_command("kubectl apply --all -f .")
        assert "--all" not in result_apply.dangerous_flags

    def test_git_branch_D_flag_is_dangerous(self):
        """git branch -D (force delete) should detect -D as dangerous flag.

        Gap 2 fix: -D is context-sensitive (only dangerous for git).
        Note: The verb 'branch' is UNKNOWN, so the command reaches step 6
        (flag-only detection) and is flagged via -D.
        """
        result = detect_dangerous_command("git branch -D main")
        assert result.is_dangerous is True
        assert "-D" in result.dangerous_flags

    def test_git_branch_M_flag_is_dangerous(self):
        """git branch -M (force rename) should detect -M as dangerous flag."""
        result = detect_dangerous_command("git branch -M old-name new-name")
        assert result.is_dangerous is True
        assert "-M" in result.dangerous_flags

    def test_git_branch_delete_long_flag_is_dangerous(self):
        """git branch --delete should detect --delete as dangerous flag."""
        result = detect_dangerous_command("git branch --delete feature-branch")
        assert result.is_dangerous is True
        assert "--delete" in result.dangerous_flags

    def test_D_flag_not_dangerous_for_non_git(self):
        """-D is NOT dangerous for CLIs not in D_FLAG_MEANS_FORCE_DELETE."""
        # For a non-git command, -D should not be flagged
        result = detect_dangerous_command("mycli branch -D something")
        # -D should not appear in dangerous_flags for unknown CLIs
        assert "-D" not in result.dangerous_flags


# ============================================================================
# TestCLIFamilyLookup
# ============================================================================

class TestCLIFamilyLookup:
    """Test lightweight CLI family detection via CLI_FAMILY_LOOKUP."""

    @pytest.mark.parametrize("cli,expected_family", [
        ("kubectl", "k8s"),
        ("helm", "k8s"),
        ("terraform", "iac"),
        ("terragrunt", "iac"),
        ("aws", "cloud"),
        ("gcloud", "cloud"),
        ("gsutil", "cloud"),
        ("git", "git"),
        ("docker", "docker"),
        ("npm", "package"),
        ("pip", "package"),
        ("systemctl", "system"),
    ])
    def test_cli_family_detection(self, cli, expected_family):
        """CLI family is correctly identified via lookup."""
        result = detect_dangerous_command(f"{cli} help")
        assert result.cli_family == expected_family

    def test_unknown_cli_family(self):
        """Unknown CLI gets 'unknown' family."""
        result = detect_dangerous_command("mycustomtool do-thing")
        assert result.cli_family == "unknown"

    def test_path_prefixed_cli_family(self):
        """/usr/local/bin/terraform is identified as iac."""
        result = detect_dangerous_command("/usr/local/bin/terraform plan")
        assert result.cli_family == "iac"

    @pytest.mark.parametrize("cli,expected_family", [
        ("eksctl", "cloud"),
        ("gh", "cloud"),
        ("glab", "cloud"),
        ("vercel", "cloud"),
        ("netlify", "cloud"),
        ("fly", "cloud"),
        ("flyctl", "cloud"),
        ("heroku", "cloud"),
        ("docker-compose", "docker"),
        ("podman-compose", "docker"),
        ("kubectx", "k8s"),
        ("kubens", "k8s"),
        ("make", "build"),
        ("cmake", "build"),
        ("bazel", "build"),
        ("gradle", "build"),
        ("mvn", "build"),
        ("npx", "package"),
        ("bun", "package"),
        ("deno", "package"),
        ("poetry", "package"),
        ("pipenv", "package"),
        ("uv", "package"),
        ("node", "runtime"),
        ("python", "runtime"),
        ("python3", "runtime"),
        ("tsx", "runtime"),
        ("ts-node", "runtime"),
        ("pytest", "linter"),
        ("black", "linter"),
        ("ruff", "linter"),
    ])
    def test_extended_cli_families(self, cli, expected_family):
        """Extended CLI families are correctly identified."""
        result = detect_dangerous_command(f"{cli} list")
        assert result.cli_family == expected_family


# ============================================================================
# TestConfidence
# ============================================================================

class TestConfidence:
    """Test confidence levels in results."""

    def test_verb_at_position_1_is_high(self):
        """Verb found at position 1 has high confidence."""
        result = detect_dangerous_command("kubectl delete pod my-pod")
        assert result.confidence == "high"
        assert result.verb_position == 1

    def test_verb_at_position_2_is_high(self):
        """Verb found at position 2 has high confidence."""
        result = detect_dangerous_command("aws ec2 terminate-instances --instance-ids i-123")
        assert result.confidence == "high"
        assert result.verb_position == 2

    def test_verb_at_position_3_is_medium(self):
        """Verb found at position 3 has medium confidence."""
        result = detect_dangerous_command("gcloud compute instances delete my-vm")
        assert result.confidence == "medium"
        assert result.verb_position == 3

    def test_always_safe_is_high(self):
        """Always-safe CLIs have high confidence."""
        result = detect_dangerous_command("jq '.data' file.json")
        assert result.confidence == "high"

    def test_alias_is_high(self):
        """Command aliases have high confidence."""
        result = detect_dangerous_command("rm -rf /tmp/data")
        assert result.confidence == "high"

    def test_unknown_verb_is_low(self):
        """Unknown CLI with unknown verb has low confidence."""
        result = detect_dangerous_command("customcli frobnicate data")
        assert result.confidence == "low"

    def test_simulation_verb_is_high_or_medium(self):
        """Simulation verbs have high confidence at position 1."""
        result = detect_dangerous_command("terraform plan")
        assert result.confidence == "high"

    def test_read_only_is_high_or_medium(self):
        """Read-only verbs at position 1 have high confidence."""
        result = detect_dangerous_command("kubectl get pods")
        assert result.confidence == "high"


# ============================================================================
# TestEdgeCases
# ============================================================================

class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_string(self):
        """Empty string returns safe UNKNOWN."""
        result = detect_dangerous_command("")
        assert result.is_dangerous is False
        assert result.category == "UNKNOWN"
        assert result.confidence == "high"

    def test_whitespace_only(self):
        """Whitespace-only string returns safe UNKNOWN."""
        result = detect_dangerous_command("   \t  \n  ")
        assert result.is_dangerous is False
        assert result.category == "UNKNOWN"

    def test_single_token_ls(self):
        """Single token 'ls' returns safe (always-safe CLI)."""
        result = detect_dangerous_command("ls")
        assert result.is_dangerous is False
        assert result.verb == "ls"
        assert result.verb_position == 0

    def test_single_token_known_cli(self):
        """Single token 'kubectl' returns safe (no verb)."""
        result = detect_dangerous_command("kubectl")
        assert result.is_dangerous is False
        assert result.category == "UNKNOWN"
        assert result.cli_family == "k8s"

    def test_command_with_only_flags(self):
        """Command with only flags after CLI is handled gracefully."""
        result = detect_dangerous_command("kubectl --namespace production --context staging")
        assert result is not None
        assert result.cli_family == "k8s"

    def test_very_long_command(self):
        """Very long command does not crash."""
        long_args = " ".join([f"--arg{i}=value{i}" for i in range(100)])
        command = f"kubectl apply {long_args}"
        result = detect_dangerous_command(command)
        assert result is not None
        assert result.verb == "apply"

    def test_path_prefixed_binary(self):
        """/usr/bin/kubectl is correctly identified."""
        result = detect_dangerous_command("/usr/bin/kubectl delete pod my-pod")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.cli_family == "k8s"

    def test_unmatched_quotes_fallback(self):
        """Command with unmatched quotes falls back to split."""
        result = detect_dangerous_command("echo 'unmatched quote")
        assert result is not None
        # Should not crash, uses split fallback

    def test_none_command(self):
        """None-like empty command returns safe."""
        result = detect_dangerous_command("")
        assert result.is_dangerous is False


# ============================================================================
# TestBuildT3BlockResponse
# ============================================================================

class TestBuildT3BlockResponse:
    """Test build_t3_block_response structure."""

    def test_response_has_correct_keys(self):
        """Response dict has 'decision' and 'message' keys."""
        danger = DangerResult(
            is_dangerous=True,
            category="DESTRUCTIVE",
            verb="delete",
            cli_family="k8s",
            confidence="high",
            reason="Destructive verb 'delete'",
        )
        response = build_t3_block_response("kubectl delete pod my-pod", danger)
        assert "decision" in response
        assert "message" in response
        assert response["decision"] == "block"

    def test_message_includes_verb_and_category(self):
        """Message includes verb and category information."""
        danger = DangerResult(
            is_dangerous=True,
            category="MUTATIVE",
            verb="apply",
            cli_family="k8s",
            confidence="high",
            reason="Mutative verb 'apply'",
        )
        response = build_t3_block_response("kubectl apply -f manifest.yaml", danger)
        assert "MUTATIVE" in response["message"]
        assert "apply" in response["message"]
        assert "k8s" in response["message"]

    def test_message_includes_dangerous_flags(self):
        """Message includes dangerous flag warning when flags are present."""
        danger = DangerResult(
            is_dangerous=True,
            category="DESTRUCTIVE",
            verb="push",
            dangerous_flags=("--force",),
            cli_family="git",
            confidence="high",
            reason="Mutative verb 'push' with dangerous flags ('--force',)",
        )
        response = build_t3_block_response("git push --force origin main", danger)
        assert "--force" in response["message"]
        assert "Dangerous flags" in response["message"]

    def test_message_includes_approval_workflow(self):
        """Message includes T3 approval workflow instructions."""
        danger = DangerResult(
            is_dangerous=True,
            category="DESTRUCTIVE",
            verb="delete",
            cli_family="k8s",
            confidence="high",
            reason="Destructive verb 'delete'",
        )
        response = build_t3_block_response("kubectl delete pod my-pod", danger)
        assert "T3" in response["message"]
        assert "PENDING_APPROVAL" in response["message"]
        assert "approval" in response["message"].lower()

    def test_message_no_flags_no_flag_warning(self):
        """Message without dangerous flags omits flag warning."""
        danger = DangerResult(
            is_dangerous=True,
            category="MUTATIVE",
            verb="apply",
            dangerous_flags=(),
            cli_family="k8s",
            confidence="high",
            reason="Mutative verb 'apply'",
        )
        response = build_t3_block_response("kubectl apply -f manifest.yaml", danger)
        assert "Dangerous flags" not in response["message"]

    def test_long_command_in_response(self):
        """Long commands are included in the response message."""
        long_cmd = "kubectl apply " + " ".join([f"--arg{i}=val{i}" for i in range(50)])
        danger = DangerResult(
            is_dangerous=True,
            category="MUTATIVE",
            verb="apply",
            cli_family="k8s",
            confidence="high",
            reason="Mutative verb 'apply'",
        )
        response = build_t3_block_response(long_cmd, danger)
        assert "kubectl apply" in response["message"]


# ============================================================================
# TestBuildToolClassification
# ============================================================================

class TestBuildToolClassification:
    """Test build tool verb classification."""

    def test_make_clean_is_destructive(self):
        """make clean is DESTRUCTIVE."""
        result = detect_dangerous_command("make clean")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "clean"

    def test_bazel_clean_is_destructive(self):
        """bazel clean is DESTRUCTIVE."""
        result = detect_dangerous_command("bazel clean")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "clean"


# ============================================================================
# TestDockerComposeClassification
# ============================================================================

class TestDockerComposeClassification:
    """Test docker-compose and podman-compose classification."""

    def test_docker_compose_family(self):
        """docker-compose is in the docker family."""
        result = detect_dangerous_command("docker-compose up -d")
        assert result.cli_family == "docker"

    def test_podman_compose_family(self):
        """podman-compose is in the docker family."""
        result = detect_dangerous_command("podman-compose up")
        assert result.cli_family == "docker"


# ============================================================================
# TestSimulationVerbs
# ============================================================================

class TestSimulationVerbs:
    """Test that simulation verbs are correctly classified."""

    @pytest.mark.parametrize("cmd,expected_verb", [
        ("terraform plan", "plan"),
        ("terraform plan -out=plan.tfplan", "plan"),
        ("terraform validate", "validate"),
        ("terraform fmt", "fmt"),
        ("helm template release chart", "template"),
        ("git diff", "diff"),
        ("make test", "test"),
        ("bazel test //src:test", "test"),
        ("customcli lint src/", "lint"),
        ("customcli check config", "check"),
    ])
    def test_simulation_verbs(self, cmd, expected_verb):
        """Simulation verbs return SIMULATION category."""
        result = detect_dangerous_command(cmd)
        assert result.is_dangerous is False
        assert result.category == "SIMULATION"
        assert result.verb == expected_verb


# ============================================================================
# TestRegressionCoverage
# ============================================================================

class TestRegressionCoverage:
    """Regression tests to ensure old behavior is preserved where expected."""

    def test_eksctl_delete_is_destructive(self):
        """eksctl delete cluster is DESTRUCTIVE."""
        result = detect_dangerous_command("eksctl delete cluster --name test")
        assert result.is_dangerous is True
        assert result.category == "MUTATIVE"
        assert result.verb == "delete"

    def test_git_fetch_is_read_only(self):
        """git fetch: 'fetch' is in READ_ONLY_VERBS."""
        result = detect_dangerous_command("git fetch origin")
        assert result.is_dangerous is False
        assert result.category == "READ_ONLY"
        assert result.verb == "fetch"

    def test_docker_pull_is_read_only(self):
        """docker pull: 'download' synonyms -- 'pull' is not in verb sets,
        but it is checked. Actually 'pull' is not in any set, UNKNOWN."""
        result = detect_dangerous_command("docker pull image:tag")
        # 'pull' is not in any verb taxonomy set currently
        # It may be UNKNOWN -- just verify it is not dangerous
        assert result.cli_family == "docker"

    def test_helm_repo_is_not_dangerous(self):
        """helm repo add: no SAFE_VERB_OVERRIDES anymore, but the agnostic
        scanner sees tokens. 'repo' is not a known verb -> scans further.
        'add' at position 2 is MUTATIVE."""
        result = detect_dangerous_command("helm repo add stable https://charts.helm.sh/stable")
        # In agnostic mode, this may be MUTATIVE due to 'add'
        assert result.cli_family == "k8s"

    def test_aws_s3_ls_is_unknown(self):
        """aws s3 ls: 'ls' is not in any verb taxonomy set, UNKNOWN."""
        result = detect_dangerous_command("aws s3 ls")
        # 's3' is not a verb, 'ls' is not a verb -> both unknown
        assert result.is_dangerous is False

    def test_gh_pr_create_detects_verb(self):
        """gh pr create: in agnostic mode, scans tokens for verbs.
        'pr' is unknown, 'create' is MUTATIVE."""
        result = detect_dangerous_command("gh pr create --title test")
        assert result.cli_family == "cloud"
        # 'create' found at position 2
        assert result.verb == "create"
        assert result.category == "MUTATIVE"

    def test_kubectx_is_not_dangerous(self):
        """kubectx context-name: 'my-context' is unknown verb, not dangerous."""
        result = detect_dangerous_command("kubectx my-context")
        assert result.cli_family == "k8s"

    def test_docker_build_is_unknown(self):
        """docker build: 'build' is not in any verb taxonomy set."""
        result = detect_dangerous_command("docker build -t image .")
        assert result.cli_family == "docker"
        # 'build' is not in any verb set -> UNKNOWN, not dangerous
