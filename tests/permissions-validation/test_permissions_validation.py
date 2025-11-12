#!/usr/bin/env python3
"""
Test suite for permissions validation in settings.json and settings.local.json

This test validates:
1. settings.json has strict, standard configurations
2. settings.local.json has more open, query-focused configurations
3. All deny rules are properly configured
4. All allow rules (gets, queries) are properly configured
5. Ask rules require user approval
"""

import json
import re
import sys
import os
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field


@dataclass
class PermissionValidationResult:
    """Result of a permission validation"""
    rule_type: str  # 'allow', 'deny', 'ask'
    pattern: str
    is_valid: bool
    reason: str = ""
    examples: List[str] = field(default_factory=list)
    file_source: str = ""  # 'settings.json' or 'settings.local.json'


@dataclass
class ValidationSummary:
    """Summary of all validation results"""
    total_rules: int = 0
    valid_rules: int = 0
    invalid_rules: int = 0
    allow_rules: int = 0
    deny_rules: int = 0
    ask_rules: int = 0
    results: List[PermissionValidationResult] = field(default_factory=list)


class PermissionsValidator:
    """Validator for permissions configuration"""

    # Read-only operations that should be in 'allow'
    READ_ONLY_OPERATIONS = [
        'get', 'describe', 'logs', 'show', 'list', 'status',
        'diff', 'branch', 'top', 'version', 'config get', 'explain',
        'wait', 'check'
    ]

    # Dangerous operations that should be in 'deny'
    DANGEROUS_OPERATIONS = [
        'delete', 'apply', 'destroy', 'create', 'patch', 'scale',
        'reset --hard', 'push --force', 'push -f', 'mv'
    ]

    # Operations that should require approval ('ask')
    APPROVAL_OPERATIONS = [
        'Edit', 'Write', 'NotebookEdit', 'rm', 'rmdir',
        'install', 'upgrade', 'uninstall', 'rollback',
        'commit', 'push'
    ]

    def __init__(self, settings_path: str, settings_local_path: str = None):
        self.settings_path = Path(settings_path)
        self.settings_local_path = Path(settings_local_path) if settings_local_path else None
        self.settings = self._load_json(self.settings_path)
        self.settings_local = self._load_json(self.settings_local_path) if self.settings_local_path else {}
        self.summary = ValidationSummary()

    def _load_json(self, path: Path) -> Dict:
        """Load JSON file"""
        if not path or not path.exists():
            return {}
        with open(path, 'r') as f:
            return json.load(f)

    def _extract_operation(self, pattern: str) -> str:
        """Extract the operation from a permission pattern"""
        # Pattern format: Tool(operation:args) or Tool(*)
        match = re.search(r'\(([^:)]+)', pattern)
        if match:
            return match.group(1).lower()
        return ""

    def _is_read_only_pattern(self, pattern: str) -> bool:
        """Check if pattern matches read-only operations"""
        pattern_lower = pattern.lower()
        return any(op in pattern_lower for op in self.READ_ONLY_OPERATIONS)

    def _is_dangerous_pattern(self, pattern: str) -> bool:
        """Check if pattern matches dangerous operations"""
        pattern_lower = pattern.lower()
        return any(op in pattern_lower for op in self.DANGEROUS_OPERATIONS)

    def _is_approval_pattern(self, pattern: str) -> bool:
        """Check if pattern matches operations requiring approval"""
        return any(op in pattern for op in self.APPROVAL_OPERATIONS)

    def _generate_example_commands(self, pattern: str) -> List[str]:
        """Generate example commands for a permission pattern"""
        examples = []

        if 'Read(*)' in pattern:
            examples = [
                'Read("/path/to/file.txt")',
                'Read("/home/user/config.yaml")'
            ]
        elif 'Glob(*)' in pattern:
            examples = [
                'Glob("**/*.py")',
                'Glob("src/**/*.ts")'
            ]
        elif 'Grep(*)' in pattern:
            examples = [
                'Grep("pattern", "file.txt")',
                'Grep("error", "**/*.log")'
            ]
        elif 'kubectl get' in pattern:
            examples = [
                'kubectl get pods -n default',
                'kubectl get services -A',
                'kubectl get deployments'
            ]
        elif 'kubectl describe' in pattern:
            examples = [
                'kubectl describe pod my-pod',
                'kubectl describe service my-service'
            ]
        elif 'kubectl logs' in pattern:
            examples = [
                'kubectl logs my-pod',
                'kubectl logs my-pod -f'
            ]
        elif 'kubectl delete' in pattern:
            examples = [
                'kubectl delete pod my-pod',
                'kubectl delete deployment my-deployment'
            ]
        elif 'kubectl apply' in pattern:
            examples = [
                'kubectl apply -f manifest.yaml',
                'kubectl apply -k ./kustomize'
            ]
        elif 'git status' in pattern:
            examples = ['git status']
        elif 'git diff' in pattern:
            examples = ['git diff', 'git diff HEAD~1']
        elif 'git commit' in pattern:
            examples = ['git commit -m "feat: add feature"']
        elif 'git push' in pattern and '--force' in pattern:
            examples = ['git push --force', 'git push -f']
        elif 'git push' in pattern:
            examples = ['git push origin main']
        elif 'Edit(*)' in pattern:
            examples = [
                'Edit("file.py", "old_text", "new_text")',
                'Edit("config.yaml", "key: old", "key: new")'
            ]
        elif 'Write(*)' in pattern:
            examples = [
                'Write("new_file.py", "content")',
                'Write("output.txt", "data")'
            ]
        elif 'terraform destroy' in pattern:
            examples = ['terraform destroy', 'terraform destroy -auto-approve']
        elif 'flux delete' in pattern:
            examples = ['flux delete kustomization my-app']
        elif 'helm install' in pattern:
            examples = ['helm install my-release stable/nginx']
        elif 'helm upgrade' in pattern:
            examples = ['helm upgrade my-release stable/nginx']

        return examples

    def validate_allow_rules(self, permissions: Dict, source: str) -> List[PermissionValidationResult]:
        """Validate 'allow' rules"""
        results = []
        allow_rules = permissions.get('allow', [])

        for pattern in allow_rules:
            is_valid = True
            reason = "Valid read-only/query operation"

            # Check if it's actually a dangerous operation
            if self._is_dangerous_pattern(pattern):
                is_valid = False
                reason = "Dangerous operation in 'allow' section - should be in 'deny'"

            # Check if it requires approval
            elif self._is_approval_pattern(pattern) and 'Read' not in pattern and 'Glob' not in pattern and 'Grep' not in pattern:
                is_valid = False
                reason = "Operation requires approval - should be in 'ask'"

            # Validate it's a read-only operation
            elif not self._is_read_only_pattern(pattern) and pattern not in ['Read(*)', 'Glob(*)', 'Grep(*)', 'Task(*)']:
                is_valid = False
                reason = "Not a clear read-only operation - validate pattern"

            examples = self._generate_example_commands(pattern)

            result = PermissionValidationResult(
                rule_type='allow',
                pattern=pattern,
                is_valid=is_valid,
                reason=reason,
                examples=examples,
                file_source=source
            )
            results.append(result)

            self.summary.allow_rules += 1
            if is_valid:
                self.summary.valid_rules += 1
            else:
                self.summary.invalid_rules += 1

        return results

    def validate_deny_rules(self, permissions: Dict, source: str) -> List[PermissionValidationResult]:
        """Validate 'deny' rules"""
        results = []
        deny_rules = permissions.get('deny', [])

        for pattern in deny_rules:
            is_valid = True
            reason = "Valid dangerous operation blocked"

            # Check if it's actually a safe operation
            if self._is_read_only_pattern(pattern):
                is_valid = False
                reason = "Read-only operation in 'deny' section - should be in 'allow'"

            # Validate it's a dangerous operation
            elif not self._is_dangerous_pattern(pattern):
                is_valid = False
                reason = "Not clearly a dangerous operation - validate pattern"

            examples = self._generate_example_commands(pattern)

            result = PermissionValidationResult(
                rule_type='deny',
                pattern=pattern,
                is_valid=is_valid,
                reason=reason,
                examples=examples,
                file_source=source
            )
            results.append(result)

            self.summary.deny_rules += 1
            if is_valid:
                self.summary.valid_rules += 1
            else:
                self.summary.invalid_rules += 1

        return results

    def validate_ask_rules(self, permissions: Dict, source: str) -> List[PermissionValidationResult]:
        """Validate 'ask' rules"""
        results = []
        ask_rules = permissions.get('ask', [])

        for pattern in ask_rules:
            is_valid = True
            reason = "Valid operation requiring approval"

            # Check if it's a dangerous operation that should be denied
            if self._is_dangerous_pattern(pattern) and not any(op in pattern for op in ['commit', 'push', 'install', 'upgrade']):
                is_valid = False
                reason = "Too dangerous - should be in 'deny'"

            # Check if it's a read-only operation that should be allowed
            elif self._is_read_only_pattern(pattern):
                is_valid = False
                reason = "Read-only operation - should be in 'allow'"

            examples = self._generate_example_commands(pattern)

            result = PermissionValidationResult(
                rule_type='ask',
                pattern=pattern,
                is_valid=is_valid,
                reason=reason,
                examples=examples,
                file_source=source
            )
            results.append(result)

            self.summary.ask_rules += 1
            if is_valid:
                self.summary.valid_rules += 1
            else:
                self.summary.invalid_rules += 1

        return results

    def validate_settings_philosophy(self) -> Tuple[bool, str]:
        """
        Validate that settings.json is strict and settings.local.json is more open

        Returns:
            (is_valid, reason)
        """
        if not self.settings_local:
            return True, "No settings.local.json found - skipping philosophy check"

        settings_perms = self.settings.get('permissions', {})
        local_perms = self.settings_local.get('permissions', {})

        # Check that local has more 'allow' rules (more open)
        settings_allow = len(settings_perms.get('allow', []))
        local_allow = len(local_perms.get('allow', []))

        # Check that local has fewer 'deny' rules (more permissive)
        settings_deny = len(settings_perms.get('deny', []))
        local_deny = len(local_perms.get('deny', []))

        issues = []

        if local_allow <= settings_allow:
            issues.append(f"settings.local.json should have MORE allow rules than settings.json ({local_allow} <= {settings_allow})")

        if local_deny >= settings_deny:
            issues.append(f"settings.local.json should have FEWER deny rules than settings.json ({local_deny} >= {settings_deny})")

        if issues:
            return False, "; ".join(issues)

        return True, "Philosophy check passed: settings.local.json is more permissive"

    def run_validation(self) -> ValidationSummary:
        """Run complete validation"""
        print("=" * 80)
        print("PERMISSIONS VALIDATION TEST")
        print("=" * 80)
        print()

        # Validate settings.json
        print(f"📋 Validating: {self.settings_path}")
        print("-" * 80)
        settings_perms = self.settings.get('permissions', {})

        self.summary.results.extend(self.validate_allow_rules(settings_perms, 'settings.json'))
        self.summary.results.extend(self.validate_deny_rules(settings_perms, 'settings.json'))
        self.summary.results.extend(self.validate_ask_rules(settings_perms, 'settings.json'))

        # Validate settings.local.json if exists
        if self.settings_local:
            print(f"\n📋 Validating: {self.settings_local_path}")
            print("-" * 80)
            local_perms = self.settings_local.get('permissions', {})

            self.summary.results.extend(self.validate_allow_rules(local_perms, 'settings.local.json'))
            self.summary.results.extend(self.validate_deny_rules(local_perms, 'settings.local.json'))
            self.summary.results.extend(self.validate_ask_rules(local_perms, 'settings.local.json'))

        # Validate philosophy
        print("\n" + "=" * 80)
        print("PHILOSOPHY VALIDATION")
        print("=" * 80)
        philosophy_valid, philosophy_reason = self.validate_settings_philosophy()
        print(f"{'✅' if philosophy_valid else '❌'} {philosophy_reason}")

        self.summary.total_rules = len(self.summary.results)

        return self.summary

    def print_summary(self):
        """Print validation summary"""
        print("\n" + "=" * 80)
        print("VALIDATION SUMMARY")
        print("=" * 80)
        print(f"Total rules validated: {self.summary.total_rules}")
        print(f"Valid rules: {self.summary.valid_rules} ✅")
        print(f"Invalid rules: {self.summary.invalid_rules} ❌")
        print()
        print(f"Allow rules: {self.summary.allow_rules}")
        print(f"Deny rules: {self.summary.deny_rules}")
        print(f"Ask rules: {self.summary.ask_rules}")
        print()

        # Print invalid rules
        invalid_results = [r for r in self.summary.results if not r.is_valid]
        if invalid_results:
            print("⚠️  INVALID RULES FOUND:")
            print("-" * 80)
            for result in invalid_results:
                print(f"[{result.file_source}] {result.rule_type.upper()}: {result.pattern}")
                print(f"  Reason: {result.reason}")
                print()
        else:
            print("✅ All rules are valid!")

        return self.summary.invalid_rules == 0

    def generate_manual_validation_markdown(self, output_path: str):
        """Generate markdown file with manual validation instructions"""
        from datetime import datetime

        output = []
        output.append("# Manual Permissions Validation Guide")
        output.append(f"\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        output.append("This guide provides step-by-step instructions to manually validate all permission rules.\n")

        # Group by rule type
        allow_results = [r for r in self.summary.results if r.rule_type == 'allow']
        deny_results = [r for r in self.summary.results if r.rule_type == 'deny']
        ask_results = [r for r in self.summary.results if r.rule_type == 'ask']

        # ALLOW section
        output.append("## 1. ALLOW Rules - Should Execute Automatically\n")
        output.append("These commands should execute WITHOUT asking for approval.\n")
        output.append("**Expected behavior:** Commands run immediately and return results.\n")

        for i, result in enumerate(allow_results, 1):
            output.append(f"### Test {i}: {result.pattern}")
            output.append(f"**Source:** `{result.file_source}`")
            output.append(f"**Status:** {'✅ Valid' if result.is_valid else '❌ Invalid'}")
            if not result.is_valid:
                output.append(f"**Issue:** {result.reason}")
            output.append("\n**Example commands:**")
            for example in result.examples:
                output.append(f"```bash\n{example}\n```")
            output.append("\n**Validation steps:**")
            output.append("1. Execute the example command")
            output.append("2. Verify it runs WITHOUT asking for approval")
            output.append("3. Verify it returns results successfully")
            output.append(f"4. Mark result: [ ] ✅ Pass | [ ] ❌ Fail\n")
            output.append("---\n")

        # DENY section
        output.append("\n## 2. DENY Rules - Should Block Automatically\n")
        output.append("These commands should be BLOCKED WITHOUT asking.\n")
        output.append("**Expected behavior:** Commands are blocked with an error message.\n")

        for i, result in enumerate(deny_results, 1):
            output.append(f"### Test {i}: {result.pattern}")
            output.append(f"**Source:** `{result.file_source}`")
            output.append(f"**Status:** {'✅ Valid' if result.is_valid else '❌ Invalid'}")
            if not result.is_valid:
                output.append(f"**Issue:** {result.reason}")
            output.append("\n**Example commands:**")
            for example in result.examples:
                output.append(f"```bash\n{example}\n```")
            output.append("\n**Validation steps:**")
            output.append("1. Execute the example command")
            output.append("2. Verify it is BLOCKED immediately")
            output.append("3. Verify an error message is shown")
            output.append("4. Verify NO approval prompt is shown")
            output.append(f"5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail\n")
            output.append("---\n")

        # ASK section
        output.append("\n## 3. ASK Rules - Should Prompt for Approval\n")
        output.append("These commands should ASK for user approval before execution.\n")
        output.append("**Expected behavior:** User is prompted to approve/deny before execution.\n")

        for i, result in enumerate(ask_results, 1):
            output.append(f"### Test {i}: {result.pattern}")
            output.append(f"**Source:** `{result.file_source}`")
            output.append(f"**Status:** {'✅ Valid' if result.is_valid else '❌ Invalid'}")
            if not result.is_valid:
                output.append(f"**Issue:** {result.reason}")
            output.append("\n**Example commands:**")
            for example in result.examples:
                output.append(f"```bash\n{example}\n```")
            output.append("\n**Validation steps:**")
            output.append("1. Execute the example command")
            output.append("2. Verify an approval prompt is shown")
            output.append("3. Test DENY: Select 'No' and verify command is blocked")
            output.append("4. Test APPROVE: Select 'Yes' and verify command executes")
            output.append(f"5. Mark result: [ ] ✅ Pass | [ ] ❌ Fail\n")
            output.append("---\n")

        # Summary checklist
        output.append("\n## Validation Summary Checklist\n")
        output.append(f"- [ ] All {len(allow_results)} ALLOW rules execute automatically")
        output.append(f"- [ ] All {len(deny_results)} DENY rules block automatically")
        output.append(f"- [ ] All {len(ask_results)} ASK rules prompt for approval")
        output.append("- [ ] settings.json is strict (standard operations)")
        output.append("- [ ] settings.local.json is more open (query operations)")

        # Write to file
        with open(output_path, 'w') as f:
            f.write('\n'.join(output))

        print(f"\n📝 Manual validation guide generated: {output_path}")


def find_claude_shared_dir() -> Optional[Path]:
    """
    Try to find .claude-shared directory by:
    1. Checking CLAUDE_SHARED_PATH environment variable
    2. Looking in parent directories for .claude-shared
    3. Returning None if not found
    """
    # Check environment variable
    env_path = os.getenv("CLAUDE_SHARED_PATH")
    if env_path:
        path = Path(env_path)
        if path.exists():
            return path

    # Look in parent directories
    current = Path(__file__).resolve().parent
    for _ in range(5):  # Look up to 5 levels
        claude_shared = current / ".claude-shared"
        if claude_shared.exists():
            return claude_shared
        current = current.parent

    return None


def main(argv=None):
    """Main test execution

    Args:
        argv: Optional list of command-line arguments (for testing)
    """
    parser = argparse.ArgumentParser(
        description="Validate permissions in settings.json and settings.local.json"
    )
    parser.add_argument(
        "--settings-dir",
        type=str,
        help="Path to directory containing settings.json (defaults to found .claude-shared dir)"
    )
    parser.add_argument(
        "--settings-file",
        type=str,
        help="Path to settings.json file"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Path to output validation report"
    )

    args = parser.parse_args(argv)

    # Determine paths
    if args.settings_file:
        settings_path = Path(args.settings_file)
    elif args.settings_dir:
        settings_path = Path(args.settings_dir) / "settings.json"
    else:
        # Try to find .claude-shared
        base_path = find_claude_shared_dir()
        if not base_path:
            print("❌ Error: Could not find .claude-shared directory")
            print("   Set CLAUDE_SHARED_PATH or use --settings-dir option")
            sys.exit(1)
        settings_path = base_path / "settings.json"

    # Determine settings local path
    settings_local_path = settings_path.parent / "settings.local.json"

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = Path(__file__).parent / "MANUAL_VALIDATION.md"

    # Validate files exist
    if not settings_path.exists():
        print(f"❌ Error: {settings_path} not found")
        sys.exit(1)

    # Create validator
    validator = PermissionsValidator(
        str(settings_path),
        str(settings_local_path) if settings_local_path.exists() else None
    )

    # Run validation
    summary = validator.run_validation()

    # Print results
    all_valid = validator.print_summary()

    # Generate manual validation guide
    validator.generate_manual_validation_markdown(str(output_path))

    # Exit with appropriate code
    sys.exit(0 if all_valid else 1)


if __name__ == "__main__":
    main()