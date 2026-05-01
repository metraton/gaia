---
name: orchestrator-approval
description: Use when processing APPROVAL_REQUEST with approval_id from a subagent -- enforces showing values before asking for user consent
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

**Scope:** This skill applies when a subagent emits `APPROVAL_REQUEST` with an `approval_id` in its `approval_request`.

## Pre-Flight Checklist

Before calling AskUserQuestion, verify ALL of the following. If any check fails, go back to the agent's `approval_request` and extract the missing field.

1. Does the question text contain the VERBATIM command or file content from `exact_content`? Not summarized, not paraphrased -- the literal string.
2. Does the question text contain all 5 labeled fields (OPERATION, COMMAND, SCOPE, RISK, ROLLBACK)?
3. Does the "Approve" option label name the SPECIFIC action (e.g., "Approve -- push 2 commits to origin/main"), not a generic phrase?
4. Is the command/content complete? No "..." truncation, no "the above changes".
5. Does the "Approve" option label end with `[P-{nonce_prefix8}]`? The nonce comes from `approval_request.approval_id` (first 8 chars).

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

- Format: `"Approve -- {specific_action_description} [P-{nonce_prefix8}]"`
- The action description comes from `approval_request.operation`
- The nonce comes from `approval_request.approval_id` (first 8 chars)

## Rules

1. **Grant activates through the PostToolUse hook for AskUserQuestion -- not SendMessage.** Resume the subagent via SendMessage with natural language only. The grant is active before SendMessage is sent -- no delay or verification step is needed.

2. **Scope guard -- resume only with the approved command.** The grant is scoped to the exact command that was blocked. When the agent's `approval_request.exact_content` differs in ANY argument from what the orchestrator put in `COMANDO:` -- even one path segment, one flag, one filename -- the grant will miss and the agent will be blocked again. Do NOT send the agent a resume message that instructs it to run a different command. If the operation has genuinely changed, present a new approval.

3. **Fresh presentation every time.** Each hook-blocked APPROVAL_REQUEST requires its own presentation with all mandatory fields. Prior approvals do not carry forward.

4. **`mode` does NOT survive a SendMessage resume.** See `security-tiers/SKILL.md` -> "Mode runtime rules" R3. Operational consequence below: "Re-dispatch instead of resume".

### Re-dispatch instead of resume (when mode was load-bearing)

The dispatch-vs-resume choice is the decision that actually shapes runtime behavior on protected-path bundles. SendMessage resumes always run in the background literal: AskUserQuestion auto-denies, and the original `mode` does not survive. If the agent's continuation could need approval mid-task, or if the original dispatch relied on `mode: bypassPermissions` or `mode: acceptEdits` to satisfy CC native on `.claude/` writes, **do not resume with SendMessage**. Instead:

1. Kill the blocked subagent (it already reported APPROVAL_REQUEST or BLOCKED).
2. Present the approval via AskUserQuestion (same mandatory format) so the Gaia grant activates for the exact command signature.
3. Dispatch a **fresh** subagent with the same `mode` the original needed.
4. The fresh prompt enumerates ALL remaining steps and instructs the subagent to execute them in a single turn. Tell it explicitly: "If a hook blocks any step, emit BLOCKED and stop -- do NOT emit APPROVAL_REQUEST mid-task, do NOT split across turns."
5. The Gaia grant (scoped to the specific blocked command) activates on the approved step; the new dispatch's `mode` satisfies CC native for every other step.

This applies specifically to multi-step bundles on protected paths (mv/rm/mkdir on `.claude/` + Edit/Write on `.claude/project-context/**`). Splitting such a bundle across dispatch + SendMessage resume is the failure mode -- the symptom is CC native blocking what used to pass under the original dispatch.

Resume via SendMessage is correct when the agent's next move is bounded (act on a clarification, retry the exact approved command) and no new approval is expected during the resume.

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
| "Same operation, slightly different path" | Grants match by command signature -- different path = grant miss = immediate re-block |
| "I'll tell the agent to run a similar rm" | The agent must run the exact command that was approved, or it gets blocked again |
| "I'll skip the [P-...] suffix, it's cosmetic" | "The hook extracts the nonce from the label — without it, targeted activation fails" |
| "Original dispatch had bypassPermissions, resume will too" | `mode` is per-dispatch; resume via SendMessage runs in `default` -- CC native re-blocks. Re-dispatch fresh. |
| "Subagent blocked mid-task, I'll approve then SendMessage" | If the blocker is CC native on `.claude/` writes, approval alone won't help -- resume loses the mode. Re-dispatch fresh with the needed mode. |
| "Multi-step mv + Edit can be split: dispatch, approve, resume" | Each turn boundary drops the mode. Pack ALL steps in one fresh dispatch after approval. |

For GOOD vs BAD examples, batch flow, grant mechanics, and the dispatch mode checklist, see `reference.md`.
