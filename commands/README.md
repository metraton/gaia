# Gaia-Ops Slash Commands

Slash commands are quick shortcuts that let you invoke specific system functions directly. They're like keyboard shortcuts for common tasks.

## Purpose

Slash commands provide a fast and consistent way to access advanced features without needing to write complete natural language requests.

**Problem it solves:** Some tasks require direct invocation of specific tools. Instead of verbosely describing what you want to do, you simply use a slash command.

## How It Works

### Architecture Flow

```
User types /command
        |
[Claude Code] detects / pattern
        |
[Command Handler] loads command .md file
        |
[Orchestrator] executes command instructions
        |
Result to user
```

## Available Commands

### Planning Commands

#### `/gaia-plan`
Plan a feature -- create a brief and decompose into verifiable tasks.

**When to use:**
- Starting a new feature or project
- Breaking down work into agent-dispatched tasks

**Example:**
```bash
/gaia-plan
/gaia-plan Add OAuth2 authentication
/gaia-plan --execute .claude/project-context/briefs/auth/brief.md
```

**What it does:**
- Sizes the work (S/M/L)
- Asks focused questions for M/L features
- Writes a brief with acceptance criteria and verify commands
- Decomposes into Tasks dispatched to domain agents

---

### Project Commands

#### `/scan-project`
Scan the current project to detect stack, infrastructure, tools, and generate/update `project-context.json`.

**When to use:**
- First time setting up Gaia in a project
- After significant project changes

---

## General Usage

### Basic Syntax

```bash
/command [arguments]
```

### Common Features

**Autocomplete:**
Claude Code suggests available commands when typing `/`

**Inline help:**
All commands support contextual help if invoked without arguments

**Validation:**
Commands validate arguments and give clear feedback if information is missing

### Difference vs Natural Language

| Natural Language | Slash Command |
|------------------|---------------|
| "Create a spec for OAuth authentication" | Talk to the orchestrator conversationally |

**Advantages of slash commands:**
- Faster
- Consistent syntax
- Direct tool invocation
- Less ambiguous

**When to use natural language:**
- Exploratory questions
- Problem diagnosis
- Complex queries

## Technical Details

### Command Structure

Each command is a Markdown file in `commands/[name].md` with frontmatter:

```markdown
---
name: command
description: Brief description
usage: Usage syntax
---

# Command

[Detailed instructions for the orchestrator]
```

### Available Commands

```
commands/
├── gaia-plan.md
├── gaia.md
└── scan-project.md
```

**Total:** 3 commands

> **Note:** The Gaia meta-agent is invoked directly via the `gaia` agent (see [agents/gaia-system.md](../agents/gaia-system.md)), not as a slash command.

## References

**Related documentation:**
- [Gaia Planner](../skills/gaia-planner/reference.md) - Planning workflow reference
- [Gaia Agent](../agents/gaia-system.md) - The meta-agent
- [Episodic Memory](../tools/memory/episodic.py) - Context memory system
- [Config](../config/) - System configuration

**Underlying tools:**
- Context provider: `tools/context/context_provider.py`
- Episodic memory: `tools/memory/episodic.py`

---

**Version:** 4.2.0
**Last updated:** 2026-03-11
**Total commands:** 5 spec-kit
**Maintained by:** Gaia (meta-agent)
