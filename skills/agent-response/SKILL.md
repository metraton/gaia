---
name: agent-response
description: Use when the orchestrator must read, reconcile, route, and present an agent_contract_handoff
---

# Agent Response — Consumer Branch

The durable contract row is the turn's whole delivery. The final message is the signal that the turn
ended and carries no envelope of its own, so there is nothing to reconcile against: what the agent
declared and what was persisted are one artifact.

**The read is not free, and that is the economics that decides whether it happens at all.**
`contract view --field <dotted.path>` prints only that subtree of the envelope
(`agent_status.agent_state`, `evidence_report.open_gaps`, ...), exiting 0 with the value verbatim when
the path exists and 1 when it does not — so "searched and found nothing" and "did not search" are
distinguishable without opening anything else. Without that distinction the only choice is between the
WHOLE row (expensive) and nothing (free), and free wins every time; the narrow `--field` read is what
makes reading the row cheaper than skipping it. `contract list --state DISPATCHED` lists turns still
open; `contract list --cut` lists every turn that did not close cleanly, naming the specialist and the
lane; `contract view --harness-id <agentId>`, with the id the dispatch returned, gives that turn's own
partial evidence. Before re-dispatching or judging where a plan's spend went, `usage show --plan <id>`
gives its tokens per session and per agent, split into bound, unbound and main (main is an upper bound);
`brief history <slug>` shows how the brief's ACs, decisions and plan versions changed.

## Reading order

**Phase 0 — triage, always, cheap.** Read the row with `gaia contract view --contract-id <id> --row
--json` -- `agent_state`, `cut_reason` and `harness_agent_id` together; `--field` reads the envelope
only and exits 1 on a row column -- then `gaia contract chain --contract-id <id>` for continuation.
`agent_state` routes everything downstream. `cut_reason` says
whether that state is honest: it is stamped at birth and cleared only by a clean finalize, so
clean-versus-cut is recorded by design, not inferred; a cut row keeps whatever state it last mirrored,
so `agent_state` alone never says the turn is alive. `never_finalized` means the turn never closed
itself; `backstop_capture` is the stop hook's capture; `reaped` has two authors -- the stop hook, for a
turn that stopped without its own close, and SessionStart's host-death reaper
(`hooks/modules/agents/dispatch_lifecycle.py::reap_stale_turn`), for a DISPATCHED row with no tool call
inside two hours (`gaia contract reap --older-than-seconds N` runs it by hand); `salvaged_truncation` is
rebuilt purely from the agent's incremental on-disk draft — which is why incremental writes are what
survive a cut. The chain says whether "empty" means anything: a continuation row is born
empty by design (Trap 3), and reading this before judging any field as empty is what keeps that design
from reading as a failure. What the row does not hold was never delivered — an `open_gaps` entry the
agent only narrated in its message did not survive the turn; treat the row's silence as the finding it
is rather than reading the prose as if it had been persisted.

**Phase 1 — load what that state demands, one block.**

| `agent_state` | Load |
|---|---|
| `APPROVAL_REQUEST` | `approval_request`, and nothing else matters until the user signs. Load `orchestrator-present-approval`; do not execute. |
| `NEEDS_INPUT` | `pending_steps` and `next_action`: the turn is stuck on an answer the orchestrator must obtain -- from the user or from context it holds -- and resuming the SAME agent keeps its draft. |
| `BLOCKED` | `failure_report` and `open_gaps`, read with the rule proven true today: the block is usually a stale premise in the dispatch itself — information about the dispatch, not about the agent. Route only when another owner can resolve it. |
| `NEEDS_VERIFICATION` | Not "done": a plan-task-bound turn cannot seal itself. First read `open_gaps` and `pending_steps` against the task goal: a gap that contradicts the goal, or a declared check left unrun, resumes the producer -- a verifier would only rediscover it, and a gap declared costs one round where a hidden one costs two. Otherwise dispatch `gaia-verifier`, binding `parent_handoff_id` to this producer row. What a verifier promotes is the task's gate (`gaia task gate set-status`), never the row, which stays as it closed; a fail returns the task to the producer with evidence. An unbound `NEEDS_VERIFICATION` (no `plan_task_id`, no `parent_handoff_id`; finalize now refuses it, so only older rows carry it) is a producer that took the free door: resume it to close `COMPLETE` or `BLOCKED`, and dispatch no verifier for a row no task owns. Never accept a plan-task-bound producer's self-asserted `COMPLETE` as independent verification. |
| `COMPLETE` | `evidence_report.verification` first, then `open_gaps`. Terminal: relay/synthesize the verified result. |

Only `COMPLETE` is terminal. A finalized `IN_PROGRESS`, `APPROVAL_REQUEST`, `BLOCKED`, `NEEDS_INPUT`,
or `NEEDS_VERIFICATION` row is persisted, not complete.

For COMMAND_SET, reconcile `approval_request` with DB progress: exact ordered commands, `next_index`,
consumed indexes, failed index/reason, and grant status. Do not infer that the set ran merely because
it was approved. After a partial failure, report completed and untouched indexes distinctly, treat `FAILED` as a
terminal/frozen grant, and route fresh investigation plus a new request/approval for any
retry or remainder.

**Phase 2 — the findings.** `key_outputs`, and `report_prose` when the why is needed or when this
row's coordinate is about to be handed to another agent. Read `memory_delta` too: its proposals are
the specialist's, not yet memory, and what is kept, merged or dropped is yours to curate per
Skill('memory'). The dispatch result flags a non-empty one with a line, `N project facts proposed
for <project> — contract <id>`; a result without that line carries none. A project-context proposal
(`update_contracts`) is curated by you too, flagged by its own line, and if you accept it you apply it
with `gaia context accept <contract-id>`.

**Phase 3 — evidence on demand, only for the claim about to be made.** `commands_run` and
`verbatim_outputs` for a positive; `patterns_checked` for a NEGATIVE; `cross_layer_impacts` before
touching anything adjacent. A row's claim about an ARTIFACT'S CONTENTS is a claim, never the
contents: a `key_outputs` line saying the file now reads X is the agent's reading of it, and
relaying that as the file's text skips the one step that could have caught a misreading. Relay it
only after the `verbatim_outputs` excerpt that shows it, or after opening the artifact yourself; if
neither happened, say it as the row's claim rather than as the artifact's content.

External state — a PR's CI, a deploy, a reconcile — reaches you as a snapshot in the row: the
literal table in `verbatim_outputs`, the commit SHA it covers, and the time it was seen. Before
calling a PR green, compare that SHA with the PR head you are about to report. A mismatch means the
evidence is stale: it describes a commit that is no longer the head. Answer it with a re-dispatch of
the owning specialist, never with a reading of the forge of your own.

**Phase 4 — handoff.** Pass the COORDINATE, not the narrative. Ids that arrive in a return — a
contract, a handoff, a plan, a slug — are appended to the project's thread row before answering,
so the coordinates survive the session.

## When the return is not clean

| Situation | Action |
|---|---|
| A specialist's command was blocked by a hook | Relay the hook's message verbatim when resuming it — a paraphrase drops the `approval_id` or softens "do NOT retry", and the specialist follows your version instead of the security layer's. A denial with NO `approval_id` is categorical, not a tier decision: `orchestrator-present-approval` says what it is and what it is not |
| A contract claims verification without evidence | Open the artifact yourself, or re-dispatch narrowly declaring that pasted output is the only evidence that counts |
| A return arrives truncated, empty, or repeated with no new tool calls | Read the rows — `contract list --cut`, then `contract view --harness-id <agentId>`, which resolves the latest link of a resume chain and lists every link in `links` — and re-dispatch only what the rows are genuinely missing; a stalled turn shows zero delta in tool-call count |
| A turn in flight is heading the wrong way | Correct it in flight, continue it once it closes, or dispatch fresh — `dispatch` principle 8; the return is not the only moment to act |
| A verification fails | Its cause decides: `product` sends the fix to the owning surface with the refuting probe's evidence id, and the producer adds that probe to the suite as its red run before changing code, so the finding stays a test; `environment` or `broken_test` makes the check itself the object — a mis-designed gate is not a code defect, and whether it guards something a user or a release would feel decides between mending it and cutting it, before any fix is re-dispatched; `requirement_changed` goes to `gaia-planner`, whose gate wording claims more than its task |
| A verifier leaves a user-judged gate pending | Ask the user and record the answer with `gaia task gate set-status` and its evidence, as `gaia-planner` step 5 lays out |
| A specialist refutes a premise the user stated | The refutation reaches the user first, with the evidence id that carries it, never filtered by the intent: the goal carried the premise as a claim (`dispatch`), and the refutation is the deliverable it owed |
| Several agents are in flight | The consolidated result, said once when all have returned; an interim finding that stands alone may be reported early, a pending result is awaited rather than anticipated |

## Three traps, all measured today on real rows

1. **`evidence_report.verification.result == "pass"` means the check RAN, not that the artifact is
   good.** A real row closed `pass` over a document rejected for three blocking defects. A reader who
   SWEEPS rows looking for `pass` reports the opposite of what happened. The row is read, not swept.
2. **`patterns_checked` is the only field a zero-result search survives in.** If the claim about to be
   made is that something does NOT exist, this is the only possible evidence for it.
3. **A continuation row is born empty by design.** That is exactly why `cut_reason` and
   the chain are read in Phase 0, before any field is judged empty — and why
   completeness is judged over the CHAIN, never the last link alone.

## Relaying to the user

A specialist answers you, never the user: nothing it writes reaches the user's display before you
handled it (`decision_gaia_especialistas_responden_al_orquestador_no_al_usuario_20261022`). Its
`conclusion` is its verdict for you, in its technical register, and arrives once on both hosts: as
`conclusion: <text>` on the dispatch result's contract summary line when the turn closed before that
result, otherwise -- a background dispatch -- as `<agentId>: closed <state>, conclusion: <text>` in the
next prompt's context, at most three per prompt and the rest named by their reader; `contract view --field conclusion
--harness-id <agentId>` reads it again (a row closed before the rename answers from its
`user_facing_summary`). Treat it as a claim, not as copy: validate it against the row's evidence, the
artifacts it names and the user's intent, discuss with the specialist when they disagree, and only then
answer the user in plain language in your own words. At N>1 the conclusions are inputs to one synthesis.
Preserve ownership boundaries and do not hide evidence the row and the verdict disagree on.
