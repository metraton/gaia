---
name: execution
description: Use in a fresh specialist dispatch after the user has granted a T3 operation or COMMAND_SET
---

# Approved Execution

Approval resumes work in a fresh dispatch owned by the relevant specialist. It
does not turn the orchestrator into an executor and does not broaden scope.

## Source boundary

For Gaia components, edit only the canonical `gaia/` source tree. Never write,
copy, generate, or stage anything under `.claude/`; installation propagates
source changes. The same prohibition applies to fixtures and bulk operations.

## Ordered execution

1. Read the granted request from the trusted handoff/DB and confirm its exact
   id, scope, order, and next unconsumed index.
2. Execute exactly one command per tool call using `command-execution`.
3. For COMMAND_SET, run only the exact next index. Never join commands, skip an
   index, substitute an equivalent spelling, or add an unapproved command.
4. After every result, checkpoint the exact command, index, exit status, and
   runtime progress fields when exposed.
5. On failure, apply `command-execution`'s COMMAND_SET fail-fast rule, then use
   `agent-protocol` to reconcile the result for its consumer.
6. After successful mutations, verify desired state with separate read-only
   checks. Success exit codes alone are insufficient.
7. Checkpoint verification and emit `NEEDS_VERIFICATION` for a plan-task-bound
   producer; only an eligible unbound turn/verifier may reach `COMPLETE`.

This skill sequences the approved work; it does not duplicate the failure rule
or the consumer reconciliation owned by those two skills.
