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

1. **Every APPROVAL_REQUEST is single-use per sub-agent invocation.** A grant is created when the user approves a pending file, matched once by the agent's retry, marked consumed at SubagentStop, and never re-issued. A SendMessage resume of an agent that already returned (because it emitted APPROVAL_REQUEST and ended its turn) is effectively a fresh sub-agent invocation -- the previous SubagentStop already consumed the grant, so the retried command produces a **new `approval_id`**. This is intentional: it ties one consent action to one execution. Re-approve on each surfaced request; do not assume any grant transfers across the turn boundary. The full lifecycle is documented in `hooks/modules/security/approval_grants.py` -> "Grant lifetime and the same-intent-new-approval-id pattern". If you see the same approved command emit a fresh `approval_id` on retry, that is the expected behavior, not a bug -- show the new request and approve again.

2. **For batch operations, request a verb-family grant.** Single-use-per-subagent is the safe default, but it is the wrong default when one user intent legitimately expands into N commands sharing the same base CLI and verb (e.g. modify 500 Gmail messages, delete 12 pending grants). The escape hatch is `approval_request.batch_scope: "verb_family"`, which creates a `SCOPE_VERB_FAMILY` grant with `multi_use=True` that survives until its TTL (10 minutes) expires or the sub-agent stops. The agent declares the batch intent; the orchestrator presents it with "batch" in the option label so the PostToolUse hook activates a multi-use grant. See `reference.md` -> "Batch Approval Flow". Approving N times in a row instead of requesting a batch is friction; approving once and letting the agent silently exceed the approved scope is the opposite failure.

3. **Scope guard -- copy `exact_content` byte-for-byte into the next attempt.** The grant is keyed to the exact statement signature. Anything the orchestrator adds on top -- a redirect, a `cd` prefix, a wrapper, a flag the user did not approve -- creates a different statement that the grant does not cover. Equivalent is not the same: the user approved what was shown, not what would also work. Whether you continue via SendMessage resume OR a fresh Agent re-dispatch, copy `approval_request.exact_content` literally into the next prompt and instruct the subagent to run that exact string. If the operation has genuinely changed, present a new approval -- do not retrofit it through wrapping.

4. **Fresh presentation every time.** Each hook-blocked APPROVAL_REQUEST requires its own presentation with all mandatory fields. Prior approvals do not carry forward.

5. **`mode` does NOT survive a SendMessage resume.** See `security-tiers/SKILL.md` -> "Mode runtime rules" R3. When the original dispatch relied on `mode: bypassPermissions` or `mode: acceptEdits` to satisfy CC native on `.claude/` writes, prefer a **fresh re-dispatch** carrying the same `mode` and the `exact_content` of the approved command, over a SendMessage resume that would run in `default` and re-block on the next protected operation outside the grant.

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
| "I'll add anything around the approved command -- redirect, cd prefix, flag, wrapper" | The grant matches the statement byte-for-byte; anything added on top is a different statement and a fresh re-block. Equivalent is not the same. |
| "I'll skip the [P-...] suffix, it's cosmetic" | The hook extracts the nonce from the label -- without it, targeted activation fails |
| "The same command emitted a new approval_id, the grant must be broken" | Single-use per sub-agent invocation is intentional. SubagentStop consumed the previous grant; the retry is a fresh invocation, so the hook issues a fresh nonce. Re-approve. |
| "Approving 500 commands one by one is the only safe path" | When the intent legitimately covers a verb family, the agent should emit `batch_scope: "verb_family"` and you present it as a batch -- multi-use, TTL-bounded, audited as one consent |
| "Original dispatch had bypassPermissions, resume will too" | `mode` is per-dispatch; resume via SendMessage runs in `default` -- CC native re-blocks. Re-dispatch fresh with the needed mode. |
| "Multi-step mv + Edit can be split: dispatch, approve, resume" | Each turn boundary drops the mode and consumes the grant. Pack ALL steps in one fresh dispatch after approval. |

For GOOD vs BAD examples, batch flow, grant mechanics, and the dispatch mode checklist, see `reference.md`.
