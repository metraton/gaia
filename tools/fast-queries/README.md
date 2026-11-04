# Fast-Queries: Quick Health Checks

Diagnostic scripts that print one verdict line per check and list detail only for
what is wrong. Each needs its own CLI on the PATH: `kubectl`, `terraform` (or
`terragrunt`), `gcloud`.

## Quick Usage

```bash
# Run all health checks
.claude/tools/fast-queries/run_triage.sh

# Run specific checks
.claude/tools/fast-queries/run_triage.sh gitops    # Kubernetes/pods
.claude/tools/fast-queries/run_triage.sh terraform # Terraform validation
.claude/tools/fast-queries/run_triage.sh gcp       # GCP resources
```

`run_triage.sh` runs the scripts below with no arguments, so each uses its own
defaults. `k8s` and `kubernetes` also select gitops, `tf` selects terraform and
`cloud` selects gcp.

## Available Scripts

### 1. GitOps Health Check
```bash
.claude/tools/fast-queries/gitops/quicktriage_gitops_operator.sh [namespace]
```
**Output:** Pods that are not Running or Completed, deployments whose ready and
desired replicas differ, a HelmRelease count when that resource exists, and the
last five Warning events. The namespace defaults to `tcm-non-prod`.

### 2. Terraform Validation
```bash
.claude/tools/fast-queries/terraform/quicktriage_terraform_architect.sh [directory]
```
**Output:** Format, validation and plan verdicts for the directory (default `.`).
It uses `terragrunt` when `USE_TERRAGRUNT=true` or the directory holds a
`terragrunt.hcl`. With Terraform it runs `init -backend=false` when `.terraform` is
missing, then `plan -lock=false -refresh=false`; Terragrunt runs a plain
`plan -lock=false` and prints no verdict for it.

### 3. GCP Resources Check
```bash
.claude/tools/fast-queries/cloud/gcp/quicktriage_gcp_troubleshooter.sh [project]
```
**Output:** GKE cluster and Cloud SQL states, error-level log entries of the last
hour, and quotas above 80% use. The project comes from `GCP_PROJECT`, then the
first argument, then `gcloud config get-value project`.

### Not run by `run_triage.sh`
- `appservices/quicktriage_devops_developer.sh [repo_path]` lists the project
  files, git status, `node_modules`, package scripts and Docker availability.
- `cloud/aws/quicktriage_aws_troubleshooter.sh [profile] [region]` only prints
  that it is in standby and what it would check.

## For Agents

Add this to agent prompts for quick diagnostics:

```bash
# Instead of multiple kubectl/terraform/gcloud commands:
bash .claude/tools/fast-queries/gitops/quicktriage_gitops_operator.sh namespace
```

## Output Format

- ✅ = Healthy/OK
- ❌ = Problem detected
- ⚠️  = Warning/non-critical issue

## Exit codes

| Script | 0 | 1 | 2 |
|--------|---|---|---|
| gitops | no problem pod, no deployment short of replicas, no failed HelmRelease | one of those | never set |
| terraform | validation output has no `Error:` | validation output has `Error:` | tool missing or directory not found |
| gcp | no unhealthy cluster or Cloud SQL instance | no project configured, or an unhealthy cluster or instance | `gcloud` missing, the project lookup or any query failed |

Format problems, plan changes, log errors and quota warnings print a line but do
not change the exit code. The gitops script reads `HR_FAILED` on its last line,
and that variable is only set when the cluster has a HelmRelease resource; without
one the script ends with status 1 even when everything printed is healthy.

`run_triage.sh` runs under `set -e`: the first script that exits non-zero ends the
run with status 1, so the remaining scripts of `all` do not run.
