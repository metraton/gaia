#!/usr/bin/env python3
"""
Test suite for permissions validation in settings.json

This test EMULATES Claude Code's actual permission matching behavior:
1. Pattern matching: Prefix matching with :* wildcard
2. Precedence order: Deny → Allow → Ask (deny has absolute precedence)
3. Validates that specific commands produce expected results

Based on Claude Code documentation and behavior analysis:
- https://www.petefreitag.com/blog/claude-code-permissions/
- https://github.com/anthropics/claude-code/issues (various pattern matching issues)
"""

import json
import re
import sys
import os
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from enum import Enum


class PermissionResult(Enum):
    """Result of permission check - matches Claude Code behavior"""
    DENY = "deny"      # Command is blocked
    ALLOW = "allow"    # Command executes automatically
    ASK = "ask"        # Command requires user approval
    DEFAULT = "default"  # No pattern matched - Claude Code default behavior


@dataclass
class TestCase:
    """A test case with command and expected result"""
    command: str
    expected: PermissionResult
    description: str = ""
    category: str = ""


@dataclass
class TestResult:
    """Result of running a test case"""
    test_case: TestCase
    actual: PermissionResult
    matched_pattern: str = ""
    passed: bool = False

    def __post_init__(self):
        self.passed = self.actual == self.test_case.expected


class ClaudeCodePermissionMatcher:
    """
    Emulates Claude Code's permission matching behavior.

    Pattern Syntax:
    - Exact match: "Bash(git status)"
    - Wildcard: "Bash(git:*)" matches any command starting with "git"
    - Tool-only: "Read" matches all Read operations

    Precedence (Claude Code behavior):
    1. Deny list (checked first - absolute precedence)
    2. Allow list (checked second)
    3. Ask list (checked if no deny/allow match)
    4. Default (if nothing matches)
    """

    def __init__(self, permissions: Dict):
        self.deny_patterns = permissions.get('deny', [])
        self.allow_patterns = permissions.get('allow', [])
        self.ask_patterns = permissions.get('ask', [])

    def _pattern_matches(self, pattern: str, tool: str, command: str) -> bool:
        """
        Check if a pattern matches a tool/command combination.

        Emulates Claude Code's prefix matching with :* wildcard.

        Examples:
        - Pattern "Bash(git status:*)" matches command "git status --short"
        - Pattern "Bash(kubectl get:*)" matches "kubectl get pods -n default"
        - Pattern "Read" matches any Read operation
        - Pattern "Bash(echo:*)" matches "echo hello world"
        """
        # Tool-only pattern (e.g., "Read", "Glob", "Task")
        if '(' not in pattern:
            return pattern == tool

        # Extract tool and command pattern from "Tool(command:args)"
        match = re.match(r'^(\w+)\((.+)\)$', pattern)
        if not match:
            return False

        pattern_tool = match.group(1)
        pattern_cmd = match.group(2)

        # Tool must match
        if pattern_tool != tool:
            return False

        # Wildcard matching
        if pattern_cmd == '*':
            # Matches any command for this tool
            return True

        if pattern_cmd.endswith(':*'):
            # Prefix matching: "git push:*" matches "git push origin main"
            prefix = pattern_cmd[:-2]  # Remove :*
            return command.startswith(prefix)

        if ':*' in pattern_cmd:
            # Middle wildcard: "aws s3:*:bucket" - not commonly used
            # For safety, treat as prefix up to :*
            prefix = pattern_cmd.split(':*')[0]
            return command.startswith(prefix)

        # Exact match
        return pattern_cmd == command

    def _find_matching_pattern(self, patterns: List[str], tool: str, command: str) -> Optional[str]:
        """Find the first matching pattern in a list"""
        for pattern in patterns:
            if self._pattern_matches(pattern, tool, command):
                return pattern
        return None

    def check_permission(self, tool: str, command: str = "") -> Tuple[PermissionResult, str]:
        """
        Check permission for a tool/command combination.

        Returns:
            Tuple of (PermissionResult, matched_pattern)

        Emulates Claude Code's precedence:
        1. Check deny list first (absolute precedence)
        2. Check allow list second
        3. Check ask list third
        4. Return default if nothing matches
        """
        # 1. Check deny list first (highest precedence)
        matched = self._find_matching_pattern(self.deny_patterns, tool, command)
        if matched:
            return PermissionResult.DENY, matched

        # 2. Check allow list second
        matched = self._find_matching_pattern(self.allow_patterns, tool, command)
        if matched:
            return PermissionResult.ALLOW, matched

        # 3. Check ask list third
        matched = self._find_matching_pattern(self.ask_patterns, tool, command)
        if matched:
            return PermissionResult.ASK, matched

        # 4. No match - default behavior
        return PermissionResult.DEFAULT, ""


def get_test_cases() -> List[TestCase]:
    """
    Define test cases for permission validation.

    These test the expected behavior of our permission configuration:
    - Read-only commands should be ALLOW
    - Destructive commands should be DENY
    - Modifying commands should be ASK
    """
    return [
        # ========== READ-ONLY COMMANDS (should be ALLOW) ==========

        # Shell basics
        TestCase("ls -la", PermissionResult.ALLOW, "List files", "shell"),
        TestCase("pwd", PermissionResult.ALLOW, "Print working directory", "shell"),
        TestCase("cat /etc/hosts", PermissionResult.ALLOW, "Read file content", "shell"),
        TestCase("head -n 10 file.txt", PermissionResult.ALLOW, "Read first lines", "shell"),
        TestCase("tail -f log.txt", PermissionResult.ALLOW, "Read last lines", "shell"),
        TestCase("grep pattern file.txt", PermissionResult.ALLOW, "Search in file", "shell"),
        TestCase("find . -name '*.py'", PermissionResult.ALLOW, "Find files", "shell"),
        TestCase("which python", PermissionResult.ALLOW, "Find command path", "shell"),
        TestCase("whoami", PermissionResult.ALLOW, "Current user", "shell"),
        TestCase("date", PermissionResult.ALLOW, "Current date", "shell"),
        TestCase("env", PermissionResult.ALLOW, "Environment variables", "shell"),

        # Git read-only
        TestCase("git status", PermissionResult.ALLOW, "Git status", "git"),
        TestCase("git log --oneline", PermissionResult.ALLOW, "Git log", "git"),
        TestCase("git diff HEAD", PermissionResult.ALLOW, "Git diff", "git"),
        TestCase("git branch -a", PermissionResult.ALLOW, "List branches", "git"),
        TestCase("git remote -v", PermissionResult.ALLOW, "List remotes", "git"),
        TestCase("git show HEAD", PermissionResult.ALLOW, "Show commit", "git"),
        TestCase("git blame file.py", PermissionResult.ALLOW, "Git blame", "git"),

        # Kubernetes read-only
        TestCase("kubectl get pods", PermissionResult.ALLOW, "List pods", "kubernetes"),
        TestCase("kubectl get pods -n default", PermissionResult.ALLOW, "List pods in namespace", "kubernetes"),
        TestCase("kubectl get services -A", PermissionResult.ALLOW, "List all services", "kubernetes"),
        TestCase("kubectl describe pod my-pod", PermissionResult.ALLOW, "Describe pod", "kubernetes"),
        TestCase("kubectl logs my-pod", PermissionResult.ALLOW, "Pod logs", "kubernetes"),
        TestCase("kubectl logs my-pod -f", PermissionResult.ALLOW, "Follow pod logs", "kubernetes"),
        TestCase("kubectl top pods", PermissionResult.ALLOW, "Pod metrics", "kubernetes"),
        TestCase("kubectl config current-context", PermissionResult.ALLOW, "Current context", "kubernetes"),

        # Helm read-only
        TestCase("helm list", PermissionResult.ALLOW, "List releases", "helm"),
        TestCase("helm list -A", PermissionResult.ALLOW, "List all releases", "helm"),
        TestCase("helm status my-release", PermissionResult.ALLOW, "Release status", "helm"),
        TestCase("helm get values my-release", PermissionResult.ALLOW, "Get values", "helm"),
        TestCase("helm history my-release", PermissionResult.ALLOW, "Release history", "helm"),

        # Flux read-only
        TestCase("flux get all", PermissionResult.ALLOW, "List all Flux resources", "flux"),
        TestCase("flux get kustomizations", PermissionResult.ALLOW, "List kustomizations", "flux"),
        TestCase("flux logs", PermissionResult.ALLOW, "Flux logs", "flux"),

        # Terraform read-only
        TestCase("terraform version", PermissionResult.ALLOW, "Terraform version", "terraform"),
        TestCase("terraform show", PermissionResult.ALLOW, "Show state", "terraform"),
        TestCase("terraform output", PermissionResult.ALLOW, "Show outputs", "terraform"),
        TestCase("terraform state list", PermissionResult.ALLOW, "List state", "terraform"),
        TestCase("terraform validate", PermissionResult.ALLOW, "Validate config", "terraform"),
        TestCase("terraform fmt -check", PermissionResult.ALLOW, "Check formatting", "terraform"),

        # AWS read-only
        TestCase("aws sts get-caller-identity", PermissionResult.ALLOW, "Get AWS identity", "aws"),
        TestCase("aws s3 ls", PermissionResult.ALLOW, "List S3 buckets", "aws"),
        TestCase("aws ec2 describe-instances", PermissionResult.ALLOW, "Describe EC2", "aws"),
        TestCase("aws iam list-users", PermissionResult.ALLOW, "List IAM users", "aws"),
        TestCase("aws rds describe-db-instances", PermissionResult.ALLOW, "Describe RDS", "aws"),

        # GCP read-only
        TestCase("gcloud config list", PermissionResult.ALLOW, "GCP config", "gcp"),
        TestCase("gcloud projects list", PermissionResult.ALLOW, "List projects", "gcp"),
        TestCase("gcloud compute instances list", PermissionResult.ALLOW, "List instances", "gcp"),
        TestCase("gcloud container clusters list", PermissionResult.ALLOW, "List GKE clusters", "gcp"),
        TestCase("gcloud sql instances list", PermissionResult.ALLOW, "List SQL instances", "gcp"),

        # Docker read-only
        TestCase("docker ps", PermissionResult.ALLOW, "List containers", "docker"),
        TestCase("docker images", PermissionResult.ALLOW, "List images", "docker"),
        TestCase("docker logs my-container", PermissionResult.ALLOW, "Container logs", "docker"),
        TestCase("docker inspect my-container", PermissionResult.ALLOW, "Inspect container", "docker"),

        # ========== MODIFYING COMMANDS (should be ASK) ==========

        # Git modifying
        TestCase("git add .", PermissionResult.ASK, "Stage all changes", "git"),
        TestCase("git commit -m 'message'", PermissionResult.ASK, "Create commit", "git"),
        TestCase("git push origin main", PermissionResult.ASK, "Push to remote", "git"),
        TestCase("git pull origin main", PermissionResult.ASK, "Pull from remote", "git"),
        TestCase("git merge feature", PermissionResult.ASK, "Merge branch", "git"),
        TestCase("git rebase main", PermissionResult.ASK, "Rebase branch", "git"),
        TestCase("git reset HEAD~1", PermissionResult.ASK, "Reset commit", "git"),
        TestCase("git checkout -b new-branch", PermissionResult.ASK, "Create branch", "git"),

        # Kubernetes modifying
        TestCase("kubectl apply -f manifest.yaml", PermissionResult.ASK, "Apply manifest", "kubernetes"),
        TestCase("kubectl create deployment nginx", PermissionResult.ASK, "Create deployment", "kubernetes"),
        TestCase("kubectl delete pod my-pod", PermissionResult.ASK, "Delete pod", "kubernetes"),
        TestCase("kubectl scale deployment nginx --replicas=3", PermissionResult.ASK, "Scale deployment", "kubernetes"),
        TestCase("kubectl rollout restart deployment nginx", PermissionResult.ASK, "Restart deployment", "kubernetes"),
        TestCase("kubectl exec -it my-pod -- /bin/bash", PermissionResult.ASK, "Exec into pod", "kubernetes"),

        # Helm modifying
        TestCase("helm install my-release chart/", PermissionResult.ASK, "Install release", "helm"),
        TestCase("helm upgrade my-release chart/", PermissionResult.ASK, "Upgrade release", "helm"),
        TestCase("helm uninstall my-release", PermissionResult.ASK, "Uninstall release", "helm"),
        TestCase("helm rollback my-release 1", PermissionResult.ASK, "Rollback release", "helm"),

        # Terraform modifying
        TestCase("terraform plan", PermissionResult.ASK, "Plan changes", "terraform"),
        TestCase("terraform apply", PermissionResult.ASK, "Apply changes", "terraform"),
        TestCase("terraform destroy", PermissionResult.ASK, "Destroy resources", "terraform"),

        # File operations
        TestCase("rm file.txt", PermissionResult.ASK, "Remove file", "file"),
        TestCase("rm -rf directory/", PermissionResult.ASK, "Remove directory", "file"),
        TestCase("mv old.txt new.txt", PermissionResult.ASK, "Move/rename file", "file"),
        TestCase("cp source.txt dest.txt", PermissionResult.ASK, "Copy file", "file"),
        TestCase("mkdir new-directory", PermissionResult.ASK, "Create directory", "file"),
        TestCase("chmod 755 script.sh", PermissionResult.ASK, "Change permissions", "file"),

        # AWS modifying
        TestCase("aws s3 cp file.txt s3://bucket/", PermissionResult.ASK, "Upload to S3", "aws"),
        TestCase("aws ec2 start-instances --instance-ids i-123", PermissionResult.ASK, "Start EC2", "aws"),
        TestCase("aws ec2 stop-instances --instance-ids i-123", PermissionResult.ASK, "Stop EC2", "aws"),
        TestCase("aws lambda update-function-code --function-name fn", PermissionResult.ASK, "Update Lambda", "aws"),

        # GCP modifying
        TestCase("gcloud compute instances start my-vm", PermissionResult.ASK, "Start GCE instance", "gcp"),
        TestCase("gcloud compute instances stop my-vm", PermissionResult.ASK, "Stop GCE instance", "gcp"),
        TestCase("gcloud container clusters resize my-cluster", PermissionResult.ASK, "Resize GKE", "gcp"),

        # Docker modifying
        TestCase("docker build -t my-image .", PermissionResult.ASK, "Build image", "docker"),
        TestCase("docker run my-image", PermissionResult.ASK, "Run container", "docker"),
        TestCase("docker push my-image", PermissionResult.ASK, "Push image", "docker"),
        TestCase("docker stop my-container", PermissionResult.ASK, "Stop container", "docker"),

        # Package managers
        TestCase("npm install", PermissionResult.ASK, "Install npm packages", "npm"),
        TestCase("npm publish", PermissionResult.ASK, "Publish npm package", "npm"),
        TestCase("pip install package", PermissionResult.ASK, "Install pip package", "pip"),

        # ========== DESTRUCTIVE COMMANDS (should be DENY) ==========

        # AWS destructive
        TestCase("aws ec2 terminate-instances --instance-ids i-123", PermissionResult.DENY, "Terminate EC2", "aws"),
        TestCase("aws s3 rb s3://bucket --force", PermissionResult.DENY, "Delete S3 bucket", "aws"),
        TestCase("aws rds delete-db-instance --db-instance-identifier db", PermissionResult.DENY, "Delete RDS", "aws"),
        TestCase("aws lambda delete-function --function-name fn", PermissionResult.DENY, "Delete Lambda", "aws"),
        TestCase("aws iam delete-user --user-name user", PermissionResult.DENY, "Delete IAM user", "aws"),
        TestCase("aws iam delete-role --role-name role", PermissionResult.DENY, "Delete IAM role", "aws"),
        TestCase("aws cloudformation delete-stack --stack-name stack", PermissionResult.DENY, "Delete CFN stack", "aws"),

        # GCP destructive
        TestCase("gcloud compute instances delete my-vm", PermissionResult.DENY, "Delete GCE instance", "gcp"),
        TestCase("gcloud container clusters delete my-cluster", PermissionResult.DENY, "Delete GKE cluster", "gcp"),
        TestCase("gcloud sql instances delete my-sql", PermissionResult.DENY, "Delete Cloud SQL", "gcp"),
        TestCase("gcloud projects delete my-project", PermissionResult.DENY, "Delete project", "gcp"),
        TestCase("gsutil rm -r gs://bucket", PermissionResult.DENY, "Delete GCS bucket contents", "gcp"),

        # Kubernetes destructive
        TestCase("kubectl delete namespace production", PermissionResult.DENY, "Delete namespace", "kubernetes"),
        TestCase("kubectl delete pv my-pv", PermissionResult.DENY, "Delete persistent volume", "kubernetes"),
        TestCase("kubectl delete clusterrole admin", PermissionResult.DENY, "Delete cluster role", "kubernetes"),
        TestCase("kubectl drain node-1", PermissionResult.DENY, "Drain node", "kubernetes"),

        # System destructive
        TestCase("dd if=/dev/zero of=/dev/sda", PermissionResult.DENY, "Overwrite disk", "system"),
        TestCase("mkfs.ext4 /dev/sda1", PermissionResult.DENY, "Format partition", "system"),
        TestCase("fdisk /dev/sda", PermissionResult.DENY, "Partition disk", "system"),
    ]


def run_tests(permissions: Dict) -> Tuple[List[TestResult], bool]:
    """
    Run all test cases against the permission configuration.

    Returns:
        Tuple of (list of results, all_passed)
    """
    matcher = ClaudeCodePermissionMatcher(permissions)
    test_cases = get_test_cases()
    results = []

    for test_case in test_cases:
        # All test cases are Bash commands
        actual, matched_pattern = matcher.check_permission("Bash", test_case.command)

        result = TestResult(
            test_case=test_case,
            actual=actual,
            matched_pattern=matched_pattern
        )
        results.append(result)

    all_passed = all(r.passed for r in results)
    return results, all_passed


def print_results(results: List[TestResult], verbose: bool = False):
    """Print test results in a readable format"""

    print("=" * 80)
    print("CLAUDE CODE PERMISSION MATCHING TEST")
    print("=" * 80)
    print()
    print("This test emulates Claude Code's actual permission matching behavior:")
    print("- Pattern matching: Prefix matching with :* wildcard")
    print("- Precedence: Deny → Allow → Ask")
    print()

    # Group by category
    categories = {}
    for result in results:
        cat = result.test_case.category
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(result)

    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)

    print(f"Total tests: {len(results)}")
    print(f"Passed: {passed} ✅")
    print(f"Failed: {failed} ❌")
    print()

    # Print failures first
    if failed > 0:
        print("=" * 80)
        print("FAILED TESTS")
        print("=" * 80)
        for result in results:
            if not result.passed:
                print(f"❌ {result.test_case.command}")
                print(f"   Expected: {result.test_case.expected.value}")
                print(f"   Actual: {result.actual.value}")
                if result.matched_pattern:
                    print(f"   Matched pattern: {result.matched_pattern}")
                else:
                    print(f"   No pattern matched (defaulted to {result.actual.value})")
                print(f"   Description: {result.test_case.description}")
                print()

    # Print summary by category
    print("=" * 80)
    print("SUMMARY BY CATEGORY")
    print("=" * 80)
    for cat, cat_results in sorted(categories.items()):
        cat_passed = sum(1 for r in cat_results if r.passed)
        cat_total = len(cat_results)
        status = "✅" if cat_passed == cat_total else "❌"
        print(f"{status} {cat}: {cat_passed}/{cat_total}")
    print()

    # Print all results if verbose
    if verbose:
        print("=" * 80)
        print("ALL TEST RESULTS")
        print("=" * 80)
        for cat, cat_results in sorted(categories.items()):
            print(f"\n### {cat.upper()} ###")
            for result in cat_results:
                status = "✅" if result.passed else "❌"
                print(f"{status} {result.test_case.command}")
                if not result.passed or verbose:
                    print(f"   Expected: {result.test_case.expected.value}, Actual: {result.actual.value}")
                    if result.matched_pattern:
                        print(f"   Pattern: {result.matched_pattern}")

    return failed == 0


def validate_permission_structure(permissions: Dict) -> List[str]:
    """Validate the basic structure of permissions configuration"""
    errors = []

    required_keys = ['allow', 'deny', 'ask']
    for key in required_keys:
        if key not in permissions:
            errors.append(f"Missing required key: '{key}'")
        elif not isinstance(permissions[key], list):
            errors.append(f"'{key}' must be a list")

    # Check for duplicate patterns
    all_patterns = []
    for key in required_keys:
        if key in permissions:
            for pattern in permissions[key]:
                if pattern in all_patterns:
                    errors.append(f"Duplicate pattern found: '{pattern}'")
                all_patterns.append(pattern)

    # Check for conflicting patterns (same pattern in multiple lists)
    for pattern in permissions.get('deny', []):
        if pattern in permissions.get('allow', []):
            errors.append(f"Pattern in both deny and allow: '{pattern}'")
        if pattern in permissions.get('ask', []):
            errors.append(f"Pattern in both deny and ask: '{pattern}'")

    for pattern in permissions.get('allow', []):
        if pattern in permissions.get('ask', []):
            errors.append(f"Pattern in both allow and ask: '{pattern}'")

    return errors


def main(argv=None):
    """Main test execution"""
    parser = argparse.ArgumentParser(
        description="Test permissions using Claude Code's actual matching behavior"
    )
    parser.add_argument(
        "--settings-file",
        type=str,
        required=True,
        help="Path to settings.json file to test"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show all test results, not just failures"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON"
    )

    args = parser.parse_args(argv)

    # Load settings file
    settings_path = Path(args.settings_file)
    if not settings_path.exists():
        print(f"❌ Error: {settings_path} not found")
        sys.exit(1)

    with open(settings_path, 'r') as f:
        settings = json.load(f)

    permissions = settings.get('permissions', {})

    # Validate structure first
    print("Validating permission structure...")
    structure_errors = validate_permission_structure(permissions)
    if structure_errors:
        print("❌ Structure validation failed:")
        for error in structure_errors:
            print(f"   - {error}")
        sys.exit(1)
    print("✅ Structure validation passed")
    print()

    # Run tests
    results, all_passed = run_tests(permissions)

    if args.json:
        # JSON output
        output = {
            "total": len(results),
            "passed": sum(1 for r in results if r.passed),
            "failed": sum(1 for r in results if not r.passed),
            "results": [
                {
                    "command": r.test_case.command,
                    "expected": r.test_case.expected.value,
                    "actual": r.actual.value,
                    "passed": r.passed,
                    "matched_pattern": r.matched_pattern,
                    "description": r.test_case.description,
                    "category": r.test_case.category
                }
                for r in results
            ]
        }
        print(json.dumps(output, indent=2))
    else:
        # Human-readable output
        print_results(results, verbose=args.verbose)

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
