---
name: orchestrator-approval
description: Use when processing AWAITING_APPROVAL from a subagent -- enforces showing values before asking for user consent (not for REVIEW)
metadata:
  user-invocable: false
  type: discipline
---

# Orchestrator Approval

```
THIS SKILL HANDLES ONLY AWAITING_APPROVAL (hook-blocked T3 with nonce).
REVIEW states are handled directly by the orchestrator -- this skill does not apply.
NEVER RELAY APPROVE:<nonce> WITHOUT SHOWING THE USER
(1) WHAT WILL HAPPEN, (2) EXACT CONTENT/COMMAND, (3) WHAT IT MODIFIES.
```

## Mental Model

The orchestrator sits between the subagent and the user. The subagent presents a plan; the user decides. But the user cannot decide on information they have not seen. Every approval prompt must contain enough detail for informed consent -- not a summary, not a reference to "the plan above", not an offer to show details on request. The values go in the prompt, every time, before the question is asked.

The agent-facing approval skill (`skills/approval/SKILL.md`) ensures the subagent builds a complete plan. This skill ensures the orchestrator presents that plan faithfully before asking for consent.

**Scope:** This skill applies ONLY when a subagent emits `AWAITING_APPROVAL` (a hook blocked a T3 command and a nonce is present). When a subagent emits `REVIEW` (plan-first, no nonce), the orchestrator handles it directly by summarizing and asking the user -- no nonce relay is involved.

## Mandatory Presentation Block

Every `AWAITING_APPROVAL` presented to the user MUST include these 5 fields.
Read them from the `approval_request` object in the agent's `json:contract` block:

| Field | Source in `approval_request` | Content |
|-------|------------------------------|---------|
| **OPERATION** | `approval_request.operation` | What will happen (verb + target) |
| **EXACT_CONTENT** | `approval_request.exact_content` | The literal command, file content, or config values |
| **SCOPE** | `approval_request.scope` | What gets modified (files, resources, environments) |
| **RISK_LEVEL** | `approval_request.risk_level` | LOW / MEDIUM / HIGH / CRITICAL |
| **ROLLBACK** | `approval_request.rollback` | How to undo if wrong |

The nonce comes from `approval_request.nonce` (present only for `AWAITING_APPROVAL`).

Present these fields, then use AskUserQuestion with labeled options: **Approve / Modify / Reject**.

## Rules

**1. Human approval is not a hook nonce.**
Human approval = semantic consent for an operation. Hook nonce = machine token for one blocked T3 command. They are different things with different lifecycles.

**2. Never synthesize a nonce.**
Only use `APPROVE:<nonce>` with a real hex nonce from the subagent's latest blocked command output. Never construct tokens like `APPROVE:commit`, `APPROVE:git push`, or `APPROVE:terraform apply prod`.

**3. Approval intent vs nonce relay.**
If the user approves but no nonce exists yet, store that as intent only. Resume the subagent with natural language and let it continue until the hook generates a real nonce.

**4. Scope guard.**
When a nonce arrives, compare the blocked command's scope to what the user originally approved. If the command expands scope, changes operation, or targets something materially different -- present the new scope and ask again.

**5. Fresh presentation every time.**
Each `AWAITING_APPROVAL` requires its own presentation with all mandatory fields. Prior approvals do not carry forward.

## Nonce Relay Procedure

1. Extract the 5 mandatory fields from `approval_request` in the subagent's `json:contract` block. Extract the nonce from `approval_request.nonce` (present only for `AWAITING_APPROVAL`).
2. Present to the user via AskUserQuestion with all mandatory fields populated. Options: **Approve / Modify / Reject**. Never include the nonce in user-facing text.
3. On user approval:
   - If nonce exists: silently resume the subagent with `APPROVE:<nonce>`.
   - If no nonce yet: store approval intent. Resume subagent with natural language describing the approved direction.
4. On scope change: if the eventual blocked command differs materially from what the user approved, present the new scope with all mandatory fields and ask again.

**Note:** After relaying the nonce, Claude Code will show a native confirmation
dialog to the user for the first execution. This is the double-barrier security
gate -- the user approved the plan through the orchestrator, and now confirms the
actual command execution through the native dialog. Subsequent commands within the
approval TTL window proceed without the native dialog.

## Red Flags -- Stop Before Relaying

If you are forming any of these thoughts, stop. You are about to violate the presentation contract:

- *"The change is obvious from the operation name"* -- Show exact content anyway.
- *"The subagent already showed the user the plan"* -- Show it again in the approval prompt.
- *"It's just a git commit / small edit"* -- Size does not change the contract.
- *"I'll show details if they ask"* -- Show BEFORE asking, not after.
- *"The user already approved this type of operation"* -- Each AWAITING_APPROVAL requires fresh presentation.
- *"I can construct the nonce from the operation description"* -- Nonces are hex tokens from the hook, never synthesized.

## Anti-Patterns

- **Summary-only approval** -- presenting "Deploy to dev?" without the exact command, files, or rollback.
- **Stale nonce** -- relaying a nonce from a previous blocked command instead of the latest one.
- **Nonce in user text** -- showing the hex token to the user. The nonce is a machine handshake, never user-facing.
- **Implicit carry-forward** -- treating a prior approval as valid for a new AWAITING_APPROVAL.
- **Details on demand** -- offering to show the plan instead of showing it upfront.
