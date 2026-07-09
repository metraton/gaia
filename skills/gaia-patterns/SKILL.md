---
name: gaia-patterns
description: Use when building or modifying Gaia components -- agents, skills, hooks, CLI tools, or routing config
metadata:
  user-invocable: false
  type: domain
---

# Gaia Code Patterns

Construction patterns for building Gaia components. Every component type follows a discoverable pattern -- read 2-3 existing examples before creating a new one. For the full component inventory, see `reference.md`.

## Prompt -> Result Flow

```
1. User sends prompt
   |
2. Orchestrator routes to agent (DB-backed surface_routing table)
   |
3. Pre-Tool Hook (pre_tool_use.py)
   +-- Inject project context (from ~/.gaia/gaia.db via context_provider.py)
   +-- Load skills from frontmatter
   +-- Validate permissions
   |
4. Agent executes -> returns agent_contract_handoff
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
---
```

**Identity** (1-2 paragraphs): domain, output format. **Scope**: CAN DO / CANNOT DO -> DELEGATE table. **Domain Errors**: agent-specific errors only.

Agents get instantiated as: identity (.md) + skills (injected from frontmatter) + project-context (filtered by DB-backed contracts from `project_context_contracts`) + orchestrator request.

## Routing Patterns

The DB-backed `surface_routing` table maps user intent to agents. The source of truth is each agent's `routing:` frontmatter block (`agents/*.md`): `surface`, `adjacent_surfaces`, `signals` (`commands`/`artifacts`), `required_checks`, optional `sub_surfaces`. Keywords were retired as a signal source -- the matcher (`tools/context/surface_router.py::_score_surface`) scores `commands` and `artifacts` only; a legacy `keywords` key in a signals block is ignored by scoring. The surface's `intent` is the agent's `description`; `contract_sections` derives from `project_context_contracts.read`. `tools/scan/seed_surface_routing.py` seeds the table at install time (mirror of `seed_contract_permissions.py`); `tools/context/surface_router.py` reads it via `load_surface_routing_config()`.

**To add a surface:** Add a `routing:` block to the owning agent's frontmatter, register the agent in `build/gaia.manifest.json`, re-run `gaia install`, and update the surface-router tests.
**To add a signal:** Add command/artifact patterns to the owning agent's `routing:` block.

## CLI Tool Patterns

CLI tools live in `bin/` and are registered in `package.json` `bin` field. Pattern: parse args, resolve paths (follow symlinks to source), run checks, exit with code. `gaia doctor` is the diagnostic model -- read it first.

## Documentation Drift Awareness

When you modify any Gaia component (hook, skill, agent definition, routing config, security rule), check if existing reference docs describe that component's behavior. If drift exists, report it via `cross_layer_impacts` in your agent_contract_handoff. The orchestrator then decides whether to dispatch a documentation update task.

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
- **Delegation first.** The orchestrator routes; it cannot run commands or edit code, and it reads a file directly only to triangulate evidence with the user (a document or an image validated together) -- never as a substitute for a specialist's investigation.
- **Consolidation loop.** For multi-surface work, the orchestrator may dispatch multiple agent rounds, stopping when gaps are no longer actionable.
