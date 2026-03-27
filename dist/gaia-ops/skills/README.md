# Skills System

Skills are knowledge modules that extend agent capabilities. They use Claude Code's native skill system for automatic discovery and injection.

## Architecture

```
.claude/skills/
├── agent-protocol/        # json:contract format, state machine, repair flow
├── agent-response/        # Orchestrator: interpret agent json:contract responses
├── security-tiers/        # T0-T3 classification
│   └── reference.md
├── investigation/         # Diagnosis methodology and pattern analysis
├── command-execution/     # Defensive execution, safe shell patterns
│   └── reference.md
├── context-updater/       # CONTEXT_UPDATE format and contract-driven writable sections
│   └── examples.md
├── git-conventions/       # Conventional commits (on-demand)
├── skill-creation/        # How to create new skills
├── gaia-patterns/         # Gaia meta-system patterns
│   └── reference.md
├── terraform-patterns/    # Terraform/Terragrunt patterns
│   └── reference.md
├── gitops-patterns/       # GitOps/Flux patterns
│   └── reference.md
├── developer-patterns/    # Developer workflow patterns
├── fast-queries/          # Quick diagnostic scripts
├── speckit-workflow/      # Speckit phase management
├── specification/         # Feature specification workflow
├── orchestrator-approval/ # T3 approval presentation for orchestrator
├── approval/              # T3 plan presentation and approval workflow
│   └── examples.md
├── execution/             # Post-approval execution protocol
└── reference.md           # Cross-skill reference
```

## How Skills Work

Skills are assigned to agents via the `skills:` field in agent frontmatter (`.claude/agents/<name>.md`). Claude Code injects the full skill content at subagent startup.

```yaml
# Example: agents/cloud-troubleshooter.md
---
name: cloud-troubleshooter
skills:
  - security-tiers
  - agent-protocol
  - context-updater
  - fast-queries
  - command-execution
  - investigation
---
```

## Skill Assignment Matrix

| Agent | Core Skills | Domain Skills |
|-------|-------------|---------------|
| cloud-troubleshooter | agent-protocol, security-tiers | fast-queries |
| terraform-architect | agent-protocol, security-tiers, terraform-patterns | fast-queries |
| gitops-operator | agent-protocol, security-tiers, gitops-patterns | fast-queries |
| devops-developer | agent-protocol, security-tiers, developer-patterns | fast-queries |
| gaia | agent-protocol, security-tiers, gaia-patterns, skill-creation | - |
| speckit-planner | agent-protocol, security-tiers, speckit-workflow | - |

Orchestrator skills (loaded on-demand via Skill tool, not assigned to agents):
- **agent-response** -- contract status interpretation and presentation
- **orchestrator-approval** -- T3 approval presentation and grant activation

## Skill Types

| Type | Injection | Examples |
|------|-----------|----------|
| **Core** | Always via `skills:` | agent-protocol, security-tiers |
| **Common** | Most agents via `skills:` | command-execution, context-updater |
| **Domain** | Per-agent via `skills:` | terraform-patterns, gitops-patterns, developer-patterns, gaia-patterns |
| **Workflow** | On-demand (agent reads file) | approval, execution, git-conventions |
| **Orchestrator** | On-demand via Skill tool | agent-response, orchestrator-approval |

Workflow skills are loaded on-demand -- agents read them from disk when needed rather than receiving them at startup. Supporting files (`examples.md`, `reference.md`) are also read on-demand.

## SKILL.md Format

```yaml
---
name: skill-name
description: When Claude should use this skill
metadata:
  user-invocable: false  # Background knowledge, not a slash command
  type: core             # Optional: core, common, domain, workflow
---

# Skill Content

Instructions and patterns the agent follows.
```

## Development Guidelines

- Keep skills focused and specific
- Use `metadata.user-invocable: false` for background knowledge
- Keep injected skills under 100 lines (move details to supporting files)
- Reference workflow skills as readable files, not injected content
- Avoid duplicating content across skills -- use references instead
- See `skill-creation/SKILL.md` for detailed creation guidelines

---

**Updated:** 2026-03-19 | **Total skills:** 20
