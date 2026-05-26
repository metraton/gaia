---
name: subagent-request-approval
description: Use when a mutative command was blocked by the hook and you need to request user approval, or when presenting a plan for a T3 operation before executing it
metadata:
  user-invocable: false
  type: technique
---

# Subagent Request Approval

## Overview

This skill teaches the subagent how to **emit** an approval request. It does not
approve anything -- the orchestrator presents the request verbatim to the user,
who grants consent. The subagent's job is to stop after the hook blocks, assemble
the `sealed_payload`, and emit a structured `APPROVAL_REQUEST` the orchestrator
can relay without re-authoring.

The core rule is **attempt first**: do not pre-ask the user for permission.
Attempt the T3 command, let the hook block it with an `approval_id`, then emit
`plan_status: "APPROVAL_REQUEST"` with that `approval_id` in your
`approval_request`. Pre-asking either approves a speculative plan the hook
would have rejected anyway or stalls a command that would have passed without
friction -- both waste a turn and train the agent to second-guess the gate.

## Attempt First Flow

```
Subagent plans a T3 command
        |
Subagent EXECUTES the command (does NOT pre-ask)
        |
  +-- hook allows -> command runs -> continue
  |
  +-- hook blocks with [T3_BLOCKED] + approval_id
        |
Subagent emits APPROVAL_REQUEST with approval_id + sealed_payload
        |
Orchestrator loads Skill('orchestrator-present-approval')
        |
Orchestrator validates fingerprint, presents to user -> user decides
        |
Grant activates -> orchestrator resumes subagent -> subagent retries -> continue
```

## sealed_payload Schema

The `sealed_payload` carries the 7 fields the orchestrator relays verbatim.
Do not paraphrase, summarize, or merge these fields -- the orchestrator
copies them byte-for-byte into the AskUserQuestion presentation.

```json
"sealed_payload": {
  "operation":     "human-readable action description (e.g. 'Delete branch feature/x')",
  "exact_content": "verbatim command(s) the agent will run, newline-separated if multiple",
  "scope":         "resource path or identifier the command targets",
  "risk_level":    "low | medium | high | critical",
  "rollback_hint": "human-readable inverse; null if not reversible",
  "rationale":     "why this T3 is needed in this context",
  "commands":      ["array of discrete command strings to be executed in order"]
}
```

Canonicalization for fingerprint: the hook computes
`SHA-256(json.dumps(payload, sort_keys=True, separators=(',', ':')))`.
Any mutation to the payload after emission changes the fingerprint and
causes the orchestrator to reject the relay before it reaches the user.
Do not touch the payload after constructing it.

## Approval Request Object

Include an `approval_request` object in your `json:contract` with these fields:

```json
"approval_request": {
  "operation":     "<from sealed_payload.operation>",
  "exact_content": "<from sealed_payload.exact_content>",
  "scope":         "<from sealed_payload.scope>",
  "risk_level":    "<from sealed_payload.risk_level>",
  "rollback":      "<from sealed_payload.rollback_hint>",
  "verification":  "how to confirm success after execution",
  "approval_id":   "<P-{...} from hook deny response>",
  "batch_scope":   "verb_family (only for sweeps -- see below)"
}
```

The `approval_id` is the `P-{...}` token returned by the hook in the
`[T3_BLOCKED]` deny message. Include it in the `approval_request`; the
orchestrator uses it to look up the REQUESTED event in the DB and validate
the fingerprint before presenting.

The orchestrator parses this object directly. Fields written only in prose
are invisible to the presentation -- the user approves blind.

## Verbatim Always

`exact_content` is the literal command or file change, not a paraphrase. The
runtime grant is keyed to the exact command signature: a single argument,
flag, or path segment that drifts between approval and retry produces a
grant miss and an immediate re-block. If the operation has genuinely changed,
emit a new `approval_request` -- do not reword.

## Hook Block Flow

When a hook blocks your command the deny message includes an `approval_id` --
a `P-{...}` token tied to exactly this command stored in the DB. The instinct
is to retry. That is wrong: each retry generates a fresh token, the old
`approval_id` goes stale, and the loop never terminates.

Instead: emit `APPROVAL_REQUEST` with the `approval_id` in your
`approval_request`, stop, and wait. When the user approves, the grant
activates and the orchestrator resumes you to retry the same command.

If you lose the `approval_id`, re-attempt the command once for a fresh one.

## Status to Emit

Always emit `plan_status: "APPROVAL_REQUEST"`. Whether `approval_id` is
present tells the orchestrator which path:

- With `approval_id` -- the hook blocked; orchestrator validates fingerprint and activates the grant
- Without `approval_id` -- plan-first; orchestrator gates on user consent before any execution

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
of single approvals is the safer default.

For mode/resume runtime rules, see `security-tiers/SKILL.md` -> "Mode runtime rules".

## Anti-Patterns

- **Pre-asking before attempting** -- the hook is the gate; the agent's guess is not.
- **Retrying after T3_BLOCKED** -- each retry generates a new token; the old `approval_id` goes stale and the loop never closes.
- **Approval fields in prose only** -- the orchestrator parses the JSON; prose is invisible.
- **Paraphrased `exact_content`** -- grants match the literal command signature; one drifted argument is a re-block.
- **Modifying sealed_payload after construction** -- the hook fingerprinted it; any byte change fails the orchestrator's verify_fingerprint check.
- **Reusing prior approvals** -- grants are scoped to a specific token and command.
- **Fabricating an approval_id** -- the hook validates against the DB; an invented token never matches.
- **Single approval for a sweep** -- N commands without `batch_scope` produce N prompts and re-blocks.
- **Using `batch_scope` for one command** -- the multi-use grant adds presentation noise the user does not need.
- **Assuming `mode` survives a resume** -- it does not; pack steps in one turn or accept the re-dispatch. See `security-tiers/SKILL.md` -> "Mode runtime rules" R3.
