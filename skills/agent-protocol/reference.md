# Agent Protocol -- Reference

Mechanics behind `SKILL.md`: the two state machines, where each moment's answer
lands in the envelope, the dispatch kernel field by field, how the contract is
stored and recovered, and the edge cases. Read on demand; the principles in
`SKILL.md` are what a turn needs to run.

## The two state machines

A turn runs two machines at once, and they never collapse into one.

**`agent_status.agent_state` -- the communication machine.** How the turn
currently reports back. It feeds routing and the finalize gate.

| State | Meaning |
|---|---|
| `IN_PROGRESS` | Work can continue. Not a closing state -- `finalize` refuses it. |
| `BLOCKED` | An external obstacle prevents progress. |
| `NEEDS_INPUT` | A material choice only the user can make. |
| `APPROVAL_REQUEST` | Exact T3 consent is handed off. Requires a non-empty `approval_request.exact_content`. |
| `NEEDS_VERIFICATION` | A producer result handed to an independent verifier. |
| `COMPLETE` | Terminal, and the only terminal one. |

Canonical list: `VALID_PLAN_STATUSES` in `gaia/state/__init__.py`, re-exported
by `hooks/modules/agents/response_contract.py`.

**`work_phase` -- the work machine.** Where the producer is in the work itself:
`framing` -> `investigating` -> `planning` -> `executing` -> `verifying`
(`VALID_WORK_PHASES` in `gaia/contract/validator.py`).

The two are orthogonal: a turn sits at `agent_state=IN_PROGRESS` through every
one of its five work phases. `work_phase` is optional at the schema level --
`WORK_PHASE_SHAPE` fires only when the field is present and outside the enum --
which is what makes it safe to omit a phase that did not happen rather than
simulate it.

## Where each moment's answer lands

The envelope is a form with one section active at a time. This maps the work
machine's moments onto the sections they fill; several sections span more than
one moment, because the work does.

| Moment | Section |
|---|---|
| `framing` | No section of its own. Its output is one `evidence_report.key_outputs` note: the goal restated, and what the available context lets you do about it. |
| `investigating` | `evidence_report` -- `patterns_checked`, `files_checked`, `commands_run`, `key_outputs`, `verbatim_outputs`, `open_gaps`. Keeps accumulating once execution starts. |
| `planning` | `agent_status.pending_steps` (the ordered list of remaining pieces); `approval_request` when a COMMAND_SET is required -- opened here, plan-first. |
| `executing` | `evidence_report` continued; the runtime's typed COMMAND_SET progress fields; `failure_report` when something breaks. |
| `verifying` | `evidence_report.verification`; `consolidation_report` for multi-surface work; the closing `agent_state`. |

`pending_steps` doubles as the progress meter. Retire an entry as its piece
finishes (`gaia contract set agent_status.pending_steps '[...]'` with the
remaining entries only): the list shrinking is what an observer polling the
contract mid-flight sees as progress, and `COMPLETE_SHAPE` requires it empty at
the close anyway.

Keep approval payloads inside `approval_request`; do not copy command text into
evidence fields.

## The dispatch kernel, field by field

`# Your Contract` names a contract that already exists -- as a row in gaia.db
and as an on-disk draft -- before you run anything.

| Field | Meaning |
|---|---|
| `contract_id` | The id passed as `--draft-id <contract_id>` on every `gaia contract` call. It addresses both the row and the draft. |
| `agent_id` | Copied verbatim into `agent_status.agent_id` whenever state is declared. Shape: `a` plus 16 or more lowercase hex characters. |
| `goal` | The assignment, whole and bounded. Nothing outside it belongs to this turn. |
| `role` / `surface` | The turn's relationship to the task, and the surface that owns it. |
| `project` | The project this turn is about, as `name (/abs/path)`. Dispatch data first (the orchestrator's `project=<name>` token), cwd resolution only as fallback. A name the substrate does not know yet appears bare, with no path suffix. Absent when the dispatch named no project and the cwd matched none. |
| `can_read` / `can_write` | The menu of project-knowledge sections this turn may pull on demand and cite, and the ones it may propose updates to. Nothing from the menu arrives preloaded. |
| `plan_task_id` + `acceptance` | Present only on a plan-task-bound turn: the binding and the verifiable floor the increment must clear. Such a turn cannot self-`COMPLETE`; it closes `NEEDS_VERIFICATION`. |

### The agent_id floor is measured

`AGENT_ID_MIN_HEX = 16` in `gaia/contract/validator.py` is not a convention.
Cross-session handle collisions fall off a cliff with length, because a biased
model only collides where it can compress the digits it has to invent: 6 hex
collided on 27 of 82 handles (32.9%), 7 hex on 12 of 103 (11.7%), 17 hex on 0
of 2658 (0.0%). Sixteen is the smallest floor comfortably inside the
zero-collision regime, and is exactly what `secrets.token_hex(8)` produces --
which is what `gaia contract init` mints when no `--agent-id` is supplied.

The floor gates what an agent may mint for a NEW turn. Historical rows keyed by
a shorter handle are read back by exact string and never re-validated, so no
grandfathering window exists or is needed.

### Project knowledge is pulled, not preloaded

The kernel deliberately carries data about the turn, not about the project.
Read one section from the `can_read` menu with
`gaia context get-contract --section <s>` (`--workspace <w>` when the workspace
is not the cwd's; `--section` is required; `--text` for the human form). It
resolves against `project_context_contracts.contract_name` -- the exact names
the menu lists -- and exits 1 naming the available sections when the name does
not exist.

Its sibling `gaia context get --section <s>` is a different namespace: it
resolves against the workspace SHAPE (`apps`, `services`, `git`,
`environment`, ...) and never reaches these sections. The names overlap without
meaning the same thing -- `gaia context get --section stack` returns the
shape's empty scanner placeholder `{}`, while the real `stack` payload comes
only from `get-contract`.

## Storage and recovery

**Birth.** The `Task` PreToolUse hook creates the `agent_contract_handoffs` row
for the dispatch; SubagentStart claims it and renders the kernel. The row and
its draft are open before the agent's first token.

**Adoption.** `gaia contract set` / `add` / `fill --json --draft-id <id>` writes
the draft AND mirrors the partial envelope onto the born row, which is what
makes evidence reach the database while the turn is still running. There is no
separate adopt verb.

**Close.** `gaia contract finalize --draft-id <id>` validates and converges the
born row idempotently (add `--plan-task-id <id>` when the turn executes a plan
task). It accepts any closing state -- `APPROVAL_REQUEST`, `BLOCKED`,
`NEEDS_INPUT`, `NEEDS_VERIFICATION`, `COMPLETE` -- and refuses `IN_PROGRESS`,
the one state that says the turn has not ended. `gaia contract validate
--draft-id <id>` runs the same shape check without persisting.

Do not pass `--session-id` unless the dispatch handed one over: the born row
already carries the session attribution, and an invented value (the literal
`unknown`, say) corrupts it.

**The gate at the wall.** `_resolve_subagent_stop_gate_full` in
`hooks/adapters/claude_code.py` resolves this turn's own dispatch row first and
decides in three cases:

1. Row reachable and cleanly finalized -> its persisted envelope is validated
   (`GATE_SOURCE_ROW`). The row wins over the fence unconditionally, in both
   directions.
2. Row reachable but not cleanly finalized, and the stop was not a harness
   truncation -> reject (`GATE_SOURCE_ROW_UNFINALIZED`), regardless of what the
   fenced block in the final message says.
3. No row reachable at all -> the fenced `agent_contract_handoff` block is the
   fallback (`GATE_SOURCE_FENCE`).

The fenced block in the final message is therefore still required output, but
it decides the close only in case 3.

**The reaper is not a second way to close.** A turn that stops after its last
`fill` leaves the row where the draft left it: open. The SubagentStop persister
(`hooks/modules/agents/handoff_persister.py`) eventually converges it with
`degraded` + `reaped` -- forensic cleanup that records that `finalize` was
never called. It never earns `COMPLETE`.

**Recovering a cut turn.** `gaia contract list --cut --json` lists cut rows;
`gaia contract view --draft-id <contract_id>` or
`gaia contract view --harness-id <harness_agent_id>` prints the envelope,
recovering accumulated evidence from `raw_handoff_json` when no draft file
remains. `view` never writes, so it is safe to point at any row, historical or
cut. `gaia contract view --field <dotted.path>` prints one subtree, exiting 1
when the path does not exist -- an existing-but-empty field and an absent one
are never the same response.

## Edge cases

**Build order for a terminal state.** Validation runs on every write against
the FULL envelope, not just the field being set. So fill the fields a terminal
state depends on FIRST -- `evidence_report.verification` for `COMPLETE`,
`approval_request.exact_content` for `APPROVAL_REQUEST` -- and set
`agent_status.agent_state` to the terminal value LAST. Reversing the order
rejects the `set agent_state COMPLETE` call with `VERIFICATION_RESULT`. A
rejected write leaves the draft at its last-known-good state, but a terminal row
is immutable once `finalize` persists it.

**The turn that never leaves framing.** A question answered entirely from
already-known context sets `work_phase=framing` once and closes `COMPLETE`
having touched no other phase. `COMPLETE` has no `work_phase` precondition --
only `COMPLETE_SHAPE` and `VERIFICATION_RESULT` gate it. `finalize` still runs.

**No contract at dispatch.** A dispatch outside this harness, or one whose born
row could not be claimed, arrives with no `# Your Contract` block. Then and only
then: run bare `gaia contract init` (no `--agent-id`), reuse the identity it
mints, and work the protocol identically. An unclaimed born row, if one exists,
is closed by the stop-hook persister, never by the agent.

**A failed COMMAND_SET is frozen.** Stop on the first non-zero or mismatched
result. Preserve the completed, failed and untouched indexes with the exact
command, exit status and stderr; do not claim the effect occurred. The grant is
terminal -- it cannot authorize a retry or any remaining index. Continuing
requires fresh read-only investigation and a new request-set for every retry or
remainder command still needed.

**Blind verification has two seams.** The SubagentStop gate
(`_blind_verification_required` in `hooks/adapters/claude_code.py`) and the CLI
finalize path (`cmd_finalize` in `bin/cli/contract.py`, resolving the binding
via `dispatched_binding_plan_task_id_by_contract`) apply the same decision, so
neither is a bypass of the other. The decision is a pure function of
`(agent_state, plan_task_id)` -- not of the agent's role, not of the turn's
kind. A turn with no `plan_task_id` (investigation, memory, a free-standing
verifier turn) is unbound and may self-`COMPLETE`.

**A verifier turn binds differently.** It carries `parent_handoff_id` and no
`plan_task_id` of its own, which is what lets it promote the producer's
`NEEDS_VERIFICATION` to `COMPLETE`. Rejecting instead sends the increment back
to `IN_PROGRESS`.
