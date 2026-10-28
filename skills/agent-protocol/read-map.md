# The Read Map

What a turn can read, with which verb, and what comes back.

A goal increasingly arrives carrying COORDINATES instead of a retelling: a contract id, a memory
slug, a brief name. A coordinate is only worth what the reader can open, and a read verb is the one
kind of capability that never teaches itself -- a blocked mutation produces a rejection that names
the rule, while a read nobody knows about produces no error at all, only absence. This file is the
one place that vocabulary is written down; everywhere else points here.

## Two rules that decide whether any of it resolves

**A read by slug starts from a WORKSPACE, and the workspace comes from the current directory.** A
workspace is a folder the user declared, a group is a folder inside it holding
one product's, client's or theme's projects, and a project is one git repository. A
memory slug verb (`show`, `story`, `append`, `reclassify`, `delete`, `link`) looks in your
workspace, then in the user and host scopes, then in whichever workspace holds the slug as a row of
a project (`bin/cli/memory.py::_workspace_holding`): a project's memory follows the project, so a
slug `get-relevant --initiative` printed opens from any directory. An explicit `--workspace X` is
only where the search starts: when X lacks the slug, the same fallback still finds the project row
stored in another workspace. A row with no project never
crosses: from another workspace it is `not found in workspace '<ws>'`, and `--workspace <name>`
reaches it. A slug held by project rows in two other workspaces is refused with code
`ambiguous_slug` naming both, answered with `--workspace`. `gaia workspace current` prints which one
you are in. A brief named to the coordination verbs (brief, plan, task, ac, evidence, milestone)
without `--workspace` is likewise looked up in the other workspaces when yours lacks it -- a name
two of them hold is an error naming both. `gaia memory search` stays scoped to your workspace and
the two sentinels. When a coordinate does not resolve, check the workspace before concluding the
row is gone -- almost every verb below takes `--workspace`.

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
| `gaia contract list` | `--harness-id`, `--contract-id`, `--session`, `--agent-id`/`--agent`, `--agent-name`, `--grep <text>`, `--plan-task`/`--task`, `--state`, `--cut [reason]`, `--workspace`, `--since`/`--until`, `--limit`, `--json` | One row per turn: id, agent, state, cut reason, session. The searchable handle when you know the front but not the row; `--grep` matches the goal, description or conclusion, `--agent-name` the dispatched name (open and cut rows only). |
| `gaia contract view` | `--draft-id`/`--contract-id <contract_id>` or `--harness-id <harness_agent_id>`; `--field <dotted.path>`; `--row`; `--json` | The whole envelope, or exactly one subtree named by the same schema keys the envelope uses (`evidence_report.open_gaps`, `agent_status.agent_state`); `--row` prints the row's goal, description, agent name, project, session and state instead. Never writes. |
| `gaia contract chain` | `--contract-id <any link>` | Every contract in one continuation chain, oldest first, walking back to the root and forward to the live link. A turn that was never resumed prints as one link. |
| `gaia contract proposals list` | `--contract-id`, `--kind memory\|context\|thread`, `--all`, `--json` | What closed turns proposed and nobody has decided yet, oldest first, each with its id and proposing contract; `--all` adds the accepted and dismissed. Deciding one (`accept`/`dismiss`) is the orchestrator's write, not a read. |

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
| `gaia memory search '<term>'` | `--workspace`, `--type`, `--class`, `--all`, `--include-superseded` | FTS5 across curated rows and episodes. Plain words match a row holding ANY of them, rows holding more of them first, stopwords dropped from a multi-word query; `--all` requires every word; FTS5 syntax passes as written; a superseded row is hidden unless `--include-superseded`. Searches description and body, not the slug. The handle to use when you have a topic and not a name. |
| `gaia memory list` | `--type project\|user\|feedback\|atom\|decision\|negative`, `--include-superseded`, `--json` | Name, type and one-line description per live row -- the index, not the bodies. A superseded row shows only with `--include-superseded`, naming its successor. |
| `gaia memory story <slug>` | `--workspace` | The row's lineage as one fused timeline: related/derivative/graduated nodes with depth, then events in order. |
| `gaia memory get-relevant` | `--sections carry_forward\|anchor\|thread_open`, `--initiative <k>` or `--project <name>` | The compact block SessionStart injects, on demand; with a project, its whole live-pending set from every workspace; `--initiative <k> --sections anchor` returns that project's live standing notes with their bodies, from every workspace, a superseded one excluded. |
| `gaia memory stats` | `--json` | Counts per type (live and superseded) and FTS coverage, no conflicts scan. |
| `gaia memory conflicts` | `--owner`, `--exclude-class`, `--limit`, `--threshold` | Candidate contradictions across rows, with one slug held in two workspaces listed apart as duplicates. |
| `gaia memory episode-show <episode_id>` | -- | One episode with score, age, tags and retrieval count. Episode ids come from `gaia query --surface episodes`. |

## Project knowledge -- two namespaces that share section names and are not the same

The dispatch project's `can_read` sections are preloaded as the kernel's `# Project context` block
(whole when they fit, else pointer lines, else one line; a group dispatch gets one pointer per
member section). What the block only points at is pulled
with exactly one verb.

- `gaia context get-contract --section <s> [--project <name>] [--workspace <w>] [--text]` is the ONLY
  verb that reaches `project_context_contracts` -- the names the `can_read`/`can_write` menu lists.
  `--section` is required; `--project` reads that project's section and falls back to the workspace
  row; `--workspace` when the workspace is not the cwd's; `--text` for the human form. It returns
  the contract payload; an unknown name exits 1 listing the available ones.
- `gaia context project <name>` returns one project's sheet by name or basename, exact before
  ignoring case, read-only: its `projects` row and facets, its `project_identity` entry, and an
  index (slug and description) of at most 10 memory rows anchored to it. It prints no other section;
  read those with `get-contract --project <name> --section <s>`. A group name answers as its
  members instead: each with the sections it holds itself and the
  `get-contract --project <member> --section <s>` line that reads one, plus the workspace sections
  a member without its own falls back to (`--json` for the same as data). `get-contract --project
  <group>` exits 1 naming the members. Both verbs are in the orchestrator's lane, so the
  orchestrator reads any project's sections itself.
- `gaia context get --section <s>` and `gaia context show` resolve against the workspace SHAPE
  (`apps`, `services`, `stack`, `git`, ...). The names OVERLAP without meaning the same thing:
  `gaia context get --section stack` returns the scanner placeholder `{}` while the real `stack`
  payload comes only from `get-contract`.
- `gaia context query "<SELECT ...>"` runs one read-only SELECT against the substrate. Reserve it
  for what no verb above answers; it is not in the orchestrator's lane (see the asymmetry below).

## Units of work

| Verb | Addressing | What comes back |
|---|---|---|
| `gaia brief show\|list\|search\|deps\|verify` | brief slug (`search` takes a term) | The brief as markdown with its ACs; the index; FTS hits; the dependency graph; invariant violations one per line (`--json` for the structured report). |
| `gaia brief decision list <brief>` | `--json` | The brief's current decisions, apart from the ones they superseded. |
| `gaia brief history <brief>` | `--json` | The brief's change history, oldest first: AC added, edited or removed (with before/after in `--json`), decisions, replaced plan versions. Rows marked reconstructed predate v58. |
| `gaia plan show\|list` | brief slug | The plan attached to that brief, with plan_id, status, version and pause reason; or every plan in the workspace. |
| `gaia plan history <brief>` | `--content` | Every replaced version of the plan with the reason it was replaced. |
| `gaia plan change list <brief>` | `--json` | The plan's managed changes: justification, status, and the proposed tasks with their reasons. |
| `gaia task list <brief>` | `--plan <brief>` (same as the positional), `--status`, `--format table\|json\|count` | One row per task: ORDER (plan position) and TASK_ID (`tasks.id`) -- they are different numbers and dispatch wants the second. |
| `gaia task show <brief> <order>` | `--json` | One task, printing both numbers with that distinction stated. |
| `gaia task gate list <brief> <order>` | `--json` | The task's verification gates: id, type, status; `--json` adds the shape, the stale mark and the fail cause. |
| `gaia evidence list --brief <b>` / `gaia evidence show <id>` | -- | Recorded per-AC evidence. |
| `gaia worktree list` | `--repo`, `--json` | Every Gaia-managed worktree for a repo (default: cwd), under `<workspace>/.project-worktrees` or the legacy `~/.gaia/worktrees`: identity (contract_id, agent_id), branch, commit, lifecycle. |
| `gaia worktree show <path>` | `--json` | One worktree's identity. `create`/`release` are the specialist's own writes, not a read -- see `gaia-patterns`. |
| `gaia worktree show-capture <contract_id>` | `--diff`, `--json` | The artifact (path, sha256, size) a dirty brief-less worktree's diff was captured to at release; `--diff` prints the diff itself. |

## Across surfaces, and the operational record

| Verb | Addressing | What comes back |
|---|---|---|
| `gaia query` | `--surface memory\|episodes\|harness_events\|all`, `--since`/`--until`, `--last`, `--agent`, `--type`, `--command-like`, `--failed`, `--group-by surface\|agent\|type\|day`, `--count`, `--format` | The merged event reader over curated memory, agent episodes and the hook log. `--group-by` aggregates instead of listing. |
| `gaia history` | `--today`, `--blocked`, `--agent`, `--limit` | Recent agent sessions: time, agent, truncated task, end status, approximate tokens. |
| `gaia defects` | `--origin`, `--type`, `--severity`, `--agent`, `--since`, `--count` | Failures one row at a time, never aggregated -- subagent anomalies plus hook-log failures above `info`. |
| `gaia metrics` | `--range`, `--since`/`--until`, `--agent` | The aggregate dashboard behind those rows: tier usage, commands, per-agent totals, anomalies. |
| `gaia usage show --plan <id>` / `--session <id>` | `--since`/`--until`, `--json` | Tokens per session and agent, one row per API message from the transcripts; for a plan, split into bound, unbound and main (main is an upper bound). |
| `gaia now` | `--json` | The machine's local time, UTC offset and zone name; `--json` adds the UTC instant. Opens no database. The clock to read before fixing a reminder's absolute time. |
| `gaia map` | an intent key, e.g. remember | Computed from the database, never writes: NOW (only what has live state -- due reminders and routines, pending proposals, turns dispatched and never closed, unread signals for this session, active plans with their next task -- each with the verb that moves it; omitted when empty), the five habits, and the intent-first YOU WANT -> USE table. With an intent, that one row with its exact command and one example. On demand, not a signal channel. |
| `gaia status` | `--json` | What Gaia has wired into this workspace: last agent, pending context updates, contract success rate. |
| `gaia doctor` | `--workspace <path>`, `--json` | Health checks, read-only. `--fix` MUTATES and is a different verb in every sense that matters. |
| `gaia lint comments` | `--repo`, `--head`, `--base`, `--json` | Net comment and docstring lines a branch adds over its merge-base against max(2, 2% of added code); lists each offender as file:line. The check a `git push` of the branch is gated on. |

## Consent state, and the channels that outlive the turn

| Verb | What comes back |
|---|---|
| `gaia approvals pending` / `gaia approvals list` | This session's live requests awaiting a decision, with a trailer counting what the scope hides; `list` adds grant state and command count. `--all-sessions` adds other sessions' requests, `--include-stale` the orphaned and expired ones, `--session` names the session. |
| `gaia approvals show <P-id>` | One approval: its derived State and Outcome, requester, window, directory, grant and sealed payload. `--json` returns the same as machine detail. |
| `gaia approvals question <P-id> [<P-id> ...]` | The exact question-tool input for 1 to 4 pending commands, one `[ GAIA-SECURITY ]` line per command (`--details`: each command's Details line); it records which signature it handed out at which position, and the hook accepts only that object asked unchanged. Run it again at any time for the approvals still pending. |
| `gaia approvals history [<P-id>]` | The N most recent approvals, or one approval's full event chain. |
| `gaia approvals stats` | Totals by outcome and the pending verb breakdown. |
| `gaia notifications show <id>` | One report, reminder or routine in full. |
| `gaia notifications list` | Reports, reminders and routines: what is due now, and with `--upcoming` what is coming. |
| `gaia signals` | The curated memory rows other sessions changed, newest first, each with the command that opens it; by default from the start of the last `~ changed:` line, `--since <id>` for another range, `--json`. Never moves the session's cursor. |
| `gaia session preview` | The SessionStart injection manifest — what the orchestrator receives at session start, built read-only with no side effects. |
| `gaia session snapshot --session-id <id>` | One session's open contracts, pending signatures, active brief/plan/task and resume point -- the block compaction carries forward. `--json` returns it structured. Never writes. |
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
