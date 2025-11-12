# Fast-Queries: Agent Diagnostic Scripts

Quick diagnostic and health-check scripts for each Gaia-Ops agent.
These scripts provide instant snapshots of system state without invoking the full orchestration workflow.

## Purpose

- **Sub-10 second diagnostics** for each agent domain
- **Self-contained validation** (no orchestrator overhead)
- **Domain-specific knowledge** captured in executable form
- **Development/debugging** reference for agent capabilities

## Directory Structure

```
fast-queries/
├── terraform/          # Terraform/Terragrunt validation
├── gitops/             # Kubernetes/Flux/Helm snapshots
├── cloud/
│   ├── gcp/            # GCP GKE/SQL/IAM diagnostics
│   └── aws/            # AWS EKS/VPC/CloudWatch diagnostics
└── appservices/        # Application health & hygiene
```

## Quick Start

### Run All Diagnostics

```bash
# Execute all triages
.claude/tools/fast-queries/run_triage.sh

# View summary output
.claude/tools/fast-queries/run_triage.sh all 2>&1 | tail -20
```

### Run Specific Agent Triage

```bash
# Terraform validation
.claude/tools/fast-queries/run_triage.sh terraform

# GitOps/Kubernetes health
.claude/tools/fast-queries/run_triage.sh gitops

# GCP infrastructure snapshot
.claude/tools/fast-queries/run_triage.sh gcp

# AWS infrastructure snapshot
.claude/tools/fast-queries/run_triage.sh aws

# Application/DevOps hygiene
.claude/tools/fast-queries/run_triage.sh devops
```

### Run Individual Scripts Directly

```bash
# Terraform triage (detects Terraform/Terragrunt automatically)
.claude/tools/fast-queries/terraform/quicktriage_terraform_architect.sh

# With explicit directory
.claude/tools/fast-queries/terraform/quicktriage_terraform_architect.sh /path/to/terraform

# GitOps triage (checks current kubectl context)
.claude/tools/fast-queries/gitops/quicktriage_gitops_operator.sh

# GCP triage
GCP_PROJECT=my-project .claude/tools/fast-queries/cloud/gcp/quicktriage_gcp_troubleshooter.sh

# AWS triage
AWS_REGION=us-east-1 .claude/tools/fast-queries/cloud/aws/quicktriage_aws_troubleshooter.sh

# DevOps triage (current directory or explicit path)
.claude/tools/fast-queries/appservices/quicktriage_devops_developer.sh
.claude/tools/fast-queries/appservices/quicktriage_devops_developer.sh /path/to/app
```

## Output Format

Each script outputs timestamped logs with `[quicktriage]` prefix:

```
[quicktriage] Starting Terraform quick triage (dir=., terragrunt=false)
[quicktriage] terraform fmt -check
[quicktriage] terraform init -backend=false
[quicktriage] terraform validate
[quicktriage] terraform plan (detailed exit code)
[quicktriage] Quick triage completed. Exit code 1 on plan indicates drift...
```

## Integration with Orchestrator

Fast-queries are **NOT** invoked automatically by the orchestrator.
They are **optional pre-checks** for developers:

1. **Local development:** Run before submitting infrastructure changes
2. **CI/CD pipelines:** Include in pre-flight checks
3. **Debugging:** Used to validate state during development
4. **Documentation:** Each script demonstrates agent capabilities

## Exit Codes

- `0` - Success (system state healthy, no issues)
- `1` - Check failed (warnings, drift, or validation errors detected)
- `2` - Script error (command not found, permission denied, etc.)

## Performance Targets

| Domain | Target Duration | Common Issues Detected |
|--------|-----------------|----------------------|
| Terraform | 2-5 seconds | Format issues, validation errors, plan drift |
| GitOps | 2-3 seconds | Pod failures, deployment health, cluster connectivity |
| GCP | 5-8 seconds | GKE/Cloud SQL availability, quota issues |
| AWS | 5-8 seconds | EKS/VPC issues, endpoint health, IAM access |
| DevOps | 2-4 seconds | Lint errors, test discovery, security audits |

## Common Environment Variables

### Terraform

```bash
USE_TERRAGRUNT=true         # Use terragrunt instead of terraform
TERRAGRUNT_DOWNLOAD=.tg     # Cache directory for terragrunt
TARGET_DIR=./terraform      # Directory to validate
```

### GitOps

```bash
NAMESPACE=prod              # Kubernetes namespace to check
LABEL_SELECTOR=tier=backend # Pod selector filter
KUBECONFIG=~/.kube/config   # Custom kubeconfig path
```

### GCP

```bash
GCP_PROJECT=my-project      # GCP project ID
GKE_CLUSTER=prod-cluster    # GKE cluster name
GKE_REGION=us-central1      # GKE region
CLOUD_SQL_INSTANCE=prod-db  # Cloud SQL instance name
```

### AWS

```bash
AWS_REGION=us-east-1        # AWS region
EKS_CLUSTER=prod-cluster    # EKS cluster name
VPC_ID=vpc-12345            # VPC to inspect
```

### DevOps

```bash
LINT_CMD="npm run lint"     # Custom lint command
TEST_CMD="npm test"         # Custom test command
APP_PATH=./apps/api         # Application path
```

## See Also

- `USAGE_GUIDE.md` - Detailed usage examples and integration patterns
- Individual script comments for domain-specific details
- `.claude/config/orchestration-workflow.md` - Full orchestrator workflow

## Future Extensions

Planned fast-queries for:
- [ ] Helm release audits
- [ ] Terraform state locking analysis
- [ ] Security policy compliance checks
- [ ] Cost optimization snapshots
- [ ] Custom domain-specific queries
