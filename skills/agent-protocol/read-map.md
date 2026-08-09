# The Read Map

What a turn can read, with which verb, and what comes back.

A goal increasingly arrives carrying COORDINATES instead of a retelling: a contract id, a memory
slug, a brief name. A coordinate is only worth what the reader can open, and a read verb is the one
kind of capability that never teaches itself -- a blocked mutation produces a rejection that names
the rule, while a read nobody knows about produces no error at all, only absence. This file is the
one place that vocabulary is written down; everywhere else points here.

## Two rules that decide whether any of it resolves

**A read is scoped to a WORKSPACE, and the workspace comes from the current directory.** The same
verb answers differently from two directories: run from a repo nested inside a workspace,
`gaia memory show <slug>` returns `not found in workspace '<repo>'`; run from the workspace root, or
with `--workspace <name>`, it returns the row. `gaia workspace current` prints which one you are in.
When a coordinate does not resolve, check the workspace before concluding the row is gone -- almost
every verb below takes `--workspace`.

**Absent and empty are different answers, and the verbs keep them apart.** A path that exists prints
its value even when that value is `[]`; a path that does not exist is an error with a non-zero exit.
Do not read one as the other.

## The record of past turns

A turn is a contract. Reading another turn's contract is how judgment is inherited -- its
conclusions, its verification, its declared gaps. It is NOT how operative facts are inherited: the
declared-command fidelity of that population was measured at 6.7% (the measurement and its scope are
in memory, `thread_gaia_coordenadas_en_vez_de_recuentos`), so treat `commands_run` as a claim, not a
transcript.

| Verb | Addressing | What comes back |
|---|---|---|
| `gaia contract list` | `--harness-id`, `--contract-id`, `--session`, `--agent-id`, `--state`, `--cut [reason]`, `--workspace`, `--since`/`--until`, `--limit`, `--json` | One row per turn: id, agent, state, cut reason, session. The searchable handle when you know the front but not the row. |
| `gaia contract view` | `--draft-id <contract_id>` or `--harness-id <harness_agent_id>`; `--field <dotted.path>`; `--json` | The whole envelope, or exactly one subtree named by the same schema keys the envelope uses (`evidence_report.open_gaps`, `agent_status.agent_state`). Never writes. |
| `gaia contract chain` | `--contract-id <any link>` | Every contract in one continuation chain, oldest first, walking back to the root and forward to the live link. A turn that was never resumed prints as one link. |

`--field` is the difference between inheriting a conclusion and inheriting a whole turn. An absent
path is a clean error: `Error: no such field 'evidence_report.nope': no key 'nope' under
evidence_report`, exit 1.

`gaia contract validate --draft-id <id>` is the read of your OWN draft -- the verdict without
persisting. It belongs to the write cycle, not to a coordinate; that cycle is in `SKILL.md` and
`reference.md`.

## Memory

| Verb | Addressing | What comes back |
|---|---|---|
| `gaia memory show <slug>` | `--links` (graph edges), `--history` (versions: when, which fields, body delta), `--workspace`, `--json` | One curated row, whole. The verb a slug in a goal is meant for. |
| `gaia memory search '<term>'` | `--workspace` | FTS5 across curated rows and episodes, scored. The handle to use when you have a topic and not a name. |
| `gaia memory list` | `--type project\|user\|feedback\|atom\|decision\|negative`, `--json` | Name, type and one-line description per row -- the index, not the bodies. |
| `gaia memory story <slug>` | `--workspace` | The row's lineage as one fused timeline: related/derivative/graduated nodes with depth, then events in order. |
| `gaia memory get-relevant` | `--sections carry_forward\|anchor\|thread_open`, `--initiative <k>` | The compact block SessionStart injects, on demand. |
| `gaia memory stats` / `gaia memory conflicts` | -- | Counts and FTS coverage; contradiction scan across rows. |
| `gaia memory episode-show <episode_id>` | -- | One episode with score, age, tags and retrieval count. Episode ids come from `gaia query --surface episodes`. |

## Project knowledge -- two namespaces that share section names and are not the same

Nothing from the project is preloaded. The kernel's `can_read` is a MENU, pulled on demand -- and it
is pulled with exactly one verb.

- `gaia context get-contract --section <s> [--workspace <w>] [--text]` is the ONLY verb that reaches
  `project_context_contracts` -- the names the `can_read`/`can_write` menu lists. `--section` is
  required; `--workspace` when the workspace is not the cwd's; `--text` for the human form. It
  returns the contract payload; an unknown name exits 1 listing the available ones.
- `gaia context get --section <s>` and `gaia context show` resolve against the workspace SHAPE
  (`apps`, `services`, `stack`, `git`, ...). The names OVERLAP without meaning the same thing:
  `gaia context get --section stack` returns the scanner placeholder `{}` while the real `stack`
  payload comes only from `get-contract`.
- `gaia context query "<SELECT ...>"` runs one read-only SELECT against the substrate. Reserve it
  for what no verb above answers; it is not in the orchestrator's lane (see the asymmetry below).

## Units of work

| Verb | Addressing | What comes back |
|---|---|---|
| `gaia brief show\|list\|search\|deps\|verify` | brief slug (`search` takes a term) | The brief as markdown with its ACs; the index; FTS hits; the dependency graph; invariant violations one per line, each naming the command that fixes it. |
| `gaia plan show\|list` | brief slug | The plan attached to that brief, with plan_id and status; or every plan in the workspace. |
| `gaia task list <brief>` | `--status`, `--format table\|json\|count` | One row per task: ORDER (plan position) and TASK_ID (`tasks.id`) -- they are different numbers and dispatch wants the second. |
| `gaia task show <brief> <order>` | `--json` | One task, printing both numbers with that distinction stated. |
| `gaia task gate list <brief> <order>` | `--json` | The task's verification gates: id, type, status. |
| `gaia evidence list --brief <b>` / `gaia evidence show <id>` | -- | Recorded per-AC evidence. |

## Across surfaces, and the operational record

| Verb | Addressing | What comes back |
|---|---|---|
| `gaia query` | `--surface memory\|episodes\|harness_events\|all`, `--since`/`--until`, `--last`, `--agent`, `--type`, `--command-like`, `--failed`, `--group-by surface\|agent\|type\|day`, `--count`, `--format` | The merged event reader over curated memory, agent episodes and the hook log. `--group-by` aggregates instead of listing. |
| `gaia history` | `--today`, `--blocked`, `--agent`, `--limit` | Recent agent sessions: time, agent, truncated task, end status, approximate tokens. |
| `gaia defects` | `--origin`, `--type`, `--severity`, `--agent`, `--since`, `--count` | Failures one row at a time, never aggregated -- subagent anomalies plus hook-log failures above `info`. |
| `gaia metrics` | `--range`, `--since`/`--until`, `--agent` | The aggregate dashboard behind those rows: tier usage, commands, per-agent totals, anomalies. |
| `gaia status` | `--json` | What Gaia has wired into this workspace: last agent, pending context updates, contract success rate. |
| `gaia doctor` | `--workspace <path>`, `--json` | Health checks, read-only. `--fix` MUTATES and is a different verb in every sense that matters. |

## Consent state, and the channels that outlive the turn

| Verb | What comes back |
|---|---|
| `gaia approvals pending` / `gaia approvals list` | Live requests awaiting a decision; `list` adds grant state and command count. |
| `gaia approvals show <P-id>` | One approval: the exact command, verb, age, session, risk, and why it was gated. |
| `gaia approvals history [<P-id>]` | The N most recent approvals, or one approval's full event chain. |
| `gaia approvals stats` | Totals by outcome and the pending verb breakdown. |
| `gaia notifications list` / `gaia notifications show <id>` | The headless-task inbox: what a scheduled or detached run reported back. |
| `gaia schedule list` / `gaia schedule show <name>` / `gaia schedule status` | Registered recurring tasks, their native translation, and desired-state-vs-scheduler reconciliation. |
| `gaia workspace current` / `gaia workspace info` | Which workspace a read will resolve against, and where its storage actually is. |

## Where the membership of this lane is enforced, and the one asymmetry

Lane membership is not a matter of prose. `ALLOWED_READ_PHRASES` in
`hooks/modules/security/gaia_cli_only_guard.py` is the enforced set, and `gaia --help`'s READ lane
epilogue is its human rendering, printed beside the code. **That guard binds the ORCHESTRATOR only**
-- it is closed by default, so a verb missing from it is denied there exactly like one that does not
exist. A specialist is governed by the other lane (`mutative_verbs`), where a read is free because it
is not mutative.

The two lanes do not coincide, and the gap is worth knowing: `gaia contract chain` and
`gaia context query` are live, read-only, and available to a specialist, but are absent from
`ALLOWED_READ_PHRASES` -- so the orchestrator cannot run them and must dispatch. Do not read that
denial as the verb being wrong.

`tests/skills/test_read_map_verbs_are_real.py` holds this file to that machinery in both directions:
every verb named here must exist in the real CLI parser and every flag named must be a real flag of
that verb, AND every phrase in `ALLOWED_READ_PHRASES` must appear here. The second half is what keeps
the map from being quietly incomplete -- a read capability added to the code and not written down
breaks the suite instead of becoming another absence nobody trips over.

One convention keeps that check meaningful: **naming a verb inside a procedure is use; explaining the
vocabulary is this file's job.** A skill that says "read it back with `gaia contract view --draft-id`"
in the middle of its own cycle is using the verb, and that is fine. A skill that explains which verbs
exist, how they address, or what they return is writing a second map, and the second map is the one
that rots.

## What the system does without being asked

The read map covers what you REQUEST. Four behaviors happen TO your contract when you write it,
each announced rather than silent -- recognize them in output you did not ask for. They are
documented where writing is documented: `reference.md`, "Storage and recovery".
