# Producer approval reference

The current COMMAND_SET intake is plan-first:

```
gaia approvals request-set \
  --command '<exact T3 command 0>' \
  --command '<exact T3 command 1>' \
  --rationale '<bounded goal>' \
  --agent-id <agent_id> \
  --session-id <session_id>
```

The implementation is `bin/cli/approvals.py::cmd_request_set` plus
`gaia/approvals/command_set.py::validate_request_set`. It requires at least one
exact, atomic, non-interactive T3 string and rejects shell composition,
protected paths, permanently blocked operations, and non-T3 items. A set of
one is the proactive path for a single predictable T3 command: request it
before any attempt, the same way a longer set is requested.

The CLI persists a REQUESTED payload containing `request_type`, `operation`,
`exact_content`, `commands`, `command_set` (each command plus SHA-256
fingerprint), the order-sensitive `request_fingerprint`, `scope`, `risk_level`,
`rollback_hint`, and `rationale`. Relay its returned approval id and data; do not
derive them.

The reactive single-command path is separate and begins only after the hook
already returned `[T3_BLOCKED]` for an attempted command. Relay its sealed
payload unchanged, checkpoint, and stop. A blocked command must not be
rewritten or folded into a retrospective set. Prefer the proactive one-command
request-set above when the T3 command is known in advance from read-only
investigation, so consent is sought because the operation is mutative, not
because the hook intercepted it.

Grouping criteria are semantic, not merely numeric: one goal, exact known
commands, ordered execution, coherent risk/rollback/verification, and no
dependency on unseen output. Otherwise request singularly or investigate again.
