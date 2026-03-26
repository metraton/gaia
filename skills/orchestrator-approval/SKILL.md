---
name: orchestrator-approval
description: Use when processing REVIEW with approval_id from a subagent -- enforces showing values before asking for user consent
metadata:
  user-invocable: false
  type: discipline
---

# Orchestrator Approval

```
THIS SKILL HANDLES REVIEW WITH approval_id (hook-blocked T3).
Plain REVIEW (plan-first, no approval_id) is handled directly by the orchestrator.
NEVER PRESENT AN APPROVAL WITHOUT SHOWING THE USER
(1) WHAT WILL HAPPEN, (2) EXACT CONTENT/COMMAND, (3) WHAT IT MODIFIES.
```

## Mental Model

The orchestrator sits between the subagent and the user. The subagent presents a plan; the user decides. But the user cannot decide on information they have not seen. Every approval prompt must contain enough detail for informed consent -- not a summary, not a reference to "the plan above", not an offer to show details on request. The values go in the prompt, every time, before the question is asked.

When a hook blocks a T3 command in a subagent, the hook writes a pending approval and returns an `approval_id` in the deny response. The subagent includes this `approval_id` in its `approval_request`. The orchestrator presents the plan to the user via AskUserQuestion. When the user approves, the UserPromptSubmit hook detects the affirmative response and activates the pending grant. No nonce relay through SendMessage is needed.

**Scope:** This skill applies ONLY when a subagent emits `REVIEW` with an `approval_id` in its `approval_request` (a hook blocked a T3 command). When a subagent emits `REVIEW` without `approval_id` (plan-first, no hook block), the orchestrator handles it directly by summarizing and asking the user.

## Mandatory Presentation Block

Every hook-blocked `REVIEW` presented to the user MUST include these 5 fields.
Read them from the `approval_request` object in the agent's `json:contract` block:

| Field | Source in `approval_request` | Content |
|-------|------------------------------|---------|
| **OPERATION** | `approval_request.operation` | What will happen (verb + target) |
| **EXACT_CONTENT** | `approval_request.exact_content` | The literal command, file content, or config values |
| **SCOPE** | `approval_request.scope` | What gets modified (files, resources, environments) |
| **RISK_LEVEL** | `approval_request.risk_level` | LOW / MEDIUM / HIGH / CRITICAL |
| **ROLLBACK** | `approval_request.rollback` | How to undo if wrong |

Present these fields, then use AskUserQuestion with labeled options: **Approve / Modify / Reject**.

## Rules

**1. Approval activates through the user's next prompt.**
When the user approves, their affirmative response triggers the UserPromptSubmit hook which activates the pending grant. The orchestrator simply resumes the subagent with natural language ("Proceed with the approved operation") via SendMessage.

**2. No nonce relay.**
Do not include any nonce, approval_id, or APPROVE: token in the SendMessage. The grant activation happens in the UserPromptSubmit hook, not in the SendMessage path.

**3. Scope guard.**
Compare the blocked command's scope to what the user originally approved. If the command expands scope, changes operation, or targets something materially different -- present the new scope and ask again.

**4. Fresh presentation every time.**
Each hook-blocked REVIEW requires its own presentation with all mandatory fields. Prior approvals do not carry forward.

## Approval Procedure

1. Extract the 5 mandatory fields from `approval_request` in the subagent's `json:contract` block.
2. Present to the user via AskUserQuestion with all mandatory fields populated. Options: **Approve / Modify / Reject**. Never include the approval_id in user-facing text.
3. On user approval: resume the subagent via SendMessage(to: agentId) with natural language describing the approved direction (e.g., "Proceed with the git push as planned."). The UserPromptSubmit hook has already activated the grant.
4. On scope change: if the eventual blocked command differs materially from what the user approved, present the new scope with all mandatory fields and ask again.

## Red Flags -- Stop Before Presenting

If you are forming any of these thoughts, stop. You are about to violate the presentation contract:

- *"The change is obvious from the operation name"* -- Show exact content anyway.
- *"The subagent already showed the user the plan"* -- Show it again in the approval prompt.
- *"It's just a git commit / small edit"* -- Size does not change the contract.
- *"I'll show details if they ask"* -- Show BEFORE asking, not after.
- *"The user already approved this type of operation"* -- Each REVIEW with approval_id requires fresh presentation.

## Anti-Patterns

- **Summary-only approval** -- presenting "Deploy to dev?" without the exact command, files, or rollback.
- **Nonce relay in SendMessage** -- including APPROVE:<hex> or approval_id in the resume message. Grant activation is handled by UserPromptSubmit.
- **Implicit carry-forward** -- treating a prior approval as valid for a new hook-blocked REVIEW.
- **Details on demand** -- offering to show the plan instead of showing it upfront.
