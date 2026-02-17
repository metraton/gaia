# Skills System

Skills are knowledge modules that extend agent capabilities. They use Claude Code's native skill system for automatic discovery and injection.

## Architecture

```
.claude/skills/
├── agent-protocol/        # AGENT_STATUS, local-first, error handling
├── security-tiers/        # T0-T3 classification
├── output-format/         # Report structure and icons
├── context-updater/       # CONTEXT_UPDATE format
├── git-conventions/       # Conventional commits
├── fast-queries/          # Quick diagnostic scripts
├── terraform-patterns/    # Terraform/Terragrunt patterns
├── gitops-patterns/       # GitOps/Flux patterns
├── command-execution/     # Defensive execution, timeout protection, safe shell patterns
├── investigation/         # Local-first analysis methodology
├── approval/              # T3 plan presentation workflow
└── execution/             # Post-approval execution workflow
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
---
```

## Skill Assignment Matrix

| Agent | Core Skills | Domain Skills |
|-------|-------------|---------------|
| cloud-troubleshooter | security-tiers, output-format, agent-protocol, context-updater | fast-queries, command-execution |
| terraform-architect | security-tiers, output-format, agent-protocol, context-updater | terraform-patterns, command-execution |
| gitops-operator | security-tiers, output-format, agent-protocol, context-updater | gitops-patterns, command-execution |
| devops-developer | security-tiers, output-format, agent-protocol, context-updater | command-execution |
| gaia | security-tiers, output-format, agent-protocol | git-conventions |
| speckit-planner | output-format | |

## Skill Types

| Type | Injection | Examples |
|------|-----------|----------|
| **Core** | Always via `skills:` | agent-protocol, security-tiers, output-format |
| **Domain** | Per-agent via `skills:` | terraform-patterns, gitops-patterns |
| **Workflow** | On-demand (agent reads file) | investigation, approval, execution |

Workflow skills are large (200-500 lines) and loaded on-demand. Agents read them from disk when needed rather than receiving them at startup.

## SKILL.md Format

```yaml
---
name: skill-name
description: When Claude should use this skill
user-invocable: false  # Background knowledge, not a slash command
---

# Skill Content

Instructions and patterns the agent follows.
```

## Development Guidelines

- Keep skills focused and specific
- Use `user-invocable: false` for background knowledge
- Keep injected skills under 100 lines (move details to supporting files)
- Reference workflow skills as readable files, not injected content
