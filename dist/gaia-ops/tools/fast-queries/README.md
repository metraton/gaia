# Fast-Queries: Quick Health Checks

Optimized diagnostic scripts that provide instant health status in <5 seconds.
Only shows problems, not everything.

## Quick Usage

```bash
# Run all health checks
.claude/tools/fast-queries/run_triage.sh

# Run specific checks
.claude/tools/fast-queries/run_triage.sh gitops    # Kubernetes/pods
.claude/tools/fast-queries/run_triage.sh terraform # Terraform validation
.claude/tools/fast-queries/run_triage.sh gcp       # GCP resources
```

## Available Scripts

### 1. GitOps Health Check
```bash
.claude/tools/fast-queries/gitops/quicktriage_gitops_operator.sh [namespace]
```
**Output:** Only shows problematic pods, deployments not ready, and recent warnings.

### 2. Terraform Validation
```bash
.claude/tools/fast-queries/terraform/quicktriage_terraform_architect.sh [directory]
```
**Output:** ✅/❌ for format, validation, and drift detection.

### 3. GCP Resources Check
```bash
.claude/tools/fast-queries/cloud/gcp/quicktriage_gcp_troubleshooter.sh [project]
```
**Output:** Status of GKE clusters, Cloud SQL, recent errors, and quota warnings.

## For Agents

Add this to agent prompts for quick diagnostics:

```bash
# Instead of multiple kubectl/terraform/gcloud commands:
bash .claude/tools/fast-queries/gitops/quicktriage_gitops_operator.sh namespace
```

## Output Format

All scripts follow the same pattern:
- ✅ = Healthy/OK
- ❌ = Problem detected
- ⚠️  = Warning/non-critical issue

Exit codes:
- 0 = All healthy
- 1 = Issues found
- 2 = Script error (missing tools)

## Performance

| Script | Duration | Focus |
|--------|----------|-------|
| GitOps | 2-3 sec | Pod/deployment health |
| Terraform | 3-4 sec | Validation & drift |
| GCP | 4-5 sec | Resource availability |