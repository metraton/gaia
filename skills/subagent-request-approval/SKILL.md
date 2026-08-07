---
name: subagent-request-approval
description: Use when a T3 command was blocked or a predictable ordered T3 set must be requested before execution
---

# Request Approval — Producer Branch

## Plan-first set

After read-only investigation, collect every predictable exact T3 command that
forms one coherent bounded operation. Do not attempt the commands first.

Run one CLI request, with one `--command` per atomic item in execution order:

```
gaia approvals request-set --command '<exact 0>' --command '<exact 1>' --rationale '<goal, risk, rollback, verification>' --agent-id <agent_id> --session-id <session_id>
```

The CLI validates T3 eligibility, persists REQUESTED, and returns the
`approval_id`, ordered set, and fingerprints. Relay those returned values inside
`approval_request`; do not calculate an id or fingerprint yourself.

## Blocked single command

When PreToolUse returns `[T3_BLOCKED]`, stop. Copy the returned `approval_id`
and sealed payload verbatim into `approval_request`. Do not retry, reword, split,
wrap, or seek the same effect through another tool.

## Checkpoint and stop

1. Set `agent_state` to `APPROVAL_REQUEST`.
2. Put all exact consent data in `approval_request`, including full ordered set,
   risk, rollback, and verification when COMMAND_SET.
3. Add only the block/request outcome to evidence; do not duplicate payloads.
4. Set `pending_steps` to execution after consent and `next_action` to relay the
   request to the user.
5. Finalize the non-terminal contract, emit its compatibility fence, and stop.

The producer does not present an approval as already granted and does not verify
execution before it happens. `orchestrator-present-approval` presents consent;
after grant, a fresh specialist dispatch loads `execution`.

## Grouping boundary

Use a set only for exact commands already implied by the accepted plan, with
one risk/rollback/verification narrative. Do not group unrelated effects,
alternatives, speculative cleanup, or commands derived from earlier outputs.
Consent grouping is not atomic execution.

A previous COMMAND_SET in `FAILED` cannot be resumed. After investigating its
partial state, request a new exact set (or singular approval) containing every
retry and still-needed remainder command.
