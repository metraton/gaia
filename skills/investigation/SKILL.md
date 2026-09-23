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
2. Pull what the goal needs from the substrate on demand -- none of it is
   preloaded, and a coordinate in the goal (a contract id, a memory slug, a
   brief) is an instruction to go read it. The verbs, their addressing, and the
   workspace rule that decides whether they resolve are in
   `agent-protocol/read-map.md`. Do not read Gaia's database directly.
3. Inspect the smallest relevant source files, tests, configuration, git diff,
   or runtime query. Prefer authoritative implementation over prose. Never
   search from the home directory (`~`) or from `/`: a search rooted there
   walks every workspace, cache and package store. Locate a binary or package
   with `gaia paths`, `which`, or its known path, and start a search at the
   smallest directory that can hold the answer.
4. Record each material source immediately in the contract:
   `files_checked`, `patterns_checked`, `commands_run`, `key_outputs`, exact
   excerpts in `verbatim_outputs`, whatever your change reaches outside the
   file you were sent to in `cross_layer_impacts`, and uncertainty in
   `open_gaps`. When the finding needs a supporting file too large for
   `verbatim_outputs` (a full command dump, a rendered report), stage it
   under the canonical Gaia scratch directory (`~/.gaia/scratch`, never the
   workspace/client repo), named after the current turn's `contract_id`
   (`<agent_id>.<token>`, bare or with one trailing extension) so Gaia's own
   retention rule can attribute and reclaim it once the contract closes, then
   deposit it as evidence through the contract's evidence clause -- see
   `agent-contract-handoff` -- rather than leaving it as a loose file.
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

Request every predictable T3 command before attempting it, the instant it is
known, rather than waiting for the policy gate to block it. The forecast is what
the request is built from -- what each command does, its impact, how it is
undone and checked: `subagent-request-approval` says which phrases to write and
how to group the commands into the fewest signatures, leaving for a later
request anything whose exact form depends on a result not yet seen. Do not
group speculative clean-up, alternatives, or unrelated repositories or
services. A command already blocked is requested through the line its denial
carries; never retrofit it into a different spelling. Consent grouping reduces
repeated consent; it does not make execution atomic.

## Evidence quality

A finding names the source and observation. A failed command records its exact
text, exit status, stdout/stderr, and what remains unknown. An exit code alone
does not establish desired state; verification must query or inspect the result.
Keep assumptions visibly separate from confirmed facts.
