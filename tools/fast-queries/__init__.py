"""
Fast-Queries Module: Agent diagnostic scripts

This module provides quick diagnostic and health-check scripts for each Gaia-Ops agent.
Scripts provide instant snapshots of system state without invoking the full orchestration workflow.

Typical usage:
    # Run all diagnostics via CLI
    $ .claude/tools/fast-queries/run_triage.sh all

    # Run specific agent
    $ .claude/tools/fast-queries/run_triage.sh terraform

Available agents:
    - terraform: Terraform/Terragrunt validation
    - gitops: Kubernetes/Flux/Helm snapshots
    - gcp: GCP GKE/SQL/IAM diagnostics
    - aws: AWS EKS/VPC/CloudWatch diagnostics
    - devops: Application health & hygiene checks

See README.md for detailed documentation.
"""

__version__ = "1.0.0"
__all__ = [
    "terraform",
    "gitops",
    "cloud",
    "appservices",
]
