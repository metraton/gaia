# Hooks

Hooks are the event-driven spine of Gaia. Every significant moment in a Claude Code session — a prompt arriving, a tool being called, an agent completing — has a corresponding hook file in this directory. The hooks are not optional middleware; they are the security gate, the context injector, the audit system, and the memory writer. Remove them, and Gaia becomes a collection of agent definitions with no enforcement.

Each hook is a Python script that reads a JSON event from stdin, processes it, and writes a JSON response to stdout. Claude Code calls these scripts synchronously before or after each tool execution, which means the hook can allow, modify, or block the operation. The hook cannot do complex async work — it runs inline, in the critical path, so every module it calls must complete quickly.

The hooks form a pipeline. A prompt enters at `user_prompt_submit.py`, gets routed to an agent, triggers `pre_tool_use.py` before each tool call, generates audit records in `post_tool_use.py`, and closes out in `subagent_stop.py` when the agent finishes. The event handler hooks (`session_start.py`, `stop_hook.py`, `subagent_start.py`, `task_completed.py`) fire at lifecycle transitions and carry lighter responsibilities.

## Cuándo se activa

```
User sends prompt
        |
[user_prompt_submit.py] <- fires on UserPromptSubmit event
        |  Builds orchestrator identity via ops_identity.py
        |  Injects surface routing recommendation (deterministic, from surface-routing.json)
        |  Skills loaded on-demand: agent-response
        v
Orchestrator dispatches agent (Task/Agent tool call)
        |
[pre_tool_use.py] <- fires on PreToolUse for: Bash, Task, Agent, SendMessage, Write, Edit
        |  Bash calls: security gate (blocked_commands, mutative_verbs, cloud_pipe_validator)
        |  Task/Agent calls: context injection from context-contracts.json
        |  Write/Edit calls: protected path validation (_is_protected())
        v
    ALLOWED / BLOCKED / ask dialog (T3)
        |
Tool executes
        |
[post_tool_use.py] <- fires on PostToolUse for: Bash, AskUserQuestion
        |  Audits result, logs to .claude/logs/
        v
[subagent_stop.py] <- fires on SubagentStop for all agents
        |  Validates json:contract format
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
    -> modules/security/           <- blocked_commands, mutative_verbs, cloud_pipe_validator
    -> modules/context/            <- context_injector, contracts_loader
    -> modules/agents/             <- contract_validator, skill_injection
    -> modules/validation/         <- commit_validator
    -> modules/audit/              <- logger, metrics
```

To add a new behavior to an existing hook: write a module in `modules/<package>/`, import it in the adapter, and call it from the relevant adapter method. Modules receive parsed context as arguments and return results. They never read stdin or write stdout directly.

To add a new hook entry point: create `hooks/<event_name>.py`, register it in `build/gaia-ops.manifest.json` under `hooks.entries` and `hooks.matchers`, then write the adapter method. The entry point pattern is always the same: read stdin JSON, call adapter, print response.

## Qué hay aquí

```
hooks/
├── user_prompt_submit.py  # Identity injection (UserPromptSubmit event)
├── pre_tool_use.py        # Security gate + context injection (PreToolUse)
├── post_tool_use.py       # Audit logging (PostToolUse)
├── subagent_stop.py       # Contract validation + memory (SubagentStop)
├── subagent_start.py      # Subagent start — additional context injection
├── session_start.py       # Session start event handler
├── stop_hook.py           # Stop event handler
├── task_completed.py      # Task completed event handler
├── pre_compact.py         # Pre-compaction event handler
├── post_compact.py        # Post-compaction event handler
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

- [`build/gaia-ops.manifest.json`](../build/gaia-ops.manifest.json) — hook registration and matchers
- [`config/surface-routing.json`](../config/surface-routing.json) — read by `user_prompt_submit.py`
- [`config/context-contracts.json`](../config/context-contracts.json) — read by `pre_tool_use.py` for context injection
- [`skills/security-tiers/SKILL.md`](../skills/security-tiers/SKILL.md) — tier classification that agents use; hook enforces the same tiers
