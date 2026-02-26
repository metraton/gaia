---
name: execution
description: Use when the user has approved a T3 operation and execution is about to begin
user-invocable: false
---

# Execution

```
NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE.
Commands finishing is not success. Verification criteria passing is success.
```

## Mental Model

T3 operations modify live state. Live state is irreversible. That is why approval exists — and why verification must happen *after* execution, not during planning.

The approval token is not a formality. It is a machine-checked contract: the orchestrator validates the exact token before the hook allows execution to proceed. "The user said yes" is not the same as the canonical token.

Verification criteria exist because commands completing ≠ system in the desired state. A `terraform apply` can exit 0 and leave resources in a broken configuration. A `git push` can succeed and still not trigger Flux reconciliation. You cannot claim COMPLETE until you have read evidence — not assumed it.

## Pre-Execution Checklist (MANDATORY)

Before executing ANY approved operation:

- [ ] Prompt contains canonical token: `User approved: <operation>`
- [ ] Git status clean — no uncommitted changes in the way
- [ ] Plan still valid — no drift since plan was created (re-run dry-run if in doubt)
- [ ] Credentials available — can access cloud/cluster
- [ ] Commands configured for non-interactive mode (no prompts mid-execution)

If ANY check fails → STOP and report with `PLAN_STATUS: BLOCKED`

## Approval Token (Canonical)

`User approved: <operation description>`

The scope must describe the specific operation approved:
- `User approved: terraform apply prod/vpc`
- `User approved: git push origin feature/my-branch`
- `User approved: kubectl apply namespace payment-service`

If the resume prompt does not include this token → do not execute. Generic scopes ("the changes", "everything") are accepted but flag them in your response.

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
2. Git commit and push BEFORE applying — if apply fails, intended state is recorded
3. On failure — classify as recoverable or not (see `agent-protocol` error handling)
4. After all steps — run the Verification Criteria defined in the plan

## Error Reporting

When a step fails, report:

```
Error Type: [Transient | Validation | Permission | State conflict]
Error Message: [exact error output]
Root Cause: [Analysis]
Rollback Status: [What needs rollback if partial changes applied]
```

Recoverable → `FIXING`. Non-recoverable → `BLOCKED`.

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

```
## Execution Complete ✅
Git: Committed [SHA], pushed to [branch]
Changes: [Resource 1] Created ✓, [Resource 2] Updated ✓
Verification: All criteria passed ✅
Next Steps: [Follow-up or "None - deployment complete"]
```

Then emit `PLAN_STATUS: COMPLETE`.

---

## Red Flags — Stop Before Proceeding

If you're forming any of these thoughts, stop:

- *"The plan just ran, there can't be drift"* → Re-run dry-run anyway — the checklist is not optional
- *"The dry-run passed earlier, that counts"* → Dry-run from planning is stale — re-run before executing
- *"I'll commit to git after apply succeeds"* → Commit before apply — if apply fails, intended state must be recorded
- *"The user said yes, the token format doesn't matter"* → The hook validates the canonical token — format matters
- *"All commands ran without error, I can claim COMPLETE"* → Commands finishing ≠ verification passing — run the criteria
- *"It's only dev, I can skip some checks"* → No environment exceptions — irreversibility is irreversibility
- *"A partial verification is enough"* → Partial proves nothing — run all criteria

## Rationalization Table

| Rationalization | Reality | Checklist Item |
|----------------|---------|----------------|
| "Plan was just created, no drift possible" | Drift can occur between planning and execution at any time | Plan still valid |
| "Dry-run passed during planning, skipping now" | Stale dry-run from planning ≠ current state validation | Dry-run still passes |
| "Git commit can happen after apply" | If apply fails partially, intended state is unrecorded in Git | Git commit before apply |
| "User said yes, token is just a formality" | The hook validates the exact token format — it's a machine check, not a courtesy | Approval token |
| "All steps completed successfully" | Commands exiting 0 ≠ system in desired state — only criteria confirm this | Verification criteria |
| "Dev environment doesn't need strict checks" | Irreversible operations are irreversible regardless of environment | All checks |

## Anti-Patterns

- **Skip pre-execution checklist** — "it's obvious everything is ready". It isn't always. Run it.
- **Apply before git commit** — if apply fails mid-way, you have no record of what you intended.
- **COMPLETE without running verification criteria** — the Iron Law exists because this is the most common failure mode.
- **Execute on approximate approval** — "user approved something like this" is not the canonical token. The `approval` skill defines what an explicit scope looks like for destructive operations.
