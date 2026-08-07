---
name: investigation
description: Use when starting an investigation, analyzing existing code or infrastructure, or building findings before proposing changes
---

# Investigation

Investigate from cheapest authoritative context toward narrowly scoped live
evidence. The purpose is both diagnosis and a reliable mutation forecast.

## Evidence ladder

1. Read the injected dispatch kernel -- `# Your Contract` (goal, role,
   `project`, the `can_read` menu) and `# What I know about you`. Do not
   re-derive facts it already supplies.
2. Pull the project context the goal needs on demand -- it is NOT preloaded:
   a scoped `gaia context get-contract --section <s>` (within your `can_read`
   menu -- `gaia context get`/`show` resolve `--section` against the workspace
   shape instead, never against these contracts), then `gaia context show` or
   `gaia context query` for wider reads. Do not read Gaia's database directly.
3. Inspect the smallest relevant source files, tests, configuration, git diff,
   or runtime query. Prefer authoritative implementation over prose.
4. Record each material source immediately in the contract:
   `files_checked`, `patterns_checked`, `commands_run`, `key_outputs`, exact
   excerpts in `verbatim_outputs`, and uncertainty in `open_gaps`.
5. Two rules apply here, at two different levels, and they do not compete.
   Entering this phase at all is `agent-protocol`'s phase-transition floor: the
   instant work becomes investigation, write `work_phase=investigating` once
   (or fold it into the first evidence write) -- that mark is not optional
   when this phase genuinely runs. WITHIN the phase, what else gets
   checkpointed is governed by value-at-risk, not a fixed list: write a
   finding the instant re-deriving it would cost more than recording it. A
   long synthesis that runs with no tool calls in between is the costliest
   case: checkpoint the gathered evidence and hypotheses before you start it
   (you cannot predict where it lands), then fill the conclusion the instant
   you reach it -- before composing the report that states it, not after. A
   one-file read that never reaches a costly-to-redo finding earns zero
   mid-turn checkpoints beyond the one phase mark; do not add ritual where
   nothing is at risk.

## Forecast mutations after read-only work

Once the cause and desired outcome are known, enumerate the exact mutations the
accepted plan predictably requires. Classify every command through
`security-tiers` before execution.

A single predictable T3 command is requested plan-first too -- proactively,
the instant it is known, rather than waiting for PreToolUse to block it. For
two or more exact T3 commands, prefer grouping them into one plan-first
COMMAND_SET only when:

- all commands serve one bounded goal and are known verbatim now;
- order is meaningful and can be shown explicitly;
- the risk, rollback, and verification can be explained for the complete set;
- no item depends on unseen output from an earlier item; and
- each item is one atomic invocation, never `&&`, `;`, a pipe, substitution, or
  other shell composition.

Do not group speculative clean-up, alternatives, unrelated repositories or
services, condition-dependent follow-ups, or commands that must be derived from
earlier results. Request those later only after new read-only investigation.
Consent grouping reduces repeated consent; it does not make execution atomic.

Use `gaia approvals request-set --command '<exact 1>' [--command '<exact 2>' ...]`
before attempting any item -- one `--command` for a single operation, one per
item for a group. A command already blocked (the reactive path, reached only
after an attempted command trips PreToolUse) is relayed exactly; never
retrofit it into a different spelling or self-mint consent metadata.

## Evidence quality

A finding names the source and observation. A failed command records its exact
text, exit status, stdout/stderr, and what remains unknown. An exit code alone
does not establish desired state; verification must query or inspect the result.
Keep assumptions visibly separate from confirmed facts.
