---
name: approval
description: How to present plans for T3 approval - structured plan presentation and risk communication
user-invocable: false
---

# Approval Skill

## When to Use This Skill

Use when the `security-tiers` skill classifies an operation as T3 and the plan is ready for user approval.

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
- [Resource]: [Description]

**Resources to MODIFY:**
- [Resource]: [What changes] (before → after)

**Resources to DELETE:**
- [Resource]: [Why deletion]

### Validation Results

**Dry-run status:**
- ✅ `[simulation command]` - [result summary]

**Dependencies verified:**
- [Dependency]: Available ✓

### Risk Assessment

**Risk Level:** [LOW | MEDIUM | HIGH | CRITICAL]

**Potential Risks:**
1. [Risk]: [Impact]
   - Mitigation: [How we handle it]

**Rollback Plan:**
- If operation fails: [Rollback steps]
- Recovery time estimate: [time]

### Execution Steps

When approved, will execute:
1. [Step 1]
2. [Step 2]
3. [Step 3]

### Verification Criteria

After execution, these checks MUST pass before emitting COMPLETE:
- `[read-only command] [args]` → [expected output or state]

### Files Affected

**Git changes:**
- Modified: [files]
- Added: [files]
- Deleted: [files]
```

## Risk Level Classification

| Level | Criteria | Examples |
|-------|----------|----------|
| **LOW** | Single resource, non-prod, no dependencies | Create dev namespace |
| **MEDIUM** | Multiple resources, non-prod, some dependencies | Deploy app to dev cluster |
| **HIGH** | Production changes, dependencies, potential downtime | Deploy app to prod cluster |
| **CRITICAL** | Irreversible operations, data loss possible | Delete production database |

## Approval Request

After presenting the plan, request approval:

```markdown
## Approval Required

**Operation:** [what will be executed]
**Environment:** [dev / staging / prod]
**Risk Level:** [LOW / MEDIUM / HIGH / CRITICAL]

**Ready to proceed?**
- [ ] Yes, execute the plan
- [ ] No, I need to review further
- [ ] Modify the plan (specify changes)
```

Then emit AGENT_STATUS with `PLAN_STATUS: PENDING_APPROVAL`.

## After User Responds

| User Response | Agent Action | AGENT_STATUS |
|---------------|-------------|--------------|
| **Approves** | Read `.claude/skills/execution/SKILL.md` and execute | `APPROVED_EXECUTING` |
| **Rejects** | Acknowledge. Ask if they want to abandon or re-investigate | `NEEDS_INPUT` |
| **Requests modifications** | Revise plan per feedback, re-run simulation, present updated plan | `PLANNING` |

**Note:** Partial approval is not supported by the current approval gate implementation.
If user wants scope reduction, treat it as **Requests modifications** and present a new plan.

## Resume Token (Orchestrator Responsibility)

When the user approves, the orchestrator resumes the agent with:

```
User approved: <operation description>
```

The scope must describe the specific operation approved. Examples:
- `User approved: terraform apply prod/vpc`
- `User approved: git push origin feature/my-branch`
- `User approved: kubectl apply namespace payment-service`

The hook validates this token before allowing the Task tool to proceed.

## Anti-Patterns

❌ **Asking for approval without showing the plan** — always present full plan first
❌ **Vague change descriptions** — be specific about what changes and why
❌ **Missing rollback plan** — always document how to undo
❌ **Wrong risk level** — be honest, don't minimize production changes
❌ **Proceeding without explicit approval** — silence is not consent
