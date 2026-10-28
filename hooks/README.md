# Hooks

Hooks are the event-driven spine of Gaia. Every significant moment in a Claude Code session — a prompt arriving, a tool being called, an agent completing — has a corresponding hook file in this directory. The hooks are not optional middleware; they are the security gate, the context injector, the audit system, and the memory writer. Remove them, and Gaia becomes a collection of agent definitions with no enforcement.

Each hook is a Python script that reads a JSON event from stdin, processes it, and writes a JSON response to stdout. Claude Code calls these scripts synchronously before or after each tool execution, which means the hook can allow, modify, or block the operation. The hook cannot do complex async work — it runs inline, in the critical path, so every module it calls must complete quickly.

The hooks form a pipeline. A session opens at `session_start.py`, which hands the event to the host-agnostic `modules/session/session_lifecycle.py::start_session`; that core emits a one-shot `additionalContext` birth block for the orchestrator, the same one for every source, compaction included (see "The birth block and the kernel" below). Each prompt then enters at `user_prompt_submit.py`, gets routed to an agent, triggers `pre_tool_use.py` before each tool call, generates audit records in `post_tool_use.py`, and closes out in `subagent_stop.py` when the agent finishes. The session closes at `session_end_hook.py`. The remaining event handlers (`stop_hook.py`, `subagent_start.py`, `task_completed.py`, `pre_compact.py`, `post_compact.py`) fire at lifecycle transitions and carry lighter responsibilities. Approval-grant activation is not among them: it rides the `AskUserQuestion` matcher on PostToolUse, in `adapters/claude_code.py::_handle_ask_user_question_result`. The question itself is Gaia's: the orchestrator opens exactly what `gaia approvals question <approval_id>` printed -- one one-line question per command, opened by `[ GAIA-SECURITY ]`, at most four per signature (`gaia/approvals/surface.py`) -- and the PreToolUse `AskUserQuestion` matcher refuses any other shape.

Every registered command runs through `hooks/launch.sh`, which picks the first of `python3`, `python` or `py -3` that is Python 3. OpenCode loads none of these entry points: `opencode/plugin.ts` forwards the host's events to `opencode/bridge.py`, and `adapters/opencode.py` maps them onto the same host-neutral cores (session lifecycle, tool policy, the SubagentStop close). There the plugin, not a hook, checks the `question` call, and only the orchestrator may open a signature question.

## When it runs

```
Session opens
        |
[session_start.py] <- fires on SessionStart (matcher: startup|resume|clear|compact|fork)
        |  Creates or repairs the .claude/hooks link
        |  Registers session in heartbeat-based session_registry
        |  Sweeps stale registry entries, marks expired approval grant rows
        |  EXPIRED in gaia.db (no grant files exist on disk), snapshots
        |  gaia.db at most once per 24 h, sweeps idle worktrees;
        |  on the plugin channel, migrates a database that is behind
        |  Emits one-shot hookSpecificOutput.additionalContext birth block
        |  (## Projects + ## Environment + ## The user + ## User preferences,
        |   and ## Database schema when the database and the code disagree)
        v
User sends prompt
        |
[user_prompt_submit.py] <- fires on UserPromptSubmit event
        |  Refreshes the session heartbeat (throttled, non-fatal)
        |  Emits the unread-notification counter, and nothing when there are none
        |  Skills loaded on-demand: agent-response
        v
Orchestrator dispatches agent (Task/Agent tool call)
        |
[pre_tool_use.py] <- fires on PreToolUse for: Bash, Task, Agent, SendMessage, AskUserQuestion,
        |                 and Read|Edit|Write|Glob|Grep|WebSearch|WebFetch|NotebookEdit
        |  AskUserQuestion calls: an approval question must be the exact object
        |        `gaia approvals question` printed; the hook shows each signature's text
        |  Bash calls: security gate (gaia_cli_only_guard for the orchestrator's Gaia coordination console, blocked_commands, mutative_verbs, cloud_pipe_validator, protected_path_guard)
        |  Task/Agent calls: context injection via DB-backed contracts (project_context_contracts)
        |  Write/Edit calls: protected path validation (_is_protected())
        |  NOTE: .claude/ tree is protected on BOTH surfaces -- _is_protected() for Write/Edit
        |        file_path, protected_path_guard.py for Bash command strings (categorical deny)
        |  Write/Edit calls, subagent-only, non-protected path: advisory
        |        artifact-skill reminder (artifact_skill_map + artifact_skill_reminder) --
        |        always "allow", never blocks
        v
    ALLOWED / BLOCKED / ask dialog (T3)
        |
Tool executes
        |
[post_tool_use.py] <- fires on PostToolUse for: Bash, Task, AskUserQuestion, Skill,
        |                 and on PostToolUseFailure for: Bash
        |  Audits result, logs to the data home's logs/ (~/.gaia/logs, or GAIA_DATA_DIR); a signed call closes on its own
        |        terminal event (success or failure), matched by tool_use_id
        |  Every gaia CLI call appends a `cli.invoked` harness event (verb, flag names, agent, role, session,
        |        outcome, exit code; never argument values); a call PreToolUse denied is recorded there instead,
        |        since no terminal event follows it. Every skill load appends `skill.loaded`
        |        (modules/events/capability_usage.py)
        v
[subagent_stop.py] <- fires on SubagentStop for all agents
        |  Validates the turn's own agent_contract_handoffs row
        |  Records workflow metrics
        |  Writes to episodic memory
        v
[subagent_start.py] <- fires on SubagentStart for all agents
        |  Can inject additional context (e.g. persisted memory output)
```

## The birth block and the kernel

Claude Code replaces a hook's `additionalContext` over 10,000 chars with a 2,000-char preview and a file path, and never asks the model to read the file. Everything Gaia injects is therefore held under `CONTEXT_BUDGET` (9,500, `modules/context/user_sections.py`) on both hosts, so the model always reads it whole.

**The birth block** (`modules/session/session_manifest.py::build_session_context`) is one assembler for every start source. A compaction is one more birth of the same session, not a special refresh: `session_lifecycle.start_context` calls the same function for `startup`, `resume`, `clear`, `fork` and `compact`, and OpenCode's `adapt_session_start` calls it for its first message and for the first message after a compaction. `modules/context/compact_context_builder.py` keeps only the instructions for the summarizer, which end the summary with the same `gaia map` pointer. The block reads, in order:

1. a heading naming the source (`# Gaia session context (source: compact)`), which is the orchestrator's cue that the context was compacted;
2. the alarms that condition every write (database upgrade, data home, workspace, schema mismatch);
3. `## The user` and `## User preferences`, each row whole (`user_sections`, shared with the kernel);
4. `## Resume point`: the session's snapshot (`gaia/session_snapshot.py::render_resume_section`) with the resume point whole, the active task, pending signatures and open contracts. It is absent when the session has nothing to resume;
5. `## Environment`: where the session stands (machine, folder, zone, CLI path, data home, tools), one line pointing at `gaia map` for live state, habits and which capability serves an intent, and the recurring work that is due;
6. `## Projects`.

The order defends what matters most if the block is ever cut. Over the budget it shrinks in a fixed order, and each step runs only when the previous one was not enough:

1. the `## Leftovers` notice leaves the model context; the host still shows it to the user as a system message;
2. the project index (names only: each workspace as its groups, each group as its projects) degrades to one line per workspace naming its groups, then one line naming every group, then a line that counts projects, groups and workspaces;
3. the snapshot lists become counts plus the `gaia session snapshot --session-id` command;
4. whole user rows leave, stalest first (the reader orders them by `updated_at`), and one line names them. No row is ever cut mid-text, and only the rows the block carries count an injection.

**The kernel** (`modules/context/kernel_builder.py::build_kernel_context`) holds the same budget, with OpenCode's `# Your skills` block counted inside it (`with_skills=True`). `# Project context` carries the dispatch project's `can_read` sections in the room left once every other block, the user rows included, is at full size, and it gives way before any user row does. It degrades in three stages: sections whole where they fit and a `gaia context get-contract --section <s> --project <name>` line for the rest, then pointer lines only, then one line that counts the sections and is shown whatever the room. Then the kernel shrinks in this order: `# Your CLI` becomes its `gaia --help` line, which rebuilds the rest, the skills lose their descriptions, `# Standing notes` keeps only the command that lists them, then whole user rows leave, stalest first. `# Your Contract` is never shrunk, so a dispatch prompt over about 8,000 chars can still exceed the cap.

## Entry point -> adapter -> module

Every hook entry point is thin by design. The entry point reads stdin, calls the adapter, and writes stdout. All logic lives in the adapter and module layers.

```
hooks/pre_tool_use.py              <- Entry point: stdin/stdout glue only
  -> adapters/claude_code.py       <- Adapter: parses event, dispatches to modules
    -> modules/security/           <- blocked_commands, mutative_verbs, cloud_pipe_validator, protected_path_guard
    -> modules/context/            <- context_injector, contracts_loader
    -> modules/agents/             <- contract_validator, skill_injection, artifact_skill_map, artifact_skill_reminder
    -> modules/validation/         <- commit_validator
    -> modules/audit/              <- logger, metrics
```

To add a new behavior to an existing hook: write a module in `modules/<package>/`, import it in the adapter, and call it from the relevant adapter method. Modules receive parsed context as arguments and return results. They never read stdin or write stdout directly.

`hooks/mods/` holds Claude Code mods, hooks modules the host loads in-process and that `hooks/hooks.json` names under its top-level `modules` key (generated from the build manifest's `host_mods` list); `mods/compaction/` adds Gaia's steering to every compaction's instructions and nothing else; the session snapshot after a compaction comes only from the SessionStart(compact) refresh. OpenCode's `session.compacting` receives the same `gaia/session_snapshot.py` block from `adapters/opencode.py::adapt_pre_compact`; which contracts it lists and where the resume point is written are in ARCHITECTURE.md's Session snapshot and Resume point rows.

To add a new hook entry point: create `hooks/<event_name>.py`, register it in `build/gaia.manifest.json` under `hooks.entries` and `hooks.matchers`, then write the adapter method. The entry point pattern is always the same: read stdin JSON, call adapter, print response.

## What's here

```
hooks/
├── launch.sh              # Launcher every registered command runs through; picks the first Python 3 of python3, python, py -3
├── user_prompt_submit.py  # Sparse notices + heartbeat refresh
├── pre_tool_use.py        # Security gate + context injection (PreToolUse)
├── post_tool_use.py       # Audit logging (PostToolUse, PostToolUseFailure)
├── subagent_stop.py       # Contract validation + approval cleanup + memory (SubagentStop)
├── subagent_start.py      # Subagent start — additional context injection
├── session_start.py       # SessionStart entry; manifest and registry work run in modules/session/session_lifecycle.py
├── session_end_hook.py    # Unregister session from heartbeat registry (SessionEnd)
├── stop_hook.py           # Stop event handler
├── task_completed.py      # Task completed event handler
├── pre_compact.py         # Pre-compaction event handler
├── post_compact.py        # Post-compaction event handler
├── hooks.json             # Plugin-channel hook configuration — GENERATED, never hand-edited
├── adapters/              # Adapter layer — event parsing and module dispatch
└── modules/               # Module layer — security, context, validation, audit logic
```

**`hooks.json` is a generated artifact — do not edit it by hand.** Its single source of truth is `build/gaia.manifest.json` (`hooks.matchers` + `hooks.entries`); the file is produced by `generate_hooks_json()` in `scripts/build-plugin.py` and regenerated by `npm run generate:plugin-root`. A hand edit that is not also made in the manifest is reverted by the next regeneration, silently: the file comes back byte-identical to its committed state, so `git status` shows nothing and only the runtime behaviour changes. To change a matcher or add an event, edit `build/gaia.manifest.json` and regenerate. Two guards enforce this: `scripts/check_hooks_drift.py` at publish time (via `bin/pre-publish-validate.js`), and `TestHooksJsonManifestSync` in `tests/hooks/adapters/test_plugin_manifests.py` in the suite.

**`modules/events/gaia_cli_grammar.py` is generated too.** It holds the `gaia` CLI's subcommand tree and declared options, which the `cli.invoked` recorder uses to name a call without storing argument values. Its source of truth is `bin/gaia`'s argparse parser; after adding or renaming a subcommand or an option, regenerate it with `python3 scripts/generate_gaia_cli_grammar.py` (`--check` reports drift without writing). `test_the_grammar_is_the_cli_parser_tree_and_its_declared_options` in `tests/hooks/modules/events/test_capability_usage.py` fails on any drift and names that command.

Neither `pre_compact.py` nor `post_compact.py` can deliver model-facing `additionalContext`: Claude Code's hook-output validator has no `PreCompact`/`PostCompact` case in its discriminated union, and its response-consumption switch never applies their output even when the JSON is otherwise well-formed — so both now emit a schema-valid empty `{}` no-op. The real post-compaction context delivery happens via `session_start.py` when SessionStart fires with `source == "compact"` (see above). The pre-compaction window — injecting anything in the narrow moment *before* compaction erases context — remains a genuine platform limitation: no hook shape can inject there, which is why `pre_compact.py` is a registered no-op rather than a carrier.

## Conventions

**Security tiers enforced by pre_tool_use:**

| Tier | Operation Type | Approval | Hook action |
|------|----------------|----------|-------------|
| T0 | Read-only (get, list) | No | Allow immediately |
| T1 | Local validation (validate, lint) | No | Allow immediately |
| T2 | Simulation (plan, diff) | No | Allow immediately |
| T3 | Execution (apply, delete) | Yes — native `ask` dialog | Pause, request approval |
| T3-blocked | Irreversible (delete-vpc, drop db) | Permanently blocked | Exit 2 (hard block) |

**Protected paths** (blocked regardless of permissionMode):
- `.claude/hooks/` — hooks cannot be modified by any agent
- `.claude/settings.json` and `.claude/settings.local.json` — settings cannot be modified by any agent

**Artifact-skill reminder (advisory, never blocking):** on a subagent's Write/Edit to a non-protected path, `pre_tool_use.py` resolves the file's extension to a governing skill via `modules/agents/artifact_skill_map.py` (`expected_skill_for_path`), and — if that skill has not already been reminded this turn — returns `permissionDecision: "allow"` with the reminder text in `hookSpecificOutput.additionalContext`. It is restricted to subagents (`is_subagent=True` with a non-empty `agent_id`); the orchestrator's own foreground writes never trigger it. The reminder always travels in `additionalContext`, never in `permissionDecisionReason`: with `permissionDecision: "allow"`, Claude Code's own hook contract surfaces `permissionDecisionReason` only in logs and the debug transcript, not to the model, so a reminder placed there would never reach the agent it is meant for (see `code.claude.com/docs/en/hooks.md`, "PreToolUse decision control"). Noise is bounded by `modules/agents/artifact_skill_reminder.py` (`should_remind`, `cleanup_stale_markers`): it fires at most once per (session, agent, skill) — once per turn, per artifact class, never per file — via a marker file under `gaia-artifact-skill-reminders/` in Gaia's tmp dir (`gaia.paths.tmp_dir()`). This per-turn dedup is not something Claude Code's hooks provide natively; Gaia implements it itself. The reminder cannot verify whether the agent actually loaded the skill: a subagent's Write/Edit payload carries no `transcript_path` (only `SubagentStop` gets `agent_transcript_path`), and the `Skill` tool is not wired into any `PreToolUse` matcher in `hooks.json`, so a `Skill(...)` call never reaches this hook. That real gap-detection — did the transcript actually show the skill's fingerprint — still lives at `SubagentStop`, in `modules/agents/skill_injection_verifier.py`; this PreToolUse reminder only reminds, it never accuses.

**Tool-call ceiling notice (advisory, never blocking):** a dispatch prompt may carry `ceiling=<N>` beside `project=` and `task_id=` (`modules/agents/dispatch_binding.py::extract_dispatch_binding`); the kernel renders it as `ceiling:` in `# Your Contract`. Each host passes a subagent's call that is allowed to run, with its harness id -- Claude Code at PreToolUse with the payload `agent_id` (`adapters/claude_code.py::_with_ceiling_notice`), OpenCode at `tool.execute.after` with the child session id -- to `modules/agents/call_ceiling.py::record_call`, which reads the ceiling from the turn's row, counts the call in `<data_dir>/state/call_ceiling/<harness id>.json`, and on the call that reaches 80% of `N` returns, once, a notice to write the evidence lists and close. On Claude Code it travels in PreToolUse `additionalContext`, which the host adds to the model's context with the tool result; on OpenCode in `additional_context`, appended to the tool output. PreToolUse is the counting point because its matchers reach Read, Edit, Write, Glob and Grep, which PostToolUse's (Bash, Task, AskUserQuestion) do not. The orchestrator carries no harness id and is never counted, and SubagentStop deletes the count once it accepts the close. Limits: Claude Code tools no PreToolUse matcher names (Skill, TodoWrite, MCP tools) and AskUserQuestion are not counted, while OpenCode counts every tool that ran; and a dispatch without the token gets no notice, because neither host gives a hook the turn's context use.

**Two identifier spaces at SubagentStop — and why draft rescues used to miss.** The harness stamps `hook_data['agent_id']` (e.g. `aac5be534edc91e44`), while `gaia contract init` mints its OWN agent id and keys the on-disk draft by `{minted-agent-id}.{token}`. Both match `^a[0-9a-f]{16,}$`, so confusing them fails SILENTLY: `resolve_draft_id` globs `{harness-id}.*`, matches no file, and returns `None`. That single silent miss disabled BOTH draft rescues at once — the M4 missing-fence reconstruction (`adapters/subagent_stop_core.py::reconstruct_contract_from_finalized_draft`) and step 1a of the T9 backstop (`modules/agents/handoff_persister.py::persist_handoff`), the two paths designed for exactly the lost-fence case. The turn's transcript is one place the spaces meet: `modules/agents/transcript_reader.py::extract_minted_agent_id_from_transcript` recovers the minted id from it, `task_info_builder` precomputes it once per turn as `task_info['minted_agent_id']`, and `handoff_persister.resolve_minted_agent_id` prefers it. **The ROW is the other, and it is the one the current dispatch shape needs.** A turn born with its draft already open never runs `gaia contract init`, so there is no mint report in its transcript to scan; what it does have is a row carrying BOTH identities — `agent_id` (minted, stamped at birth) and `harness_agent_id` (stamped at the SubagentStart claim by `gaia.store.writer.stamp_harness_agent_id`). `handoff_persister.dispatch_row_by_harness_id` is that bridge, shared by the minted-id resolver and by `subagent_stop_core.resolve_dispatch_row`'s first lane, so the gate and the rescues can never disagree about which row is this turn's. It joins on `harness_agent_id` alone — the harness mints that id per dispatch run, so it already names one turn — and treats `session_id` as an after-the-fact consistency check rather than a SQL filter, which cannot make the match more exact but can lose a correct row. Ambiguity (2+ rows under one harness id) is DECLINED with a warning, never resolved by recency. **The harness id is never a fallback identity.** `resolve_minted_agent_id` returns `None` — and logs why — when nothing minted is recoverable; it used to return the harness id as a "last-resort label", and because that value is non-empty it satisfied every `if not minted_agent_id` guard downstream, silently carrying a value that can never key a draft (measured: handoff row 11304 lost a complete `update_contracts` proposal with no log line at all). A caller that genuinely needs a non-empty label for a degraded row supplies its own fallback (the agent NAME). **Mentioning a draft id is not owning it.** The recovery scans ONLY for the `gaia contract init` mint report (its text and `--json` forms), never for the bare dotted draft-id shape: a turn routinely names another agent's draft — an operator asked to recover a peer's contract runs `gaia contract view --draft-id <peer>` after its own `init` — and taking the last match would hand the reconstruction a peer's finalized envelope to seal as this turn's outcome, since `reconstruct_contract_from_finalized_draft` checks only that a terminal row exists, never who owns it. That would trade a silent miss for a silent misattribution, which is worse: a lost contract is noticed, an adopted one is not. Ambiguity therefore fails CLOSED — zero mint reports, or two naming different ids, both yield `None`, which is the pre-existing "no draft found" path.

**A rejected turn no longer loses its work.** When the full-verdict gate rejects a turn (exit 2, because its own `agent_contract_handoffs` row is missing or was never cleanly finalized), the harness delivers the rejection to the SUBAGENT, and the repair turn it produces REPLACES the rejected message in everything the orchestrator receives — so a full diagnosis can be silently swapped for a thin "this only adds the envelope" re-emission. The gate is unchanged; the cost of a rejection is not. `modules/agents/rejected_turn_relay.py` strips any envelope-shaped fence from the rejected message — since the fence was retired there is usually nothing to strip, which costs only a stricter preservation — persists the remaining substantive text under `<data_dir>/rejected_turns/<session>.<agent_id>.txt`, and reinjects it VERBATIM into the rejection message the harness hands back, with an explicit instruction to reproduce it — the only in-band route to an orchestrator that reads the subagent's final message and nothing else. A second rejection carries the ORIGINAL text forward rather than letting a thinner repair overwrite it, and a turn that finally passes reports (`preserved_output_relayed`) whether the text actually came back.

**Harness-observed defect events.** Failures visible only from outside a
subagent are persisted in `harness_events` and appear through `gaia defects
--origin=orchestrator`. `agent.cut` means the Task result claimed completion
but contained no parseable contract fence; it is observational and does not
recover the lost work. `agent.contract_rejected` means the full-verdict gate
rejected the emitted contract; its substantive output may have been preserved
by `rejected_turn_relay.py` as described above. Both are warning-grade by
default and can be isolated with `gaia defects --type=<event>`.

## Project context is keyed by project (schema v69)

A context section describes one project, so it is stored under that project's
identity in `project_context` (`gaia/store/project_context.py`). Before v69 it was
keyed by workspace in `project_context_contracts`, and a section proposed from a
session opened in another workspace landed where no reader of the project looked.

- **Routing** (`modules/context/context_writer.py::_route`): a proposal lands
  under the project the entry names (`"project"`), else, for `project_identity`,
  the project each key names, else the dispatch project of the proposing turn.
  Nothing falls back to the caller's workspace. A part with no resolvable
  project is refused, and SubagentStop stamps the reason on the proposing row
  as `update_contracts_refused`, so the proposal and the refusal both stay on
  the contract for a later proposal store.
- **History**: the writer, not a trigger, appends to `project_context_history`
  on every write, the first insert included, naming the agent and the
  proposing contract id (`gaia context accept` passes both).
- **Fallback per section, not per project**: until the old rows are moved
  (plan 94, task 2) and the old table is retired, a project reads its own row
  for a section and otherwise the old row of its workspace. A per-project
  fallback would hide every unmoved section of a project as soon as one
  section was written. For the same reason a first write merges into the
  section the project's readers saw, so a delta never hides old keys.
- **A workspace is a container, not an owner**: `project_context` holds
  project facts only. Scanner promotion (`tools/scan/promote.py`) moves each
  `project_identity` entry that resolves to a project (path, then name) into
  that project's row. The `_workspace` reserved key, anything describing the
  workspace itself, and any entry no project owns (reported as a warning)
  stay in the workspace-keyed row, which remains the store for
  workspace-level data. `gaia context accept` and SubagentStop both stamp
  refusals on the proposing row as `update_contracts_refused`.
- **Workspace readers** (`workspace_sections`: the context provider, the
  contracts loader, freshness, the session roster, `get-contract --workspace`)
  read the old row merged with the rows of that workspace's projects.
- **Compatibility**: `v68_to_v69.sql` is declared breaking. Code older than v69
  would keep writing the old table, which readers no longer prefer, so it must
  stop writing. Ship it with an install of every local host that shares
  `gaia.db` (Claude Code and OpenCode).

## See also

- [`build/gaia.manifest.json`](../build/gaia.manifest.json) — hook registration and matchers
- `surface_routing` table (`~/.gaia/gaia.db`) — DB-backed routing read by `tools/context/surface_router.py`; seeded from agent `routing:` frontmatter by `tools/scan/seed_surface_routing.py` (replaced `config/surface-routing.json`)
- [`config/context-contracts.json`](../config/context-contracts.json) — seeding source for context contracts; runtime SSOT is `~/.gaia/gaia.db` (`project_context_contracts` table)
- [`skills/security-tiers/SKILL.md`](../skills/security-tiers/SKILL.md) — tier classification that agents use; hook enforces the same tiers
