# Commands

Slash commands are direct shortcuts into Gaia's orchestration layer. When you type `/gaia` or `/scan-project`, Claude Code detects the slash prefix, finds the matching `.md` file in this directory, and injects its contents as instructions to the currently active orchestrator. There is no subagent spawn — the orchestrator reads the command file and executes inline.

This makes slash commands different from agent dispatch. An agent dispatch creates a new Claude Code subprocess with its own identity, skills, and tool set. A slash command is a context injection into the orchestrator's current session. Think of it as a macro: the `.md` file says "when the user invokes this command, do the following." The orchestrator follows those instructions directly.

The practical implication is that slash commands are best suited for tasks that the orchestrator can complete by delegating to existing agents — not tasks that require a new agent identity. They are entry points, not agents.

## Cuándo se activa

```
User types /command-name [args]
        |
Claude Code detects / prefix
        |
Looks up commands/<command-name>.md
        |
Injects the file's contents into the orchestrator's active context
        |
Orchestrator reads the command instructions and executes them
  (may dispatch agents, call tools, or respond directly)
        |
Result returned to user in the current session
```

No subagent is spawned. No new identity is loaded. The orchestrator handles execution within its current session using its existing tool set and the instructions from the command file.

## Qué hay aquí

```
commands/
├── gaia.md           # /gaia — invoke the Gaia meta-agent (gaia-system) for system work
└── scan-project.md   # /scan-project — scan codebase, detect stack, update project-context.json
```

Note: a `/gaia-plan` command is referenced in some older documentation but the file does not exist here. Planning is handled conversationally through the orchestrator and the `gaia-planner` agent — not via a slash command.

## Convenciones

**File format:**

```markdown
---
name: command-name
description: One-line description shown in Claude Code autocomplete
---

# Command Name

[Instructions the orchestrator follows when this command is invoked]
```

**Registration:** Each command file must also be listed in `build/gaia-ops.manifest.json` under the `commands` array. A file that exists here but is not in the manifest will not appear in Claude Code's slash command list.

**Scope:** Commands inject instructions into the orchestrator. If the task requires domain work (Terraform, code changes, cloud ops), the command's instructions should dispatch the appropriate agent — the command itself should not attempt domain execution.

**Arguments:** Slash commands can receive arguments after the command name (e.g., `/gaia-plan Add OAuth2 support`). The command's `.md` file can reference these as context, and the orchestrator receives them as part of the injected content.

## Ver también

- [`build/gaia-ops.manifest.json`](../build/gaia-ops.manifest.json) — command registration
- [`agents/gaia-system.md`](../agents/gaia-system.md) — the Gaia meta-agent invoked by `/gaia`
- [`agents/gaia-orchestrator.md`](../agents/gaia-orchestrator.md) — orchestrator that executes command instructions
- [`skills/gaia-planner/SKILL.md`](../skills/gaia-planner/SKILL.md) — planning workflow (used by gaia-planner agent, not a slash command)
