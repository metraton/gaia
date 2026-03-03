# Skills System

Skills are knowledge modules that extend agent capabilities. They use Claude Code's native skill system for automatic discovery and injection.

## Architecture

```
.claude/skills/
├── agent-protocol/        # AGENT_STATUS, search protocol, error handling
├── security-tiers/        # T0-T3 classification
│   └── reference.md
├── output-format/         # Report structure and icons
├── investigation/         # Diagnosis methodology and pattern analysis
├── command-execution/     # Defensive execution, safe shell patterns
│   └── reference.md
├── context-updater/       # CONTEXT_UPDATE format
│   └── examples.md
├── git-conventions/       # Conventional commits
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
| cloud-troubleshooter | agent-protocol, security-tiers, output-format, investigation, command-execution, context-updater | fast-queries |
| terraform-architect | agent-protocol, security-tiers, output-format, investigation, command-execution, context-updater, git-conventions | terraform-patterns, fast-queries |
| gitops-operator | agent-protocol, security-tiers, output-format, investigation, command-execution, context-updater, git-conventions | gitops-patterns, fast-queries |
| devops-developer | agent-protocol, security-tiers, output-format, investigation, command-execution, context-updater, git-conventions | developer-patterns |
| gaia | agent-protocol, security-tiers, output-format, investigation, command-execution, git-conventions | gaia-patterns, skill-creation |
| speckit-planner | agent-protocol, security-tiers, output-format | speckit-workflow, speckit.* (9 phase skills) |

## Skill Types

| Type | Injection | Examples |
|------|-----------|----------|
| **Core** | Always via `skills:` | agent-protocol, security-tiers, output-format, investigation |
| **Common** | Most agents via `skills:` | command-execution, context-updater, git-conventions |
| **Domain** | Per-agent via `skills:` | terraform-patterns, gitops-patterns, developer-patterns, gaia-patterns |
| **Workflow** | On-demand (agent reads file) | approval, execution |

Workflow skills are loaded on-demand -- agents read them from disk when needed rather than receiving them at startup. Supporting files (`examples.md`, `reference.md`) are also read on-demand.

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
- Avoid duplicating content across skills -- use references instead
- See `skill-creation/SKILL.md` for detailed creation guidelines

---

**Updated:** 2026-03-03 | **Total skills:** 17
