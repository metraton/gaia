---
name: session-channels
description: Use when a section of the birth block or a line beside a prompt arrives and its meaning is not held -- `## User memory`, `## Due`, `## Environment`, `## Workspaces I manage`, `## Resume point`, `~ user memory changed:`, `~ project memory changed:`, `🔔 N notifications due`, a `closed <state>, conclusion:` line, a task result line, a denial, a ceiling notice, an install notice -- or after a compaction, to re-read what the new birth carries. A lookup for the orchestrator, not a procedure.
---

# Session channels

The birth block and the per-prompt lines carry values, never explanations; this document is the explanation. Each row names
the renderer that writes the line, so the row can be checked against it when a line looks different. The rows that change the
orchestrator's behaviour on sight are repeated in its identity, *What changes my behaviour on sight*; the rest lives here only.
The per-prompt lines follow one rule of relevance: the preferences bind every answer; the rest is news, heard whole once at
birth or after a compaction, then raised only when its subject meets the topic at hand or at a natural pause, never each turn.

## What I am born with

A birth block arrives at session start -- on Claude Code before the first turn, on OpenCode
attached to the session's first user message -- and again after a compaction, a resume or a
fork (`hooks/modules/session/session_manifest.py::build_session_context`). These sections
reach the orchestrator and no specialist.

| Section | What it means | Renderer |
|---|---|---|
| `## User memory` | The user's live facts and preferences, grouped by `kind=`: each line a title and `ref=memory:user/<slug>`, and `gaia memory show <slug>` opens its why. A preference's title is the rule itself; `surface=conversation` marks the ones that set the form of every answer, listed first and never cut; `until=` says how long it holds. | `hooks/modules/context/user_sections.py::USER_HEADER` |
| `## Due` | Reminders and routines due now, reports excluded: `overdue=N`, then one line each with its title, `due=` or `repeat=`/`missed=`, `late=`, `ref=` and `id=`. Named to the user once, briefly, at birth; `Skill('reminders')` closes each. | `hooks/modules/session/session_manifest.py::build_due_section`, `::due_item_line`; the rule is `gaia/store/reader.py::due_now` |
| `## Environment` | Where the session stands: machine and `host=`, `gaia=` version and channel, `cli=`, `cwd=` (home-relative), `session=`, `zone=`, `data=`/`db=` -- none of which changes mid-session. `tools=` lists the user's tool rows, then Gaia's detected catalog, then executables under `$HOME` minus package console scripts: what a specialist can count on running here, current as of session start. Each `tool=<binary>` line is a program built for the user, present while its binary is on PATH: its title says how to use it, `replaces=<cli>` means the hooks judge it as that CLI, and `ref=` opens its row. Nothing in it is the orchestrator's to invoke; its lane admits the `gaia` binary alone. | `hooks/modules/session/session_manifest.py::build_environment_section`, `hooks/modules/context/user_sections.py::tool_lines` |
| `## Workspaces I manage` | Every workspace, its groups and their projects, busiest first. `name(N)` is N live pendings; a name with no count has nothing pending, not nothing tracked. `+N` is N more projects the index did not list -- look them up, never assume them absent. A name the index does not place goes to `gaia map <word>`; a project's card is `gaia context project <name>`. | `hooks/modules/session/session_manifest.py::build_projects_section` |
| `## Resume point` | Only after a compaction, a resume or a fork, never at startup or clear: this session's ledger, written from the database. `session=` and `compactions=`, then `in_flight:` contracts not COMPLETE, each with its `state=` and `ref=contract:`; `pending:` signatures waiting or approved and unused, each with its id and `state=`; `written:` memory rows, each with `ref=memory:`; `changed:` what other sessions changed in user and project memory since this session was born, each with its place, op, `kind=`, title and `ref=`; `branches:` worktrees this session's agents opened, each with its commit; `(+N)` is N more. `note:` is the orchestrator's own hand note (`gaia session resume-point set`), shown only when this session wrote it. | `hooks/modules/session/session_manifest.py::build_resume_section`, `gaia/session_ledger.py::render_resume_point` |

Live counts never reach the birth: `gaia plan list --status active`, `gaia contract proposals
list`, `gaia approvals list`.

## What reaches me during a session

| Channel | What it is, and what to do | Renderer |
|---|---|---|
| `~ Your conversation preferences:` | The titles of the user's preferences marked `surface=conversation`, beside every prompt and not news: they set the form of this answer over the host's output style and norm 7's shape, and the answer is checked against them before it is written. At most four such preferences exist, and their titles always arrive in full, within 660 characters; past four rows the line adds how many more there are, with their coordinates. `Load Skill('human-register')` is appended until that skill is loaded after the last compaction. | `hooks/modules/session/session_lifecycle.py::preferences_line`, `hooks/modules/context/user_sections.py::preferences_line` |
| `~ user memory changed:` | Another session added, updated or retired a row of the user's own memory: one line each, op, `kind=`, quoted title, `ref=memory:user/<slug>`. Read every one and act on one that changes behaviour; mention one only when its subject comes up. | `gaia/signals.py::render_line`, delivered by `hooks/modules/session/session_lifecycle.py::signals_line` |
| `~ project memory changed:` | Another session changed a project's memory: lines grouped under the place `## Workspaces I manage` names (`ws/project`, `ws/group`, `ws`), three per place, then `ws/project ×N`. A place being worked on is placed as a name is placed and opened before deciding; the rest is awareness, connected when that project is named. `gaia signals` lists them all. | same |
| `🔔 N notifications due` | What `## Due` shows, by the same rule: up to three due, `🔔 N notifications due:` followed by their `## Due` item lines; more than three, the count with `gaia notifications list --unread` to see them. The line repeats on every prompt while they stay due, so it is news, not an order: named once at birth or after a compaction, then raised only when one touches the topic at hand or at a natural pause, never the whole list mid-topic; `Skill('reminders')` closes each. | `hooks/modules/session/session_lifecycle.py::notifications_counter` |
| Conclusions and proposed facts | A specialist closed while the orchestrator was not reading: its verdict and the facts it proposes for memory. The verdict is validated against its row before it is relayed; the orchestrator decides which facts are kept. | `hooks/modules/session/session_lifecycle.py::conclusions_lines`, `gaia/conclusions.py::take_lines` |
| The task result line | `state=...` is the specialist's closing state; `launched, not finalized yet` means it is still running or was cut: read its row before waiting or dispatching over it. | `hooks/modules/agents/task_result_observer.py::build_contract_summary_line`, `::_launch_pointer_line` |
| Denials and remediation | A refusal with an `approval_id` is a consent request, presented through `Skill('orchestrator-present-approval')`; one without is categorical, nothing to sign, and its `Rule:`/`Remediation:` line says the route that remains. | `hooks/modules/security/verdict.py::render` |
| The tool-call ceiling | A specialist nearing its call budget is told to record and close; a partial close names what remains, which the orchestrator routes. | `hooks/modules/agents/call_ceiling.py::NOTICE_PERCENT` |
| SessionStart install notices | The install changed or drifted: an upgrade, a reconcile, leftovers. Tell the user what matters; never repair the install from the conversation. | `hooks/modules/session/session_lifecycle.py::_reconcile_install` |
