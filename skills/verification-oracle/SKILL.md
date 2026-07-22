---
name: verification-oracle
description: Use when re-running a task_gates entry (or a proposed contract verification block) whose verification_type is `command` or `code`, to deterministically re-execute the declared check and compare the actual result against the gate's expected value -- not for `semantic` or `self_review` gates, which need judgment rather than re-execution.
---

# Verification Oracle

Deterministic re-execution of a command/code task gate: run the declared
check again, compare the actual result to what the gate expects, and return
an objective pass/fail with the evidence that produced it. This is the mode
a verifier loads when a gate's `verification_type` is one of the two
DETERMINISTIC types in `gaia.state.VALID_VERIFICATION_TYPES` -- `command` and
`code` -- as opposed to `semantic` (needs human/rubric judgment) or
`self_review` (trusts the producing agent's own statement).

## Position in the flow

This mode runs mid-verification, not standalone. Upstream, a planner already
authored the gate (`gaia task gate add ... --type=command|code
--evidence-shape='<check>'`, persisted in `task_gates`) or a producing agent
already proposed `evidence_report.verification` on its contract
(`gaia.contract.validator`) -- either way the check spec exists as text
before this skill runs; the oracle never invents what to check, only whether
it currently holds. Downstream, the pass/fail verdict feeds whatever decides
the gate's/AC's status next -- so a wrong verdict here propagates as a false
pass or a false block one layer up, not just a local mistake.

## Why command and code are ONE mechanism, not two

Read `gaia.state.gate_validation` and `gaia.contract.validator` before
assuming `code` needs different execution machinery than `command`: both
types resolve to the SAME shape, a runnable string -- `evidence_shape` on the
persisted gate, or `command` on the contract envelope (e.g. `{"type": "code",
"command": "ruff check ."}`). `gaia.state.__init__` calls the two "synonyms
for the two shapes of a deterministic check" -- `command` typically names a
broader run (a test suite, a script); `code` typically names a narrower
code-level check (a linter, a type-checker, an assertion). The label changes
what a human reads in a report; it does not change how the oracle runs it.

## Process

1. **Confirm the type is deterministic.** Read `verification_type` (gate) or
   `type` (envelope). If it is not `command` or `code`, this mode does not
   apply -- `semantic` needs a rubric/human, `self_review` needs to trust the
   agent's own statement. Re-running either of those as if it were a shell
   command is a category error: there is nothing to exec.
2. **Extract the check spec.** Pull the runnable string off `evidence_shape`
   (gate shape) or `command` (envelope shape), whichever is present. An
   absent or blank spec on a declared deterministic type is a hard rejection
   -- mirrors `gate_validation.validate_gate`'s own required-field check --
   never fall back to "assume pass."
3. **Re-execute, do not re-read.** Run the exact string as a subprocess --
   tokenized, never `shell=True` (see `command-execution`) -- and capture
   stdout, stderr, and the exit code. This is the one point in the flow that
   performs real I/O: the entire point of an oracle is that it re-observes
   the world instead of trusting a prior claim about it.
4. **Compare against the gate's expected value, not against zero by
   convention.** Default expectation is exit code `0`, but a gate MAY declare
   a different `expected_exit_code` (e.g. a linter that exits `2` on findings
   by design) -- read it when present, default to `0` when absent. Pass is
   `actual_exit_code == expected_exit_code`, nothing softer than that.
5. **Return the full verdict, not a bare boolean.** Carry `ok`,
   `verification_type`, the resolved `command`, `exit_code`,
   `expected_exit_code`, `stdout`/`stderr`, and `errors` (unresolvable type,
   empty spec, un-tokenizable string, command not found, timeout). Whatever
   consumes this needs the evidence to report `verbatim_outputs`, not just
   the pass/fail bit.
6. **Never launder a failure into a pass.** A non-zero unexpected exit code,
   a timeout, or a missing binary are all `ok=False` with a distinct
   `errors` entry -- collapsing any of them into a silent pass defeats the
   purpose of an oracle mode (see `agent-protocol`'s verification honesty
   rule: a clean exit is not the same as the change working, and here the
   exit code itself IS the thing under test).

## Reference implementation

`gaia.state.gate_oracle.run_oracle_check(gate, timeout=60.0)` is the
importable, tested mechanism this mode describes -- read it alongside this
skill rather than re-deriving the subprocess handling from scratch. It
returns an `OracleVerdict` (`ok`, `verification_type`, `command`,
`exit_code`, `expected_exit_code`, `stdout`, `stderr`, `errors`) and accepts
either the gate shape or the envelope shape directly, so a caller does not
have to translate between them first.

## Anti-patterns

- **Treating `code` as needing a different execution path than `command`.**
  Both resolve to the same runnable-string shape; inventing a second
  mechanism (e.g. `exec()` on a Python snippet) diverges from what
  `gate_validation`/`validator` already settled and doubles the surface to
  maintain for no real distinction.
- **Assuming exit-code-0 always means expected.** A gate's
  `expected_exit_code` is data, not a universal constant -- checking only for
  `0` silently fails every gate that legitimately expects otherwise.
- **Skipping re-execution because "the agent already said it passed."** That
  is exactly what `self_review` is for, not this mode. If the gate is typed
  `command`/`code`, the whole point is that the oracle re-runs it instead of
  trusting the claim.
