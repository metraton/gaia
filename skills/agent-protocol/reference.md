# Agent Protocol -- Reference

Mechanics behind `SKILL.md`: the argument standing behind each of the eleven
principles, the table of what the gate rejects, the two state machines, where
each moment's answer lands in the envelope, the dispatch kernel field by field,
how the contract is stored and recovered, and the edge cases. Read on demand;
the principles in `SKILL.md` are what a turn needs to run.

## What the gate will reject

| Rejected | Enforced by |
|---|---|
| An `agent_id` that is not `a` plus 16+ lowercase hex characters | `AGENT_ID_FORMAT` |
| An `agent_state` outside the six | `PLAN_STATUS`, against `VALID_PLAN_STATUSES` (`gaia/state/__init__.py`) |
| `COMPLETE` with a non-empty `pending_steps`, or `next_action` other than `"done"` | `COMPLETE_SHAPE` |
| `COMPLETE` whose `evidence_report.verification.result` is not `"pass"` | `VERIFICATION_RESULT` |
| `APPROVAL_REQUEST` without a non-empty `approval_request.exact_content` | `APPROVAL_REQUEST_SHAPE` |
| `COMPLETE` while `loop_state` still has iterations left below threshold | `_check_loop_state_blocking` (`hooks/modules/agents/contract_validator.py`) |
| `COMPLETE` on a turn whose dispatch binding carries a `plan_task_id`, at both seams | `_blind_verification_required` (`hooks/adapters/claude_code.py`) and `cmd_finalize` (`bin/cli/contract.py`) |
| A close whose persisted contract is unfinalized, however complete the final message | `_resolve_subagent_stop_gate_full` (`hooks/adapters/claude_code.py`) |
| `finalize` on a draft still declaring `IN_PROGRESS` | `cmd_finalize` (`bin/cli/contract.py`) |

Unqualified names above are `FormErrorCode` members returned by `validate_form`
in `gaia/contract/validator.py`.

## The argument behind each principle

`SKILL.md` states each principle as its rule and its consequence -- what it asks
and what goes wrong when it is violated. What follows is the elaboration behind
it: the measured evidence, the worked cases, the extended why, keyed by the same
number. The eleven run the arc of a turn:

```
Ground   what already governs this -- injected context, memory, the code, the skills
Plan     what "done" means, what the pieces are, which exits need one signature
Work     declare the phase, write each finding as you reach it
Verify   close each increment verified; on failure, back to the sources
Close    declare a state, finalize, degrade honestly
```

Two doors open onto that arc. **A fresh dispatch** arrives with a kernel --
`# Your Contract`, `# Your CLI`, `# What I know about you`. Your contract is
named there, already open. Start at Ground.

**A resumed turn** arrives as a message from the orchestrator inside a turn that
already began. There is no second kernel: `# Your Contract` is rendered once, at
dispatch. What arrives instead is a `# Contract Draft (resumed)` block naming
your draft id and stating that the draft was not reset -- it is the contract you
already adopted, still open, still holding everything you wrote before the
interruption. Read it back (`gaia contract view --draft-id <contract_id>`)
rather than re-deriving it, and pick the cycle up at the phase your last write
left.

### 1. The contract is the delivery; the final message only signals the end

Finishing means leaving the contract written. The check at the end reads the
finished contract, so its shape is already guaranteed by something other than
you. What no check can see is how the turn ran: a turn that wrote nothing until
its last second and then closed perfectly passes every validation. That is what
the skill owns. "The gate at the wall" below sets out the three cases the stop
gate decides between, and why the fenced block is still required output.

### 2. Adopt the contract you were born with

It exists before you read your goal. `# Your Contract` shows it to you, and your
first `gaia contract set/add/fill --draft-id <contract_id>` adopts it; there is
no separate adoption step. Pass `--draft-id` on every later call and copy
`agent_id` verbatim.

The gate checks that identifier's shape. Nothing checks that it is *yours*. A
`gaia contract init` run on a turn that already has a contract mints a second,
well-formed identity that passes every validation and that nobody is watching:
your evidence lands on it, while the contract your dispatch is bound to stays
open, is reaped unfinalized, and is what the orchestrator reads when it asks how
your turn went. `init` is only for a turn that received no contract at all --
see "No contract at dispatch" under Edge cases.

### 3. Grounding, in precedence

Ask what pattern already governs what you are about to do, in precedence: what
was injected (already paid for -- do not re-derive it with a tool call) ->
memory, which is queryable and not merely received, since what arrived at
session start is a sample and `gaia memory search` reaches the rest -> the code
already written, which decides over any description of it -> the skills ->
outside.

That order is cost and authority at once, both running the same direction. The
failure it prevents is not a wrong answer; it is a right answer in the wrong
idiom -- a solution the system already decided differently about, which whoever
reads it next now has to reconcile. Reading the injected memory sample as the
whole archive is the ordinary form of this: the turn concludes something the
substrate already recorded against.

State no tool sequence and promise no reach. A sequence announced before the
first read is a commitment made by the part of the turn that knows least, and
reach claimed before it is exercised is read downstream as established.

### 4. One signature for what leaves the machine

Commit, write files, branch -- none of that needs a signature. Pushes, opening
the PR, applies and every other exit into the world go into one ordered set with
a single signature: a COMMAND_SET. Build it from what the plan already implies
and can be written out exactly in advance, never from what you discover as you
go. Keep it small and coherent -- one bounded operation.

Both halves have a cost. Asking per command turns the user into a
keystroke-approver, and consent granted that way has stopped being informed. But
grouping consent is not atomic execution: if the third of six fails, the grant is
terminal, the remaining three die with it, and you are back asking a second
signature for the remainder -- the interruption the set existed to prevent, now
with a half-applied change under it. A set spanning two goals therefore costs
more consent than two sets holding one each.

### 5. Cadence set by value at risk

If the turn is cut, only what you already wrote exists. A harness cut lands
mid-turn with no warning and no last message; the persister records it honestly
(`degraded` + `reaped`, in `hooks/modules/agents/handoff_persister.py`) and
cannot record work it never saw. Everything else survives in the transcript as
narrative and dies as evidence.

So the cadence is value at risk: write a finding the instant re-deriving it
would cost more than recording it, and before any step whose outcome you cannot
predict -- a long synthesis with no tool calls in it, an approval handoff, a
mutation, the final message -- confirm you are already complete enough to be
resumed from.

The same reason is why the close is not where the record gets composed: a
summary written from memory at the end is a second telling, produced under the
pressure that ends the turn, and it drops fields. What the contract already
holds gets queried later, at whatever granularity the question needs.

### 6. Declaring the phase before the phase's work

`framing`, `investigating`, `planning`, `executing`, `verifying` -- write the
phase before that phase's work, not after. It is what makes half a turn legible
from outside: a turn observed mid-flight either reads as "investigating, four
files in" or as an opaque box, and that difference decides whether the
orchestrator waits or dispatches again over the top of you.

Write only the phases you actually entered. A turn answered from framing alone
never touches the other four, and a phase written to satisfy a checklist makes
the record state something that did not happen -- worse than a gap, because a
reader cannot tell it from a true one. A phase's spelling is validated when it
is present (`WORK_PHASE_SHAPE`); that it is present at all is never checked.

### 7. Verified increments, and which search a failure calls for

Close each piece verified before starting the next: compounding failures grow
exponentially, and separating two entangled failures costs far more than
verifying the first one did. Verify by result -- an exit code says the command
ran, not that the state changed.

On a failure, search before retrying; do not vary the attempt. Which search
depends on what failed, and the two pull opposite ways:

| What was rejected | What to do | Why |
|---|---|---|
| Your **contract** | Reissue it complete, without re-investigating. Two attempts, maximum. | A problem of form. The knowledge is already in the record; re-running the investigation burns the context principle 5 was protecting and changes nothing about the rejection. |
| An **operation** | Go back to the sources before retrying. | A problem of knowledge. Retrying with variations is guessing, and each variation leaves another unexplained state on top of the one you could not explain. |

### 8. The three doors before blocking

Three doors come first: is the answer already in the goal you received? is it in
context, or in memory you can query? is it a material choice only the user can
make -- then close `NEEDS_INPUT` naming the concrete options, which is how the
question reaches them.

Blocking is the fourth door and the costliest. It and `NEEDS_INPUT` both end the
turn, but not alike: an answered question comes back to you, and the resume
hands your own draft back unreset, so the turn continues with everything it
gathered. `BLOCKED` says an external obstacle stands in the way, so the
orchestrator routes it to whoever owns that obstacle -- a different agent, from
zero, with none of what you found. Take it when no answer would unblock you, not
when you have not asked.

### 9. Why a self-declared pass is worth nothing

Evidence is the command's output, not your assertion about it. On a `COMPLETE`
the gate requires the verification field to say `pass`; nothing checks whether
it is true, so a fabricated pass passes -- and at the wall it is
indistinguishable from a real one, which is exactly why its whole cost lands on
whoever trusts it next. A turn bound to a plan task cannot seal itself: it
closes `NEEDS_VERIFICATION` and an independent verifier promotes it.

### 10. The closing state

`IN_PROGRESS` (work can continue), `BLOCKED`, `NEEDS_INPUT`, `APPROVAL_REQUEST`,
`NEEDS_VERIFICATION`, `COMPLETE`. Only `COMPLETE` is terminal. Set the closing
state in the contract, then `gaia contract finalize --draft-id <contract_id>` as
your last tool call. A rejection at this seam is a form problem, repaired the
way principle 7 says a rejected contract is -- reissue complete, do not
re-investigate.

### 11. Why the costs of degrading and faking are not symmetric

What you could not do, what you did not verify and what stayed open are part of
the record too. A gap declared is routed: the orchestrator dispatches it or puts
it to the user. A gap hidden is found later by whoever already built on the
claim that it was closed.

The costs are not symmetric. A partial turn that says so is worth exactly its
evidence. A complete-looking turn with one invented field is worth nothing,
because a reader who catches one has no way to bound how many others there are.

## The two state machines

A turn runs two machines at once, and they never collapse into one.

**`agent_status.agent_state` -- the communication machine.** How the turn
currently reports back. It feeds routing and the finalize gate.

| State | Meaning |
|---|---|
| `IN_PROGRESS` | Work can continue. Not a closing state -- `finalize` refuses it. |
| `BLOCKED` | An external obstacle prevents progress. |
| `NEEDS_INPUT` | A material choice only the user can make. |
| `APPROVAL_REQUEST` | Exact T3 consent is handed off. Requires a non-empty `approval_request.exact_content`. |
| `NEEDS_VERIFICATION` | A producer result handed to an independent verifier. |
| `COMPLETE` | Terminal, and the only terminal one. |

Canonical list: `VALID_PLAN_STATUSES` in `gaia/state/__init__.py`, re-exported
by `hooks/modules/agents/response_contract.py`.

**`work_phase` -- the work machine.** Where the producer is in the work itself:
`framing` -> `investigating` -> `planning` -> `executing` -> `verifying`
(`VALID_WORK_PHASES` in `gaia/contract/validator.py`).

The two are orthogonal: a turn sits at `agent_state=IN_PROGRESS` through every
one of its five work phases. `work_phase` is optional at the schema level --
`WORK_PHASE_SHAPE` fires only when the field is present and outside the enum --
which is what makes it safe to omit a phase that did not happen rather than
simulate it.

## Where each moment's answer lands

The envelope is a form with one section active at a time. This maps the work
machine's moments onto the sections they fill; several sections span more than
one moment, because the work does.

| Moment | Section |
|---|---|
| `framing` | No section of its own. Its output is one `evidence_report.key_outputs` note: the goal restated, and what the available context lets you do about it. |
| `investigating` | `evidence_report` -- `patterns_checked`, `files_checked`, `commands_run`, `key_outputs`, `verbatim_outputs`, `open_gaps`. Keeps accumulating once execution starts. |
| `planning` | `agent_status.pending_steps` (the ordered list of remaining pieces); `approval_request` when a COMMAND_SET is required -- opened here, plan-first. |
| `executing` | `evidence_report` continued; the runtime's typed COMMAND_SET progress fields; `failure_report` when something breaks. |
| `verifying` | `evidence_report.verification`; `consolidation_report` for multi-surface work; the closing `agent_state`. |

`pending_steps` doubles as the progress meter. Retire an entry as its piece
finishes (`gaia contract set agent_status.pending_steps '[...]'` with the
remaining entries only): the list shrinking is what an observer polling the
contract mid-flight sees as progress, and `COMPLETE_SHAPE` requires it empty at
the close anyway.

Keep approval payloads inside `approval_request`; do not copy command text into
evidence fields.

## The dispatch kernel, field by field

`# Your Contract` names a contract that already exists -- as a row in gaia.db
and as an on-disk draft -- before you run anything.

| Field | Meaning |
|---|---|
| `contract_id` | The id passed as `--draft-id <contract_id>` on every `gaia contract` call. It addresses both the row and the draft. |
| `agent_id` | Copied verbatim into `agent_status.agent_id` whenever state is declared. Shape: `a` plus 16 or more lowercase hex characters. |
| `goal` | The assignment, whole and bounded. Nothing outside it belongs to this turn. |
| `role` / `surface` | The turn's relationship to the task, and the surface that owns it. |
| `project` | The project this turn is about, as `name (/abs/path)`. Dispatch data first (the orchestrator's `project=<name>` token), cwd resolution only as fallback. A name the substrate does not know yet appears bare, with no path suffix. Absent when the dispatch named no project and the cwd matched none. |
| `can_read` / `can_write` | The menu of project-knowledge sections this turn may pull on demand and cite, and the ones it may propose updates to. Nothing from the menu arrives preloaded. |
| `plan_task_id` + `acceptance` | Present only on a plan-task-bound turn: the binding and the verifiable floor the increment must clear. Such a turn cannot self-`COMPLETE`; it closes `NEEDS_VERIFICATION`. |

### The agent_id floor is measured

`AGENT_ID_MIN_HEX = 16` in `gaia/contract/validator.py` is not a convention.
Cross-session handle collisions fall off a cliff with length, because a biased
model only collides where it can compress the digits it has to invent: 6 hex
collided on 27 of 82 handles (32.9%), 7 hex on 12 of 103 (11.7%), 17 hex on 0
of 2658 (0.0%). Sixteen is the smallest floor comfortably inside the
zero-collision regime, and is exactly what `secrets.token_hex(8)` produces --
which is what `gaia contract init` mints when no `--agent-id` is supplied.

The floor gates what an agent may mint for a NEW turn. Historical rows keyed by
a shorter handle are read back by exact string and never re-validated, so no
grandfathering window exists or is needed.

### Project knowledge is pulled, not preloaded

The kernel deliberately carries data about the turn, not about the project.
Read one section from the `can_read` menu with
`gaia context get-contract --section <s>` (`--workspace <w>` when the workspace
is not the cwd's; `--section` is required; `--text` for the human form). It
resolves against `project_context_contracts.contract_name` -- the exact names
the menu lists -- and exits 1 naming the available sections when the name does
not exist.

Its sibling `gaia context get --section <s>` is a different namespace: it
resolves against the workspace SHAPE (`apps`, `services`, `git`,
`environment`, ...) and never reaches these sections. The names overlap without
meaning the same thing -- `gaia context get --section stack` returns the
shape's empty scanner placeholder `{}`, while the real `stack` payload comes
only from `get-contract`.

## Storage and recovery

**Birth.** The `Task` PreToolUse hook creates the `agent_contract_handoffs` row
for the dispatch; SubagentStart claims it and renders the kernel. The row and
its draft are open before the agent's first token.

**Adoption.** `gaia contract set` / `add` / `fill --json --draft-id <id>` writes
the draft AND mirrors the partial envelope onto the born row, which is what
makes evidence reach the database while the turn is still running. There is no
separate adopt verb.

**Close.** `gaia contract finalize --draft-id <id>` validates and converges the
born row idempotently (add `--plan-task-id <id>` when the turn executes a plan
task). It accepts any closing state -- `APPROVAL_REQUEST`, `BLOCKED`,
`NEEDS_INPUT`, `NEEDS_VERIFICATION`, `COMPLETE` -- and refuses `IN_PROGRESS`,
the one state that says the turn has not ended. `gaia contract validate
--draft-id <id>` runs the same shape check without persisting.

Do not pass `--session-id` unless the dispatch handed one over: the born row
already carries the session attribution, and an invented value (the literal
`unknown`, say) corrupts it.

**The gate at the wall.** `_resolve_subagent_stop_gate_full` in
`hooks/adapters/claude_code.py` resolves this turn's own dispatch row and
decides in three cases, all of them about the row -- nothing in the agent's
final-message text is read:

1. Row reachable and cleanly finalized -> its persisted envelope is validated
   (`GATE_SOURCE_ROW`).
2. Row reachable but not cleanly finalized -> reject
   (`GATE_SOURCE_ROW_UNFINALIZED`), softened to a non-rejecting
   `salvaged_truncation` verdict only when the stop was a harness truncation.
3. No row reachable at all -> reject (`GATE_SOURCE_ROW_MISSING`), softened the
   same way on a harness truncation.

The fenced block in the final message is therefore still required output, but
it no longer decides the close in any case -- the row is the only source the
gate reads.

**The reaper is not a second way to close.** A turn that stops after its last
`fill` leaves the row where the draft left it: open. The SubagentStop persister
(`hooks/modules/agents/handoff_persister.py`) eventually converges it with
`degraded` + `reaped` -- forensic cleanup that records that `finalize` was
never called. It never earns `COMPLETE`.

**Recovering a cut turn.** `gaia contract list --cut --json` lists cut rows;
`gaia contract view --draft-id <contract_id>` or
`gaia contract view --harness-id <harness_agent_id>` prints the envelope,
recovering accumulated evidence from `raw_handoff_json` when no draft file
remains. `view` never writes, so it is safe to point at any row, historical or
cut. `gaia contract view --field <dotted.path>` prints one subtree, exiting 1
when the path does not exist -- an existing-but-empty field and an absent one
are never the same response.

## Edge cases

**Build order for a terminal state.** Validation runs on every write against
the FULL envelope, not just the field being set. So fill the fields a terminal
state depends on FIRST -- `evidence_report.verification` for `COMPLETE`,
`approval_request.exact_content` for `APPROVAL_REQUEST` -- and set
`agent_status.agent_state` to the terminal value LAST. Reversing the order
rejects the `set agent_state COMPLETE` call with `VERIFICATION_RESULT`. A
rejected write leaves the draft at its last-known-good state, but a terminal row
is immutable once `finalize` persists it.

**The turn that never leaves framing.** A question answered entirely from
already-known context sets `work_phase=framing` once and closes `COMPLETE`
having touched no other phase. `COMPLETE` has no `work_phase` precondition --
only `COMPLETE_SHAPE` and `VERIFICATION_RESULT` gate it. `finalize` still runs.

**No contract at dispatch.** A dispatch outside this harness, or one whose born
row could not be claimed, arrives with no `# Your Contract` block. Then and only
then: run bare `gaia contract init` (no `--agent-id`), reuse the identity it
mints, and work the protocol identically. An unclaimed born row, if one exists,
is closed by the stop-hook persister, never by the agent.

**A failed COMMAND_SET is frozen.** Stop on the first non-zero or mismatched
result. Preserve the completed, failed and untouched indexes with the exact
command, exit status and stderr; do not claim the effect occurred. The grant is
terminal -- it cannot authorize a retry or any remaining index. Continuing
requires fresh read-only investigation and a new request-set for every retry or
remainder command still needed.

**Blind verification has two seams.** The SubagentStop gate
(`_blind_verification_required` in `hooks/adapters/claude_code.py`) and the CLI
finalize path (`cmd_finalize` in `bin/cli/contract.py`, resolving the binding
via `dispatched_binding_plan_task_id_by_contract`) apply the same decision, so
neither is a bypass of the other. The decision is a pure function of
`(agent_state, plan_task_id)` -- not of the agent's role, not of the turn's
kind. A turn with no `plan_task_id` (investigation, memory, a free-standing
verifier turn) is unbound and may self-`COMPLETE`.

**A verifier turn binds differently.** It carries `parent_handoff_id` and no
`plan_task_id` of its own, which is what lets it promote the producer's
`NEEDS_VERIFICATION` to `COMPLETE`. Rejecting instead sends the increment back
to `IN_PROGRESS`.
