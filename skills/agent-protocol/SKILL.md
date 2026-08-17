---
name: agent-protocol
description: Use when producing any agent response
---

# Agent Protocol

Every agent turn writes one contract -- what it was asked, what it found, what it changed, how it ended
-- during the turn, not composed at the close. The eleven principles run that arc, ground to close.

## 1. Your contract is the delivery; your final message is only the signal that the turn ended

The gate validates only your persisted contract: one found unfinalized -- or no row at all -- rejects the
close however complete your message reads (`_resolve_subagent_stop_gate_full`). The message carries no copy.

## 2. You were born with a contract -- adopt it, do not create another

`# Your Contract` names it on a fresh dispatch, a `# Contract Draft (resumed)` block on a resumed one;
your first `gaia contract set/add/fill --draft-id <contract_id>` adopts that draft, and every later
call carries `--draft-id` too -- omitted, the CLI targets the most recently touched draft, which is
not necessarily yours. Copy `agent_id` verbatim into `agent_status.agent_id`. `AGENT_ID_FORMAT` checks
the identifier's shape, never its ownership, so a stray `gaia contract init` mints a rival identity
the gate accepts while your real contract is reaped unfinalized; a bare `init` fits exactly one turn,
the one that arrived with no contract block at all.

## 3. Ground yourself before acting

Ask what already governs this, in precedence: injected context -> memory, queryable past the sample
you were sent -> the code, which outranks any description of it -> the skills -> outside. A goal often
carries a COORDINATE -- a contract id, a memory slug, a brief name -- and `read-map.md`, beside this
file, is the verb that opens it. Announce no tool sequence and claim no reach in advance.

## 4. Local and reversible work just happens; what goes out into the world is asked once

Commits, files and branches need no signature; pushes, PRs, applies and every other exit into the
world go into one ordered COMMAND_SET under a single signature, written out exactly in advance --
`security-tiers` owns what may be grouped. A failed COMMAND_SET is terminal/frozen, remainder and all.

## 5. The record is written in flight, at the cadence of what would hurt to lose

If the turn is cut, only what you already wrote exists; everything else survives in the transcript as
narrative and dies as evidence. Write a finding the instant re-deriving it would cost more than
recording it, and be resumable before any step whose outcome you cannot predict -- a record composed
at the close is a second telling, made under the pressure that ends the turn, and it drops fields.
Seven lists carry it, each written with `gaia contract set/add/fill --draft-id <contract_id>` as the
evidence arrives, never in one pass at the close: `files_checked`, `patterns_checked`, `commands_run`,
`key_outputs`, `verbatim_outputs`, `open_gaps`, and `cross_layer_impacts` -- what your change reached
outside the file you were sent to, which no other field records for you. `patterns_checked` is written
the moment you SEARCH, not at the close, and above all when the search returns NOTHING: the file you
opened comes back in `files_checked` and the command you ran comes back in `commands_run`, but a `grep`
that matched zero lines is held by no other field -- unwritten as it happens, the negative it proves is gone.

`report_prose` sits beside these seven lists, never inside them: it carries the why (a hypothesis
dropped, a path chosen over another), the discovery order when it explains the result, the purpose
frame this turn served inside something larger, and the synthesis judgment answering the assignment's
question -- for the orchestrator and the next agent reading this row by coordinate, never the end
user. No evidence belongs here: a figure, a command, a path, or a literal output that lives ONLY in
`report_prose` is in the wrong field, and re-narrating what a list already states is not written at
all. (`user_facing_summary`, in `agent-contract-handoff`, is the separate end-user-facing line; the
two coexist and only this one is defined here.)

## 6. The phase is declared before doing that phase's work

Write `framing`, `investigating`, `planning`, `executing` or `verifying` before that phase's work, not
after: reading as "investigating, four files in" rather than as an opaque box decides whether the
orchestrator waits or dispatches over the top of you. Write only the phases you entered.

## 7. Every increment closes verified, and fixing starts by going back to the sources

Close each piece verified before starting the next, by result and never by exit code -- failures
compound, and separating two entangled ones costs more than verifying the first. On failure, search
before retrying: a rejected contract is a problem of form, reissued complete without re-investigating;
a rejected operation is a problem of knowledge, and varying the attempt only stacks another state.

## 8. Before declaring yourself blocked, ask

Three doors come first: the answer is in the goal you received, in context or memory you can query,
or it is a material choice only the user can make -- then close `NEEDS_INPUT` naming the concrete
options. `BLOCKED` is the fourth and costliest: an answered question comes back with your draft
unreset, while a block is routed to whoever owns the obstacle, with none of what you found.

## 9. The producer does not verify its own production

Evidence is the command's output, not your assertion about it: a `COMPLETE` needs a `pass` in
`evidence_report.verification.result` (`VERIFICATION_RESULT`), which cannot tell a fabricated one from
a real one. A plan-task-bound turn cannot seal itself (`_blind_verification_required`); it closes
`NEEDS_VERIFICATION` for an independent verifier.

## 10. Every turn closes by declaring a state

`IN_PROGRESS`, `BLOCKED`, `NEEDS_INPUT`, `APPROVAL_REQUEST`, `NEEDS_VERIFICATION`, `COMPLETE`; only
`COMPLETE` is terminal, anything outside the six is rejected (`PLAN_STATUS`). Set `agent_status.agent_state`
to the closing value, then `gaia contract finalize --draft-id <contract_id>` last: it is the only
promotion of that row to a clean close, and it refuses `IN_PROGRESS` (`cmd_finalize`). Add
`--plan-task-id <id>` when the turn executes a plan task, and never pass `--session-id` unless the
dispatch handed one over -- the born row already carries it, and an invented `unknown` corrupts it.

## 11. Degrading honestly costs less than faking

What you could not do, what you did not verify and what stayed open go in the record: a gap declared
gets routed, a gap hidden surfaces later. A complete-looking turn with one invented field is worth nothing.

## Where to go next

- `reference.md` -- the argument behind each principle, keyed by number, plus the state machines, the kernel, storage and recovery, what the gate rejects, and the edge cases. `agent-contract-handoff` -- envelope fields and rules; `examples.md` -- filled envelopes, state by state; `read-map.md` -- what a turn can read, with which verb.
- `investigation` -- evidence and mutation forecasting; then `security-tiers` -> `command-execution` for one operation, or `subagent-request-approval` (payload `agent-approval-protocol`) -> `execution` for a COMMAND_SET. Orchestrator-side: `orchestrator-present-approval`, `pending-approvals`, `agent-response`.
