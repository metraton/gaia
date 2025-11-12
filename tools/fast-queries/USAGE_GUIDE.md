# Fast-Queries Usage Guide

Complete guide for running and interpreting fast-query diagnostics.

## Quick Reference

| Agent | Script | Common Usage |
|-------|--------|--------------|
| terraform-architect | `terraform/quicktriage_*` | Validate TF/Terragrunt config |
| gitops-operator | `gitops/quicktriage_*` | Check K8s workload health |
| gcp-troubleshooter | `cloud/gcp/quicktriage_*` | GKE + Cloud SQL snapshot |
| aws-troubleshooter | `cloud/aws/quicktriage_*` | EKS + VPC endpoint snapshot |
| devops-developer | `appservices/quicktriage_*` | Lint + test discovery + audit |

## Basic Usage

### Run All Triages

```bash
# From any directory
.claude/tools/fast-queries/run_triage.sh

# With verbose output
VERBOSE=true .claude/tools/fast-queries/run_triage.sh all
```

### Run Single Agent Triage

```bash
# Terraform
.claude/tools/fast-queries/run_triage.sh terraform

# GitOps
.claude/tools/fast-queries/run_triage.sh gitops

# GCP
.claude/tools/fast-queries/run_triage.sh gcp

# AWS
.claude/tools/fast-queries/run_triage.sh aws

# DevOps
.claude/tools/fast-queries/run_triage.sh devops
```

### Run Script Directly

Each script can be executed independently:

```bash
# Terraform (auto-detects directory, supports both TF and Terragrunt)
.claude/tools/fast-queries/terraform/quicktriage_terraform_architect.sh
.claude/tools/fast-queries/terraform/quicktriage_terraform_architect.sh /path/to/tf

# GitOps (uses kubectl current context)
.claude/tools/fast-queries/gitops/quicktriage_gitops_operator.sh

# GCP (uses gcloud configured project by default)
.claude/tools/fast-queries/cloud/gcp/quicktriage_gcp_troubleshooter.sh

# AWS (uses AWS_REGION environment variable)
.claude/tools/fast-queries/cloud/aws/quicktriage_aws_troubleshooter.sh

# DevOps (current directory or explicit path)
.claude/tools/fast-queries/appservices/quicktriage_devops_developer.sh
.claude/tools/fast-queries/appservices/quicktriage_devops_developer.sh /path/to/app
```

## Advanced Usage

### 1. Environment Variables

Each script respects domain-specific environment variables for customization:

**Terraform:**
```bash
export USE_TERRAGRUNT=true
export TARGET_DIR=/path/to/terraform
export TERRAGRUNT_DOWNLOAD=.terragrunt-cache

.claude/tools/fast-queries/terraform/quicktriage_terraform_architect.sh
```

**GitOps:**
```bash
export NAMESPACE=prod
export LABEL_SELECTOR=tier=backend
export KUBECONFIG=~/.kube/prod-config

.claude/tools/fast-queries/gitops/quicktriage_gitops_operator.sh
```

**GCP:**
```bash
export GCP_PROJECT=my-project
export GKE_CLUSTER=prod-cluster
export GKE_REGION=us-central1
export CLOUD_SQL_INSTANCE=prod-db

.claude/tools/fast-queries/cloud/gcp/quicktriage_gcp_troubleshooter.sh
```

**AWS:**
```bash
export AWS_REGION=us-east-1
export AWS_PROFILE=prod
export EKS_CLUSTER=prod-cluster
export VPC_ID=vpc-12345

.claude/tools/fast-queries/cloud/aws/quicktriage_aws_troubleshooter.sh
```

**DevOps:**
```bash
export LINT_CMD="npm run lint:strict"
export TEST_DISCOVERY_CMD="npm run test -- --listTests"
export APP_PATH=./apps/api

.claude/tools/fast-queries/appservices/quicktriage_devops_developer.sh
```

### 2. Integration with CI/CD

**GitHub Actions Example:**
```yaml
name: Pre-Flight Diagnostics
on: [pull_request, push]

jobs:
  fast-queries:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Setup Node.js
        uses: actions/setup-node@v3
        with:
          node-version: '18'

      - name: Install gaia-ops
        run: npm install @jaguilar87/gaia-ops

      - name: Run diagnostics
        run: .claude/tools/fast-queries/run_triage.sh all
        env:
          GCP_PROJECT: ${{ secrets.GCP_PROJECT }}
          AWS_REGION: us-east-1
```

**GitLab CI Example:**
```yaml
pre-flight:
  stage: validation
  script:
    - npm install @jaguilar87/gaia-ops
    - .claude/tools/fast-queries/run_triage.sh all
  variables:
    GCP_PROJECT: ${GCP_PROJECT}
    AWS_REGION: us-east-1
  only:
    - merge_requests
```

### 3. Parsing Output for Automation

Extract specific information from triage output:

```bash
# Count successful checks
.claude/tools/fast-queries/run_triage.sh all | grep -c "✓" || echo "Some checks failed"

# Extract only failure lines
.claude/tools/fast-queries/terraform/quicktriage_terraform_architect.sh | \
  grep "✗" || echo "All terraform checks passed"

# Save output to log file
.claude/tools/fast-queries/run_triage.sh all > /tmp/triage-$(date +%s).log 2>&1

# Parse and format for notification
TRIAGE_OUTPUT=$(.claude/tools/fast-queries/run_triage.sh all 2>&1)
PASS_COUNT=$(echo "$TRIAGE_OUTPUT" | grep -c "✓" || echo "0")
FAIL_COUNT=$(echo "$TRIAGE_OUTPUT" | grep -c "✗" || echo "0")
echo "Triage Report: $PASS_COUNT passed, $FAIL_COUNT failed"
```

### 4. Conditional Execution

Run triages only when relevant:

```bash
# Only run Terraform triage if TF directory exists
[[ -d ./terraform ]] && \
  .claude/tools/fast-queries/run_triage.sh terraform

# Only run GitOps triage if kubectl is available
command -v kubectl >/dev/null && \
  .claude/tools/fast-queries/run_triage.sh gitops

# Run GCP triage only if gcloud is configured
gcloud config get-value project >/dev/null 2>&1 && \
  .claude/tools/fast-queries/run_triage.sh gcp

# Run all triages except AWS (if AWS credentials missing)
[[ -z "${AWS_REGION:-}" ]] && export AWS_REGION=us-east-1
.claude/tools/fast-queries/run_triage.sh all
```

### 5. Custom Extensions

Create new fast-queries for domain-specific needs by adding a script:

**Template: `tools/fast-queries/custom/quicktriage_helm.sh`**
```bash
#!/usr/bin/env bash
# QuickTriage script for Helm releases
# Validates Helm chart integrity and release health

set -euo pipefail

NAMESPACE="${NAMESPACE:-default}"

info() {
  printf '[quicktriage] %s\n' "$*"
}

run_cmd() {
  local description="$1"
  shift

  if ! command -v "$1" >/dev/null 2>&1; then
    info "Skipping ${description} (command $1 not available)"
    return
  fi

  info "$description"
  "$@" || info "Command failed: $*"
}

info "Starting Helm quick triage (namespace=${NAMESPACE})"

run_cmd "helm list" helm list -n "$NAMESPACE"
run_cmd "helm status" helm status -n "$NAMESPACE"
run_cmd "helm lint" helm lint .

info "Helm quick triage completed."
```

Then update `run_triage.sh` to include:
```bash
# Helm (add to appropriate location)
if [[ "$SELECTED_AGENT" == "all" ]] || [[ "$SELECTED_AGENT" == "helm" ]]; then
  TOTAL=$((TOTAL + 1))
  if run_triage \
    "$SCRIPT_DIR/custom/quicktriage_helm.sh" \
    "Helm Release Triage"; then
    PASSED=$((PASSED + 1))
  else
    FAILED=$((FAILED + 1))
  fi
fi
```

## Interpreting Results

### Exit Codes

- **0** - Success (all checks passed, system state healthy)
- **1** - Warnings/Errors (drift, validation issues, or resource problems detected)
- **2** - Script Error (missing command, permission denied, etc.)

### Common Issues by Domain

**Terraform:**
- `exit 1` = Format issues or validation errors found
- `exit 1` = Plan shows drift from current state
- Check `terraform plan` output for specific changes

**GitOps:**
- `exit 1` = Pod or deployment is not healthy
- `exit 1` = Cluster connectivity issue
- Check `kubectl get pods` for details

**GCP:**
- `exit 1` = GKE cluster unreachable or unhealthy
- `exit 1` = Cloud SQL instance offline
- Check `gcloud container clusters describe`

**AWS:**
- `exit 1` = EKS cluster endpoint unavailable
- `exit 1` = VPC endpoint health check failed
- Check `aws eks describe-cluster`

**DevOps:**
- `exit 1` = Lint errors found in code
- `exit 1` = Security vulnerabilities detected
- Check linter output for specific violations

## Troubleshooting

### "Command not found" messages

Scripts gracefully skip unavailable commands. Install required tools:

```bash
# Terraform/Terragrunt
brew install terraform terragrunt

# Kubernetes tools
brew install kubectl helm

# GCP tools
curl https://sdk.cloud.google.com | bash

# AWS tools
brew install awscli

# DevOps tools
brew install node pnpm
```

### Scripts hang or timeout

- Ensure cloud credentials are configured (`.gcloud/`, `~/.aws/`)
- Check network connectivity to clusters/APIs
- Reduce scope with environment variables:
  ```bash
  # Limit to specific namespace
  NAMESPACE=default .claude/tools/fast-queries/run_triage.sh gitops
  ```

### Symlink resolution issues

If scripts won't run from `.claude/tools/fast-queries/`:

```bash
# Verify symlinks are valid
ls -la .claude/tools/fast-queries/

# If broken, reinstall
npm install @jaguilar87/gaia-ops
```

## Performance Metrics

Typical execution times (cold start, with all CLIs installed):

| Script | Time | Dependencies |
|--------|------|--------------|
| terraform | 2-4s | terraform, terragrunt |
| gitops | 2-3s | kubectl, flux |
| gcp | 5-8s | gcloud CLI |
| aws | 5-8s | aws CLI |
| devops | 1-3s | npm, pnpm |
| **all** | 20-35s | all above |

## Security Notes

- **Read-only operations only** (T0 operations, no infrastructure changes)
- Safe to run in pre-prod and production environments
- Uses existing credentials from `~/.gcloud/` and `~/.aws/`
- No credentials are logged or stored by scripts
- All checks are non-destructive

## See Also

- `README.md` - Overview and quick start
- Individual script headers - Domain-specific options
- `.claude/config/orchestration-workflow.md` - Orchestrator workflow
- Agent documentation - Detailed agent capabilities
