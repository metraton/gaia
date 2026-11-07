---
name: agent-protocol
description: Use when producing any agent response
---

# Agent Protocol

Every agent turn writes one contract -- what it was asked, what it found, what it changed, how it ended -- during the turn, not composed at the close. The twelve principles run that arc, ground to close.

## 1. Your contract is the delivery; your final message is only the signal that the turn ended

The gate validates only your persisted contract: one found unfinalized -- or no row at all -- rejects the close however complete your message reads (`hooks/adapters/subagent_stop_core.py::_resolve_subagent_stop_gate_full`). The message carries no copy. Address it to the orchestrator, in two lines: the closing state and the `contract_id` -- nothing the row already holds.

## 2. You were born with a contract -- adopt it, do not create another

`# Your Contract` names it on a fresh dispatch, a `# Contract Draft (resumed)` block on a resumed one; your first `gaia contract set/add/fill --draft-id <contract_id>` adopts that draft, and every later call carries `--draft-id` too -- omitted, the CLI targets the most recently touched draft, which is not necessarily yours. The harness stamps `agent_status.agent_id`. `AGENT_ID_FORMAT` (`gaia/contract/validator.py::FormErrorCode`) checks the identifier's shape, never its ownership, so a stray `gaia contract init` mints a rival identity the gate accepts while your real contract is reaped unfinalized; a bare `init` fits exactly one turn, the one that arrived with no contract block at all.

## 3. Ground yourself before acting

Ask what already governs this, in precedence: injected context -> memory, queryable past the sample you were sent -> the code, which outranks any description of it -> the skills -> outside. A skill the code refutes is outranked by it AND is itself a finding: name the skill and the contradicting symbol in `open_gaps`, or the drift outlives your turn to be rediscovered by the next agent that trusts it. A goal often carries a COORDINATE -- a contract id, a memory slug, a brief name -- and `read-map.md`, beside this file, is the verb that opens it. Announce no tool sequence and claim no reach in advance.

The ladder orders where you look; what governs an object is its owner's implementation of the act. What you can read of an object from the side that consumes it is its inventory, never its procedure, and what a goal asserts about the world is a claim your reads may refute however it is filed -- only what it asks of your conduct binds you. A procedure for an object you do not own, whether you would run it or hand it on, goes in `cross_layer_impacts` naming the owner, never in your steps: written from the consuming side it is a flawless procedure for the wrong system, and whoever runs it becomes the only one left who could know better.

Injected context is also something your reads can refute. When a value you observed contradicts a section injected into your kernel or read with `gaia context get-contract`, propose the correction in `update_contracts` -- the section, a payload holding the new value, and a `drift` object `{key_path, old, new, command, observed_at}` whose `command` is the T0 read that observed it -- and a memory row it contradicts in `memory_delta`. Calling the context stale only in prose loses the correction: finalize warns when `report_prose` or `open_gaps` say so while `update_contracts` is empty.

That precedence settles claims about existing behavior, not the quality or permission
of that behavior. A normative requirement is not refuted merely because current code
violates it; use `code-standards` for that distinction when producing or judging code.

## 4. Local and reversible work just happens; what goes out into the world is asked once, and you run it

Commits, files and branches need no signature; operations that leave the local
workspace follow `security-tiers` classification. When that routes to T3,
`subagent-request-approval` owns request construction and `execution` owns the
post-grant lifecycle.

You run every command the work needs, a signed one included; a person receives
only what is irreducibly theirs -- an interactive login, a value only they hold
typed at a hidden prompt, a judgment that is theirs -- or a command a Gaia defect
keeps you from running, handed over named as the defect it is, in `open_gaps` and
as a proposal in `memory_delta`, so the handoff lasts until the defect is fixed. A
runbook written for a human operator describes what must happen, not who does it:
read it as the sequence of effects, run each step yourself, and request the T3
ones as one set through `subagent-request-approval`. The person's steps go in
`conclusion` as a block headed `Human steps:`, one step per line with why it is
theirs, each ready to paste and run with its values filled in, and only those -- the orchestrator reads a step an agent could run as the
cue to resume you with it (`agent-response`).

Approval is authority to attempt execution; it is never evidence that execution
happened. When consuming a failed COMMAND_SET result, reconcile and report the
completed indexes, the failed index, and untouched indexes as three distinct
states. A failed COMMAND_SET is terminal/frozen: route any retry or untouched
work through fresh investigation and the approval branch rather than inferring
that the original decision still authorizes it.

## 5. The record is written in flight, at the cadence of what would hurt to lose

If the turn is cut, only what you already wrote exists; everything else survives in the transcript as narrative and dies as evidence. Write a finding the instant re-deriving it would cost more than recording it, and be resumable before any step whose outcome you cannot predict -- a record composed at the close is a second telling, made under the pressure that ends the turn, and it drops fields. Seven lists carry it: `fill` each ONCE (it refuses to discard), `add` extends, `set` replaces, as the evidence arrives, never in one pass at the close: `files_checked`, `patterns_checked`, `commands_run`, `key_outputs`, `verbatim_outputs`, `open_gaps`, and `cross_layer_impacts` -- what your change reached outside the file you were sent to, which no other field records for you. `patterns_checked` is written the moment you SEARCH, not at the close, and above all when the search returns NOTHING: the file you opened comes back in `files_checked` and the command you ran comes back in `commands_run`, but a `grep` that matched zero lines is held by no other field -- unwritten as it happens, the negative it proves is gone. `commands_run` and `files_checked` are rebuilt by the harness at finalize (`gaia/contract/tool_record.py::stamp_harness_fields`); agent entries are kept as marked annotations.

`report_prose` sits beside these seven lists, never inside them: the why, the discovery order when it explains the result, the purpose frame, the synthesis judgment -- for the orchestrator and the next agent reading the row, never the end user. No evidence here, and no re-narrating a list.

A stable fact of this project that a future turn would otherwise re-derive and that neither your kernel nor a skill already carries (a tool URL, an account, an endpoint) you propose in `memory_delta` -- `{"version": 1, "proposals": [{"operation": "create", "create": {"slug", "type", "description", "body"}}]}`, the `create` fields as `gaia memory add --help` describes them -- and the orchestrator decides what is kept; you never write memory or project context yourself.

Each `key_outputs` entry is one coordinate or one verifiable claim -- a symbol, an id, a count, a path, a quoted line -- never a paragraph; a paragraph belongs to `report_prose`. `conclusion` is your verdict for the orchestrator, and every clean close carries it: `finalize` refuses a row without one and names the `set` call that fills it (`agent-contract-handoff`). Write it in your own technical register -- what holds, on what evidence, what is open -- for the orchestrator, which validates it against the row and the artifacts before it answers the user; nothing you write reaches the user's screen directly. Rows cut by the reaper, backstop or salvage are exempt.

## 6. Your progress is read from the record, not announced

The orchestrator sees where an unfinished turn stands by reading its row: `gaia contract view --draft-id <contract_id>` shows the evidence lists and `pending_steps` as they stand, and the harness record shows each tool call as it happens. A row written in flight (principle 5) reads as "four files in, two findings, one step left" rather than as an opaque box, and that decides whether the orchestrator waits or dispatches over the top of you. Keep `pending_steps` naming what is left; no separate phase field exists.

## 7. Every increment closes verified, and fixing starts by going back to the sources

Close each piece verified before starting the next, against the acceptance the kernel or the goal carries: run the assertion, re-run it after each fix until it holds or the gap is declared -- by result, never by exit code. What only judgment settles is named a rubric and left to the verifier, not looped on. A clear defect in your own deliverable you fix without asking, and say so in one line. Going deeper inside the goal rather than wider is `investigation`'s "Deeper, not bigger". Failures compound, and separating two entangled ones costs more than verifying the first. On failure, search before retrying: a rejected contract is a problem of form, reissued complete without re-investigating; a rejected operation is a problem of knowledge, and varying the attempt only stacks another state.

## 8. Before declaring yourself blocked, ask

Three doors come first: the answer is in the goal you received, in context or memory you can query, or it is a material choice only the user can make -- then close `NEEDS_INPUT` naming the concrete options. `BLOCKED` is the fourth and costliest: an answered question comes back with your draft unreset, while a block is routed to whoever owns the obstacle, with none of what you found.

## 9. The producer does not verify its own production

Evidence is the command's output, not your assertion about it: a `COMPLETE` needs a `pass` in `evidence_report.verification.result` (`VERIFICATION_RESULT`), and finalize refuses one whose `command` this turn never ran, or last ran with an exit other than the expected one, and that cites no evidence id (`agent-contract-handoff`). A plan-task-bound turn cannot seal itself (`hooks/adapters/subagent_stop_core.py::_blind_verification_required`); it closes `NEEDS_VERIFICATION`, and an independent verifier settles the task's gate -- never the row, which stays as it closed. `NEEDS_VERIFICATION` belongs to that turn alone: a turn with no `plan_task_id` and no `parent_handoff_id` closes `COMPLETE` with a verification it can stand behind (`type: none` when no oracle was required, `self_review` with `reviewed`, or a `command` the record shows ran), or `BLOCKED` / `NEEDS_INPUT` naming why not -- finalize refuses it otherwise (`gaia/contract/crosscheck.py::validate_verification_binding`).

## 10. Every turn closes by declaring a state

`IN_PROGRESS`, `BLOCKED`, `NEEDS_INPUT`, `APPROVAL_REQUEST`, `NEEDS_VERIFICATION`, `COMPLETE`; only `COMPLETE` is terminal, anything outside the six is rejected (`PLAN_STATUS` in `gaia/contract/validator.py::FormErrorCode`). The row itself is born `DISPATCHED`, hook-written and never yours, and keeps it until finalize, the stop hook or the reaper closes it. Set `agent_status.agent_state` to the closing value, then `gaia contract finalize --draft-id <contract_id>` last: it is the only promotion of that row to a clean close, and it refuses `IN_PROGRESS` (`bin/cli/contract.py::cmd_finalize`). Add `--plan-task-id <id>` when the turn executes a plan task, and never pass `--session-id` unless the dispatch handed one over -- the born row already carries it, and an invented `unknown` corrupts it.

## 11. Degrading honestly costs less than faking

What you could not do, what you did not verify and what stayed open go in the record: a gap declared gets routed, a gap hidden surfaces later. A complete-looking turn with one invented field is worth nothing.

## 12. Your worktree is scoped to what you touch

A turn that writes to a repository works in its own worktree (`gaia worktree create`, released with `gaia worktree release` at close) while a turn that only reads stays on the live tree -- `dispatch` and its `reference.md` hold the mechanics and the reasons.

## Where to go next
- `reference.md` -- the argument behind each principle, the state machines, storage and recovery, what the gate rejects; `agent-contract-handoff` -- envelope schema; `examples.md` -- filled envelopes; `read-map.md` -- read verbs. Then `investigation` -> `security-tiers` -> `command-execution` for one operation or `subagent-request-approval` -> `execution` for a COMMAND_SET; orchestrator-side: `orchestrator-present-approval`, `pending-approvals`, `agent-response`.
