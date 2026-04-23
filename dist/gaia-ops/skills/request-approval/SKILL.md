---
name: request-approval
description: Use when a mutative command was blocked by the hook and you need to request user approval, or when presenting a plan for a T3 operation before executing it
metadata:
  user-invocable: false
  type: technique
---

# Request Approval

## Overview

This skill does not approve anything -- it teaches the agent how to
**request** approval when the hook blocks a mutative command. The
orchestrator and the user own approval; the agent owns the request.

The core rule is **attempt first**: do not pre-ask the user for
permission. Attempt the T3 command, let the hook block it with an
`approval_id`, then emit `plan_status: "APPROVAL_REQUEST"` with the
captured `approval_id` in your `approval_request` object. The hook is
the authoritative gate; the agent only records what happened.

Asking the user before attempting produces two failure modes: the
agent approves itself on a speculative plan the hook would have
rejected anyway, or the agent blocks on a command that would have
passed without any friction. Both waste a turn.

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
Agent emits plan_status: "APPROVAL_REQUEST"
with approval_id in approval_request
        |
Orchestrator presents plan to user
        |
User approves -> grant activates -> agent retries
```

## Approval Request Object

Include an `approval_request` object in your `json:contract` with these 6 fields,
plus `approval_id` when a hook blocked the command:

```json
"approval_request": {
  "operation": "verb + target",
  "exact_content": "literal command, config, or file change",
  "scope": "files, resources, environments affected",
  "risk_level": "LOW | MEDIUM | HIGH | CRITICAL",
  "rollback": "how to undo if wrong",
  "verification": "how to confirm success after execution",
  "approval_id": "hex from hook deny response (when blocked)"
}
```

### Risk Levels

| Level | Criteria |
|-------|----------|
| LOW | Single resource, non-prod, no dependencies |
| MEDIUM | Multiple resources, non-prod, some dependencies |
| HIGH | Production, dependencies, potential downtime |
| CRITICAL | Irreversible, data loss possible |

## Status to Emit

Always emit `plan_status: "APPROVAL_REQUEST"`. The presence or absence
of `approval_id` tells the orchestrator which path to take:

- With `approval_id` -- the hook blocked; orchestrator activates the grant
- Without `approval_id` -- plan-first; orchestrator gates on user consent

The legacy name `REVIEW` is gone from runtime. If a doc still references
`REVIEW` as a plan_status literal, it is drift scheduled for cleanup.

## Hook Block Flow

When a hook blocks your command the deny response includes an
`approval_id` -- a one-time hex token tied to exactly this command.

The instinct is to retry. That is the wrong move: each retry generates
a fresh nonce, the old `approval_id` goes stale, and you enter an
infinite retry-block loop.

Instead: emit `APPROVAL_REQUEST` with the `approval_id` in your
`approval_request`, stop, and wait. When the user approves, the grant
activates and the orchestrator resumes you to retry.

Deny message format:
```
[T3_BLOCKED] This command requires user approval.
Do NOT retry this command. Report APPROVAL_REQUEST with this approval_id in your json:contract.
approval_id: <hex>
```

If you lose the `approval_id`, re-attempt the command once for a fresh one.

## Dispatch mode and foreground/background

When a subagent edits `.claude/skills/**`, `.claude/agents/**`, `.claude/commands/**`, briefs, plans, or evidence files, the orchestrator **must pass `mode: acceptEdits`** in the Agent tool dispatch. Without it, CC native intercepts with a permission prompt (foreground) or auto-denies (background).

**The `mode` is NOT inherited.** Each dispatch of the Agent tool is independent. If the orchestrator runs with `acceptEdits`, that does not flow to any subagent it invokes. The mode must be specified explicitly in every dispatch that needs it.

**The `mode` also does NOT survive a SendMessage resume.** If your original dispatch gave you `mode: bypassPermissions` and you emit APPROVAL_REQUEST mid-task, the orchestrator's SendMessage resume runs in `default`. The Gaia grant activates for the exact blocked command, but CC native re-blocks the next Edit/Write/Bash on `.claude/` because the mode is gone. Consequence for the subagent: when the task is a multi-step bundle on protected paths (e.g., mv on `.claude/**` + Edits in `.claude/project-context/**`), execute every step in the SAME turn the dispatch started. If a hook blocks a step, emit BLOCKED and stop -- do NOT emit APPROVAL_REQUEST mid-task expecting to continue after a SendMessage resume. The orchestrator will re-dispatch fresh.

**Foreground vs background is a separate axis.** It governs whether a user-facing interaction (like AskUserQuestion) can display. It does not resolve CC native permission prompts -- those are governed by `mode`.

### Combination table

| Case | mode in dispatch | Session type | Expected behavior |
|------|-----------------|--------------|-------------------|
| Subagent edits `skills/` with `acceptEdits` | `acceptEdits` | foreground | Edit passes -- no CC native prompt, user sees agent output |
| Subagent edits `skills/` with default mode | `default` | foreground | CC native prompts the user for consent each Edit/Write |
| Subagent edits `skills/` with default mode | `default` | background | CC native auto-denies -- agent gets permission error, edit never happens |
| Subagent edits `skills/` with `acceptEdits` | `acceptEdits` | background | Edit passes -- `mode` covers CC native; background only blocks AskUserQuestion |
| Subagent tries to edit `.claude/hooks/` | any | any | Gaia `_is_protected()` blocks regardless of mode; approval flow required |
| Orchestrator edits `skills/` directly (no subagent) | n/a (own session) | foreground | Passes if parent session has `acceptEdits` or CC auto-accepts |

The foreground/background distinction matters for approval flows: AskUserQuestion only works in foreground. In background, the orchestrator cannot present interactive prompts -- T3 operations that require user consent must be deferred or routed to a foreground session.

For the full `permissionMode` comparison, see `security-tiers/SKILL.md`.

## Anti-Patterns

- **Pre-asking the user before attempting** -- violates attempt first; the hook is the gate, not the agent's guess
- **Retrying after T3_BLOCKED** -- each retry generates a new nonce, making the previous approval_id stale; this loops forever
- **Missing fields in approval_request** -- the orchestrator presents these fields directly; missing fields mean the user approves blind
- **Approval fields in prose only** -- the orchestrator parses the JSON object, not your text; prose-only plans bypass the structured flow
- **Reusing prior approvals** -- grants are scoped to a specific nonce and command; a prior approval does not cover a new operation
- **Fabricating the approval_id** -- the hook validates against its nonce store; an invented token will never match
- **Omitting `mode: acceptEdits` from dispatch** -- subagents dispatched without it will hit CC native prompts on `.claude/` writes; in background, this auto-denies silently
- **Assuming `mode` survives a SendMessage resume** -- it does not; if the task depends on bypass/acceptEdits, pack all steps in one dispatch turn, or emit BLOCKED and let the orchestrator re-dispatch fresh
