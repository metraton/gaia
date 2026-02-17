---
name: fast-queries
description: Quick diagnostic scripts for instant health checks (<5 sec)
user-invocable: false
---

# Fast-Query Diagnostics

## Overview

You have access to fast diagnostic scripts that provide health status in **<5 seconds**. These scripts only show problems, not everything, making them ideal for quick triage.

**CRITICAL**: Always run relevant fast-queries **FIRST** when investigating issues, checking status, or validating changes.

## Available Health Checks

### 1. All Systems Triage
```bash
bash .claude/tools/fast-queries/run_triage.sh [domain]
```

**Domains**: `all`, `gitops`, `terraform`, `cloud`, `appservices`

**When to use**:
- User asks general "what's the status?"
- Starting any investigation
- Pre-flight checks before changes
- Post-deployment validation

**Example**:
```bash
# Check everything
bash .claude/tools/fast-queries/run_triage.sh all

# Check only Kubernetes
bash .claude/tools/fast-queries/run_triage.sh gitops
```

---

### 2. GitOps/Kubernetes Health
```bash
bash .claude/tools/fast-queries/gitops/quicktriage_gitops_operator.sh [namespace]
```

**Output**: Problematic pods, deployments not ready, recent warnings

**When to use**:
- Pod/deployment issues
- Investigating k8s errors
- Validating flux reconciliation
- Checking namespace health

**Example**:
```bash
# Check specific namespace
bash .claude/tools/fast-queries/gitops/quicktriage_gitops_operator.sh common

# Check all namespaces
bash .claude/tools/fast-queries/gitops/quicktriage_gitops_operator.sh
```

---

### 3. Terraform Validation
```bash
bash .claude/tools/fast-queries/terraform/quicktriage_terraform_architect.sh [directory]
```

**Output**: ✅/❌ for format, validation, and drift detection

**When to use**:
- Before terraform operations
- Validating HCL changes
- Drift detection
- Pre-commit checks

**Example**:
```bash
# Check specific terraform directory
bash .claude/tools/fast-queries/terraform/quicktriage_terraform_architect.sh terraform/environments/prod

# Check base terraform directory
bash .claude/tools/fast-queries/terraform/quicktriage_terraform_architect.sh terraform/
```

---

### 4. AWS Resources Check
```bash
bash .claude/tools/fast-queries/cloud/aws/quicktriage_aws_troubleshooter.sh
```

**Output**: Status of EKS clusters, RDS, VPC health, recent CloudWatch errors, quota warnings

**When to use**:
- AWS infrastructure issues
- EKS cluster problems
- Validating AWS resource state
- Quota/limit checks

**Example**:
```bash
bash .claude/tools/fast-queries/cloud/aws/quicktriage_aws_troubleshooter.sh
```

---

### 5. GCP Resources Check
```bash
bash .claude/tools/fast-queries/cloud/gcp/quicktriage_gcp_troubleshooter.sh [project]
```

**Output**: Status of GKE clusters, Cloud SQL, recent errors, quota warnings

**When to use**:
- GCP infrastructure issues
- GKE cluster problems
- Validating GCP resource state
- Quota/limit checks

**Example**:
```bash
# Check specific project
bash .claude/tools/fast-queries/cloud/gcp/quicktriage_gcp_troubleshooter.sh vtr-digital-prod

# Check default project
bash .claude/tools/fast-queries/cloud/gcp/quicktriage_gcp_troubleshooter.sh
```

---

## Output Format

All scripts follow the same pattern:

- ✅ **Green check** = Healthy/OK - No action needed
- ⚠️  **Yellow warning** = Warning/non-critical issue - Review recommended
- ❌ **Red X** = Problem detected - Action required

### Exit Codes
- `0` = All healthy
- `1` = Issues found (warnings or errors)
- `2` = Script error (missing tools, permissions)

## Workflow Integration

### Investigation Phase (T0 - Read Only)
```bash
# ALWAYS start with fast-queries
bash .claude/tools/fast-queries/run_triage.sh all

# Then deep-dive based on findings
# If gitops shows errors → kubectl describe pod X
# If terraform shows drift → terraform plan
# If cloud shows issues → aws/gcloud describe X
```

### Before T3 Operations (Apply/Deploy)
```bash
# Pre-flight check
bash .claude/tools/fast-queries/run_triage.sh terraform

# If ✅ proceed with terraform apply
# If ❌ fix issues first
```

### After T3 Operations (Validation)
```bash
# Post-deployment check
bash .claude/tools/fast-queries/run_triage.sh gitops

# Verify deployment succeeded
```

## Performance Characteristics

| Script | Duration | API Calls | Best For |
|--------|----------|-----------|----------|
| GitOps | 2-3 sec | ~5 kubectl | Pod health, deployment status |
| Terraform | 3-4 sec | 0 (local) | Validation, format check |
| AWS Cloud | 4-5 sec | ~8 AWS | EKS, RDS, VPC health |
| GCP Cloud | 4-5 sec | ~8 GCP | GKE, Cloud SQL health |
| All Systems | 8-15 sec | All combined | Full system triage |

## Interpreting Results

After running fast-queries:

1. **✅ All green**: System healthy, proceed with task
2. **⚠️ Warnings present**: Review warnings, decide if blocking
3. **❌ Errors found**:
   - Explain findings to user in their language
   - Suggest next steps for investigation
   - Ask if they want deep-dive diagnostics

### Example Response Pattern

```
I've run the fast-queries health check:

✅ Terraform: All modules valid
⚠️ GitOps: 2 pods in 'common' namespace restarting frequently
❌ AWS: EKS cluster 'digital-prod' has nodes in NotReady state

The critical issue is the EKS nodes. This is likely causing the pod restarts.
Would you like me to investigate the EKS node issue in detail?
```

## Cross-Agent Usage

These scripts are **shared across all agents**:

- **gitops-operator**: Can run cloud and terraform checks
- **terraform-architect**: Can run cloud and gitops checks
- **cloud-troubleshooter**: Can run all checks
- **devops-developer**: Can run all checks for debugging

## Limitations

- **No write operations**: Fast-queries are read-only (T0)
- **Snapshot in time**: Results represent current state only
- **No historical analysis**: For trends, use CloudWatch/Stackdriver
- **Requires credentials**: AWS/GCP CLI must be configured

## Best Practices

✅ **Do**:
- Run fast-queries FIRST before deep investigation
- Run relevant domain checks (gitops, terraform, cloud)
- Interpret results and explain to user
- Use for pre/post validation of changes

❌ **Don't**:
- Skip fast-queries and go straight to detailed commands
- Run full triage (`all`) when you know the specific domain
- Use for historical/trend analysis (use monitoring tools instead)
- Expect fixes (these are diagnostic only)

## Integration with Universal Protocol

Fast-queries support the investigation phase (PLAN_STATUS: INVESTIGATING):

```
1. User reports issue
2. Run fast-queries for relevant domain
3. Analyze results
4. If issues found → create plan to fix (move to PENDING_APPROVAL)
5. If all clear → investigate deeper with domain tools
```

---

**Last Updated**: 2026-01-15
**Version**: 1.0
**Maintained by**: gaia-ops system
