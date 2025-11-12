#!/usr/bin/env python3
"""
Pre-execution security hook for kubectl commands to enforce GitOps principles.
Prevents direct cluster modifications and ensures proper GitOps workflows.
"""

import sys
import re
import json
from typing import List, Dict, Any

# Forbidden kubectl commands that modify cluster state
FORBIDDEN_KUBECTL_COMMANDS = [
    r'kubectl\s+apply(?!\s+.*--dry-run)',  # kubectl apply without --dry-run
    r'kubectl\s+create(?!\s+.*--dry-run)', # kubectl create without --dry-run
    r'kubectl\s+patch',                    # kubectl patch (always modifies state)
    r'kubectl\s+replace',                  # kubectl replace (modifies state)
    r'kubectl\s+delete',                   # kubectl delete (destructive)
    r'kubectl\s+scale',                    # kubectl scale (modifies state)
    r'kubectl\s+rollout\s+restart',        # kubectl rollout restart (modifies state)
    r'kubectl\s+annotate(?!\s+.*--dry-run)', # kubectl annotate without --dry-run
    r'kubectl\s+label(?!\s+.*--dry-run)',   # kubectl label without --dry-run
]

# Forbidden flux commands that trigger reconciliation
FORBIDDEN_FLUX_COMMANDS = [
    r'flux\s+create',                      # flux create (modifies GitOps resources)
    r'flux\s+delete',                      # flux delete (destructive)
    r'flux\s+suspend',                     # flux suspend (modifies reconciliation)
    r'flux\s+resume',                      # flux resume (modifies reconciliation)
]

# Forbidden helm commands that modify releases
FORBIDDEN_HELM_COMMANDS = [
    r'helm\s+install(?!\s+.*--dry-run)',   # helm install without --dry-run
    r'helm\s+upgrade(?!\s+.*--dry-run)',   # helm upgrade without --dry-run
    r'helm\s+uninstall',                   # helm uninstall (destructive)
    r'helm\s+rollback',                    # helm rollback (modifies state)
]

# Safe read-only commands that are always allowed
SAFE_KUBECTL_COMMANDS = [
    r'kubectl\s+get',
    r'kubectl\s+describe',
    r'kubectl\s+logs',
    r'kubectl\s+top',
    r'kubectl\s+explain',
    r'kubectl\s+version',
    r'kubectl\s+cluster-info',
    r'kubectl\s+config\s+view',
    r'kubectl\s+api-resources',
    r'kubectl\s+api-versions',
]

SAFE_FLUX_COMMANDS = [
    r'flux\s+get',
    r'flux\s+check',
    r'flux\s+version',
    r'flux\s+logs',
    r'flux\s+stats',
    r'flux\s+tree',
]

SAFE_HELM_COMMANDS = [
    r'helm\s+list',
    r'helm\s+status',
    r'helm\s+history',
    r'helm\s+template',
    r'helm\s+lint',
    r'helm\s+version',
    r'helm\s+show',
    r'helm\s+search',
]

class GitOpsSecurityViolation(Exception):
    """Exception raised when a command violates GitOps security principles."""
    pass

def is_safe_command(command: str) -> bool:
    """Check if a command is explicitly safe (read-only)."""
    safe_patterns = SAFE_KUBECTL_COMMANDS + SAFE_FLUX_COMMANDS + SAFE_HELM_COMMANDS

    for pattern in safe_patterns:
        if re.search(pattern, command, re.IGNORECASE):
            return True
    return False

def is_forbidden_command(command: str) -> bool:
    """Check if a command is forbidden (modifies cluster state)."""
    forbidden_patterns = FORBIDDEN_KUBECTL_COMMANDS + FORBIDDEN_FLUX_COMMANDS + FORBIDDEN_HELM_COMMANDS

    for pattern in forbidden_patterns:
        if re.search(pattern, command, re.IGNORECASE):
            return True
    return False

def validate_gitops_workflow(command: str, agent_type: str = None) -> Dict[str, Any]:
    """
    Validate command against GitOps security principles.

    Returns:
        dict: Validation result with status and details
    """
    result = {
        "allowed": False,
        "reason": "",
        "suggestions": [],
        "severity": "info"
    }

    # Check if command is explicitly safe
    if is_safe_command(command):
        result["allowed"] = True
        result["reason"] = "Read-only operation - safe to execute"
        return result

    # Check if command is forbidden
    if is_forbidden_command(command):
        result["allowed"] = False
        result["severity"] = "critical"
        result["reason"] = "Command violates GitOps principles - modifies cluster state directly"

        # Provide specific suggestions based on command type
        if "kubectl apply" in command and "--dry-run" not in command:
            result["suggestions"].extend([
                "Use: kubectl apply --dry-run=client -f <file>",
                "Create manifests in gitops repository first",
                "Commit changes and let Flux CD reconcile"
            ])
        elif "flux reconcile" in command and "--dry-run" not in command:
            result["suggestions"].extend([
                "Use: flux reconcile <resource> --dry-run",
                "Follow GitOps workflow: commit → push → automatic reconciliation"
            ])
        elif "helm install" in command or "helm upgrade" in command:
            result["suggestions"].extend([
                "Use: helm template or helm upgrade --dry-run",
                "Deploy via HelmRelease manifests in gitops repository"
            ])
        else:
            result["suggestions"].append("Use read-only commands or --dry-run alternatives")

        return result

    # For gitops-operator agent, be extra strict
    if agent_type == "gitops-operator":
        # Even if not explicitly forbidden, require explicit dry-run for apply operations
        if ("apply" in command or "create" in command) and "--dry-run" not in command:
            result["allowed"] = False
            result["severity"] = "high"
            result["reason"] = "GitOps operator must use --dry-run for all apply operations"
            result["suggestions"].append("Add --dry-run=client flag to command")
            return result

    # Default: allow but warn about unclear intent
    result["allowed"] = True
    result["severity"] = "warning"
    result["reason"] = "Command not explicitly validated - proceed with caution"
    result["suggestions"].append("Verify command follows GitOps principles")

    return result

def main():
    """Main hook execution."""
    if len(sys.argv) < 2:
        print("Usage: pre_kubectl_security.py <command>")
        sys.exit(1)

    command = " ".join(sys.argv[1:])

    # Extract agent type from environment or command context if available
    agent_type = None
    if "gitops-operator" in command or "GITOPS_OPERATOR" in str(sys.argv):
        agent_type = "gitops-operator"

    try:
        validation = validate_gitops_workflow(command, agent_type)

        if not validation["allowed"]:
            print(f"🚨 SECURITY VIOLATION: {validation['reason']}")
            print(f"📋 Command: {command}")
            print(f"⚠️  Severity: {validation['severity'].upper()}")

            if validation["suggestions"]:
                print("💡 Suggestions:")
                for suggestion in validation["suggestions"]:
                    print(f"   • {suggestion}")

            print("\n🔒 GitOps Security Enforcement Active")
            print("📖 Review: .claude/agents/gitops-operator.md or set GAIA_DOCS_PATH environment variable")

            sys.exit(1)  # Block command execution

        elif validation["severity"] in ["warning", "high"]:
            print(f"⚠️  WARNING: {validation['reason']}")
            if validation["suggestions"]:
                for suggestion in validation["suggestions"]:
                    print(f"💡 {suggestion}")

    except Exception as e:
        print(f"❌ Hook execution error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()