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

1. **A goal completes the kernel; it never repeats it.** The kernel already
   carries the turn's identity, the contract rules, the project and its declared
   workflow, the read menu, a bound task's gates and the user's standing rows.
   The goal carries what the kernel cannot: the references already opened, the
   premise as a claim the specialist may refute, and the acceptance. What the
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
   sibling lands.

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

8. **Steering in flight is a channel, not a return.** A turn heading the wrong
   way can be messaged mid-flight, continued once it closes, or replaced by a
   fresh dispatch. Waiting for its return is only one of the three, and usually
   the dearest, because the work done the wrong way meanwhile is paid for either
   way.

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
