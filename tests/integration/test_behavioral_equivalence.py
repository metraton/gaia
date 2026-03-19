#!/usr/bin/env python3
"""
T020: Behavioral Equivalence Test Suite (50+ command scenarios).

CRITICAL test: verify that the adapter-based flow produces IDENTICAL results
to direct business logic calls. Parametrized with 50+ commands covering
all security categories.

Each test sends a command through the full flow:
  1. Build Claude Code stdin JSON
  2. ClaudeCodeAdapter.parse_event()
  3. ClaudeCodeAdapter.parse_pre_tool_use()
  4. BashValidator.validate()
  5. Verify allowed/blocked/tier matches expected

Categories covered:
  - Safe by elimination (ls, cat, docker ps, docker images, git add, git stash)
  - READ_ONLY verbs (kubectl get, helm list, terraform show, gcloud describe)
  - SIMULATION verbs (terraform plan, helm template, kubectl diff)
  - MUTATIVE verbs (git commit, git push, kubectl apply, terraform apply)
  - BLOCKED patterns (rm -rf /, terraform destroy, kubectl delete namespace)
  - HTTP verbs (post, put, patch, delete via API CLIs)
  - API implicit GET (glab api, gh api without -X)
  - Dry-run overrides (terraform apply --dry-run, kubectl apply --dry-run=client)
  - Command aliases (rm, dd, mkfs)

Modules under test:
  - hooks/adapters/claude_code.py
  - hooks/modules/tools/bash_validator.py
  - hooks/modules/security/mutative_verbs.py
  - hooks/modules/security/blocked_commands.py
"""

import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup (follows existing project conventions)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from adapters.claude_code import ClaudeCodeAdapter
from adapters.types import ValidationResult
from modules.tools.bash_validator import BashValidator
from modules.security.tiers import SecurityTier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _classify_command_via_adapter(command: str) -> tuple:
    """Run a command through the full adapter + validator flow.

    Returns:
        (allowed: bool, tier_label: str) where tier_label is one of:
        "T0", "T1", "T2", "T3", or "BLOCKED".
    """
    adapter = ClaudeCodeAdapter()
    stdin_json = json.dumps({
        "hook_event_name": "PreToolUse",
        "session_id": "behavioral-equiv-session",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    })

    event = adapter.parse_event(stdin_json)
    validation_req = adapter.parse_pre_tool_use(event.payload)

    validator = BashValidator()
    result = validator.validate(validation_req.command)

    # Determine tier label
    if not result.allowed and result.block_response is None:
        tier_label = "BLOCKED"
    else:
        tier_label = str(result.tier)

    return result.allowed, tier_label


def _classify_command_direct(command: str) -> tuple:
    """Run a command through direct validator call (no adapter).

    Returns:
        (allowed: bool, tier_label: str) matching the adapter flow.
    """
    validator = BashValidator()
    result = validator.validate(command)

    if not result.allowed and result.block_response is None:
        tier_label = "BLOCKED"
    else:
        tier_label = str(result.tier)

    return result.allowed, tier_label


# ============================================================================
# Behavioral Equivalence: adapter flow == direct flow
# ============================================================================

# Commands that must produce IDENTICAL results whether routed through the
# adapter or called directly. This is the core contract of the adapter layer.
EQUIVALENCE_COMMANDS = [
    # --- Safe by elimination ---
    "ls -la",
    "ls",
    "pwd",
    "cat /etc/hostname",
    "echo hello world",
    "docker ps",
    "docker images",
    "docker inspect container-123",
    "git add .",
    "git stash",
    "git stash list",
    "git diff",
    "git log --oneline",
    "wc -l file.txt",
    "head -20 file.txt",
    "tail -f log.txt",
    "grep -r pattern .",
    "find . -name '*.py'",
    "python3 --version",
    "node --version",
    "npm --version",
    # --- READ_ONLY verbs ---
    "kubectl get pods",
    "kubectl get pods -n default",
    "kubectl get svc -A",
    "kubectl describe pod nginx",
    "kubectl logs nginx-pod",
    "helm list",
    "helm list -A",
    "helm status my-release",
    "terraform show",
    "terraform output",
    "gcloud compute instances list",
    "gcloud compute instances describe instance-1",
    "gcloud container clusters list",
    "aws s3 ls",
    "aws ec2 describe-instances",
    "aws eks list-clusters",
    "gh api repos/owner/repo",
    "glab api projects/123",
    # --- SIMULATION verbs ---
    "terraform plan",
    "terraform plan -out=plan.tfplan",
    "helm template my-release ./chart",
    "kubectl diff -f manifest.yaml",
    "terraform validate",
    "terraform fmt",
    # --- MUTATIVE verbs ---
    "terraform apply",
    "kubectl apply -f manifest.yaml",
    "git push origin main",
    "helm install my-release ./chart",
    "helm upgrade my-release ./chart",
    "kubectl create namespace test",
    "kubectl scale deployment nginx --replicas=3",
    "glab api -X POST /projects/123/notes",
    "gh api -X POST repos/owner/repo/issues",
    "flux reconcile source git flux-system",
    "npm publish",
    "docker push my-image:latest",
    # --- BLOCKED patterns ---
    "rm -rf /",
    "rm -rf ~",
    "kubectl delete namespace production",
    "kubectl delete ns staging",
    "terraform destroy",
    "terragrunt destroy",
    "git push --force origin main",
    "git push -f origin main",
    "git reset --hard HEAD~1",
    "aws eks delete-cluster --name my-cluster",
    "gcloud container clusters delete cluster-1",
    "gh repo delete owner/repo",
    "flux uninstall",
    "docker system prune -a",
    "docker volume prune",
    # --- Command aliases ---
    "rm file.txt",
    "mv old.txt new.txt",
    "cp src.txt dst.txt",
    "chmod 755 script.sh",
    "chown user:group file.txt",
    # --- Dry-run overrides ---
    "kubectl apply --dry-run=client -f manifest.yaml",
    "kubectl apply --dry-run=server -f manifest.yaml",
]


class TestBehavioralEquivalence:
    """Verify adapter flow produces IDENTICAL results to direct flow."""

    @pytest.mark.parametrize("command", EQUIVALENCE_COMMANDS)
    def test_adapter_matches_direct(self, command):
        """Adapter-based classification must match direct classification."""
        adapter_result = _classify_command_via_adapter(command)
        direct_result = _classify_command_direct(command)

        assert adapter_result == direct_result, (
            f"Command: {command!r}\n"
            f"  Adapter: allowed={adapter_result[0]}, tier={adapter_result[1]}\n"
            f"  Direct:  allowed={direct_result[0]}, tier={direct_result[1]}"
        )


# ============================================================================
# Classification Correctness: 50+ commands with expected outcomes
# ============================================================================

# Each tuple: (command, expected_allowed, expected_tier_label)
# Tier labels: "T0", "T1", "T2", "T3", "BLOCKED"
CLASSIFICATION_SCENARIOS = [
    # === Safe by elimination ===
    ("ls -la", True, "T0"),
    ("ls", True, "T0"),
    ("pwd", True, "T0"),
    ("cat file.txt", True, "T0"),
    ("echo hello", True, "T0"),
    ("docker ps", True, "T0"),
    ("docker images", True, "T0"),
    ("git add .", True, "T0"),
    ("git stash", True, "T0"),
    ("git diff", True, "T0"),
    ("git log --oneline", True, "T0"),
    ("wc -l file.txt", True, "T0"),
    ("python3 --version", True, "T0"),
    ("node --version", True, "T0"),
    # === READ_ONLY verbs ===
    ("kubectl get pods", True, "T0"),
    ("kubectl get pods -n default", True, "T0"),
    ("kubectl get svc -A", True, "T0"),
    ("kubectl describe pod nginx", True, "T0"),
    ("kubectl logs nginx-pod", True, "T0"),
    ("helm list", True, "T0"),
    ("helm list -A", True, "T0"),
    ("helm status my-release", True, "T0"),
    ("terraform show", True, "T0"),
    ("terraform output", True, "T0"),
    ("gcloud compute instances list", True, "T0"),
    ("gcloud container clusters list", True, "T0"),
    ("aws s3 ls", True, "T0"),
    ("aws ec2 describe-instances", True, "T0"),
    ("aws eks list-clusters", True, "T0"),
    # === API implicit GET ===
    ("gh api repos/owner/repo", True, "T0"),
    ("glab api projects/123", True, "T0"),
    # === SIMULATION verbs ===
    ("terraform plan", True, "T0"),
    ("terraform plan -out=plan.tfplan", True, "T0"),
    ("helm template my-release ./chart", True, "T0"),
    ("kubectl diff -f manifest.yaml", True, "T0"),
    ("terraform validate", True, "T0"),
    ("terraform fmt", True, "T0"),
    # === Dry-run overrides ===
    ("kubectl apply --dry-run=client -f manifest.yaml", True, "T0"),
    ("kubectl apply --dry-run=server -f manifest.yaml", True, "T0"),
    # === MUTATIVE verbs (T3, denied via nonce) ===
    ("terraform apply", False, "T3"),
    ("kubectl apply -f manifest.yaml", False, "T3"),
    ("git push origin main", False, "T3"),
    ("helm install my-release ./chart", False, "T3"),
    ("helm upgrade my-release ./chart", False, "T3"),
    ("kubectl create namespace test", False, "T3"),
    ("kubectl scale deployment nginx --replicas=3", False, "T3"),
    ("glab api -X POST /projects/123/notes", False, "T3"),
    ("gh api -X POST repos/owner/repo/issues", False, "T3"),
    ("flux reconcile source git flux-system", False, "T3"),
    ("npm publish", False, "T3"),
    ("docker push my-image:latest", False, "T3"),
    # === Command aliases (MUTATIVE) ===
    ("rm file.txt", False, "T3"),
    ("mv old.txt new.txt", False, "T3"),
    ("cp src.txt dst.txt", False, "T3"),
    ("chmod 755 script.sh", False, "T3"),
    ("chown user:group file.txt", False, "T3"),
    # === BLOCKED patterns (exit 2) ===
    ("rm -rf /", False, "BLOCKED"),
    ("rm -rf ~", False, "BLOCKED"),
    ("kubectl delete namespace production", False, "BLOCKED"),
    ("kubectl delete ns staging", False, "BLOCKED"),
    ("terraform destroy", False, "BLOCKED"),
    ("terragrunt destroy", False, "BLOCKED"),
    ("git push --force origin main", False, "BLOCKED"),
    ("git push -f origin main", False, "BLOCKED"),
    ("git reset --hard HEAD~1", False, "BLOCKED"),
    ("aws eks delete-cluster --name my-cluster", False, "BLOCKED"),
    ("gcloud container clusters delete cluster-1", False, "BLOCKED"),
    ("gh repo delete owner/repo", False, "BLOCKED"),
    ("flux uninstall", False, "BLOCKED"),
    ("docker system prune -a", False, "BLOCKED"),
    ("docker volume prune", False, "BLOCKED"),
]


class TestClassificationCorrectness:
    """Verify command classification matches expected security tiers."""

    @pytest.mark.parametrize(
        "command,expected_allowed,expected_tier",
        CLASSIFICATION_SCENARIOS,
        ids=[f"{cmd[:40]}..." if len(cmd) > 40 else cmd for cmd, _, _ in CLASSIFICATION_SCENARIOS],
    )
    def test_command_classification(self, command, expected_allowed, expected_tier):
        """Each command must be classified to the expected tier."""
        allowed, tier_label = _classify_command_via_adapter(command)

        assert allowed == expected_allowed, (
            f"Command: {command!r}\n"
            f"  Expected allowed={expected_allowed}, got allowed={allowed}"
        )
        assert tier_label == expected_tier, (
            f"Command: {command!r}\n"
            f"  Expected tier={expected_tier}, got tier={tier_label}"
        )


# ============================================================================
# Category Coverage Verification
# ============================================================================

class TestCategoryCoverage:
    """Ensure all major security categories have test coverage."""

    def test_safe_by_elimination_count(self):
        """At least 10 safe-by-elimination commands are tested."""
        safe_cmds = [(c, a, t) for c, a, t in CLASSIFICATION_SCENARIOS if a is True and t == "T0"]
        assert len(safe_cmds) >= 10, f"Only {len(safe_cmds)} safe commands tested"

    def test_mutative_count(self):
        """At least 10 mutative commands are tested."""
        mutative_cmds = [(c, a, t) for c, a, t in CLASSIFICATION_SCENARIOS if a is False and t == "T3"]
        assert len(mutative_cmds) >= 10, f"Only {len(mutative_cmds)} mutative commands tested"

    def test_blocked_count(self):
        """At least 10 blocked commands are tested."""
        blocked_cmds = [(c, a, t) for c, a, t in CLASSIFICATION_SCENARIOS if t == "BLOCKED"]
        assert len(blocked_cmds) >= 10, f"Only {len(blocked_cmds)} blocked commands tested"

    def test_total_scenario_count(self):
        """At least 50 total scenarios are defined."""
        assert len(CLASSIFICATION_SCENARIOS) >= 50, (
            f"Only {len(CLASSIFICATION_SCENARIOS)} scenarios; need at least 50"
        )

    def test_equivalence_command_count(self):
        """At least 50 equivalence commands are defined."""
        assert len(EQUIVALENCE_COMMANDS) >= 50, (
            f"Only {len(EQUIVALENCE_COMMANDS)} equivalence commands; need at least 50"
        )


# ============================================================================
# Edge Cases
# ============================================================================

class TestClassificationEdgeCases:
    """Edge cases that historically caused classification issues."""

    def test_git_add_is_safe(self):
        """git add is safe by elimination (local-only operation)."""
        allowed, tier = _classify_command_via_adapter("git add .")
        assert allowed is True
        assert tier == "T0"

    def test_git_stash_is_safe(self):
        """git stash is safe by elimination (local-only operation)."""
        allowed, tier = _classify_command_via_adapter("git stash")
        assert allowed is True
        assert tier == "T0"

    def test_terraform_destroy_with_target_not_blocked(self):
        """terraform destroy -target=X passes through blocked_commands."""
        allowed, tier = _classify_command_via_adapter(
            "terraform destroy -target=aws_instance.web"
        )
        # -target makes it pass blocked_commands, but it is still mutative
        assert allowed is False
        assert tier == "T3"

    def test_npm_unpublish_with_version_not_blocked(self):
        """npm unpublish package@1.0.0 (with version) is not permanently blocked."""
        allowed, tier = _classify_command_via_adapter("npm unpublish my-package@1.0.0")
        # With @version, it passes blocked_commands but is still mutative
        assert allowed is False
        assert tier == "T3"

    def test_empty_command_blocked(self):
        """Empty command is not allowed."""
        allowed, tier = _classify_command_via_adapter("")
        assert allowed is False

    def test_whitespace_command_blocked(self):
        """Whitespace-only command is not allowed."""
        allowed, tier = _classify_command_via_adapter("   ")
        assert allowed is False

    def test_kubectl_delete_pod_is_mutative_not_blocked(self):
        """kubectl delete pod is mutative (approvable), not permanently blocked."""
        allowed, tier = _classify_command_via_adapter("kubectl delete pod nginx")
        assert allowed is False
        assert tier == "T3"  # Mutative, not BLOCKED

    def test_git_push_force_with_lease_not_blocked(self):
        """git push --force-with-lease is NOT permanently blocked."""
        allowed, tier = _classify_command_via_adapter(
            "git push --force-with-lease origin feature-branch"
        )
        assert allowed is False
        assert tier == "T3"  # Mutative, not BLOCKED

    def test_global_flags_before_command(self):
        """Global flags before the command do not bypass classification."""
        allowed, tier = _classify_command_via_adapter(
            "kubectl --context prod get pods"
        )
        assert allowed is True
        assert tier == "T0"

    def test_global_flags_before_blocked_command(self):
        """Global flags before a blocked command do not hide the block."""
        allowed, tier = _classify_command_via_adapter(
            "kubectl --context prod delete namespace production"
        )
        assert allowed is False
        assert tier == "BLOCKED"

    def test_quoted_special_characters_safe(self):
        """Quoted special characters are not treated as operators."""
        allowed, tier = _classify_command_via_adapter('echo "hello && world"')
        assert allowed is True
        assert tier == "T0"

    def test_compound_all_safe(self):
        """Compound with all safe parts is allowed."""
        allowed, tier = _classify_command_via_adapter("ls -la && pwd && cat file.txt")
        assert allowed is True
        assert tier == "T0"

    def test_compound_with_blocked_part(self):
        """Compound with a blocked part is blocked."""
        allowed, tier = _classify_command_via_adapter(
            "ls && kubectl delete namespace production"
        )
        assert allowed is False

    def test_api_delete_is_mutative(self):
        """API DELETE method is mutative."""
        allowed, tier = _classify_command_via_adapter(
            "gh api -X DELETE repos/owner/repo/issues/1"
        )
        assert allowed is False
        assert tier == "T3"
