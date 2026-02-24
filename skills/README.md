# Skills System

Skills are knowledge modules that extend agent capabilities. They use Claude Code's native skill system for automatic discovery and injection.

## Architecture

```
.claude/skills/
├── agent-protocol/        # AGENT_STATUS, local-first, error handling
├── security-tiers/        # T0-T3 classification
├── output-format/         # Report structure and icons
├── context-updater/       # CONTEXT_UPDATE format
│   └── examples.md
├── git-conventions/       # Conventional commits
├── fast-queries/          # Quick diagnostic scripts
├── terraform-patterns/    # Terraform/Terragrunt patterns
│   └── reference.md
├── gitops-patterns/       # GitOps/Flux patterns
│   └── reference.md
├── command-execution/     # Defensive execution, timeout protection, safe shell patterns
├── investigation/         # Diagnosis methodology and pattern analysis
├── approval/              # T3 plan presentation and approval/rejection workflow
│   └── examples.md
└── execution/             # Post-approval execution protocol
```

## How Skills Work

Skills are assigned to agents via the `skills:` field in agent frontmatter (`.claude/agents/<name>.md`). Claude Code injects the full skill content at subagent startup.

```yaml
# Example: agents/cloud-troubleshooter.md
---
name: cloud-troubleshooter
skills:
  - security-tiers
  - output-format
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
| cloud-troubleshooter | security-tiers, output-format, agent-protocol, context-updater, investigation, command-execution | fast-queries |
| terraform-architect | security-tiers, output-format, agent-protocol, context-updater, investigation, command-execution, git-conventions | terraform-patterns, fast-queries |
| gitops-operator | security-tiers, output-format, agent-protocol, context-updater, investigation, command-execution, git-conventions | gitops-patterns, fast-queries |
| devops-developer | security-tiers, output-format, agent-protocol, context-updater, investigation, command-execution, git-conventions | |
| gaia | security-tiers, output-format, agent-protocol, investigation, command-execution, git-conventions | |
| speckit-planner | security-tiers, output-format, agent-protocol | speckit.* (9 skills) |

## Skill Types

| Type | Injection | Examples |
|------|-----------|----------|
| **Core** | Always via `skills:` | agent-protocol, security-tiers, output-format, investigation |
| **Common** | Most agents via `skills:` | context-updater, command-execution, git-conventions |
| **Domain** | Per-agent via `skills:` | terraform-patterns, gitops-patterns, fast-queries |
| **Workflow** | On-demand (agent reads file) | approval, execution |

Workflow skills are loaded on-demand — agents read them from disk when needed rather than receiving them at startup. Supporting files (`examples.md`, `reference.md`) are also read on-demand.

## SKILL.md Format

```yaml
---
name: skill-name
description: When Claude should use this skill
user-invocable: false  # Background knowledge, not a slash command
type: core             # Optional: core, common, domain, workflow
---

# Skill Content

Instructions and patterns the agent follows.
```

## Development Guidelines

- Keep skills focused and specific
- Use `user-invocable: false` for background knowledge
- Keep injected skills under 100 lines (move details to supporting files)
- Reference workflow skills as readable files, not injected content
- Avoid duplicating content across skills — use references instead
