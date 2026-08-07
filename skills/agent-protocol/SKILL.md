---
name: agent-protocol
description: Use when producing any agent response
---

# Agent Protocol

Every agent turn writes one contract: the record of what it was asked to do, what it found, what it
changed, and how it ended. This skill is how that contract gets written -- during the turn, while the
work is still happening. A check at the end guarantees its shape; what no check can see is how the
turn ran, and that is what this skill owns. The eleven principles below run that arc -- ground, plan,
work, verify, close -- and each carries its rule and what goes wrong without it.

## 1. Your contract is the delivery; your final message is only the signal that the turn ended

The gate validates only your persisted contract: an unfinalized one rejects the close however
complete your message reads (`_resolve_subagent_stop_gate_full`). Still end the message with the
envelope in a fenced `agent_contract_handoff` block -- not because the gate falls back to it, but
because `parse_contract` still feeds it to the turn's descriptive readers: episode metrics,
`key_outputs`, `update_contracts`, response-contract anomalies, and the T9 backstop.

## 2. You were born with a contract -- adopt it, do not create another

`# Your Contract` names it on a fresh dispatch, a `# Contract Draft (resumed)` block on a resumed one;
your first `gaia contract set/add/fill --draft-id <contract_id>` adopts that draft. `AGENT_ID_FORMAT`
checks the identifier's shape, never its ownership, so a stray `gaia contract init` mints a rival
identity the gate accepts while your real contract is reaped unfinalized.

## 3. Ground yourself before acting

Ask what already governs this, in precedence: injected context -> memory, queryable past the sample
you were sent (`gaia memory search`) -> the code, which outranks any description of it -> the skills
-> outside. Out of that order the failure is a right answer in the wrong idiom that the next reader
must reconcile. Announce no tool sequence and claim no reach in advance: both are commitments made by
the part of the turn that knows least, and both are read downstream as established.

## 4. Local and reversible work just happens; what goes out into the world is asked once

Commits, files and branches need no signature; pushes, PRs, applies and every other exit into the
world go into one ordered COMMAND_SET under a single signature, written out exactly in advance. Asking
per command makes the user a keystroke-approver whose consent is no longer informed; a set spanning
two goals fails halfway and leaves a terminal grant, a dead remainder and a half-applied change.

## 5. The record is written in flight, at the cadence of what would hurt to lose

If the turn is cut, only what you already wrote exists; everything else survives in the transcript as
narrative and dies as evidence. Write a finding the instant re-deriving it would cost more than
recording it, and be resumable before any step whose outcome you cannot predict -- a record composed
at the close is a second telling, made under the pressure that ends the turn, and it drops fields.

## 6. The phase is declared before doing that phase's work

Write `framing`, `investigating`, `planning`, `executing` or `verifying` before that phase's work, not
after: reading as "investigating, four files in" rather than as an opaque box decides whether the
orchestrator waits or dispatches over the top of you. Write only the phases you entered -- one added
for a checklist states something that did not happen, which a reader cannot tell from a true one.

## 7. Every increment closes verified, and fixing starts by going back to the sources

Close each piece verified before starting the next, by result and never by exit code: failures
compound, and separating two entangled ones costs far more than verifying the first would have. On a
failure, search before retrying -- a rejected contract is a problem of form, reissued complete without
re-investigating, while a rejected operation is a problem of knowledge, where varying the attempt only
stacks another unexplained state on the one you could not explain.

## 8. Before declaring yourself blocked, ask

Three doors come first: the answer is in the goal you received, in context or memory you can query,
or it is a material choice only the user can make -- then close `NEEDS_INPUT` naming the concrete
options. `BLOCKED` is the fourth and costliest: an answered question comes back with your draft
unreset, while a block is routed to whoever owns the obstacle, with none of what you found.

## 9. The producer does not verify its own production

Evidence is the command's output, not your assertion about it: `VERIFICATION_RESULT` requires a `pass`
on a `COMPLETE` and cannot tell a fabricated one from a real one. A plan-task-bound turn cannot seal
itself (`_blind_verification_required`); it closes `NEEDS_VERIFICATION` for an independent verifier.

## 10. Every turn closes by declaring a state

`IN_PROGRESS` (work can continue), `BLOCKED`, `NEEDS_INPUT`, `APPROVAL_REQUEST`, `NEEDS_VERIFICATION`,
`COMPLETE`; only `COMPLETE` is terminal and anything outside the six is rejected (`PLAN_STATUS`). Set
the closing state, then `gaia contract finalize --draft-id <contract_id>` last -- it refuses
`IN_PROGRESS` (`cmd_finalize`).

## 11. Degrading honestly costs less than faking

What you could not do, what you did not verify and what stayed open belong in the record: a gap
declared gets routed, a gap hidden is found later by whoever already built on the claim it was closed.
A partial turn that says so is worth exactly its evidence; a complete-looking turn with one invented
field is worth nothing, because a reader who catches one cannot bound how many others there are.

## Where to go next

- `reference.md` -- the argument behind each principle, keyed by number, plus the state machines, the kernel, storage and recovery, what the gate rejects, and the edge cases.
- `agent-contract-handoff` -- envelope fields and rules; `examples.md` -- filled envelopes, state by state.
- `investigation` -- evidence and mutation forecasting; `security-tiers` then `command-execution` -- one operation.
- `subagent-request-approval` (payload `agent-approval-protocol`) then `execution` -- a COMMAND_SET.
- Orchestrator-side: `orchestrator-present-approval`, `pending-approvals`, `agent-response`.
