---
name: investigation
description: Use when starting an investigation, analyzing existing code or infrastructure, or building findings before proposing changes
---

# Investigation

Investigate from cheapest authoritative context toward narrowly scoped live
evidence. The purpose is both diagnosis and a reliable mutation forecast.

## Evidence ladder

1. Read the injected dispatch kernel -- `# Your Contract` (goal, role,
   `project` and its declared `workflow`, the `can_read` menu) and
   `# How the user works`. Do not
   re-derive facts it already supplies.
2. Pull what the goal needs from the substrate on demand -- none of it is
   preloaded, and a coordinate in the goal (a contract id, a memory slug, a
   brief) is an instruction to go read it. The verbs, their addressing, and the
   workspace rule that decides whether they resolve are in
   `agent-protocol/read-map.md`. Do not read Gaia's database directly.
   A fact a coordinate already holds is reused, cited in `key_outputs` by the
   handoff id it came from; measure again only what the coordinates leave
   uncovered, or a fact whose source changed after the commit or date it was
   measured at. A re-measured fact that disagrees with its coordinate is a
   finding for `open_gaps`, naming both.
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
5. What gets checkpointed is governed by value-at-risk, not a fixed list; the
   orchestrator reads your progress from the evidence lists you have written
   (`agent-protocol` principle 6), so there is no phase mark to set: write a
   finding the instant re-deriving it would cost more than recording it. A
   long synthesis that runs with no tool calls in between is the costliest
   case: checkpoint the gathered evidence and hypotheses before you start it
   (you cannot predict where it lands), then fill the conclusion the instant
   you reach it -- before composing the report that states it, not after. A
   one-file read that never reaches a costly-to-redo finding earns zero
   mid-turn checkpoints; do not add ritual where
   nothing is at risk.

## Forecast mutations after read-only work

Once the cause and desired outcome are known, enumerate the exact mutations the
accepted plan predictably requires. Classify every command through
`security-tiers` before execution.

Request every predictable T3 command before attempting it, the instant it is
known, rather than waiting for the policy gate to block it. Only a command
`security-tiers` classifies T3 belongs in a request: pushing a named feature
branch, opening a PR and editing one without `--base` run free, so run them
instead of requesting them. The forecast is what
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

## Deeper, not bigger

Depth stays inside the goal: each move is a read, a check, or an offer.

- Restate the acceptance measurably before working.
- Use the trade's real instrument, not a summary of what it would say.
- Prefer the raw output to a description of it.
- Predict what a read you have not yet seen will show, then run it and compare.
- The judge is something you did not write in order to pass.
- What an expert would add and the goal did not ask for, you name in one line
  as an offer, and do not do.
