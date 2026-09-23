---
name: verification-oracle
description: Use when re-running a task_gates entry (or a proposed contract verification block) whose verification_type is `command` or `code`, to deterministically re-execute the declared check and compare the actual result against the gate's expected value -- not for `semantic` or `self_review` gates, which need judgment rather than re-execution.
---

# Verification Oracle

Deterministic re-execution of a `command` or `code` gate: run the declared check
again, observe whether it passes now, and return a verdict carrying the
evidence that produced it. `command` and `code` are one mechanism with two
labels -- both carry a runnable string, `evidence_shape` on a persisted gate or
`command` on a contract's `evidence_report.verification` -- so the label changes
what a reader expects (a suite versus a linter), not how the oracle runs it.
For `semantic` and `self_review` gates, use `verification-rubric`.

## Position in the flow

Upstream, the planner authored the gate: its claim in `evidence_type`, its
check in `evidence_shape`. Upstream of that, the executor ran the check red
before its change and green after, recording both as evidence tied to the gate.
The oracle never invents what to check, only whether it holds. Downstream, its
verdict decides whether the task derives as done and, through the evidence it
leaves, whether an AC can close -- so a false pass here counts unfinished work
as done one layer up.

## Process

1. **Confirm the type.** Only `command` and `code` apply. Re-running a
   `semantic` rubric or a `self_review` statement as a shell command is a
   category error: there is nothing to execute.
2. **Extract the check.** Take `evidence_shape` (gate) or `command` (envelope).
   A blank check on a deterministic type is a failure of the gate itself,
   never an assumed pass.
3. **Treat a stale verdict as pending.** A gate whose `stale_at` is set keeps
   its old verdict for the record, but something it depended on changed since.
   Re-run it exactly as if no verdict existed.
4. **Re-execute; do not re-read.** Run the exact string tokenized, never
   through a shell (`command-execution`), and capture stdout, stderr and the
   exit code. Exit 0 is pass. Re-observing is the whole point: an oracle that
   accepts a prior claim is a `self_review` with extra steps.
5. **Check that the gate could fail.** For a gate on a change, look for its red
   run (`gaia evidence list`, negative evidence tied to this gate). A check that
   was never shown failing, or that passes whether or not the change is there,
   cannot distinguish done from not done.
6. **Return the full verdict and record it.** Pass, or fail with the cause that
   says what has to move: `product` (the change does not do it),
   `environment` (the runner, a dependency, the network), `broken_test` (the
   check is wrong or cannot fail), `requirement_changed` (the claim no longer
   matches the brief). Keep the command, exit code, stdout and stderr for
   `verbatim_outputs` and as the gate's evidence. A timeout or a missing
   binary is a failure with its own error, never a silent pass.

## Reference implementation

`gaia.state.gate_oracle.run_oracle_check(gate, timeout=60.0)` runs step 4: it
accepts either the gate or the envelope shape and returns an `OracleVerdict`
with `ok`, the resolved command, the exit code, stdout, stderr and `errors`.
Steps 3, 5 and the failure cause are the verifier's judgment on top of it.

## Anti-patterns

- **A second execution path for `code`** -- both types resolve to the same
  runnable string; an `exec()` of a snippet diverges from what
  `gaia.state.gate_validation` settled and doubles what must be maintained.
- **Passing a gate that never went red** -- a check that cannot fail proves
  nothing about the change; that is `broken_test`, not `pass`.
- **Trusting a stale pass** -- the verdict predates the change that marked it;
  only a fresh run clears it.
- **Skipping re-execution because the producer said it passed** -- that is what
  `self_review` is for; a `command`/`code` gate exists so someone re-runs it.
