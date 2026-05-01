---
name: request-approval
description: Use when a mutative command was blocked by the hook and you need to request user approval, or when presenting a plan for a T3 operation before executing it
metadata:
  user-invocable: false
  type: technique
---

# Request Approval

## Overview

This skill teaches the agent how to **request** approval. It does not approve
anything -- the orchestrator presents the plan and the user grants consent.
The agent's job is to record what happened and emit a structured
`approval_request` the orchestrator can present verbatim.

The core rule is **attempt first**: do not pre-ask the user for permission.
Attempt the T3 command, let the hook block it with an `approval_id`, then emit
`plan_status: "APPROVAL_REQUEST"` with that `approval_id` in your
`approval_request`. Pre-asking either approves a speculative plan the hook
would have rejected anyway or stalls a command that would have passed without
friction -- both waste a turn and train the agent to second-guess the gate.

## Attempt First Flow

```
Agent plans a T3 command
        |
Agent EXECUTES the command (does NOT pre-ask)
        |
  +-- hook allows -> command runs -> continue
  |
  +-- hook blocks with [T3_BLOCKED] + approval_id
        |
Agent emits APPROVAL_REQUEST with approval_id
        |
Orchestrator presents plan -> user decides -> grant activates
        |
Orchestrator resumes agent -> agent retries -> continue
```

## Approval Request Object

Include an `approval_request` object in your `json:contract` with these fields:

```json
"approval_request": {
  "operation": "verb + target",
  "exact_content": "literal command, config, or file change",
  "scope": "files, resources, environments affected",
  "risk_level": "LOW | MEDIUM | HIGH | CRITICAL",
  "rollback": "how to undo if wrong",
  "verification": "how to confirm success after execution",
  "approval_id": "hex from hook deny response (when blocked)",
  "batch_scope": "verb_family (only for sweeps -- see below)"
}
```

The orchestrator parses this object directly. Fields written only in prose are
invisible to the presentation -- the user approves blind. Risk levels, plan
templates, and full examples live in `reference.md` and `examples.md`.

## Verbatim Always

`exact_content` is the literal command or file change, not a paraphrase. The
runtime grant is keyed to the exact command signature: a single argument,
flag, or path segment that drifts between approval and retry produces a
grant miss and an immediate re-block. If the operation has genuinely changed,
emit a new `approval_request` -- do not reword.

## Hook Block Flow

When a hook blocks your command the deny response includes an `approval_id` --
a one-time hex token tied to exactly this command. The instinct is to retry.
That is wrong: each retry generates a fresh nonce, the old `approval_id` goes
stale, and the loop never terminates.

Instead: emit `APPROVAL_REQUEST` with the `approval_id` in your
`approval_request`, stop, and wait. When the user approves, the grant
activates and the orchestrator resumes you to retry the same command.

If you lose the `approval_id`, re-attempt the command once for a fresh one.

## Status to Emit

Always emit `plan_status: "APPROVAL_REQUEST"`. Whether `approval_id` is
present tells the orchestrator which path:

- With `approval_id` -- the hook blocked; orchestrator activates the grant
- Without `approval_id` -- plan-first; orchestrator gates on user consent

## Batch Approval -- One Grant for Many Commands

When one user intent expands into many commands sharing the same base CLI and
verb (archive 500 messages, delete 100 stale grants), do not emit a separate
approval per command -- N nonces produce N user prompts and the session
stalls. Add `batch_scope: "verb_family"` to your `approval_request`; the
orchestrator presents both "Approve batch" and "Approve single" options, and
batch approval creates a multi-use grant for the same `base_cmd + verb` over
a 10-minute TTL.

Use it only for genuine sweeps. For single commands the standard fields
suffice; for destructive irreversible operations the per-command audit trail
of single approvals is the safer default. See `reference.md` for the full
semantics, scope boundaries, and the mixed-verb pattern.

For mode/resume runtime rules, see `security-tiers/SKILL.md` -> "Mode runtime rules".

## Anti-Patterns

- **Pre-asking before attempting** -- the hook is the gate; the agent's guess is not.
- **Retrying after T3_BLOCKED** -- each retry generates a new nonce; the old `approval_id` goes stale and the loop never closes.
- **Approval fields in prose only** -- the orchestrator parses the JSON; prose is invisible.
- **Paraphrased `exact_content`** -- grants match the literal command signature; one drifted argument is a re-block.
- **Reusing prior approvals** -- grants are scoped to a specific nonce and command.
- **Fabricating an approval_id** -- the hook validates against its store; an invented token never matches.
- **Single approval for a sweep** -- N commands without `batch_scope` produce N prompts and re-blocks.
- **Using `batch_scope` for one command** -- the multi-use grant adds presentation noise the user does not need.
- **Assuming `mode` survives a resume** -- it does not; pack steps in one turn or accept the re-dispatch. See `security-tiers/SKILL.md` -> "Mode runtime rules" R3.
