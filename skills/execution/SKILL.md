---
name: execution
description: Use when the specialist that requested a T3 operation or COMMAND_SET is resumed after the user granted it
---

# Approved Execution

The approved work runs in the specialist that requested it, resumed by the
orchestrator; the signature is bound to that agent's name and the host
session, so any agent of that name in the session can consume it, and the one
that requested it is the one that runs it. Approval does not turn the
orchestrator into an executor and does not broaden scope.

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
   substitute an equivalent spelling, or add an unapproved command. A
   still-pending approval is not a reason to stop: attempt the command. If
   Gaia refuses it because the user has not answered yet, end the turn with
   `APPROVAL_REQUEST` and the same approval id; the orchestrator asks the
   user and resumes you after the answer. This is the same in Claude Code and
   OpenCode, and nothing is asked in your session.
3. After every result, checkpoint the exact command, index and exit status.
4. A command that exits with a code its request declared with `--expect-exit`
   has succeeded for the set: continue with the next index. Only a declaration
   made when the set was requested counts; you cannot declare one now. Any other failure
   stops the set; apply `command-execution`'s COMMAND_SET fail-fast rule, then
   use `agent-protocol` to reconcile the result for its consumer.
   An unsigned step of the set (`gaia approvals show` marks it
   `"signed": false`) runs in its position like any other command of yours:
   it holds no index, so it neither spends nor advances the signature.
5. A call the host refused before running it (Claude Code's
   `Permission to use Bash ... has been denied`) spends nothing: the item
   keeps its index. Retry the same sealed bytes once, as a new call; if the
   host refuses again, stop and report the refusal with the approval id
   instead of requesting anew. A call that never reports back is recorded as
   no result, not as a failure, and does not advance the set. Its item stays
   reserved for 10 minutes and refuses a retry meanwhile; after that a new
   call may run the same sealed bytes under the same signature while its
   window lasts (`gaia/store/writer.py::reserve_plan_command`). No result is
   not proof that the command did not run: read the target's state before
   retrying.
   A request made with `request-set` is a set even with one command, so its
   first undeclared non-zero exit freezes it. Only a signature decided on the
   request a block sealed by itself -- the user's `gaia approvals approve <id>`
   from a terminal -- is a single-command one: its command is given back once
   after a non-zero exit, for the same sealed bytes in the same directory
   while its window lasts (`gaia/store/writer.py::retry_db_semantic_grant`).
   To leave a stalled set behind, request what still has to run: the new
   request revokes the old signature (`subagent-request-approval`).
6. After successful mutations, verify desired state with separate read-only
   checks. Success exit codes alone are insufficient.
7. Checkpoint verification and emit `NEEDS_VERIFICATION` for a plan-task-bound
   producer; only an eligible unbound turn/verifier may reach `COMPLETE`.

This skill sequences the approved work; it does not duplicate the failure rule
or the consumer reconciliation owned by those two skills.
