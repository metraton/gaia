# Skills

Skills are the procedural knowledge layer of Gaia. Where agents carry identity — their scope, their tone, their domain — skills carry process: how to classify a command, how to format a response contract, how to approach an investigation. An agent without skills knows who it is but not how to operate. Skills bridge that gap by injecting step-by-step protocols that the agent follows during its session.

Each skill lives in its own directory under `skills/<name>/` and contains at minimum a `SKILL.md` file. That file is what gets injected. Supporting material (`reference.md`, `examples.md`) lives in the same directory but is read on-demand — the agent pulls it from disk when needed rather than receiving it at startup. This keeps startup context lean while making full documentation accessible.

Skills are not shared via inheritance or imports — they are text injected verbatim into the agent's context window. The size limit for injected skills is roughly 100 lines. If a skill grows beyond that, the detailed content moves to `reference.md` and the main `SKILL.md` becomes a compact index pointing there.

The assignment matrix below shows which skills each agent receives. The first two — `agent-protocol` and `security-tiers` — appear on every agent. They are the non-negotiables: every agent must understand the response contract and the tier system.

## Cuándo se activa

Skills reach an agent through two distinct routes, and understanding both matters when troubleshooting why a skill is or is not present in a session.

**Route 1 — Startup injection via frontmatter:**

```
Orchestrator dispatches agent
        |
pre_tool_use.py intercepts the Task/Agent tool call
        |
Reads agents/<name>.md frontmatter -> skills: list
        |
For each skill in the list:
  reads skills/<skill>/SKILL.md from disk
  appends content to agent's system context
        |
Agent starts with all listed skills already in context
```

**Route 2 — On-demand via Skill tool:**

```
Agent is running and encounters a situation
requiring a workflow skill (e.g. approval, execution, git-conventions)
        |
Agent calls Skill tool: Skill("request-approval")
        |
Claude Code reads skills/request-approval/SKILL.md from disk
        |
Content is injected into the agent's active context window
        |
Agent follows the newly loaded protocol
```

Orchestrator-level skills (`agent-response`, `orchestrator-approval`) are always Route 2 — they are never in a frontmatter list, only loaded when the orchestrator needs to interpret a specific situation.

## Qué hay aquí

```
skills/
├── agent-protocol/        # Response contract format, state machine, error handling
├── agent-response/        # Orchestrator: interpret agent json:contract responses
├── security-tiers/        # T0-T3 classification + hook enforcement model
│   └── reference.md
├── investigation/         # Diagnosis methodology and pattern analysis
├── command-execution/     # Defensive Bash execution, no-pipes discipline
│   └── reference.md
├── context-updater/       # CONTEXT_UPDATE format and writable sections contract
│   └── examples.md
├── git-conventions/       # Conventional Commits (on-demand workflow skill)
├── skill-creation/        # How to design and write new skills
├── gaia-patterns/         # Gaia component patterns: hooks, agents, routing, CLI
│   └── reference.md
├── terraform-patterns/    # Terraform/Terragrunt patterns
│   └── reference.md
├── gitops-patterns/       # GitOps/Flux/Kubernetes patterns
│   └── reference.md
├── developer-patterns/    # Application code patterns (Node.js, Python)
├── fast-queries/          # Quick diagnostic scripts for cloud/system state
├── gaia-planner/          # Feature planning, briefs, task decomposition
├── orchestrator-approval/ # T3 approval presentation for orchestrator
├── gaia-compact/          # Orchestrator: structured /compact prompt with preservation contract
├── request-approval/      # T3 approval-request workflow (attempt first, emit APPROVAL_REQUEST)
│   ├── reference.md
│   └── examples.md
├── execution/             # Post-approval execution discipline
├── gmail-policy/          # Gmail domain policy (label-only, no delete)
├── memory-curation/       # Curate MEMORY.md index and topic files
├── memory-search/         # Query episodic memory via `gaia memory` CLI
├── gaia-self-check/       # Validate internal consistency of the .claude/ installation
├── readme-writing/        # How to write READMEs for Gaia component folders
└── reference.md           # Cross-skill reference index
```

## Convenciones

**Skill assignment matrix:**

| Agent | Core Skills | Domain Skills |
|-------|-------------|---------------|
| cloud-troubleshooter | agent-protocol, security-tiers, command-execution | fast-queries, investigation |
| terraform-architect | agent-protocol, security-tiers, command-execution, terraform-patterns | fast-queries |
| gitops-operator | agent-protocol, security-tiers, command-execution, gitops-patterns | fast-queries |
| developer | agent-protocol, security-tiers, command-execution, developer-patterns | fast-queries |
| gaia-system | agent-protocol, security-tiers, gaia-patterns, skill-creation | - |
| gaia-planner | agent-protocol, security-tiers | - |

Orchestrator skills (loaded on-demand via Skill tool, not assigned in frontmatter):
- `agent-response` — contract status interpretation and presentation
- `orchestrator-approval` — T3 approval presentation and grant activation
- `gaia-compact` — structured `/compact` invocation with a six-category preservation prompt

**Skill types:**

| Type | Injection | Examples |
|------|-----------|---------|
| Core | Always via `skills:` frontmatter | agent-protocol, security-tiers |
| Common | Most agents via `skills:` frontmatter | command-execution, context-updater |
| Domain | Per-agent via `skills:` frontmatter | terraform-patterns, gaia-patterns |
| Workflow | On-demand (agent reads from disk) | request-approval, execution, git-conventions |
| Orchestrator | On-demand via Skill tool | agent-response, orchestrator-approval |

**SKILL.md format:**

```yaml
---
name: skill-name
description: When Claude should load and follow this skill
metadata:
  user-invocable: false
  type: core
---

# Skill Content
```

**Line budget:** Keep injected `SKILL.md` under 100 lines. Move details to `reference.md` (read on-demand). Supporting examples go in `examples.md`.

## Ver también

- [`agents/README.md`](../agents/README.md) — agent frontmatter and skills: field
- [`hooks/pre_tool_use.py`](../hooks/pre_tool_use.py) — where skill injection happens at runtime
- [`skills/skill-creation/SKILL.md`](./skill-creation/SKILL.md) — how to design a new skill
- [`skills/gaia-patterns/reference.md`](./gaia-patterns/reference.md) — full component inventory
