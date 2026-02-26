---
name: approval
description: Use when a T3 operation is ready and needs to be presented to the user for approval before execution
user-invocable: false
---

# Approval

## Mental Model

The plan is a contract. The user approves the exact contract — not a vague intent, not a summary, not "the general idea". Without a complete plan, the user cannot meaningfully consent: they would be approving blindly.

The approval token is the machine-readable proof that the contract was accepted. The hook validates it. A general "yes" without the canonical token does not pass.

The template is not bureaucracy. It is the minimum information needed for informed consent.

## Plan Presentation Format (MANDATORY)

Every T3 plan MUST follow the full template. Read `reference.md` in this directory for the complete structure.

The template requires: Summary · Changes Proposed (CREATE/MODIFY/DELETE) · Validation Results · Risk Assessment + Rollback Plan · Execution Steps · Verification Criteria · Files Affected.

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

**Note:** Partial approval is not supported. If user wants scope reduction, treat as **Requests modifications** and present a new plan.

## Resume Token

When the user approves, the orchestrator resumes the agent with:

```
User approved: <operation description>
```

The scope must describe the specific operation approved:
- `User approved: terraform apply prod/vpc`
- `User approved: git push origin feature/my-branch`
- `User approved: kubectl apply namespace payment-service`

**Destructive operations (no rollback path) require the destructive word explicitly in the scope:**
- `User approved: terraform destroy prod/vpc` — not "terraform apply"
- `User approved: kubectl delete namespace payment-service` — not "kubectl apply"
- `User approved: git push --force origin main` — not "git push"

If the user approves without naming the destructive action explicitly, request clarification. A general "yes" is not sufficient for irreversible operations.

The hook validates this token before allowing the Task tool to proceed.

---

## Red Flags — Stop Before Presenting

If you're forming any of these thoughts, stop:

- *"It's a small change, a quick summary is enough"* — the template is MANDATORY regardless of size
- *"We've done this before, they know what to expect"* — every operation is a new contract
- *"I'll ask first and show details if they want"* — the full plan goes BEFORE the approval request
- *"They responded quickly, that counts as approval"* — silence and brevity are not consent
- *"Risk is LOW so I can skip parts of the format"* — risk level does not change the process

## Anti-Patterns

Ask without plan · Vague descriptions · Missing rollback · Wrong risk level · Silence as consent · Vague scope for destructive ops

All covered in Red Flags and the template requirements above.
