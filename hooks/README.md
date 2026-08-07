# Hooks

Hooks are the event-driven spine of Gaia. Every significant moment in a Claude Code session — a prompt arriving, a tool being called, an agent completing — has a corresponding hook file in this directory. The hooks are not optional middleware; they are the security gate, the context injector, the audit system, and the memory writer. Remove them, and Gaia becomes a collection of agent definitions with no enforcement.

Each hook is a Python script that reads a JSON event from stdin, processes it, and writes a JSON response to stdout. Claude Code calls these scripts synchronously before or after each tool execution, which means the hook can allow, modify, or block the operation. The hook cannot do complex async work — it runs inline, in the critical path, so every module it calls must complete quickly.

The hooks form a pipeline. A session opens at `session_start.py`, which emits a one-shot `additionalContext` manifest (Environment, Active Agentic Loop) for the orchestrator; when SessionStart instead fires with `source == "compact"` (right after compaction), `session_start.py` builds a different, lighter manifest — a post-compaction context refresh (agent roster + active anomalies) — in place of the full startup manifest. Each prompt then enters at `user_prompt_submit.py`, gets routed to an agent, triggers `pre_tool_use.py` before each tool call, generates audit records in `post_tool_use.py`, and closes out in `subagent_stop.py` when the agent finishes. The session closes at `session_end_hook.py`. The remaining event handlers (`stop_hook.py`, `subagent_start.py`, `task_completed.py`, `pre_compact.py`, `post_compact.py`, `elicitation_result.py`) fire at lifecycle transitions and carry lighter responsibilities.

## Cuándo se activa

```
Session opens
        |
[session_start.py] <- fires on SessionStart (matcher: startup|resume|compact)
        |  Registers session in heartbeat-based session_registry
        |  Sweeps stale registry entries and expired approval files
        |  Emits one-shot hookSpecificOutput.additionalContext manifest
        |  (Environment + Active Agentic Loop)
        v
User sends prompt
        |
[user_prompt_submit.py] <- fires on UserPromptSubmit event
        |  Refreshes the session heartbeat (throttled, non-fatal)
        |  Emits sparse first-run and unread-notification notices
        |  First-run welcome on the install's first prompt only
        |  Skills loaded on-demand: agent-response
        v
Orchestrator dispatches agent (Task/Agent tool call)
        |
[pre_tool_use.py] <- fires on PreToolUse for: Bash, Task, Agent, SendMessage,
        |                 and Read|Edit|Write|Glob|Grep|WebSearch|WebFetch|NotebookEdit
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
[post_tool_use.py] <- fires on PostToolUse for: Bash, AskUserQuestion
        |  Audits result, logs to .claude/logs/
        v
[subagent_stop.py] <- fires on SubagentStop for all agents
        |  Validates agent_contract_handoff format
        |  Records workflow metrics
        |  Writes to episodic memory
        v
[subagent_start.py] <- fires on SubagentStart for all agents
        |  Can inject additional context (e.g. persisted memory output)
```

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

To add a new hook entry point: create `hooks/<event_name>.py`, register it in `build/gaia.manifest.json` under `hooks.entries` and `hooks.matchers`, then write the adapter method. The entry point pattern is always the same: read stdin JSON, call adapter, print response.

## Qué hay aquí

```
hooks/
├── user_prompt_submit.py  # Sparse notices + heartbeat refresh
├── pre_tool_use.py        # Security gate + context injection (PreToolUse)
├── post_tool_use.py       # Audit logging (PostToolUse)
├── subagent_stop.py       # Contract validation + approval cleanup + memory (SubagentStop)
├── subagent_start.py      # Subagent start — additional context injection
├── session_start.py       # Session manifest + registry registration (SessionStart)
├── session_end_hook.py    # Unregister session from heartbeat registry (SessionEnd)
├── stop_hook.py           # Stop event handler
├── task_completed.py      # Task completed event handler
├── pre_compact.py         # Pre-compaction event handler
├── post_compact.py        # Post-compaction event handler
├── elicitation_result.py  # AskUserQuestion result handler (approval activation)
├── hooks.json             # Plugin-channel hook configuration — GENERATED, never hand-edited
├── adapters/              # Adapter layer — event parsing and module dispatch
└── modules/               # Module layer — security, context, validation, audit logic
```

**`hooks.json` is a generated artifact — do not edit it by hand.** Its single source of truth is `build/gaia.manifest.json` (`hooks.matchers` + `hooks.entries`); the file is produced by `generate_hooks_json()` in `scripts/build-plugin.py` and regenerated by `npm run generate:plugin-root`. A hand edit that is not also made in the manifest is reverted by the next regeneration, silently: the file comes back byte-identical to its committed state, so `git status` shows nothing and only the runtime behaviour changes. To change a matcher or add an event, edit `build/gaia.manifest.json` and regenerate. Two guards enforce this: `scripts/check_hooks_drift.py` at publish time (via `bin/pre-publish-validate.js`), and `TestHooksJsonManifestSync` in `tests/hooks/adapters/test_plugin_manifests.py` in the suite.

Neither `pre_compact.py` nor `post_compact.py` can deliver model-facing `additionalContext`: Claude Code's hook-output validator has no `PreCompact`/`PostCompact` case in its discriminated union, and its response-consumption switch never applies their output even when the JSON is otherwise well-formed — so both now emit a schema-valid empty `{}` no-op. The real post-compaction context delivery happens via `session_start.py` when SessionStart fires with `source == "compact"` (see above). The pre-compaction checkpoint window (saving agentic-loop state in the narrow moment *before* compaction erases context) remains a genuine platform limitation — no hook shape can inject there.

## Convenciones

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

**Artifact-skill reminder (advisory, never blocking):** on a subagent's Write/Edit to a non-protected path, `pre_tool_use.py` resolves the file's extension to a governing skill via `modules/agents/artifact_skill_map.py` (`expected_skill_for_path`), and — if that skill has not already been reminded this turn — returns `permissionDecision: "allow"` with the reminder text in `hookSpecificOutput.additionalContext`. It is restricted to subagents (`is_subagent=True` with a non-empty `agent_id`); the orchestrator's own foreground writes never trigger it. The reminder always travels in `additionalContext`, never in `permissionDecisionReason`: with `permissionDecision: "allow"`, Claude Code's own hook contract surfaces `permissionDecisionReason` only in logs and the debug transcript, not to the model, so a reminder placed there would never reach the agent it is meant for (see `code.claude.com/docs/en/hooks.md`, "PreToolUse decision control"). Noise is bounded by `modules/agents/artifact_skill_reminder.py` (`should_remind`, `cleanup_stale_markers`): it fires at most once per (session, agent, skill) — once per turn, per artifact class, never per file — via a marker file under `/tmp/gaia-artifact-skill-reminders/`. This per-turn dedup is not something Claude Code's hooks provide natively; Gaia implements it itself. The reminder cannot verify whether the agent actually loaded the skill: a subagent's Write/Edit payload carries no `transcript_path` (only `SubagentStop` gets `agent_transcript_path`), and the `Skill` tool is not wired into any `PreToolUse` matcher in `hooks.json`, so a `Skill(...)` call never reaches this hook. That real gap-detection — did the transcript actually show the skill's fingerprint — still lives at `SubagentStop`, in `modules/agents/skill_injection_verifier.py`; this PreToolUse reminder only reminds, it never accuses.

**Two identifier spaces at SubagentStop — and why draft rescues used to miss.** The harness stamps `hook_data['agent_id']` (e.g. `aac5be534edc91e44`), while `gaia contract init` mints its OWN agent id and keys the on-disk draft by `{minted-agent-id}.{token}`. Both match `^a[0-9a-f]{16,}$`, so confusing them fails SILENTLY: `resolve_draft_id` globs `{harness-id}.*`, matches no file, and returns `None`. That single silent miss disabled BOTH draft rescues at once — the M4 missing-fence reconstruction (`adapters/claude_code.py::_reconstruct_contract_from_finalized_draft`) and step 1a of the T9 backstop (`modules/agents/handoff_persister.py::persist_handoff`), the two paths designed for exactly the lost-fence case. The turn's transcript is where the spaces meet: `modules/agents/transcript_reader.py::extract_minted_agent_id_from_transcript` recovers the minted id from it, `task_info_builder` precomputes it once per turn as `task_info['minted_agent_id']`, and `handoff_persister.resolve_minted_agent_id` prefers it. The harness id remains only as a last-resort label for stamping a degraded row — never as a draft key. **Mentioning a draft id is not owning it.** The recovery scans ONLY for the `gaia contract init` mint report (its text and `--json` forms), never for the bare dotted draft-id shape: a turn routinely names another agent's draft — an operator asked to recover a peer's contract runs `gaia contract view --draft-id <peer>` after its own `init` — and taking the last match would hand the reconstruction a peer's finalized envelope to seal as this turn's outcome, since `_reconstruct_contract_from_finalized_draft` checks only that a terminal row exists, never who owns it. That would trade a silent miss for a silent misattribution, which is worse: a lost contract is noticed, an adopted one is not. Ambiguity therefore fails CLOSED — zero mint reports, or two naming different ids, both yield `None`, which is the pre-existing "no draft found" path.

**A rejected turn no longer loses its work.** When the full-verdict gate rejects a turn (exit 2, missing/invalid `agent_contract_handoff` fence), the harness delivers the rejection to the SUBAGENT, and the repair turn it produces REPLACES the rejected message in everything the orchestrator receives — so a full diagnosis can be silently swapped for a thin "this only adds the envelope" re-emission. The gate is unchanged; the cost of a rejection is not. `modules/agents/rejected_turn_relay.py` strips the contract fences from the rejected message, persists the remaining substantive text under `<data_dir>/rejected_turns/<session>.<agent_id>.txt`, and reinjects it VERBATIM into the rejection message the harness hands back, with an explicit instruction to reproduce it — the only in-band route to an orchestrator that reads the subagent's final message and nothing else. A second rejection carries the ORIGINAL text forward rather than letting a thinner repair overwrite it, and a turn that finally passes reports (`preserved_output_relayed`) whether the text actually came back.

**Harness-observed defect events.** Failures visible only from outside a
subagent are persisted in `harness_events` and appear through `gaia defects
--origin=orchestrator`. `agent.cut` means the Task result claimed completion
but contained no parseable contract fence; it is observational and does not
recover the lost work. `agent.contract_rejected` means the full-verdict gate
rejected the emitted contract; its substantive output may have been preserved
by `rejected_turn_relay.py` as described above. Both are warning-grade by
default and can be isolated with `gaia defects --type=<event>`.

## Ver también

- [`build/gaia.manifest.json`](../build/gaia.manifest.json) — hook registration and matchers
- `surface_routing` table (`~/.gaia/gaia.db`) — DB-backed routing read by `tools/context/surface_router.py`; seeded from agent `routing:` frontmatter by `tools/scan/seed_surface_routing.py` (replaced `config/surface-routing.json`)
- [`config/context-contracts.json`](../config/context-contracts.json) — seeding source for context contracts; runtime SSOT is `~/.gaia/gaia.db` (`project_context_contracts` table)
- [`skills/security-tiers/SKILL.md`](../skills/security-tiers/SKILL.md) — tier classification that agents use; hook enforces the same tiers
