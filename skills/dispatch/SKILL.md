---
name: dispatch
description: Use when the orchestrator is about to dispatch a specialist turn that writes to a repository, runs alongside other turns, or carries acceptance the specialist must meet
---

# Dispatch

Dispatch is the technique for turning a route already decided into specialist
turns that can run, prove themselves and land without stepping on each other.
It runs mid-flow. Upstream, the orchestrator has cleared the intent and composed
the route. Downstream, the kernel renders the goal into a specialist's context,
the specialist works and closes a contract, a verifier judges it, and an
integration puts it on the shared branch. All three consume what this skill
produces -- a goal, a wave, an integration order -- so a defect here is paid
downstream, where it is harder to see.

The orchestrator's identity lists what every goal carries at the moment of
dispatch; this skill holds the reasons behind the writing, parallel and
integration parts. Commands, sequences and numbers are in `reference.md`; one
two-writer wave, end to end, is in `examples.md`.

## The eight principles

1. **A goal completes the kernel; it never repeats it.** The kernel is data
   only: it carries the turn's identity, the project and its declared workflow,
   the read menu, a bound task's gates, the user's standing rows and an index of
   the project's standing notes (bodies open on demand). The contract
   rules reach the specialist through the `agent-protocol` skill, not the kernel.
   The goal carries what neither can: the references already opened, the
   premise as a claim the specialist may refute, and the acceptance. Where they
   change the work, it also names the rawest source, the trade's instrument, a
   target precise enough to miss and the outside check that will show it is
   right: a specialist born in clean context gets that depth from the goal or
   not at all, and where they change nothing they are ceremony. What the
   goal repeats costs attention twice and drifts from the kernel the first time
   either one changes; what it omits that the kernel does not supply does not
   exist for the turn.

2. **Acceptance is an assertion the specialist can run and loop on; what only
   judgment settles is named as a rubric.** The split decides who loops and who
   seals: the specialist re-runs a command gate until it holds, the verifier
   grades a rubric it did not write. A check travels as the command to run
   directly and the result it must produce -- never as an instruction to capture
   its exit code, filter its output or chain it to another, because a goal that
   invites shell composition is a defect: the guards refuse the composed form or
   classify it above what it is, and the check never runs as written. A rubric
   dressed as a command gets retried instead of judged; a command dressed as a
   rubric reaches a reader who cannot re-run it.

3. **Isolation by checkout, serialization by branch.** A writing turn owns a
   worktree cut from the accumulating branch's current tip, so no two writers
   share a tree or an index. What still serializes is the shared branch:
   integrations queue one at a time, each composed against the branch as it
   stands when it runs, because a base fixed at dispatch is stale the moment a
   sibling lands. Cleanup follows the same two moments: integrating a task
   releases its worktree and deletes its branch, and once the PR merges, a
   post-merge pass removes the branches it integrated. Both decide by content,
   not by hash, since a cherry-pick keeps the change under a new hash.

4. **Parallelism is bounded by shared files and integration cost, never by the
   count of tasks.** A wave groups turns whose blast radii do not overlap; two
   turns touching one file become one turn or two waves, since their conflict
   is otherwise paid at integration by whoever lands second. The project's own
   rule caps how many writers run at once, and that cap is the user's standing
   rule, not a default to reason around.

5. **A writing task closes through an independent verifier bound to the
   producer, and the shared branch's CI is the last gate before "done".** The
   producer cannot seal its own work, so its turn closes needing verification,
   and the verifier's dispatch names the producer's row -- without that binding
   nobody can later tell which contract verified which. A green verdict on a
   branch whose CI is red is not done; it has happened, and the red was found by
   the next turn to touch the branch.

6. **The specialist creates and releases its own worktree, with its born
   identity.** The worktree is stamped with the contract and agent ids that
   exist only once the turn is born, so the orchestrator cannot create it on the
   specialist's behalf, and it never enters it. The goal names the repository,
   the project and the base; the specialist does the rest and releases the tree
   at close, where a dirty one is captured as evidence rather than removed.

7. **A turn is sized before it starts, and is fresh or resumed by what it must
   know.** A tool-call ceiling fixed in advance, with permission to close
   partially, is what lets a turn report that it ran out; a turn that exhausts
   its context mid-work cannot. When the previous turn was cut near its close,
   implementing and integrating split into two turns. A turn is fresh when it
   must not know something -- a review, a blind check -- and resumed when it
   executes what it already knows, because rebuilding known state is pure cost.
   Its acceptance names the checks that fit inside it, and it declares only the
   runs it completed: the whole-tree suite is the shared branch's CI (principle
   5), never a step a turn carries forward to the next.

8. **Steering in flight is a channel, not a return.** A turn heading the wrong
   way can be messaged mid-flight, continued once it closes, or replaced by a
   fresh dispatch. Waiting for its return is only one of the three, and usually
   the dearest, because the work done the wrong way meanwhile is paid for either
   way. A message in flight corrects direction within the acceptance the turn
   was born with; new acceptance -- a plan change, a further check -- waits for
   the turn to close or goes to a fresh dispatch, since a turn sized for one
   goal exhausts its ceiling on two.

## What the goal says, beyond the kernel

The orchestrator's identity lists only the tokens a goal literally carries
(`project=`, `ceiling=`, `parent_handoff_id`, the raw goal, one integration
at a time); the craft of the rest is here.

- **Acceptance as a property, not a checklist.** A checklist is satisfied by
  its items; a property makes the specialist find the cases the goal did not
  name, proven by literal output, never by the agent's assertion -- for a
  "found nothing", what was searched and how, because an unproven nothing is
  indistinguishable from not looking. The goal leaves the HOW to the
  specialist, since the HOW is the pattern choice it was dispatched for, and is
  written in the affirmative, because naming a forbidden behavior primes it;
  anything ruled out arrives with the route to the same result.
- **The premise as a claim.** The goal carries its premise as something the
  specialist may refute, with the refutation owed back as a deliverable: a
  competent agent executes a false premise flawlessly, and the goal is the only
  place it can be caught.
- **A destination for every class of rationale.** Turn reasoning to the
  contract, change explanation to the PR body, cross-unit facts to docs, and
  only the why the code cannot say to code, beside the declaration whose change
  would falsify it -- a goal that rewards self-explanation without a
  destination produces comments in proportion to its own quality.
- **References the orchestrator already opened.** Every reference passed is
  one it opened itself, with the same verb, the same flag, from the same
  workspace the recipient will use -- "I saw it in a listing" has twice not
  meant "it resolves for the specialist". A prior turn's judgment -- a spec, an
  approved wording -- travels literally in the goal or at a reference that
  resolves, never only in a return message, because contract rows persist
  verdicts, not bodies. A sibling's finished contract is handed off by pointing
  at the rows and fields that hold the evidence.
- **What is already measured, before an investigation.** Before dispatching
  one, the orchestrator searches prior contract rows, memory and deposited
  evidence for the part of the question they already answer, and passes each
  hit as a coordinate -- the handoff id and field, the slug -- with the commit
  or date it was measured at. A question answered twice costs a turn, and the
  second answer can disagree with the first with no reason recorded for the
  disagreement. Where the source changed since that measure, the goal says so,
  and that part is measured again.
- **A seeded agent for contract-bearing work.** A host-native agent cannot
  finalize a contract row, so one is used only when the returned message is the
  whole deliverable.
- **A fan-out buys contrast; a lens is an activity.** A question that spans
  surfaces goes to each owning specialist and comes back as one contrast. Where
  the perspective needed is a craft rather than a surface, each lens reaches its
  goal as the activity it performs -- red-team the diff, trace the data lineage
  -- never as a persona, because a character yields a voice and an activity
  yields evidence. Several blind turns on one question declare their
  discriminators before any of them return; a conclusion shared in advance buys
  only confirmation.
- **A follow-up is a resume.** A resume names the contract row the turn owns
  and follows the raw-goal rule. A question about what a specialist just did, a
  correction, the cleanup after a Reject, a signature that stayed pending is a
  resume of that same specialist, never a fresh dispatch; a granted T3 survives
  the resume, so requesting and executing belong in one turn. Claude Code
  resumes with `SendMessage` to the agent id; OpenCode with the `task` tool
  carrying `task_id`, the session id its last result or Gaia's notice printed.

## Anti-patterns

- **The goal that re-narrates.** Retelling a sibling's findings or the kernel's
  contents: lossy where it is new, redundant where it is not, and the retelling
  is what the specialist trusts.
- **The composed check.** A gate written as a pipeline or an exit-code capture
  is refused or reclassified before it runs, and the turn reports on a check
  nobody asked for.
- **The base fixed at dispatch.** Writers cut from the tip as it stood when the
  wave began and integrated in any order: every integration after the first
  lands on a branch it never saw.
- **Counting tasks instead of files.** A wave sized by how many tasks are ready
  rather than by what they touch pays its conflicts at integration.
- **Done on the verdict alone.** A verified task on a branch whose CI is red has
  moved the defect, not closed it.
