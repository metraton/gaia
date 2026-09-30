# Dispatch -- Examples

## A two-writer wave onto one pull request

Two ready tasks of one plan integrate into the same pull request branch,
`feat/ledger`, in repository `gaia`:

- **Task A** (`tasks.id` 811) changes how the kernel renders a line:
  `hooks/modules/context/kernel_builder.py` and a new test under
  `tests/context/`.
- **Task B** (`tasks.id` 812) documents the same line for specialists:
  `skills/agent-protocol/reference.md`.

**Composing the wave (principle 4).** The file sets do not overlap, so A and B
run in parallel, which is also the plan's cap of two writers. Had B also edited
`kernel_builder.py`, they would be one turn, or B would wait for the next wave.

**The goals (principles 1, 2, 6).** Each carries only what the kernel does not.
Task A's goal, in full:

```
project=gaia task_id=811

GOAL: the kernel renders the project's declared workflow as one line under
project:, and nothing when the project declares none. Premise, refutable: the
entry is read through tools/context/context_provider.py::dispatch_project_entry.

Work in your own worktree from origin/feat/ledger (fetch it first), created with
your contract and agent ids; release it at close.

Acceptance: run `python3 -m pytest tests/context -q` directly; it passes with
exit 0. Gate 1501 is a rubric -- state what a reader should check, do not grade
it.

Integrate once: fetch, rebase onto origin/feat/ledger, re-run the tests, then
push HEAD:feat/ledger without force, as one signed set. Close with
NEEDS_VERIFICATION. Tool-call ceiling: about 60.
```

Task B's goal has the same shape, its acceptance being the read-map test run
directly. Neither restates the kernel, the contract rules or the user's rows.

**Integration order (principle 3).** B documents what A builds, so A integrates
first. B's turn does not rebase at dispatch: its signed set fetches and rebases
when it runs, so it lands on the branch with A already in it. If B's push is
rejected because A landed a moment earlier, B fetches, rebases, re-runs its
test, and asks for a new push -- never a forced one.

**Verification (principle 5).** Each producer closes `NEEDS_VERIFICATION`. Two
fresh `gaia-verifier` dispatches follow, each goal carrying the producer's
contract row as `parent_handoff_id=<N>` and nothing of the producer's
reasoning, because a verifier must not know what the producer believed. The
wave is done when both verifiers pass and `gh pr checks` for the pull request,
run directly, is green.

**Steering (principle 8).** Midway, A's turn starts editing
`skills/agent-protocol/reference.md`, which is B's file. The orchestrator sends
it a message that the file belongs to the sibling turn, rather than waiting for
the return and paying for a conflict at integration.
