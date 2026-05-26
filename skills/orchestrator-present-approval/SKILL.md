---
name: orchestrator-present-approval
description: Use when processing APPROVAL_REQUEST with approval_id from a subagent -- enforces showing values before asking for user consent
metadata:
  user-invocable: false
  type: discipline
---

# Orchestrator Present Approval

```
The user approves EXACT VALUES, not summaries.
Every AskUserQuestion shows the literal command, every option label
names the specific action. No exceptions. No brevity shortcuts.
```

## Mental Model

The orchestrator sits between the subagent and the user. The user cannot make
an informed decision on information they have not seen. A summary, a reference
to "the plan above", or an offer to show details on request -- all push the
decision without the data needed to decide.

**Verbatim relay, not re-authoring.** When the subagent emits a `sealed_payload`,
the orchestrator's job is to relay it byte-for-byte into the AskUserQuestion
presentation. Rewriting, summarizing, or paraphrasing any field breaks the
fingerprint contract and constitutes a false consent surface.

**Scope:** This skill applies when a subagent emits `APPROVAL_REQUEST` with an
`approval_id` in its `approval_request`.

## Step 0 — Fingerprint Validation (mandatory before SHOWN)

Before calling AskUserQuestion, validate the payload integrity:

```python
from gaia.approvals.chain import verify_fingerprint

# Raises ChainTamperError if fingerprint does not match the REQUESTED row.
# Raises ValueError if no REQUESTED event exists for this approval_id.
verify_fingerprint(approval_id, payload_json, con)
```

If `verify_fingerprint` raises, **do not present the approval to the user.**
Report the error to the user as a tamper-detection failure and stop. A
`ChainTamperError` means the payload was modified between the subagent emission
and your relay -- this is a security boundary, not a recoverable UX issue.

## Pre-Flight Checklist

After fingerprint validation, verify ALL of the following. If any check fails,
go back to the subagent's `approval_request` and extract the missing field.

1. Does the question text contain the VERBATIM command from `exact_content`? Not summarized, not paraphrased -- the literal string.
2. Does the question text contain all 5 labeled fields (OPERATION, COMMAND, SCOPE, RISK, ROLLBACK)?
3. Does the "Approve" option label name the SPECIFIC action (e.g., "Approve -- push 2 commits to origin/main"), not a generic phrase?
4. Is the command/content complete? No "..." truncation, no "the above changes".
5. Does the "Approve" option label end with `[P-{nonce_prefix8}]`? The nonce comes from `approval_request.approval_id` (first 8 chars after the `P-` prefix).

## Mandatory Presentation Format

Every AskUserQuestion `question` parameter must contain these 5 labeled fields,
extracted verbatim from the subagent's `sealed_payload`:

```
APPROVAL REQUIRED

OPERACION:  {sealed_payload.operation}
COMANDO:    {sealed_payload.exact_content}  <-- verbatim, never paraphrased
SCOPE:      {sealed_payload.scope}
RIESGO:     {sealed_payload.risk_level} + why (from sealed_payload.rationale)
ROLLBACK:   {sealed_payload.rollback_hint}
```

See `template.md` for the canonical AskUserQuestion text layout.

## Option Label Rules

The "Approve" option MUST name the specific action. The PostToolUse hook
activates grants by checking for "approve" in the answer value.

- Format: `"Approve -- {specific_action_description} [P-{nonce_prefix8}]"`
- The action description comes from `sealed_payload.operation`
- The nonce comes from `approval_request.approval_id` (first 8 chars after `P-`)

## Rules

1. **Every APPROVAL_REQUEST is single-use per sub-agent invocation.** A grant is
   created when the user approves, matched once by the agent's retry, marked
   consumed at SubagentStop, and never re-issued. Re-approve on each surfaced
   request; do not assume any grant transfers across the turn boundary.

2. **For batch operations, request a verb-family grant.** When `approval_request`
   contains `batch_scope: "verb_family"`, present with "batch" in the Approve
   option label so the PostToolUse hook creates a multi-use verb-family grant.
   See `reference.md` -> "Batch Approval Flow".

3. **Scope guard -- copy `exact_content` byte-for-byte into the next attempt.**
   The grant is keyed to the exact statement signature. Anything added on top
   (a redirect, a `cd` prefix, a flag the user did not approve) creates a
   different statement that the grant does not cover. Copy
   `approval_request.exact_content` literally into the next prompt.

4. **Fresh presentation every time.** Each hook-blocked APPROVAL_REQUEST requires
   its own presentation with all mandatory fields. Prior approvals do not carry
   forward.

5. **`mode` does NOT survive a SendMessage resume.** See `security-tiers/SKILL.md`
   -> "Mode runtime rules" R3. Prefer a **fresh re-dispatch** carrying the same
   `mode` and the `exact_content` of the approved command, over a SendMessage
   resume that would run in `default` and re-block on the next protected
   operation outside the grant.

## Traps

| If you're thinking... | The reality is... |
|---|---|
| "The subagent already showed the details" | Show them again -- the user needs them at the decision point |
| "It's a small change, I can summarize" | Size does not change the contract -- show the exact command |
| "I'll offer to show details if they want" | The user needs the data BEFORE the question, not after |
| "The option label 'Approve' is enough" | Without the action, the user clicks blind -- label must say WHAT is approved |
| "'Approve -- aplicar cambios' describes it" | That is a paraphrase in another language -- name the actual operation |
| "The command is long, I'll shorten it" | Show it complete -- truncation hides what the user is approving |
| "Same operation, slightly different path" | Grants match by command signature -- different path = grant miss = immediate re-block |
| "I'll add anything around the approved command" | The grant matches the statement byte-for-byte; anything added is a different statement and a fresh re-block |
| "I'll skip the [P-...] suffix, it's cosmetic" | The hook extracts the nonce from the label -- without it, targeted activation fails |
| "The same command emitted a new approval_id" | Single-use per sub-agent invocation is intentional; SubagentStop consumed the previous grant. Re-approve. |
| "Approving 500 commands one by one is the only safe path" | When the intent legitimately covers a verb family, emit `batch_scope: "verb_family"` and present one batch approval |
| "I can relay the payload after paraphrasing rationale" | Any field change invalidates the fingerprint; verify_fingerprint will raise ChainTamperError |

For GOOD vs BAD examples, batch flow, grant mechanics, and the dispatch mode checklist, see `reference.md`.
