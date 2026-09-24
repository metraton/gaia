---
name: execution
description: Use when the specialist that requested a T3 operation or COMMAND_SET is resumed after the user granted it
---

# Approved Execution

The approved work runs in the specialist that requested it, resumed by the
orchestrator; the signature belongs to that agent and session. Approval does
not turn the orchestrator into an executor and does not broaden scope.

## Source boundary

For Gaia components, edit only the canonical `gaia/` source tree. Never write,
copy, generate, or stage anything under `.claude/`; installation propagates
source changes. The same prohibition applies to fixtures and bulk operations.

## Ordered execution

1. Read the approval with `gaia approvals show <approval_id>`: its state, its
   commands in order, their directory, and how much of the grant's window is
   left.
2. Run exactly one command per tool call using `command-execution`, byte for
   byte as requested, in its sealed directory. When that directory is not the
   one your Bash runs in, run exactly `cd <sealed directory> && <sealed command>`,
   the only compound accepted. Never join commands otherwise, skip an index,
   substitute an equivalent spelling, or add an unapproved command. In OpenCode
   a still-pending approval asks the user at this attempt: the call is refused
   while the question opens in your session, so end the turn with
   `APPROVAL_REQUEST`; you are resumed after the answer.
3. After every result, checkpoint the exact command, index and exit status.
4. A command that exits with a code its request declared with `--expect-exit`
   has succeeded for the set: continue with the next index. Any other failure
   stops the set; apply `command-execution`'s COMMAND_SET fail-fast rule, then
   use `agent-protocol` to reconcile the result for its consumer.
5. A call that never reports back is recorded as no result, not as a failure,
   and does not advance the set. Read its state before deciding anything, and
   request anything still needed as a new request.
6. After successful mutations, verify desired state with separate read-only
   checks. Success exit codes alone are insufficient.
7. Checkpoint verification and emit `NEEDS_VERIFICATION` for a plan-task-bound
   producer; only an eligible unbound turn/verifier may reach `COMPLETE`.

This skill sequences the approved work; it does not duplicate the failure rule
or the consumer reconciliation owned by those two skills.
