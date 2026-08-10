---
name: command-execution
description: Use when executing any bash command, CLI tool, or shell operation
---

# Command Execution

One command, one result, one exit code. This skill owns invocation discipline;
`security-tiers` owns classification and the approval branch owns T3 payloads.

## Before the call

1. Prefer a native CLI flag to a pipe and a file tool to shell file I/O.
2. Use an absolute path or the CLI's native working-directory flag.
3. Run one atomic command. Never chain with `&&`, `||`, `;`, pipes, redirects,
   background execution, substitutions, `bash -c`, `sh -c`, or `eval`.
4. Classify the exact string with `security-tiers`. T0/T1 reads and validation
   proceed. Bounded local T2 follows its policy. T3 routes to the approval branch
   in `agent-protocol`; do not duplicate a sealed payload here.
5. Never write under `.claude/`. Gaia components are edited in the `gaia/`
   source tree and propagated by install.
6. A file that is not itself the deliverable -- a probe, a throwaway
   reproduction, an intermediate dump to inspect before deciding -- is written
   under the canonical Gaia scratch directory (`~/.gaia/scratch`, printed by
   `gaia paths`; a `GAIA_DATA_DIR` override relocates it), never into a
   workspace or client repository tree. Only the actual deliverable (the code
   change, the config, the report the task asked for) belongs in-repo. Name it
   after the current turn's `contract_id` (the `# Your Contract` value, shape
   `<agent_id>.<token>`) -- the bare id as the entry name, or that id plus one
   trailing extension (`<contract_id>.json`) -- never a free-form or
   task-derived name: that is the identifier Gaia's own retention rule reads
   back to attribute and reclaim the entry once the contract closes. A file
   worth keeping as proof of what was done is deposited as evidence through
   the contract's evidence clause (`agent-contract-handoff`), not left sitting
   in scratch or committed as a side effect.

For a plan-first COMMAND_SET, each tool call contains only the next exact item
in the approved order. Consent to a set is not permission to combine its items
into one shell call.

## After the call

Record the exact command and one result. On success, verify the desired state
with a separate read-only command or file inspection. On failure, preserve the
exact exit status, stderr/stdout excerpt, affected component, and remaining
uncertainty. Do not paraphrase away the failure and do not run a differently
spelled equivalent.

In an approved COMMAND_SET, fail fast: stop on the first non-zero or mismatched
result, checkpoint the failed index/evidence, and leave later items unexecuted.
The grant is now terminal/frozen `FAILED`; it cannot authorize a retry or any
remaining index. Continuing requires fresh investigation followed by a new
request-set and new approval for every retry/remainder command still needed.

For git, choose the canonical form once:
`git -C /absolute/repository <verb> <fixed arguments>`. A post-grant retry must
be byte-identical to the approved command.

## Examples

- Use `kubectl get pods -o json` instead of a filtering pipe.
- Use `terraform -chdir=/absolute/path plan` instead of `cd ... && terraform`.
- Use Read/Edit/Write or apply_patch for files, not `cat`, heredocs, or `sed -i`.
- Run two commands as two calls and inspect both results.
