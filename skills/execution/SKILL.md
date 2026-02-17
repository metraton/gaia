---
name: execution
description: How to execute after approval - post-approval execution with verification
user-invocable: false
---

# Execution Skill

## When to Use This Skill

Use when user has approved a T3 operation and you need to execute the plan.

**Trigger phrases from orchestrator:**
- "User approved. Execute the plan."
- "User approved deployment. Proceed."
- "Approved. Execute terraform apply."

## Pre-Execution Checklist (MANDATORY)

Before executing ANY approved operation:

```markdown
## Pre-Execution Verification

- [ ] User explicitly approved (check prompt contains "approved")
- [ ] Git status clean (no uncommitted changes in the way)
- [ ] Plan still valid (no drift since plan was created)
- [ ] Credentials available (can access cloud/cluster)
- [ ] Dry-run still passes (validation hasn't broken)
```

If ANY check fails → STOP and report with `PLAN_STATUS: BLOCKED`

## Commit Standards

Follow `git-conventions` skill. Commits are auto-validated by hooks — format violations block execution.

## Execution Protocol by Operation Type

### Terraform/Terragrunt Apply

```bash
# 1. Verify git status
git status
# Expected: clean working tree or only intended changes

# 2. Stage changes
git add [files]

# 3. Commit with conventional commit format
git commit -m "$(cat <<'EOF'
[type]([scope]): [description]

[body]

EOF
)"

# 4. Push to remote
git push origin main

# 5. Execute terraform/terragrunt
cd [terraform_path]
terragrunt apply -auto-approve

# 6. Verify success
terragrunt output
```

### GitOps (Kubectl via Flux)

```bash
# 1. Verify git status
git status

# 2. Stage manifest changes
git add [manifest_files]

# 3. Commit
git commit -m "$(cat <<'EOF'
[type]([scope]): [description]

[body]

EOF
)"

# 4. Push to GitOps repo
git push origin main

# 5. Wait for Flux to reconcile (auto, ~1 minute)
# Or force reconcile:
flux reconcile helmrelease [name] -n [namespace]

# 6. Verify pods
kubectl get pods -n [namespace] -l [selector]
kubectl describe pod [pod-name] -n [namespace]
```

### Application Code Changes

```bash
# 1. Verify tests pass
npm test  # or pytest for Python

# 2. Verify build works
npm run build

# 3. Stage changes
git add [files]

# 4. Commit
git commit -m "$(cat <<'EOF'
[type]([scope]): [description]

[body]

EOF
)"

# 5. Push
git push origin [branch]

# 6. Verify CI passes (optional)
gh pr checks [PR-number]
```

## Execution Phases

### Phase 1: Persist Changes (Git)

```markdown
## Git Operations

1. **Stage files**
   - Only stage files mentioned in the plan
   - Verify with `git diff --staged`

2. **Commit**
   - Use conventional commit format (see git-conventions skill)
   - Keep message concise but descriptive

3. **Push**
   - Push to correct branch (main, feature branch)
   - Verify push succeeded
```

### Phase 2: Apply Changes (Cloud/Cluster)

```markdown
## Apply Operations

For Terraform:
- `terragrunt apply -auto-approve`
- Monitor output for errors
- Check exit code ($? == 0)

For Kubernetes:
- Let Flux auto-reconcile (1m interval)
- Or force: `flux reconcile helmrelease [name]`
- Monitor reconciliation status

For Direct kubectl:
- `kubectl apply -f [manifest]`
- Verify resources created/updated
```

### Phase 3: Verify Success

```markdown
## Verification Steps

**Terraform:**
- `terragrunt output` - Check outputs
- `terraform show` - Verify state
- Cloud console - Visual confirmation

**Kubernetes:**
- `kubectl get pods -n [namespace]` - Check pods Running
- `kubectl logs [pod] -n [namespace]` - Check logs for errors
- `kubectl describe pod [pod]` - Check events

**Application:**
- Tests still pass
- Build succeeds
- CI checks pass
```

## Error Handling

### If Execution Fails

**Classify the error:**

| Error Type | Action | AGENT_STATUS |
|------------|--------|--------------|
| **Transient** (timeout, network) | Retry once | APPROVED_EXECUTING |
| **Validation** (syntax, config error) | Fix and retry | BLOCKED → report to user |
| **Permission** (IAM, RBAC denied) | Cannot proceed | BLOCKED → report to user |
| **State conflict** (resource exists) | Manual intervention | BLOCKED → report to user |

**Error reporting format:**

```markdown
## Execution Failed

**Error Type:** [Transient | Validation | Permission | State conflict]

**Error Message:**
```
[exact error output]
```

**Root Cause:**
[Analysis of what went wrong]

**Attempted Actions:**
1. [What was attempted]
2. [Result]

**Recommended Fix:**
[How to resolve]

**Rollback Status:**
[If partial changes applied, what needs rollback]
```

Then emit:
```html
<!-- AGENT_STATUS -->
PLAN_STATUS: BLOCKED
CURRENT_PHASE: Execute
PENDING_STEPS: ["Resolve [error]", "Retry execution"]
NEXT_ACTION: Cannot proceed - [error summary]
AGENT_ID: [agentId]
<!-- /AGENT_STATUS -->
```

### Rollback Procedure

If execution fails mid-operation:

**Terraform:**
```bash
# If apply partially succeeded
terragrunt destroy  # Remove created resources
git revert HEAD     # Undo commit
git push origin main
```

**Kubernetes:**
```bash
# Revert manifest
git revert HEAD
git push origin main
flux reconcile helmrelease [name] -n [namespace]

# Or manual rollback
kubectl rollout undo deployment [name] -n [namespace]
```

## Success Reporting

When execution succeeds:

```markdown
## Execution Complete ✅

### Changes Applied

**Git:**
- Committed: [commit SHA]
- Pushed to: [branch]
- Commit message: [message]

**Infrastructure/Cluster:**
- [Resource 1]: Created ✓
- [Resource 2]: Updated ✓
- [Resource 3]: Verified ✓

### Verification Results

**Status checks:**
- ✅ Resources created successfully
- ✅ Validation passed
- ✅ No errors in logs

**Outputs:**
[relevant terraform outputs, pod status, etc.]

### Next Steps

[Any follow-up actions needed, or "None - deployment complete"]
```

Then emit:
```html
<!-- AGENT_STATUS -->
PLAN_STATUS: COMPLETE
CURRENT_PHASE: Execute
PENDING_STEPS: []
NEXT_ACTION: Task complete - execution successful
AGENT_ID: [agentId]
<!-- /AGENT_STATUS -->
```

## AGENT_STATUS

Emit per agent-protocol skill: `APPROVED_EXECUTING` during execution, `COMPLETE` on success, `BLOCKED` on failure.

## Examples

### Example 1: Successful Terraform Apply

```markdown
## Execution Complete ✅

### Pre-Execution Checks
- ✅ User approved
- ✅ Git status clean
- ✅ Plan still valid
- ✅ AWS credentials available

### Execution Steps

1. **Git operations:**
   ```bash
   git add terraform/vpc/
   git commit -m "feat(infra): add production VPC"
   git push origin main
   ```
   ✅ Committed: abc123f
   ✅ Pushed to: main

2. **Terraform apply:**
   ```bash
   cd terraform/vpc
   terragrunt apply -auto-approve
   ```
   ✅ Apply complete! Resources: 4 added, 0 changed, 0 destroyed.

3. **Verification:**
   ```bash
   aws ec2 describe-vpcs --vpc-ids vpc-abc123
   ```
   ✅ VPC exists with CIDR 10.0.0.0/16

### Changes Applied

**Infrastructure:**
- VPC: vpc-abc123 (10.0.0.0/16) ✓
- Subnet (us-east-1a): subnet-abc111 ✓
- Subnet (us-east-1b): subnet-abc222 ✓
- Subnet (us-east-1c): subnet-abc333 ✓

### Next Steps

None - VPC deployment complete. Ready for EKS cluster creation.

<!-- AGENT_STATUS -->
PLAN_STATUS: COMPLETE
CURRENT_PHASE: Execute
PENDING_STEPS: []
NEXT_ACTION: Task complete - VPC created successfully
AGENT_ID: a12345
<!-- /AGENT_STATUS -->
```

### Example 2: Failed Execution with Rollback

```markdown
## Execution Failed ❌

### Pre-Execution Checks
- ✅ User approved
- ✅ Git status clean
- ✅ Plan still valid

### Execution Attempt

1. **Git operations:**
   ```bash
   git commit -m "feat(infra): add RDS instance"
   git push origin main
   ```
   ✅ Committed: def456g
   ✅ Pushed to: main

2. **Terraform apply:**
   ```bash
   terragrunt apply -auto-approve
   ```
   ❌ Error: DB subnet group "prod-db-subnet" does not exist

### Error Analysis

**Error Type:** Validation

**Root Cause:**
RDS instance depends on DB subnet group that doesn't exist yet.

**Attempted Actions:**
1. Terraform apply - Failed with missing dependency
2. Checked if subnet group exists - Not found

### Rollback Performed

```bash
# No partial resources created, but reverted commit
git revert HEAD
git push origin main
```
✅ Rollback complete - commit reverted

### Recommended Fix

Need to create DB subnet group first:
1. Create `terraform/rds-subnet-group/` module
2. Apply subnet group
3. Then retry RDS instance creation

<!-- AGENT_STATUS -->
PLAN_STATUS: BLOCKED
CURRENT_PHASE: Execute
PENDING_STEPS: ["Create DB subnet group", "Retry RDS creation"]
NEXT_ACTION: Cannot proceed - missing dependency (DB subnet group)
AGENT_ID: a12345
<!-- /AGENT_STATUS -->
```

## Anti-Patterns to Avoid

❌ **Executing without verifying approval**
- Always check prompt contains "approved" or similar

❌ **Skipping git commit/push**
- Infrastructure as Code changes MUST be persisted in Git

❌ **Not verifying success**
- Always check that changes actually applied

❌ **Ignoring errors**
- If terraform/kubectl fails, STOP and report as BLOCKED

## Security Considerations

### Never Execute

- `terraform destroy` without explicit "destroy" in approval
- `kubectl delete` on production resources without explicit approval
- `git push --force` (unless explicitly approved for this specific case)
- Commands with credentials in clear text

### Always Verify

- User approved the EXACT operation being executed
- Working in the correct environment (dev vs prod)
- Changes match the plan that was approved
- No additional side effects

## Success Criteria

Execution is successful when:
1. ✅ Git changes committed and pushed
2. ✅ Cloud/cluster changes applied
3. ✅ Verification checks pass
4. ✅ No errors in logs
5. ✅ AGENT_STATUS: COMPLETE emitted
