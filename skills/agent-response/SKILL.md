---
name: agent-response
description: Use when an agent returns a json:contract response that needs to be interpreted and presented to the user
metadata:
  user-invocable: false
  type: protocol
---

# Agent Response Protocol

## State Machine

```
Agent returns json:contract
  ├─ COMPLETE         → Summarize key_outputs (3-5 bullets)
  ├─ NEEDS_INPUT      → AskUserQuestion, then SendMessage answer back
  ├─ REVIEW           → AskUserQuestion (execute/modify/cancel), then SendMessage decision
  ├─ AWAITING_APPROVAL → Load Skill("orchestrator-approval")
  ├─ BLOCKED          → Present open_gaps via AskUserQuestion
  └─ IN_PROGRESS      → SendMessage to resume agent
```

## Mandatory Actions per Status

| Status | Action | Tool |
|---|---|---|
| `COMPLETE` | Summarize `key_outputs` in 3-5 bullets. Mention `cross_layer_impacts` and `open_gaps` if non-empty. Say "ask for details" if `verbatim_outputs` exists. | Direct response |
| `NEEDS_INPUT` | Present the agent's question with options | `AskUserQuestion` → `SendMessage` |
| `REVIEW` | Present plan with options: execute / modify / cancel | `AskUserQuestion` → `SendMessage` |
| `AWAITING_APPROVAL` | Do not handle directly | `Skill("orchestrator-approval")` |
| `BLOCKED` | Present alternatives from `open_gaps` | `AskUserQuestion` |
| `IN_PROGRESS` | Agent was interrupted, let it continue | `SendMessage` |

## Output Fields

| Field | When to surface |
|---|---|
| `key_outputs` | Always — base your summary on these |
| `verbatim_outputs` | Only when user asks for details — relay in code blocks |
| `cross_layer_impacts` | Always mention if non-empty |
| `open_gaps` | Always mention — never imply certainty |
| `consolidation_report` | Check for `conflicts` and `next_best_agent` |
| `next_best_agent` | Ask user if they want to dispatch |

## Multiple Agents

Wait for ALL dispatched agents before responding. Consolidate findings.
If agents conflict, present both sides and ask user to decide.

## Error Handling

| Situation | Action |
|---|---|
| Hook blocks a command | Relay message verbatim. Do not try alternatives. |
| Agent contradicts another | Show both findings, flag conflict, ask user. |
| Malformed contract | Resume agent with repair instructions (max 2 retries). |
