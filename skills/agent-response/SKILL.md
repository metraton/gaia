---
name: agent-response
description: Use when an agent returns a json:contract response that needs to be interpreted and presented to the user
metadata:
  user-invocable: false
  type: protocol
---

# Agent Response Protocol

The orchestrator's job is translation -- turning structured agent output into
clear user communication. Every status requires a different response because
each represents a different kind of decision point for the user.

## State Machine

```
Agent returns json:contract
  |- COMPLETE            -> Summarize key_outputs (3-5 bullets)
  |- NEEDS_INPUT         -> AskUserQuestion, then SendMessage answer back
  |- APPROVAL_REQUEST    -> Load Skill("orchestrator-approval") if approval_id present,
  |                         otherwise AskUserQuestion (execute/modify/cancel),
  |                         then SendMessage to resume the same agent
  |- BLOCKED             -> Present open_gaps via AskUserQuestion
  |                         If user provides direction: dispatch new agent addressing the blocker.
  |                         If user accepts the limitation: close the task as incomplete and move on.
  +- IN_PROGRESS         -> SendMessage to resume agent
```

## Mandatory Actions per Status

| Status | Action | Tool |
|---|---|---|
| `COMPLETE` | Summarize `key_outputs` in 3-5 bullets. Mention `cross_layer_impacts` and `open_gaps` if non-empty. Say "ask for details" if `verbatim_outputs` exists. | Direct response |
| `NEEDS_INPUT` | Present the agent's question with options | `AskUserQuestion` -> `SendMessage` |
| `APPROVAL_REQUEST` | If `approval_request.approval_id` is present: load `Skill("orchestrator-approval")`. Otherwise: present plan with options execute / modify / cancel. On execute or modify: resume the SAME agent via SendMessage -- it already holds full context from its investigation. | `AskUserQuestion` -> `SendMessage` |
| `BLOCKED` | Present alternatives from `open_gaps`. If user provides direction, dispatch a new agent addressing the blocker. If user accepts the limitation, close as incomplete and move on. | `AskUserQuestion` |
| `IN_PROGRESS` | Agent was interrupted, let it continue | `SendMessage` |

**Why APPROVAL_REQUEST splits on approval_id:** Hook-blocked T3 operations carry a pending
grant that requires the structured approval flow (exact content, rollback, risk).
Plan-first APPROVAL_REQUEST has no pending grant -- the user just needs to confirm direction.
Treating both the same either over-formalizes simple plans or under-secures T3 ops.

## Output Fields

| Field | When to surface |
|---|---|
| `key_outputs` | Always -- base your summary on these |
| `verbatim_outputs` | Only when user asks for details -- relay in code blocks |
| `cross_layer_impacts` | Always mention if non-empty -- these are side effects the user may not anticipate |
| `open_gaps` | Always mention -- never imply certainty the agent does not have |
| `consolidation_report` | Check for `conflicts` and `next_best_agent` |
| `next_best_agent` | Ask user if they want to dispatch |

## Multiple Agents

Wait for ALL dispatched agents before responding. Partial results
mislead -- the user acts on incomplete information, then the second
agent contradicts the first.

Consolidate findings. If agents conflict, present both sides and
ask the user to decide.

## Error Handling

| Situation | Action |
|---|---|
| Malformed contract | Resume agent with repair instructions (max 2 retries). |
