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

Per `agent-protocol`, build this by-value via the `gaia contract` CLI as the
primary path -- `gaia contract set agent_status.plan_status APPROVAL_REQUEST`,
then `gaia contract fill --json '{"approval_request": {...}}'` with the
hook's fields, then `finalize`. But the CLI build does not replace the fence:
the SubagentStop gate parses the fenced `agent_contract_handoff` block out of
your response text, not the finalized DB row, so you still close the turn by
emitting the fence -- copied verbatim from what you built -- in your final
message. A finalized `APPROVAL_REQUEST` draft with no matching fence in the
response text is invisible to both the gate and the orchestrator's
presentation.

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

The `approval_id` is the `P-{...}` token tying this request to its `REQUESTED`
row in the DB. Fields written only in prose are invisible to the presentation --
the user would approve blind.

**What your relay is for: in-loop, same-session presentation.** Your
`approval_request` is the orchestrator's source for presenting this approval to
the user. Approvals are in-loop and single-session: there is no per-turn or
cross-session resurfacing of pendings, so the orchestrator presents from your
relay in the same turn/session you emit it -- it does not wait for the pending
to reappear later. The orchestrator never dispatches a subagent to verify or
derive your request -- integrity is enforced at grant activation, not at
presentation.

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
- **The grant is single-use.** It is consumed at the match, before the command
  executes. A second run -- or a retry after the command executed and failed --
  will not match; it needs a fresh approval. (The only survival case is a
  dispatch that dies before reaching the command: the grant stays live for its
  5-minute TTL and a re-dispatch within that window reuses it.)

## Batch / many-command intents -- COMMAND_SET happens at the block, not by declaration

Grouping commands under one consent is **not something you construct** -- it is
something the hook detects when you attempt the commands. There is no
plan-first step where you declare a batch before attempting anything. The
mechanism is identical in spirit to the singular flow -- **attempt first, let
the hook decide, relay the `approval_id` it gives you**:

- Run each independent T3 command as its own Bash call, one at a time, and let
  each be blocked and approved on its own. This remains the default.
- Run a **single Bash call that chains >= 2 T3 sub-commands you already know
  belong together** (e.g. `git add -A && git commit -m 'v1.2.0' && git push
  origin main`) only when the commands are genuinely meant to execute as one
  compound operation. If the hook classifies **two or more** of the chained
  sub-commands as ungranted T3, it groups them under **one** consent
  automatically -- you do not ask for that grouping, you do not build a
  `command_set` field, and you do not decide whether batching happens.

**Never chain commands solely to manufacture a batch.** Reaching for `&&`
between commands that do not need to run together, just to reduce the number of
approvals, inverts the cost the same way inventing commands would: it trades
the user's per-command visibility for a grouping you engineered rather than one
that reflects real, already-known work.

## What arrives when the hook groups a chain

When the hook's compound-command classifier
(`bash_validator._validate_compound_command`) finds **>= 2** ungranted-T3
sub-commands in the chain you ran, it mints **exactly ONE** `COMMAND_SET`
pending covering the whole chain (`decide_t3_outcome(command_set=...)`) and
denies the Bash call with the **same denial shape as a singular block**: a
`[T3_BLOCKED]` message ending in `approval_id: P-{...}`. You relay that
`approval_id` into your `approval_request` **exactly as you would for one
command** -- there is no separate contract shape for a batch. The hook has
already built the sealed payload's `commands` and `command_set` fields for you
from the chain; you never author them.

The only internal difference from a singular block: for a COMMAND_SET the
`approval_id` is **content-derived** from the chain's commands
(`derive_command_set_id` -> `P-<first 32 hex of sha256(canonical commands)>`),
not a random uuid4 -- so retrying the identical chain reproduces the identical
id and reuses the same pending. This is purely internal to the hook; you relay
whatever `approval_id` the block gives you, singular or batch, the same way.

On the user's approval, that one pending activates into a single `COMMAND_SET`
grant (5-minute TTL, aligned to the singular grant); each sub-command is then
consumed byte-for-byte **at its match, before it executes**, with replay
protection, until the whole set is `CONSUMED`. Approving is the order to
execute -- the orchestrator re-dispatches the approved chain immediately. See
`reference.md` for the envelope shape, the chain-intake classifier, the grant
TTL, and the consume path.

## Pointers

- Envelope schema, fingerprint canonicalization, event chain: `agent-approval-protocol/SKILL.md`.
- Deep mechanics, where the payload comes from, grant lifecycle, examples: `reference.md`.

## Anti-Patterns

- **Pre-asking before attempting** -- the hook is the gate; your guess is not.
- **Retrying after T3_BLOCKED** -- emit `APPROVAL_REQUEST` and wait; looping hopes for a different result.
- **Approval fields in prose only** -- the orchestrator parses JSON; prose is invisible and the user approves blind.
- **Paraphrased `exact_content`** -- one drifted token is a re-block.
- **Fabricating `approval_id`, fingerprint, or `sealed_payload`** -- the orchestrator validates against the DB; invented values never match.
- **Reusing a prior approval** -- single-use, consumed at match (before execution); a used grant, or one whose command already ran, needs a fresh approval.
- **Emitting `batch_scope`** -- the field does not exist; it is ignored.
- **Authoring a `command_set` yourself** -- you never build or declare one; the hook mints it from a chain you attempted, and you relay the resulting `approval_id` exactly like a singular block.
- **Chaining commands with `&&` just to force a batch** -- manufacturing a compound command out of unrelated work to get one consent instead of several is the same violation as inventing commands: only chain work that is genuinely meant to run together.
