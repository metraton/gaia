---
name: approval
description: How to present plans for T3 approval - structured plan presentation and risk communication
user-invocable: false
---

# Approval Skill

## When to Use This Skill

Use when investigation complete and you need user approval for T3 operations:
- `terraform apply` / `terragrunt apply`
- `kubectl apply` (via GitOps commit + flux reconcile)
- `git commit` + `git push`
- `npm run deploy`
- Any state-modifying operation

## T3 Detection Keywords

| Keyword | Operation | Risk Level |
|---------|-----------|------------|
| apply | Infrastructure change | HIGH |
| deploy | Application deployment | HIGH |
| create | New resource | MEDIUM |
| delete | Resource removal | CRITICAL |
| push | Code/manifest to production | HIGH |
| destroy | Complete teardown | CRITICAL |

## Plan Presentation Format (MANDATORY)

Present plans using this structure:

```markdown
## Deployment Plan

### Summary (3-5 bullets)
- What will be changed
- Why this change is needed
- What the expected outcome is

### Changes Proposed

**Resources to CREATE:**
- [Resource 1]: [Description]
- [Resource 2]: [Description]

**Resources to MODIFY:**
- [Resource 3]: [What changes] (before → after)

**Resources to DELETE:**
- [Resource 4]: [Why deletion]

### Validation Results

**Dry-run status:**
- ✅ `terraform plan` - No errors, [X] changes
- ✅ `kubectl apply --dry-run=client` - Valid manifest
- ✅ Syntax check passed

**Dependencies verified:**
- [Dependency 1]: Available ✓
- [Dependency 2]: Available ✓

### Risk Assessment

**Risk Level:** [LOW | MEDIUM | HIGH | CRITICAL]

**Potential Risks:**
1. [Risk 1]: [Impact if it happens]
   - Mitigation: [How we handle it]

2. [Risk 2]: [Impact if it happens]
   - Mitigation: [How we handle it]

**Rollback Plan:**
- If operation fails: [Rollback steps]
- Recovery time estimate: [time]

### Execution Steps

When approved, will execute:
1. [Step 1]
2. [Step 2]
3. [Step 3]
4. Verify success

### Files Affected

**Git changes:**
- Modified: [file1.hcl], [file2.yaml]
- Added: [file3.tf]
- Deleted: [none]

**Commit message:**
```
[type]([scope]): [description]

[detailed explanation]
```
```

## Risk Level Classification

| Level | Criteria | Examples |
|-------|----------|----------|
| **LOW** | Single resource, non-prod, no dependencies | Create dev namespace |
| **MEDIUM** | Multiple resources, non-prod, some dependencies | Deploy app to dev cluster |
| **HIGH** | Production changes, dependencies, potential downtime | Deploy app to prod cluster |
| **CRITICAL** | Irreversible operations, data loss possible | Delete production database |

## Approval Request Template

After presenting the plan, use this format:

```markdown
## Approval Required

**Operation:** [terraform apply / kubectl apply / etc.]
**Environment:** [dev / staging / prod]
**Risk Level:** [LOW / MEDIUM / HIGH / CRITICAL]

**Ready to proceed?**
- [ ] Yes, execute the plan
- [ ] No, I need to review further
- [ ] Modify the plan (specify changes)
```

Then emit AGENT_STATUS:

```html
<!-- AGENT_STATUS -->
PLAN_STATUS: PENDING_APPROVAL
CURRENT_PHASE: Present
PENDING_STEPS: ["Get approval", "Execute changes", "Verify success"]
NEXT_ACTION: Wait for user approval to [operation]
AGENT_ID: [agentId]
<!-- /AGENT_STATUS -->
```

## Examples by Operation Type

### Example 1: Terraform Apply

```markdown
## Terraform Apply Plan

### Summary
- Creating new VPC for digital-eks-prod cluster
- Adds 3 subnets across us-east-1a, 1b, 1c
- No existing resources affected

### Changes Proposed

**Resources to CREATE:**
- `aws_vpc.prod_vpc`: VPC with CIDR 10.0.0.0/16
- `aws_subnet.prod_subnet_a`: Subnet in us-east-1a
- `aws_subnet.prod_subnet_b`: Subnet in us-east-1b
- `aws_subnet.prod_subnet_c`: Subnet in us-east-1c

**Resources to MODIFY:**
- None

**Resources to DELETE:**
- None

### Validation Results

**Dry-run status:**
- ✅ `terragrunt plan` - No errors, 4 to add, 0 to change, 0 to destroy
- ✅ `terragrunt validate` - Success
- ✅ `tflint` - No issues

### Risk Assessment

**Risk Level:** MEDIUM

**Potential Risks:**
1. VPC CIDR conflict with existing infrastructure
   - Mitigation: Verified no overlaps with existing VPCs

2. Subnet creation might timeout
   - Mitigation: Timeouts set to 10m, can retry

**Rollback Plan:**
- If creation fails: `terragrunt destroy` removes partial resources
- Recovery time: ~5 minutes

### Execution Steps

When approved, will execute:
1. `git add terraform/vpc/`
2. `git commit -m "feat(infra): add prod VPC"`
3. `git push origin main`
4. `terragrunt apply terraform/vpc/`
5. Verify VPC created with `aws ec2 describe-vpcs`

### Files Affected

**Git changes:**
- Added: `terraform/vpc/terragrunt.hcl`
- Added: `terraform/vpc/main.tf`

**Commit message:**
```
feat(infra): add production VPC for digital-eks cluster

- CIDR: 10.0.0.0/16
- Subnets across 3 AZs (us-east-1a, 1b, 1c)
- Enables EKS cluster deployment

```

## Approval Required

**Operation:** terraform apply
**Environment:** prod
**Risk Level:** MEDIUM

<!-- AGENT_STATUS -->
PLAN_STATUS: PENDING_APPROVAL
CURRENT_PHASE: Present
PENDING_STEPS: ["Get approval", "Execute terraform apply", "Verify VPC created"]
NEXT_ACTION: Wait for user approval to create production VPC
AGENT_ID: a12345
<!-- /AGENT_STATUS -->
```

### Example 2: GitOps Deployment

```markdown
## GitOps Deployment Plan

### Summary
- Updating graphql-server image to v1.0.180
- No configuration changes
- Flux will auto-reconcile in ~1 minute

### Changes Proposed

**HelmRelease to MODIFY:**
- `graphql-server` in namespace `common`
  - Image: ghcr.io/vtr/graphql-server:v1.0.176 → v1.0.180
  - No other changes

### Validation Results

**Dry-run status:**
- ✅ `kubectl apply --dry-run=client` - Valid manifest
- ✅ YAML syntax check - Passed
- ✅ Image exists in registry - Verified

### Risk Assessment

**Risk Level:** LOW

**Potential Risks:**
1. New image might have bugs
   - Mitigation: Tested in dev cluster, all tests passed

2. Pod restart might cause brief downtime
   - Mitigation: RollingUpdate strategy, 2 replicas ensure availability

**Rollback Plan:**
- If deployment fails: `git revert` + `flux reconcile`
- Recovery time: ~2 minutes

### Execution Steps

When approved, will execute:
1. `git add gitops/clusters/prod-digital-eks/common/graphql-server.yaml`
2. `git commit -m "chore(graphql): update to v1.0.180"`
3. `git push origin main`
4. Flux auto-reconciles in ~1 minute
5. Verify with `kubectl get pods -n common`

### Files Affected

**Git changes:**
- Modified: `gitops/clusters/prod-digital-eks/common/graphql-server.yaml`

**Commit message:**
```
chore(graphql): update graphql-server to v1.0.180

- Image: ghcr.io/vtr/graphql-server:v1.0.180
- Tested in dev cluster
- No breaking changes

```

## Approval Required

**Operation:** git push + flux reconcile
**Environment:** prod
**Risk Level:** LOW

<!-- AGENT_STATUS -->
PLAN_STATUS: PENDING_APPROVAL
CURRENT_PHASE: Present
PENDING_STEPS: ["Get approval", "Commit changes", "Flux reconcile", "Verify pods"]
NEXT_ACTION: Wait for user approval to update graphql-server image
AGENT_ID: a67890
<!-- /AGENT_STATUS -->
```

## After Approval

When user approves, orchestrator will resume with:
```
"User approved. Execute the plan."
```

At that point, switch to **execution-skill** mode.

## Anti-Patterns to Avoid

❌ **Asking for approval without showing the plan**
- Always present full plan with risks before asking

❌ **Vague change descriptions**
- Be specific: "Update image to v1.0.180" not "Update app"

❌ **Ignoring rollback plan**
- Always document how to undo if things go wrong

❌ **Wrong risk level**
- Be honest about risks, don't minimize production changes

## Commit Standards

Follow `git-conventions` skill for commit format. Commits are auto-validated by hooks.
