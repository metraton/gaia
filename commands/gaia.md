---
name: gaia
description: Invoke the Gaia meta-agent for system architecture analysis, agent design, skill creation, and orchestration debugging
allowed-tools:
  - Bash(*)
  - Read
  - Edit
  - Write
  - Glob
  - Grep
  - WebSearch
  - WebFetch
  - Task
  - Agent
  - Skill
---

Invoke the Gaia meta-agent (`gaia-system`) to work on the gaia-ops orchestration
system itself. This is the entry point for tasks that modify or analyze agents,
skills, hooks, or system architecture.

## When to use

- Analyze or improve the gaia-ops architecture
- Create or update agent definitions (`.md` files)
- Create or update skills (`SKILL.md` files)
- Write or debug Python hooks and tools
- Update `CLAUDE.md` or system configuration
- Research best practices for agent orchestration

## How it works

This command delegates to the `gaia-system` agent, which is the meta-agent
specialized in the orchestration system. It follows the standard agent protocol
and returns a `json:contract` block with findings and status.

$ARGUMENTS
