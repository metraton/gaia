---
name: subagent-request-approval
description: Use when a mutative command was blocked by the hook and you need to request user approval, or when presenting a plan for a T3 operation before executing it
metadata:
  user-invocable: false
  type: technique
---

# Subagent Request Approval

## Overview

When the hook blocks your T3 Bash command, it returns a `[T3_BLOCKED]` message
ending in `approval_id: P-{uuid4hex}`. This skill is how you turn that block into
an `APPROVAL_REQUEST`: copy the hook's operation fields and `approval_id` into
your `agent_contract_handoff`, set `plan_status: "APPROVAL_REQUEST"`, and wait for the
orchestrator to relay the user's decision. The hook authors and fingerprints the sealed_payload; you relay it back in your APPROVAL_REQUEST.

**Attempt first.** Run the T3 command and let the hook block it -- do not pre-ask
the user for permission. Pre-asking either requests a plan the hook would reject
anyway or stalls a command that would have passed; either way it wastes a turn
and trains you to second-guess the gate that exists to make that call.

## Flow

```
Subagent EXECUTES the T3 command (no pre-ask)
   |
   +-- hook allows -> runs -> continue
   |
   +-- hook blocks: [T3_BLOCKED] ... approval_id: P-{uuid4hex}
          |
   Emit plan_status APPROVAL_REQUEST + approval_request{...} with that approval_id
          |
   Orchestrator validates fingerprint, presents to user, user approves
          |
   Grant activates (single-use) -> orchestrator re-dispatches -> retry SAME command
```

## What to emit

Add an `approval_request` to your `agent_contract_handoff`, copying the hook's fields
**verbatim** (do not paraphrase):

The `approval_request` schema is canonical in `agent-approval-protocol` — relay the sealed_payload fields verbatim (the hook built them) and add `verification` (your own success criteria) + `approval_id` (the literal token from the denial). See `agent-approval-protocol/SKILL.md` for the full field list and types.

The `approval_id` is the `P-{...}` token the orchestrator uses to find the
`REQUESTED` row in the DB and validate the fingerprint. Fields written only in
prose are invisible to the presentation -- the user would approve blind.

## Non-negotiable rules

- **Verbatim `exact_content`.** The grant is keyed to the command's semantic
  signature (base command, verb, and normalized tokens/flags -- see
  `ApprovalSignature` in `approval_scopes.py`). One drifted flag, path, or
  argument between approval and retry is a grant miss and an immediate re-block;
  if the operation genuinely changed, attempt the new command for a fresh
  `approval_id` rather than rewording the old one.
- **Do not retry after a block.** Emit `APPROVAL_REQUEST` and stop. Each fresh
  attempt of a not-yet-pending command mints a new token and churns the audit
  trail. If you lost the `approval_id`, re-attempt once for a new one.
- **Never author the payload or fingerprint.** The hook built it; relay, do not recompute.
- **The grant is single-use.** It is consumed on your first matching retry. A
  second run within the TTL will not match -- it needs a fresh approval.

## Batch / many-command intents

There is **no `batch_scope` field** and no production batch grant: each blocked
command is one single-use approval, so a sweep of N commands is N approvals.
The `COMMAND_SET` mechanism exists in code but no path activates it from an
approval, so emitting `batch_scope` does nothing. See `reference.md` for why.

## Pointers

- Envelope schema, fingerprint canonicalization, event chain: `agent-approval-protocol/SKILL.md`.
- Mode / SendMessage-resume runtime rules: `security-tiers/SKILL.md` -> "Mode runtime rules" (R3).
- Deep mechanics, where the payload comes from, grant lifecycle, examples: `reference.md`.

## Anti-Patterns

- **Pre-asking before attempting** -- the hook is the gate; your guess is not.
- **Retrying after T3_BLOCKED** -- emit `APPROVAL_REQUEST` and wait; looping hopes for a different result.
- **Approval fields in prose only** -- the orchestrator parses JSON; prose is invisible and the user approves blind.
- **Paraphrased `exact_content`** -- one drifted token is a re-block.
- **Fabricating `approval_id`, fingerprint, or `sealed_payload`** -- the orchestrator validates against the DB; invented values never match.
- **Reusing a prior approval** -- single-use, consumed on first retry.
- **Emitting `batch_scope`** -- the field does not exist; it is ignored.
