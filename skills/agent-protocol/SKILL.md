---
name: agent-protocol
description: Use when producing any agent response
---

# Agent Protocol

Every agent turn writes one contract: the record of what it was asked to do, what it
found, what it changed, and how it ended. This skill is how that contract gets
written -- during the turn, while the work is still happening. The check at the end
reads the finished contract, so its shape is already guaranteed by something other
than you. What no check can see is how the turn ran: a turn that wrote nothing until
its last second and then closed perfectly passes every validation. That is what this
skill owns, and it is why each principle below carries its consequence.

```
Ground   what already governs this -- injected context, memory, the code, the skills
Plan     what "done" means, what the pieces are, which exits need one signature
Work     declare the phase, write each finding as you reach it
Verify   close each increment verified; on failure, back to the sources
Close    declare a state, finalize, degrade honestly
```

## Two doors

**A fresh dispatch** arrives with a kernel -- `# Your Contract`, `# Your CLI`,
`# What I know about you`. Your contract is named there, already open. Start at
Ground.

**A resumed turn** arrives as a message from the orchestrator inside a turn that
already began. The kernel is injected once, at dispatch, and never again; your
contract is the one you already adopted, still open, still holding everything you
wrote before the interruption. Read it back (`gaia contract view --draft-id
<contract_id>`) rather than re-deriving it, and pick the cycle up at the phase your
last write left.

## 1. Your contract is the delivery; your final message is only the signal that the turn ended

Finishing means leaving the contract written. The gate validates your persisted
contract, and an unfinalized one rejects the close however complete your message
reads.

## 2. You were born with a contract -- adopt it, do not create another

It exists before you read your goal. `# Your Contract` shows it to you, and your
first `gaia contract set/add/fill --draft-id <contract_id>` adopts it; there is no
separate adoption step. Pass `--draft-id` on every later call and copy `agent_id`
verbatim.

The gate checks that identifier's shape. Nothing checks that it is *yours*. A `gaia
contract init` run on a turn that already has a contract mints a second, well-formed
identity that passes every validation and that nobody is watching: your evidence
lands on it, while the contract your dispatch is bound to stays open, is reaped
unfinalized, and is what the orchestrator reads when it asks how your turn went.
`init` is only for a turn that received no contract at all.

## 3. Ground yourself before acting

Ask what pattern already governs what you are about to do, in precedence: what was
injected (already paid for -- do not re-derive it with a tool call) -> memory, which
is queryable and not merely received, since what arrived at session start is a sample
and `gaia memory search` reaches the rest -> the code already written, which decides
over any description of it -> the skills -> outside.

That order is cost and authority at once, both running the same direction. The failure
it prevents is not a wrong answer; it is a right answer in the wrong idiom -- a
solution the system already decided differently about, which whoever reads it next now
has to reconcile. Reading the injected memory sample as the whole archive is the
ordinary form of this: the turn concludes something the substrate already recorded
against.

State no tool sequence and promise no reach. A sequence announced before the first
read is a commitment made by the part of the turn that knows least, and reach claimed
before it is exercised is read downstream as established.

## 4. Local and reversible work just happens; what goes out into the world is gathered and asked once

Commit, write files, leave the PRs ready -- none of that needs a signature. Pushes,
applies and every other exit into the world go into one ordered set with a single
signature: a COMMAND_SET. Build it from what the plan already implies and can be
written out exactly in advance, never from what you discover as you go. Keep it small
and coherent -- one bounded operation.

Both halves have a cost. Asking per command turns the user into a keystroke-approver,
and consent granted that way has stopped being informed. But grouping consent is not
atomic execution: if the third of six fails, the grant is terminal, the remaining
three die with it, and you are back asking a second signature for the remainder -- the
interruption the set existed to prevent, now with a half-applied change under it. A
set spanning two goals therefore costs more consent than two sets holding one each.

## 5. The record is written in flight, and the cadence is set by what would hurt to lose

If the turn is cut, only what you already wrote exists. A harness cut lands mid-turn
with no warning and no last message; the persister records it honestly (`degraded` +
`reaped`, in `hooks/modules/agents/handoff_persister.py`) and cannot record work it
never saw. Everything else survives in the transcript as narrative and dies as
evidence.

So the cadence is value at risk: write a finding the instant re-deriving it would cost
more than recording it, and before any step whose outcome you cannot predict -- a long
synthesis with no tool calls in it, an approval handoff, a mutation, the final message
-- confirm you are already complete enough to be resumed from.

The same reason is why the close is not where the record gets composed. A summary
written from memory at the end is a second telling of work already done, produced
under the pressure that ends the turn, and it drops fields. The contract does not, and
whoever needs this turn queries it at the granularity they need, whenever they need
it.

## 6. The phase is declared before doing that phase's work

`framing`, `investigating`, `planning`, `executing`, `verifying` -- write the phase
before that phase's work, not after. It is what makes half a turn legible from
outside: a turn observed mid-flight either reads as "investigating, four files in" or
as an opaque box, and that difference decides whether the orchestrator waits or
dispatches again over the top of you.

Write only the phases you actually entered. A turn answered from framing alone never
touches the other four, and a phase written to satisfy a checklist makes the record
state something that did not happen -- worse than a gap, because a reader cannot tell
it from a true one. (A phase's spelling is validated when it is present; that it is
present at all is never checked.)

## 7. Every increment closes verified, and fixing starts by going back to the sources

Close each piece verified before starting the next: compounding failures grow
exponentially, and separating two entangled failures costs far more than verifying the
first one did. Verify by result -- an exit code says the command ran, not that the
state changed.

On a failure, search before retrying; do not vary the attempt. Which search depends on
what failed, and the two pull opposite ways:

| What was rejected | What to do | Why |
|---|---|---|
| Your **contract** | Reissue it complete, without re-investigating. Two attempts, maximum. | A problem of form. The knowledge is already in the record; re-running the investigation burns the context principle 5 was protecting and changes nothing about the rejection. |
| An **operation** | Go back to the sources before retrying. | A problem of knowledge. Retrying with variations is guessing, and each variation leaves another unexplained state on top of the one you could not explain. |

## 8. Before declaring yourself blocked, ask

Three doors come first: is the answer already in the goal you received? is it in
context, or in memory you can query? can the orchestrator answer it -- the one channel
where you initiate?

Blocking is the fourth option and the only one that costs the whole turn. A blocked
turn dies and must be dispatched again from zero -- new context, files re-read,
findings re-derived -- while an asked question is answered and the turn continues with
everything it has already gathered intact.

## 9. The producer does not verify its own production

Evidence is the command's output, not your assertion about it. On a `COMPLETE` the
gate requires the verification field to say `pass`; nothing checks whether it is true,
so a fabricated pass passes -- and at the wall it is indistinguishable from a real
one, which is exactly why its whole cost lands on whoever trusts it next. A turn bound
to a plan task cannot seal itself: it closes `NEEDS_VERIFICATION` and an independent
verifier promotes it.

## 10. Every turn closes by declaring a state

`IN_PROGRESS` (work can continue), `BLOCKED`, `NEEDS_INPUT`, `APPROVAL_REQUEST`,
`NEEDS_VERIFICATION`, `COMPLETE`. Only `COMPLETE` is terminal. Set the closing state
in the contract, then `gaia contract finalize --draft-id <contract_id>` as your last
tool call. If the contract is rejected, reissue it complete without re-investigating
-- two attempts, maximum, for the reason in principle 7's table.

## 11. Degrading honestly costs less than faking

What you could not do, what you did not verify and what stayed open are part of the
record too. A gap declared is routed: the orchestrator dispatches it or puts it to the
user. A gap hidden is found later by whoever already built on the claim that it was
closed.

The costs are not symmetric. A partial turn that says so is worth exactly its
evidence. A complete-looking turn with one invented field is worth nothing, because a
reader who catches one has no way to bound how many others there are.

## What the gate will reject

| Rejected | Enforced by |
|---|---|
| An `agent_id` that is not `a` plus 16+ lowercase hex characters | `AGENT_ID_FORMAT` |
| An `agent_state` outside the six above | `PLAN_STATUS`, against `VALID_PLAN_STATUSES` (`gaia/state/__init__.py`) |
| `COMPLETE` with a non-empty `pending_steps`, or `next_action` other than `"done"` | `COMPLETE_SHAPE` |
| `COMPLETE` whose `evidence_report.verification.result` is not `"pass"` | `VERIFICATION_RESULT` |
| `APPROVAL_REQUEST` without a non-empty `approval_request.exact_content` | `APPROVAL_REQUEST_SHAPE` |
| `COMPLETE` while `loop_state` still has iterations left below threshold | `_check_loop_state_blocking` (`hooks/modules/agents/contract_validator.py`) |
| `COMPLETE` on a turn whose dispatch binding carries a `plan_task_id`, at both seams | `_blind_verification_required` (`hooks/adapters/claude_code.py`) and `cmd_finalize` (`bin/cli/contract.py`) |
| A close whose persisted contract is unfinalized, however complete the final message | `_resolve_subagent_stop_gate_full` (`hooks/adapters/claude_code.py`) |
| `finalize` on a draft still declaring `IN_PROGRESS` | `cmd_finalize` (`bin/cli/contract.py`) |

Unqualified names above are `FormErrorCode` members raised by `validate_form` in
`gaia/contract/validator.py`.

## Where to go next

The envelope's fields and validation rules are `agent-contract-handoff`, with filled
envelopes state by state in `examples.md`. Evidence and mutation forecasting are
`investigation`. One operation is classified by `security-tiers` and invoked per
`command-execution`. A COMMAND_SET is requested through `subagent-request-approval`,
whose payload `agent-approval-protocol` defines, and run afterwards under `execution`.
The two state machines, the phase-to-section map, the kernel fields, storage and
recovery, and the edge cases are in `reference.md`.

Orchestrator-side continuations: `orchestrator-present-approval` when a request
arrives, `pending-approvals` when the user names an existing identifier,
`agent-response` when a contract comes back.
