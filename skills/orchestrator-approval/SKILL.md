---
name: orchestrator-approval
description: Use when processing REVIEW with approval_id from a subagent -- enforces showing values before asking for user consent
metadata:
  user-invocable: false
  type: discipline
---

# Orchestrator Approval

```
NEVER PRESENT AN APPROVAL WITHOUT SHOWING THE USER
(1) WHAT WILL HAPPEN, (2) EXACT CONTENT/COMMAND, (3) WHAT IT MODIFIES.
```

## Mental Model

The orchestrator sits between the subagent and the user. The subagent presents a plan; the user decides. But the user cannot decide on information they have not seen. Every approval prompt must contain enough detail for informed consent -- not a summary, not a reference to "the plan above", not an offer to show details on request. The values go in the prompt, every time, before the question is asked.

When a hook blocks a T3 command, it writes a pending approval and returns an `approval_id` in the deny response. The subagent includes this `approval_id` in its `approval_request`. The orchestrator presents the plan via AskUserQuestion with structured options (Approve / Modify / Reject). When the user selects "Approve", the PostToolUse hook for AskUserQuestion fires and activates the pending grant. No nonce or approval_id is relayed through SendMessage -- grant activation is handled entirely by the hook.

**Scope:** This skill applies ONLY when a subagent emits `REVIEW` with an `approval_id` in its `approval_request`. Without `approval_id`, the orchestrator handles REVIEW directly.

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

## Rules

**1. Grant activates through the PostToolUse hook for AskUserQuestion -- not SendMessage.**
Resume the subagent via SendMessage with natural language only (e.g., "Proceed with the approved operation"). Never include any nonce, approval_id, or APPROVE: token.

**2. Scope guard.**
Compare the blocked command's scope to what the user originally approved. If the command expands scope, changes operation, or targets something materially different -- present the new scope and ask again.

**3. Fresh presentation every time.**
Each hook-blocked REVIEW requires its own presentation with all mandatory fields. Prior approvals do not carry forward.

## Approval Procedure

1. Extract the 5 mandatory fields from `approval_request` in the subagent's `json:contract` block.
2. Present to the user via AskUserQuestion with all mandatory fields populated. Use exactly these options: **Approve / Modify / Reject**. Never include the approval_id in user-facing text.
3. On "Approve": resume the subagent via SendMessage with natural language describing the approved direction.
4. On scope change: present the new scope with all mandatory fields and ask again.

## Batch Approval

When a subagent's `approval_request` contains `batch_scope: "verb_family"`, the agent is requesting a multi-use grant that covers many commands with the same base CLI and verb but different arguments. This is typical for bulk operations like email triage (modifying hundreds of emails) or batch label creation.

**Detection:** Check for `batch_scope` in the `approval_request` object.

**Presentation:** Include the same 5 mandatory fields, but frame the scope as a batch:
- OPERATION should describe the batch (e.g., "Modify 500 Gmail messages")
- EXACT_CONTENT should show the command pattern (e.g., "`gws gmail users messages modify`")
- SCOPE should state the TTL (e.g., "All modify operations for the next 10 minutes")

**Options:** Use **Approve batch / Approve single / Reject** (three options).
- "Approve batch" creates a verb-family grant (multi-use, 10-minute TTL) that covers all future commands matching the same base_cmd + verb. The PostToolUse hook detects "batch" in the answer.
- "Approve single" creates a normal single-use grant for only the first blocked command.

**Resume:** After batch approval, resume the subagent via SendMessage with: "Batch approved. Proceed with all [verb] operations."

## Anti-Patterns

- **Summary-only approval** -- presenting "Deploy to dev?" without the exact command, files, or rollback.
- **Token relay in SendMessage** -- including approval_id or nonce in the resume message.
- **Implicit carry-forward** -- treating a prior approval as valid for a new hook-blocked REVIEW.
- **Details on demand** -- offering to show the plan instead of showing it upfront.
- **"It's just a small change"** -- size does not change the contract. Show exact content regardless.
- **"The subagent already showed it"** -- show it again in the approval prompt.
- **Batch without showing TTL** -- batch grants expire. Always state the time window.
