---
name: orchestrator-approval
description: Use when processing REVIEW with approval_id from a subagent -- enforces showing values before asking for user consent
metadata:
  user-invocable: false
  type: discipline
---

# Orchestrator Approval

```
The user approves EXACT VALUES, not summaries.
Every AskUserQuestion shows the literal command, every option label
names the specific action. No exceptions. No brevity shortcuts.
```

## Mental Model

The orchestrator sits between the subagent and the user. The user cannot make an informed decision on information they have not seen. A summary, a reference to "the plan above", or an offer to show details on request -- all push the decision without the data needed to decide. When the orchestrator shortens "git push origin main" to "aplicar cambios", the user is approving blind.

**Scope:** This skill applies when a subagent emits `REVIEW` with an `approval_id` in its `approval_request`.

## Pre-Flight Checklist

Before calling AskUserQuestion, verify ALL of the following. If any check fails, go back to the agent's `approval_request` and extract the missing field.

1. Does the question text contain the VERBATIM command or file content from `exact_content`? Not summarized, not paraphrased -- the literal string.
2. Does the question text contain all 5 labeled fields (OPERATION, COMMAND, SCOPE, RISK, ROLLBACK)?
3. Does the "Approve" option label name the SPECIFIC action (e.g., "Approve -- push 2 commits to origin/main"), not a generic phrase?
4. Is the command/content complete? No "..." truncation, no "the above changes".

## Mandatory Presentation Format

Every AskUserQuestion `question` parameter must contain these 5 labeled fields, extracted from the agent's `approval_request`:

```
APPROVAL REQUIRED

OPERACION:  {approval_request.operation}
COMANDO:    {approval_request.exact_content}  <-- verbatim, never paraphrased
SCOPE:      {approval_request.scope}
RIESGO:     {approval_request.risk_level} + why
ROLLBACK:   {approval_request.rollback}
```

## Option Label Rules

The "Approve" option MUST name the specific action. The PostToolUse hook activates grants by checking for "approve" in the answer value.

- Format: `"Approve -- {specific_action_description}"`
- The action description comes from `approval_request.operation`

## Rules

1. **Grant activates through the PostToolUse hook for AskUserQuestion -- not SendMessage.** Resume the subagent via SendMessage with natural language only.

2. **Scope guard.** Compare the blocked command's scope to what the user originally approved. If the command expands scope, changes operation, or targets something materially different -- present the new scope and ask again.

3. **Fresh presentation every time.** Each hook-blocked REVIEW requires its own presentation with all mandatory fields. Prior approvals do not carry forward.

## Traps

| If you're thinking... | The reality is... |
|---|---|
| "The subagent already showed the details" | Show them again -- the user needs them at the decision point |
| "It's a small change, I can summarize" | Size does not change the contract -- show the exact command |
| "I'll offer to show details if they want" | The user needs the data BEFORE the question, not after |
| "The option label 'Approve' is enough" | Without the action, the user clicks blind -- label must say WHAT is approved |
| "'Approve -- aplicar cambios' describes it" | That is a paraphrase in another language -- name the actual operation |
| "'Approve -- los 3' is clear from context" | Context is not the label -- spell out what "the 3" are |
| "The command is long, I'll shorten it" | Show it complete -- truncation hides what the user is approving |

For GOOD vs BAD examples, batch flow, and grant mechanics, see `reference.md`.
