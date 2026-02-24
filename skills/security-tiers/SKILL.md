---
name: security-tiers
description: T0-T3 operation classification and approval requirements for infrastructure operations
user-invocable: false
---

# Security Tiers

## Classification Principle

Before executing any command, classify it by asking:

1. **Does it modify live state?** (create, update, delete, apply, push) → **T3**
2. **Does it simulate changes?** (plan, diff, dry-run) → **T2**
3. **Does it validate locally?** (validate, lint, fmt, check) → **T1**
4. **Is it read-only?** (get, list, describe, show, logs) → **T0**

## Tier Definitions

| Tier | Name | Side Effects | Approval |
|------|------|-------------|----------|
| **T0** | Read-Only | None | No |
| **T1** | Validation | None (local only) | No |
| **T2** | Simulation | None (dry-run) | No |
| **T3** | Realization | **Modifies infrastructure** | **Yes** |

## Examples (anchors, not exhaustive)

- **T0**: `kubectl get`, `terraform show`, `gcloud describe`, `gcloud sql instances describe`, `gcloud container clusters list`, `helm status`, `flux get`
- **T1**: `terraform validate`, `helm lint`, `tflint`, `kustomize build`
- **T2**: `terraform plan`, `kubectl diff`, `helm upgrade --dry-run`
- **T3**: `terraform apply`, `kubectl apply`, `git commit`, `git push` (any branch), `flux reconcile` (write)

## T3 Workflow

For T3 operations, follow the state flow in `agent-protocol`: PLANNING → PENDING_APPROVAL → APPROVED_EXECUTING → COMPLETE.

On-demand workflow skills (read from disk when needed):
- `.claude/skills/approval/SKILL.md` — plan format and presentation
- `.claude/skills/execution/SKILL.md` — post-approval execution protocol
