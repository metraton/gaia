---
name: orchestrator-approval
description: Use when processing REVIEW with approval_id from a subagent -- enforces showing values before asking for user consent
metadata:
  user-invocable: false
  type: discipline
---

# Orchestrator Approval

```
Every approval prompt shows the user:
(1) WHAT WILL HAPPEN, (2) EXACT CONTENT/COMMAND, (3) WHAT IT MODIFIES.
Values go in the prompt -- before the question is asked.
```

## Mental Model

The orchestrator sits between the subagent and the user. The user cannot make an informed decision on information they have not seen. A summary, a reference to "the plan above", or an offer to show details on request -- all of these push the decision to the user without the data needed to decide well. The values go in the prompt, every time.

**Scope:** This skill applies when a subagent emits `REVIEW` with an `approval_id` in its `approval_request`. Without `approval_id`, the orchestrator handles REVIEW directly.

## Mandatory Presentation Fields

Every hook-blocked `REVIEW` presented to the user includes these 5 fields, read from `approval_request` in the agent's `json:contract`:

| Field | Source | Content |
|-------|--------|---------|
| **OPERATION** | `approval_request.operation` | What will happen (verb + target) |
| **EXACT_CONTENT** | `approval_request.exact_content` | Literal command, file content, or config values |
| **SCOPE** | `approval_request.scope` | What gets modified (files, resources, environments) |
| **RISK_LEVEL** | `approval_request.risk_level` | LOW / MEDIUM / HIGH / CRITICAL |
| **ROLLBACK** | `approval_request.rollback` | How to undo if wrong |

## Rules

**1. Grant activates through the PostToolUse hook for AskUserQuestion -- not SendMessage.**
Resume the subagent via SendMessage with natural language only (e.g., "Proceed with the approved operation").

**2. Scope guard.**
Compare the blocked command's scope to what the user originally approved. If the command expands scope, changes operation, or targets something materially different -- present the new scope and ask again.

**3. Fresh presentation every time.**
Each hook-blocked REVIEW requires its own presentation with all mandatory fields. Prior approvals do not carry forward.

**4. Approval option labels start with "Approve".**
The PostToolUse hook activates grants by checking for "approve" in the answer value. Labels without "approve" will not activate the grant, regardless of user intent.

## Flow

1. Extract the 5 fields from `approval_request` in the agent's `json:contract`.
2. Call AskUserQuestion with the mandatory fields visible in the question text. See `reference.md` for the template and option conventions.
3. On "Approve": resume via SendMessage with natural language describing the approved direction.
4. On "Modify": ask what to change, relay to agent via SendMessage.
5. On scope change: present the new scope with all fields and ask again.

For AskUserQuestion template, batch approval flow, and option label examples, see `reference.md`.

## Traps

| If you're thinking... | The reality is... |
|---|---|
| "The subagent already showed the details" | Show them again in the approval prompt -- the user needs them at the decision point |
| "It's a small change, I can summarize" | Size does not change the contract -- show exact content |
| "I'll offer to show details if they want" | The user needs the data before the question, not after |
| "I'll include the approval_id in SendMessage" | Grant activation happens in the hook, not through token relay |

## Anti-Patterns

- **Summary-only approval** -- presenting "Deploy to dev?" without the exact command, files, or rollback.
- **Token relay** -- including approval_id or nonce in the SendMessage resume.
- **Implicit carry-forward** -- treating a prior approval as valid for a new hook-blocked REVIEW.
- **Batch without TTL** -- batch grants expire; state the time window.
