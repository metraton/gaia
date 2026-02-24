---
name: execution
description: How to execute after approval - post-approval execution with verification
user-invocable: false
---

## When to Use This Skill

Use when user has approved a T3 operation and you need to execute the plan.

**Trigger:** orchestrator resumes with confirmation of user approval.

**Iron Law: No completion claims without fresh verification evidence.**
Emit `COMPLETE` only after Verification Criteria pass — not when commands finish.

## Pre-Execution Checklist (MANDATORY)

Before executing ANY approved operation:

- [ ] User explicitly approved (check prompt contains "approved")
- [ ] Git status clean (no uncommitted changes in the way)
- [ ] Plan still valid (no drift since plan was created)
- [ ] Credentials available (can access cloud/cluster)
- [ ] Dry-run still passes (validation hasn't broken)
- [ ] Commands configured for non-interactive mode (no prompts mid-execution)

If ANY check fails → STOP and report with `PLAN_STATUS: BLOCKED`

## Non-Interactive Flags

Tools that prompt for confirmation MUST use their non-interactive flag:

| Tool | Flag |
|------|------|
| `terragrunt apply` | `-auto-approve` |
| `terraform apply` | `-auto-approve` |
| `kubectl apply` | (no prompt by default) |
| `git push` | (no prompt by default) |
| `gcloud` commands | `--quiet` |

## Execution Protocol

Follow `command-execution` skill rules for all commands. Additionally:

1. Run each plan step separately — verify exit code before the next
2. On failure — classify as recoverable or not (see `agent-protocol` error handling)
3. After all steps — run the Verification Criteria defined in the plan

## Error Reporting

When a step fails, report:

```markdown
**Error Type:** [Transient | Validation | Permission | State conflict]
**Error Message:** [exact error output]
**Root Cause:** [Analysis]
**Rollback Status:** [What needs rollback if partial changes applied]
```

Recoverable → `FIXING`. Non-recoverable → `BLOCKED`. See `agent-protocol` state flow.

## Rollback

**Standard (any domain):**
```bash
git revert HEAD
git push origin [branch]
# System reconciles automatically (Flux, CI/CD, etc.)
```

**Emergency only** — if partial resources remain orphaned after git revert:
Run the domain-specific undo from your domain skill (`terraform-patterns`, `gitops-patterns`).

## Success Reporting

After all steps complete and Verification Criteria pass:

```markdown
## Execution Complete ✅

**Git:** Committed [SHA], pushed to [branch]
**Changes:** [Resource 1] Created ✓, [Resource 2] Updated ✓
**Verification:** All criteria passed ✅
**Next Steps:** [Follow-up or "None - deployment complete"]
```

Then emit `PLAN_STATUS: COMPLETE`.

## Anti-Patterns

❌ Executing without verifying approval
❌ Skipping git commit/push — IaC changes MUST be persisted in Git
❌ Running apply without `-auto-approve` — tool will hang waiting for y/n
❌ Ignoring errors — if any step fails, STOP and report

## Security Considerations

Never execute:
- `terraform destroy` without explicit "destroy" in approval
- `kubectl delete` on production resources without explicit approval
- `git push --force` unless explicitly approved

Always verify:
- User approved the EXACT operation being executed
- Correct environment (dev vs prod)
- Changes match the approved plan — no side effects
