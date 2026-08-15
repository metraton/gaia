---
name: gaia-patterns
description: Use when building or modifying Gaia components -- agents, skills, hooks, CLI tools, or routing config
---

# Gaia Code Patterns

Construction patterns for building Gaia components. Every component type follows a discoverable pattern -- read 2-3 existing examples before creating a new one. For the full component inventory, see `reference.md`.

## Prompt -> Result Flow

```
1. User sends prompt
   |
2. Orchestrator routes to agent (DB-backed surface_routing table --
   the routing is the ORCHESTRATOR's reasoning; it is never injected
   into the subagent)
   |
3. Pre-Tool Hook (pre_tool_use.py)
   +-- Validate the Task/Agent dispatch (agent exists, T3 scan of the
       prompt via TaskValidator) -- no frontmatter read, no skill load
   +-- Birth the agent_contract_handoffs row (identity, kernel data,
       dispatch_project resolved from cwd)
   |
   (for a dispatched SUBAGENT only: the HOST -- Claude Code, not this
   hook or any other Gaia hook -- separately reads the target agent's
   `.md` frontmatter and preloads its `skills:` list before the first
   turn. `hooks/hooks.json` carries no `Skill` matcher, so a
   `Skill(...)` call never reaches PreToolUse either -- see
   `artifact_skill_reminder.py`. The primary agent has no equivalent:
   Claude Code's own docs list only "system prompt, tool restrictions,
   and model" as inherited on the main thread, which is why
   `gaia-orchestrator.md`'s frontmatter carries no `skills:` field.)
   |
4. SubagentStart hook claims the born row and injects the KERNEL:
   "# Your Contract" (incl. project + can_read/can_write menu),
   "# Your CLI", "# What I know about you". Project context is NOT
   preloaded -- the agent pulls sections on demand, with the verbs in
   agent-protocol/read-map.md, within its can_read menu.
   |
5. Agent checkpoints/finalizes its agent_contract_handoffs row via
   `gaia contract`; the final message carries no envelope
   |
6. Post-Tool Hook -> audit + metrics
   |
7. Orchestrator processes agent_state; only COMPLETE is terminal
```

## Hook Patterns

Entry points (`hooks/*.py`) are stdin/stdout glue only. All logic lives in the adapter layer.

```
hooks/pre_tool_use.py          -- reads stdin, calls adapter, writes stdout
  -> adapters/claude_code.py   -- parses event, dispatches to modules
    -> modules/security/*      -- blocked_commands, mutative_verbs
    -> modules/context/*       -- contracts_loader, kernel_builder
    -> modules/agents/*        -- dispatch_binding, artifact_skill_map
```

**To add a new module:** Write module in `modules/<package>/`, import and call it from the relevant adapter method. Modules receive parsed context and return results; they never read stdin or write stdout.

**To add a new hook entry point:** Create `hooks/<event_name>.py`, register it in `build/<plugin>.manifest.json`, add matchers. The entry point reads stdin JSON, calls the adapter, and prints the response.

## Agent Patterns

```yaml
---
name: agent-name
description: Routing label -- triggers when orchestrator sees matching intent
tools: Read, Edit, Write, Glob, Grep, Bash  # restrict per domain
model: inherit
permissionMode: acceptEdits  # required for most agents; omit only for orchestrator and read-only agents
skills:
  - agent-protocol        # always first
  - security-tiers        # always second
  - command-execution     # if agent runs commands
  - domain-skill          # agent's domain patterns
cli:
  - "gaia <domain-verb> ..."   # optional; appends a line to "# Your CLI"
---
```

**Identity** (1-2 paragraphs): domain, output format. **Scope**: CAN DO / CANNOT DO -> DELEGATE table. **Domain Errors**: agent-specific errors only.

`skills:` and `cli:` are read by two different parties, not one -- this is why the example shows both. `skills:` is preloaded by the HOST (Claude Code), not by any Gaia hook, and only for a dispatched SUBAGENT: the primary agent (`gaia-orchestrator.md`) carries no `skills:` field, because Claude Code's own docs list only "system prompt, tool restrictions, and model" as inherited on the main thread. `cli:` is the one frontmatter field Gaia itself reads, at subagent birth (`context/kernel_builder.py::_agent_cli_extras`): its lines are appended verbatim to the "# Your CLI" kernel block. It is optional and no shipped agent declares it yet -- shown here so the two fields' different owners (host vs. Gaia) are visible side by side, not to imply it is required.

A dispatched SUBAGENT is instantiated as: identity (.md) + skills (preloaded by the host from frontmatter) + dispatch kernel (# Your Contract / # Your CLI / # What I know about you, rendered from the born row) + orchestrator request. The primary agent skips the skills step entirely. Project context is not preloaded: the kernel's `can_read` (from `agent_contract_permissions`) is the menu of `project_context_contracts` sections the agent pulls on demand -- the verb that reaches them, and the same-named sibling that does not, are in `agent-protocol/read-map.md`.

## Routing Patterns

The DB-backed `surface_routing` table maps user intent to agents. The source of truth is each agent's `routing:` frontmatter block (`agents/*.md`): `surface`, `adjacent_surfaces`, `signals` (`commands`/`artifacts`), `required_checks`, optional `sub_surfaces`. Keywords were retired as a signal source -- the matcher (`tools/context/surface_router.py::_score_surface`) scores `commands` and `artifacts` only; a legacy `keywords` key in a signals block is ignored by scoring. The surface's `intent` is the agent's `description`; `contract_sections` derives from `project_context_contracts.read`. `tools/scan/seed_surface_routing.py` seeds the table at install time (mirror of `seed_contract_permissions.py`); `tools/context/surface_router.py` reads it via `load_surface_routing_config()`.

**To add a surface:** Add a `routing:` block to the owning agent's frontmatter, register the agent in `build/gaia.manifest.json`, re-run `gaia install`, and update the surface-router tests.
**To add a signal:** Add command/artifact patterns to the owning agent's `routing:` block.

## CLI Tool Patterns

CLI tools live in `bin/` and are registered in `package.json` `bin` field. Pattern: parse args, resolve paths (follow symlinks to source), run checks, exit with code. `gaia doctor` is the diagnostic model -- read it first.

## Documentation Drift Awareness

When you modify any Gaia component (hook, skill, agent definition, routing config, security rule), check if existing reference docs describe that component's behavior. If drift exists, report it via `cross_layer_impacts` in your contract row. The orchestrator then decides whether to dispatch a documentation update task.

**Do NOT update docs yourself** -- your job is to flag the drift and let the orchestrator choose the next action.

**Examples of drift to flag:**
- Changed `_is_protected()` paths in `adapters/claude_code.py` → check `security-tiers/SKILL.md` for path documentation
- Added a new agent definition → check `gaia-patterns/reference.md` for agents table
- Modified hook enforcement logic → check `security-tiers` and `agent-protocol` references
- When adding or modifying files in agents/, skills/, hooks/, config/, bin/, tests/, build/ or the repo root, load Skill('readme-writing') to update the relevant README.md

**Format:** In `cross_layer_impacts`, list the doc file and the behavior change, e.g.:
```
"cross_layer_impacts": [
  "security-tiers/SKILL.md: _is_protected() now excludes .claude/settings.local.json"
]
```

## Key Principles

- **Skills teach process. Agents teach identity. Runtime enforces contracts.** Never duplicate across these layers.
- **Coordination before execution.** The orchestrator cannot edit code or run general commands. Its guarded Gaia CLI lane reads coordination state and persists only decisions it owns; domain evidence and execution still belong to specialists.
- **Consolidation loop.** For multi-surface work, the orchestrator may dispatch multiple agent rounds, stopping when gaps are no longer actionable.
