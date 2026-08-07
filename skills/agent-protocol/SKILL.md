---
name: agent-protocol
description: Use when producing any agent response
---

# Agent Protocol

This is the producer workflow. It routes to reference and branch skills; it does
not duplicate their schemas. `agent-contract-handoff` owns the envelope,
`investigation` owns evidence gathering, and the approval skills own T3 data.

**Two senses of "contract" meet in this skill -- keep them apart.** The
*handoff contract* is the envelope this skill produces: one row born at
dispatch under `# Your Contract`, mutated by `gaia contract set/add/fill`,
closed by `finalize`. A *project-context contract* is a different thing: a
slice of project knowledge stored per workspace
(`project_context_contracts`), pulled on demand via `gaia context get`
within the `can_read` scope the kernel names -- it is NOT injected as input.
`agent-contract-handoff` documents the distinction in full. Everywhere
below, "contract" means the handoff envelope unless said otherwise.

**Two orthogonal machines run through a turn.** `agent_status.agent_state` is
the COMMUNICATION state machine -- how this turn currently reports back
(`IN_PROGRESS`/`BLOCKED`/`NEEDS_INPUT`/`APPROVAL_REQUEST`/
`NEEDS_VERIFICATION`/`COMPLETE`). It feeds routing and the finalize/
verification gate, a pure function of `(agent_state, plan_task_id)`, and nothing
below widens that enum or that gate. `work_phase` is the separate WORK state
machine -- where the producer is in framing -> investigating -> planning ->
executing -> verifying. The two never collapse: a turn can sit at
`agent_state=IN_PROGRESS` through every one of its five work phases.

## This row is your contract

`# Your Contract` names a contract that already exists as a row in gaia.db
AND as an on-disk draft before you run anything -- this turn's contract is
not something you create, it is something you were handed, ALREADY OPEN. You
do not run `gaia contract init`. Each field of the block means one thing:

- `contract_id` -- the id you pass on every `gaia contract` call, as
  `--draft-id <contract_id>`. It addresses both the row and the draft.
- `agent_id` -- copied VERBATIM into `agent_status.agent_id` whenever you
  declare state. Shape: `a` plus at least 16 lowercase hex characters.
- `goal` -- the assignment, whole and bounded: nothing outside it belongs to
  this turn.
- `role` / `surface` -- your relationship to the task and the surface that
  owns it.
- `project` (when present) -- the project this turn is about, as
  `name (/abs/path)`: the project to pull context for on demand. It is
  dispatch data first (the orchestrator's `project=<name>` token), with
  cwd-based resolution only as fallback; a name the substrate does not know
  yet appears bare, without the `(path)` suffix. Absent when the dispatch
  named no project and the cwd matched none.
- `can_read` / `can_write` -- your project-context scope: `can_read` is the
  MENU of sections you may pull on demand (`gaia context get --section <s>`)
  and cite as evidence; `can_write` names the ones you may propose updates
  to. Nothing from that menu arrives preloaded.
- `plan_task_id` + `acceptance` (present only on a plan-task-bound turn) --
  the task binding and its gates: the verifiable floor the increment must
  clear. A turn with them cannot self-COMPLETE; it closes NEEDS_VERIFICATION.

**Project context is NOT preloaded.** The kernel deliberately carries data
about your contract, not the project. Pull what you need on demand through
the commands the `# Your CLI` block names -- `gaia context get --section <s>`
for project knowledge, `gaia memory search` for curated memory -- and only
the sections your scope and your goal actually require.

**Your contract is your primary information tool, not just your deliverable.**
Write into it as you advance, and consult it when you need to recall what you
already verified, which commands you ran, and what you ruled out. What is in
your contract is already evidence: do not produce it again.

**Your first write is your adoption.** There is no separate "adopt" step: the
moment you run `gaia contract set/add/fill --draft-id <contract_id>` (or
`view`), you are writing the draft that was opened for you. `gaia contract
init` still exists, but only as THE fallback for a turn that received no
`# Your Contract` block at all -- a resumed session, a dispatch outside this
harness, or a dispatch whose born row could not be claimed at start. Run bare
`gaia contract init` (no `--agent-id`), reuse what it mints, and work the
protocol identically; the unclaimed born row (if one exists) is closed by the
stop-hook persister, never by you. Never mint a competing identity once one
exists.

## The envelope as a form, filled one section per moment

Read the contract as a form with one section active at a time, not a
checklist of commands to run in order. This is the same cycle "Work the
increment" below walks in full; here is where each moment's answer lives:

| Moment (`work_phase`) | Section you write | |
|---|---|---|
| framing | *(no section of its own)* | its output is one `key_outputs` note -- see step 2 of "Start the turn" below. This is the one moment without a dedicated field. |
| investigating | `evidence_report` (`patterns_checked`, `files_checked`, `commands_run`, `key_outputs`, `verbatim_outputs`, `open_gaps`) | keeps accumulating once execution starts too -- it is not owned by investigating alone |
| planning | `agent_status.pending_steps`; `approval_request` when a COMMAND_SET is required | `approval_request` opens here (plan-first) and is written to again once executing starts |
| executing | `evidence_report` continued; COMMAND_SET's typed progress fields; `failure_report` if something breaks | |
| verifying | `evidence_report.verification`; `consolidation_report` for multi-surface work; the terminal `agent_state` decision | |

Do not force a clean one-section-per-phase mapping onto this table --
the schema does not carry one. `evidence_report` and `approval_request` each
span two moments by design, and `framing` has none of its own. What the table
teaches is where to look, not a rule that every section belongs to exactly one
phase. `agent_state` (how the turn currently reports back) and `work_phase`
(where the work itself is) stay the two orthogonal axes the intro above
already names; this table is a reading aid for the second axis, not a new rule
on the first.

## Start the turn

1. Read whatever context was injected first -- it is already paid for, do not
   re-derive it with a tool call. What was NOT injected is on demand: a
   `# Your CLI` block means project context is pulled per section
   (`gaia context get --section <s>`) when the goal needs it, not scanned up
   front.
2. **FRAME**, before any other work: restate the goal in your own words, and
   check what you can actually do against the project context you just read
   ("what can I do with this"). Record both in one write, e.g.
   `gaia contract fill --json '{"work_phase":"framing","evidence_report":
   {"key_outputs":["FRAME: <goal restated>; capability: <what the context
   lets you do>"]}}'`. This step always happens -- it is one cheap call, not
   the ritual the phase floor below exempts. A turn that never leaves framing
   (answered entirely from already-known context) sets it once and goes
   straight to `COMPLETE` -- that name is the `agent_state`, never a shortcut
   past the close itself: `gaia contract finalize` still runs before the
   fence, on this turn exactly as on any other. See "Checkpoint and close".
3. Checkpoint by value at risk, not by phase, for everything WITHIN a phase:
   write a finding the instant re-deriving it would cost more than recording
   it, and before any step whose outcome you cannot predict (a long synthesis,
   an approval handoff, a mutation, the final message) confirm you are
   already resume-complete -- checkpoint first if not. Use `contract add`,
   `set`, or `fill --json`. This is the WITHIN-phase rule; the transition
   ITSELF between phases is never optional -- see "Work the increment".

## Work the increment

**The phase-transition floor.** The instant work enters a new phase --
investigating, planning, executing, verifying -- write `work_phase` before
doing that phase's work: `gaia contract set work_phase <phase>` (or fold it
into a `fill --json` alongside real evidence). This is a floor, independent of
and beneath the value-at-risk rule above: value-at-risk still governs what
ELSE gets checkpointed once inside a phase, but the transition mark itself is
never skipped when the phase genuinely exists. `work_phase` is optional at the
schema level (`WORK_PHASE_SHAPE` only fires when it is present and malformed)
precisely so a phase that does not exist for this turn is never simulated: a
one-file read that answers the question during framing never touches
`investigating`/`planning`/`executing`/`verifying` at all. Do not write a
phase you did not enter just to satisfy a checklist.

### investigating

Load `investigation` for the evidence ladder, the mutation forecast, and its
own checkpoint-by-value-at-risk guidance (that guidance operates WITHIN this
phase, not instead of the transition floor above).

### planning

Before touching mutation, answer these explicitly -- write the answers, do not
only think them:

| Question | Where the answer lives |
|---|---|
| What does "done" mean here? | A `key_outputs` note stating the completion criterion, or the `evidence_report.verification` plan |
| How many independent pieces are there, and in what order? | `agent_status.pending_steps` -- write the ordered list; see below |
| What predictable T3 mutations does the COMPLETE plan require, and do they fit one COMMAND_SET? | `investigation`'s forecast step: apply its grouping conditions (one bounded goal, exact known order, coherent risk/rollback/verification, no item depending on unseen output) |
| How is each piece verified -- by result, never by exit code alone? | `evidence_report.verification`, or a note per `pending_steps` entry |
| What open_gaps do you declare before executing? | `evidence_report.open_gaps` |

This checklist exists to stimulate consolidating a plan's mutations into one
`request-set` when `investigation`'s conditions already hold -- it stimulates,
it does not force a set where the commands genuinely are not knowable together.

Close planning by writing the resulting task list to `agent_status.pending_steps`
(this is its home: the ordered list of remaining pieces) and setting
`work_phase` to `planning` if not already there.

### pending_steps is the progress meter

`agent_status.pending_steps` carries the plan planning just wrote. As each
piece finishes, retire it from the list with `gaia contract set
agent_status.pending_steps '[...]'` (the remaining entries only) -- the list
shrinking is what a mid-flight observer polling the row sees as progress. This
is not a new rule: `COMPLETE` already requires `pending_steps == []`
(`COMPLETE_SHAPE`); this is that existing requirement read forward, as a
running measure, instead of only backward, as a terminal check.

### executing / verifying

`work_phase` moves to `executing` once the first piece starts, and to
`verifying` once mutation is done and the result is being confirmed by a
separate read, not assumed from an exit code. Meanwhile `agent_state`
continues to report the turn's own communication status through all of it:
`IN_PROGRESS` means more work can continue. `BLOCKED` means an external
obstacle prevents progress. `NEEDS_INPUT` asks for a material user choice.
`APPROVAL_REQUEST` hands off exact T3 consent. `NEEDS_VERIFICATION` hands a
producer result to an independent verifier. Only `COMPLETE` is terminal.

Before mutation, finish the read-only investigation and forecast the commands
the accepted plan predictably requires. COMMAND_SET is plan-first informed
consent: collect an exact, ordered, coherent set of predictable T3 commands;
never include speculative commands. Each item is one atomic shell invocation,
not a compound command. Group commands only when they serve one bounded goal,
share a clear risk/rollback/verification story, and their exact text is already
known. Do not group unrelated effects, unknown follow-ups, condition-dependent
commands, or work whose next command depends on earlier output.

Create the request set before attempting its commands:

```
gaia approvals request-set --command '<exact T3 command 1>' --command '<exact T3 command 2>' --rationale '<bounded goal>' --agent-id <agent_id> --session-id <session_id>
```

Then follow `subagent-request-approval`. A command that was attempted and
blocked follows the single-command relay branch instead. Never evade a block,
mint an approval id, or calculate an approval fingerprint yourself.

## Branch routing

| Situation | Load / action |
|---|---|
| Need exact envelope fields | `agent-contract-handoff` |
| Need evidence or mutation forecast | `investigation` |
| Run one shell operation | `command-execution`, then `security-tiers` |
| T3 set planned or command blocked | `subagent-request-approval` |
| Orchestrator receives a request | `orchestrator-present-approval` |
| User names an old/pending id | `pending-approvals` |
| Consent was granted | fresh specialist dispatch with `execution` |
| Orchestrator receives a contract | `agent-response` |

## Checkpoint and close

**Declare your closing state IN the contract before you close it.** The write
just before `finalize` sets `agent_status.agent_state` to the state your final
message will declare -- `COMPLETE`, `NEEDS_VERIFICATION`, `BLOCKED`,
`NEEDS_INPUT`, or `APPROVAL_REQUEST` -- never the `IN_PROGRESS` your last
checkpoint left behind. The row is the record: a fence that says COMPLETE over
a row finalized IN_PROGRESS closes the record in limbo -- neither cut nor
closed, invisible to both `contract list --cut` and the terminal-state reads
(observed live: a clean close, `cut_reason` NULL, state IN_PROGRESS).
`finalize` rejects an `IN_PROGRESS` draft for exactly this reason: fix the
state, then close.

**Finalize is a floor, not a phase: every turn ends with it, including the
shortest one.** A turn closing `COMPLETE` has, by construction, an empty
`pending_steps` (the progress meter above having shrunk to nothing). A turn
that mutated typically also carries a final `work_phase` of `verifying` --
but `work_phase` itself is never a `COMPLETE` precondition; only
`agent_state`'s own shape rules (`COMPLETE_SHAPE`, `VERIFICATION_RESULT`) gate
the close. A turn that never mutated (pure investigation, answered entirely
from framing) may close `COMPLETE` having set no phase past `framing` at all
-- there is nothing to verify, but there is still a row to close, and closing
it is `finalize`, not the fence.

Approval data belongs only inside `approval_request`; do not copy command
payloads into evidence fields.
Use the runtime's typed progress fields when present and otherwise record exact
per-command results in evidence. A failed COMMAND_SET is terminal/frozen:
preserve completed, failed, and untouched indexes, then require fresh
investigation and a new approval for every retry or remainder. Capture the exact
command, exit status, and stderr; do not claim the effect occurred.

`gaia contract validate --draft-id <draft_id>` checks the draft without writing
a handoff row. `gaia contract finalize --draft-id <draft_id> ...` validates and
persists/converges the born row idempotently. Finalize does not imply COMPLETE:
it accepts any CLOSING state -- `APPROVAL_REQUEST`, `BLOCKED`, `NEEDS_INPUT`,
`NEEDS_VERIFICATION`, `COMPLETE` -- each a real end of the turn that the
orchestrator routes. What it refuses is `IN_PROGRESS`, the one state that says
the turn has NOT ended (see the floor at the top of this section). Only a
coherent, verified `COMPLETE` is terminal; a plan-task-bound producer must use
`NEEDS_VERIFICATION`.

**`gaia contract finalize` is your last tool call, every turn, with no
exception for a short one.** The row being born open at dispatch, and
`set`/`add`/`fill` mirroring your evidence onto it as you build the draft,
both describe how the row gets FILLED -- neither one closes it. A turn that
stops after its last `fill` and goes straight to the fence leaves the row
exactly where the draft left it: open. A SubagentStop hook backstop exists and
will eventually converge that row with `cut_reason=reaped` -- that is forensic
cleanup for a turn that failed to close itself, not a second way to close one:
it never earns `COMPLETE`, it only records that `finalize` was never called.
Then emit the same JSON envelope in a fenced `agent_contract_handoff` block.
The fence remains required by the current stop gate even though the DB row is
authoritative; treat this as compatibility debt, not a second protocol or a
place to reconstruct a different contract.

```agent_contract_handoff
{"agent_status":{"agent_state":"IN_PROGRESS","agent_id":"a0000000000000000","pending_steps":[],"next_action":"continue"},"evidence_report":{"patterns_checked":[],"files_checked":[],"commands_run":[],"key_outputs":[],"verbatim_outputs":[],"cross_layer_impacts":[],"open_gaps":[]},"consolidation_report":null,"approval_request":null}
```
