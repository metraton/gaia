---
name: gaia-patterns
description: Use when building or modifying gaia-ops components -- agents, skills, hooks, CLI tools, commands, or routing config
metadata:
  user-invocable: false
  type: domain
---

# Gaia-Ops Code Patterns

Construction patterns for building Gaia components. Every component type follows a discoverable pattern -- read 2-3 existing examples before creating a new one. For the full component inventory, see `reference.md`.

## Prompt -> Result Flow

```
1. User sends prompt
   |
2. Orchestrator routes to agent (surface-routing.json)
   |
3. Pre-Tool Hook (pre_tool_use.py)
   +-- Inject project-context.json
   +-- Load skills from frontmatter
   +-- Validate permissions
   |
4. Agent executes -> returns json:contract
   |
5. Post-Tool Hook -> audit + metrics
   |
6. Orchestrator processes plan_status (APPROVAL_REQUEST / NEEDS_INPUT / COMPLETE)
```

## Hook Patterns

Entry points (`hooks/*.py`) are stdin/stdout glue only. All logic lives in the adapter layer.

```
hooks/pre_tool_use.py          -- reads stdin, calls adapter, writes stdout
  -> adapters/claude_code.py   -- parses event, dispatches to modules
    -> modules/security/*      -- blocked_commands, mutative_verbs
    -> modules/context/*       -- context_injector, contracts_loader
    -> modules/agents/*        -- contract_validator, skill_injection
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
  - context-updater       # if agent modifies project state
---
```

**Identity** (1-2 paragraphs): domain, output format. **Scope**: CAN DO / CANNOT DO -> DELEGATE table. **Domain Errors**: agent-specific errors only.

Agents get instantiated as: identity (.md) + skills (injected from frontmatter) + project-context (filtered by context-contracts.json) + orchestrator request.

## Routing Patterns

`config/surface-routing.json` maps user intent to agents. Each surface has: `intent`, `primary_agent`, `adjacent_surfaces`, and `signals` (high/medium confidence keyword patterns).

**To add a surface:** Add entry to `surfaces` with intent + primary_agent + signals. Update L1 routing tests.
**To add a signal:** Add keyword patterns to the appropriate confidence level in an existing surface.

## CLI Tool Patterns

CLI tools live in `bin/` and are registered in `package.json` `bin` field. Pattern: parse args, resolve paths (follow symlinks to source), run checks, exit with code. `gaia-doctor` is the diagnostic model -- read it first.

## Command Patterns

Slash commands live in `commands/<name>.md` -- markdown files that instruct the orchestrator on `/<name>`. To add: create the `.md`, add to `build/<plugin>.manifest.json`.

## Documentation Drift Awareness

When you modify any Gaia component (hook, skill, agent definition, routing config, security rule), check if existing reference docs describe that component's behavior. If drift exists, report it via `cross_layer_impacts` in your json:contract. The orchestrator then decides whether to dispatch a documentation update task.

**Do NOT update docs yourself** -- your job is to flag the drift and let the orchestrator choose the next action.

**Examples of drift to flag:**
- Changed `_is_protected()` paths in `adapters/claude_code.py` → check `security-tiers/SKILL.md` for path documentation
- Added a new agent definition → check `gaia-patterns/reference.md` for agents table
- Modified hook enforcement logic → check `security-tiers` and `agent-protocol` references
- When adding or modifying files in agents/, skills/, hooks/, commands/, config/, bin/, tests/, build/, templates/ or the repo root, load Skill('readme-writing') to update the relevant README.md

**Format:** In `cross_layer_impacts`, list the doc file and the behavior change, e.g.:
```
"cross_layer_impacts": [
  "security-tiers/SKILL.md: _is_protected() now excludes .claude/settings.local.json"
]
```

## Key Principles

- **Skills teach process. Agents teach identity. Runtime enforces contracts.** Never duplicate across these layers.
- **Delegation first.** The orchestrator routes; it cannot read files, run commands, or edit code.
- **Consolidation loop.** For multi-surface work, the orchestrator may dispatch multiple agent rounds, stopping when gaps are no longer actionable.
