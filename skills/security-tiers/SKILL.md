---
name: security-tiers
description: T0-T3 operation classification and approval requirements for infrastructure operations
user-invocable: false
---

# Security Tiers

Operations are classified into four security tiers that govern what actions agents can perform.

## Tier Definitions

| Tier | Name | Description | Approval Required |
|------|------|-------------|-------------------|
| **T0** | Read-Only | Query state without side effects | No |
| **T1** | Validation | Local validation, syntax checks | No |
| **T2** | Simulation | Dry-run, plan, diff operations | No |
| **T3** | Realization | Apply changes to infrastructure/cluster | **Yes** |

## Operations by Tier

### T0 (Read-Only)
- `kubectl get`, `describe`, `logs`
- `terraform show`, `output`, `state list`
- `gcloud list`, `describe`
- `helm list`, `status`
- `flux get`
- File reading operations

### T1 (Validation)
- `terraform init`, `validate`, `fmt -check`
- `helm lint`, `template`
- `kustomize build`
- `tflint`, `eslint`, `ruff check`

### T2 (Simulation)
- `terraform plan`
- `kubectl apply --dry-run=server`, `kubectl diff`
- `helm upgrade --dry-run`
- Code generation and proposals

### T3 (Realization) - REQUIRES APPROVAL
- `terraform apply`
- `kubectl apply` (without dry-run)
- `git push` to main/protected branches
- `flux reconcile` with write operations
- Any operation that modifies live infrastructure

## Approval Protocol

For T3 operations:
1. Agent generates realization package (code + plan)
2. User reviews proposed changes
3. User explicitly approves: "Yes, proceed" or "Approved"
4. Agent executes only after validation["approved"] == True
