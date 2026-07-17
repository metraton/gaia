# Hooks

Hooks are the event-driven spine of Gaia. Every significant moment in a Claude Code session — a prompt arriving, a tool being called, an agent completing — has a corresponding hook file in this directory. The hooks are not optional middleware; they are the security gate, the context injector, the audit system, and the memory writer. Remove them, and Gaia becomes a collection of agent definitions with no enforcement.

Each hook is a Python script that reads a JSON event from stdin, processes it, and writes a JSON response to stdout. Claude Code calls these scripts synchronously before or after each tool execution, which means the hook can allow, modify, or block the operation. The hook cannot do complex async work — it runs inline, in the critical path, so every module it calls must complete quickly.

The hooks form a pipeline. A session opens at `session_start.py`, which emits a one-shot `additionalContext` manifest (Environment, Active Agentic Loop) for the orchestrator. Each prompt then enters at `user_prompt_submit.py`, gets routed to an agent, triggers `pre_tool_use.py` before each tool call, generates audit records in `post_tool_use.py`, and closes out in `subagent_stop.py` when the agent finishes. The session closes at `session_end_hook.py`. The remaining event handlers (`stop_hook.py`, `subagent_start.py`, `task_completed.py`, `pre_compact.py`, `post_compact.py`, `elicitation_result.py`) fire at lifecycle transitions and carry lighter responsibilities.

## Cuándo se activa

```
Session opens
        |
[session_start.py] <- fires on SessionStart (matcher: startup)
        |  Registers session in heartbeat-based session_registry
        |  Sweeps stale registry entries and expired approval files
        |  Emits one-shot hookSpecificOutput.additionalContext manifest
        |  (Environment + Active Agentic Loop)
        v
User sends prompt
        |
[user_prompt_submit.py] <- fires on UserPromptSubmit event
        |  Refreshes the session heartbeat (throttled, non-fatal)
        |  Injects deterministic Surface Routing Recommendation (per-turn signal)
        |  First-run welcome on the install's first prompt only
        |  Skills loaded on-demand: agent-response
        v
Orchestrator dispatches agent (Task/Agent tool call)
        |
[pre_tool_use.py] <- fires on PreToolUse for: Bash, Task, Agent, SendMessage, Write, Edit
        |  Bash calls: security gate (blocked_commands, mutative_verbs, cloud_pipe_validator, protected_path_guard)
        |  Task/Agent calls: context injection via DB-backed contracts (project_context_contracts)
        |  Write/Edit calls: protected path validation (_is_protected())
        |  NOTE: .claude/ tree is protected on BOTH surfaces -- _is_protected() for Write/Edit
        |        file_path, protected_path_guard.py for Bash command strings (categorical deny)
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
    -> modules/agents/             <- contract_validator, skill_injection
    -> modules/validation/         <- commit_validator
    -> modules/audit/              <- logger, metrics
```

To add a new behavior to an existing hook: write a module in `modules/<package>/`, import it in the adapter, and call it from the relevant adapter method. Modules receive parsed context as arguments and return results. They never read stdin or write stdout directly.

To add a new hook entry point: create `hooks/<event_name>.py`, register it in `build/gaia.manifest.json` under `hooks.entries` and `hooks.matchers`, then write the adapter method. The entry point pattern is always the same: read stdin JSON, call adapter, print response.

## Qué hay aquí

```
hooks/
├── user_prompt_submit.py  # Per-turn routing recommendation + heartbeat refresh
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
├── hooks.json             # Plugin-channel hook configuration
├── adapters/              # Adapter layer — event parsing and module dispatch
└── modules/               # Module layer — security, context, validation, audit logic
```

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

## Ver también

- [`build/gaia.manifest.json`](../build/gaia.manifest.json) — hook registration and matchers
- `surface_routing` table (`~/.gaia/gaia.db`) — DB-backed routing read by `tools/context/surface_router.py`; seeded from agent `routing:` frontmatter by `tools/scan/seed_surface_routing.py` (replaced `config/surface-routing.json`)
- [`config/context-contracts.json`](../config/context-contracts.json) — seeding source for context contracts; runtime SSOT is `~/.gaia/gaia.db` (`project_context_contracts` table)
- [`skills/security-tiers/SKILL.md`](../skills/security-tiers/SKILL.md) — tier classification that agents use; hook enforces the same tiers
